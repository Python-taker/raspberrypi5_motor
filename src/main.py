#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py
────────────────────────────────────────────────────────
- MQTT 수신 3종:
    1) control/hvac/{id}/value
    2) control/hvac/{id}/tsv  및  status/hvac/{id}/tsv
    3) control/hvac/{id}/power_server
- 상태 발행: status/hvac/{id}/all

변경 사항(서보 관련):
- 부팅 직후: ServoAPI OE Enable → home_all() 수행
- 소프트킬 전환 시:
  • killed=True(OFF 진입): 안전 OFF 적용 후, 서보 OE Enable → home_all() → OE Disable
  • killed=False(ON 복귀): 서보 OE Enable → home_all()
- OE 제어 메서드명 수정: global_enable_outputs / global_disable_outputs
"""

# =====================================================
# 0) Imports & Env
# =====================================================
import os
import sys
import time
import signal
import threading
from pathlib import Path
from typing import Any, List

from dotenv import load_dotenv

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import (
    HVAC_ID,
    TOPIC_STATUS_ALL,
    TOPICS_SUB,
    TOPICS_PUB,
    STATUS_USE_APPLIED,  # ★
    LED_TSV_ORDER,       # ★
    LED_HW_ORDER,        # ★
)
from mqtt_client import MQTTClient

from actuators.services.peltier import PeltierService, MIN_ON_DUTY_DEFAULT
from actuators.services.servo import ServoService
from actuators.drivers import bts7960_peltier_pwm as pdrv
from actuators.drivers.pca9685_servo_module import ServoAPI  # ServoAPI.home_all() 사용

try:
    from actuators.services.fans import FanService
except ModuleNotFoundError:
    from services.fans import FanService

try:
    from actuators.services.leds import LedService
except ModuleNotFoundError:
    from services.leds import LedService

from utils.energy_meter import estimate_energy_wh_30s

try:
    from actuators.drivers.arduino_bridge import ArduinoFanLedBridge as ArduinoFanLedClient
except Exception:
    try:
        from actuators.drivers.arduino_fan_led import ArduinoFanLedClient
    except Exception:
        ArduinoFanLedClient = None

from controls.softkill import SoftKillController  # ★

softkill: SoftKillController | None = None        # ★
_softkill_killed = False                          # ★

# =====================================================
# 1) Globals
# =====================================================
load_dotenv()
BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

mqttc: MQTTClient | None = None
svc_peltier = PeltierService(min_on_duty=MIN_ON_DUTY_DEFAULT, rounding="floor")
# 허용 오차(디그리). 같다고 볼 범위. 환경변수로 조정 가능 (기본 0.2°)
SERVO_EPS_DEG = float(os.getenv("SERVO_EPS_DEG", "0.2"))

# 기존: svc_servo = ServoService()
# 미세 떨림 줄이려면 0.1도 라운딩(선택)
svc_servo   = ServoService(round_to=1)   # 0.1° 단위 반올림
svc_leds    = LedService()
svc_fans = FanService(min_small_on=int(os.getenv("MIN_SMALL_FAN_ON", "30")))

servo_drv: ServoAPI | None = None
ardu: Any = None
_shutdown = threading.Event()

# 적용된 값(하드웨어에 실제 반영된 상태)
state = {
    "peltier_pwm": 0,
    "fan_small_pwm": [0] * 8,
    "fan_main_pwm": 0,
    "servo_internal": [0.0] * 4,
    "servo_external": [0.0] * 4,
    "led_colors": ["W", "W", "W", "W"],
}

# 원본 값(수신 그대로, 최소한의 형상만 보정)
raw_cmd = {
    "peltier_pwm": 0,
    "fan_small_pwm": [0] * 8,
    "fan_main_pwm": 0,
    "servo_internal": [0.0] * 4,
    "servo_external": [0.0] * 4,
}

# =====================================================
# 2) Drivers
# =====================================================
def _servo_home_all(tag: str = "") -> None:
    """서보 OE Enable 후 전채널 홈으로. 예외는 조용히 무시."""
    if not servo_drv:
        return
    try:
        # OE Enable (라즈베리  /OE 핀 사용 시 실제로 Enable)
        if hasattr(servo_drv, "global_enable_outputs"):
            servo_drv.global_enable_outputs()
        print(f"🔧 Servo home_all() 시작 {('('+tag+')') if tag else ''}")
        servo_drv.home_all()
        print("✅ Servo home_all() 완료")
    except Exception as e:
        print(f"⚠ Servo home_all 실패: {e}")

def _servo_disable_outputs() -> None:
    if not servo_drv:
        return
    try:
        if hasattr(servo_drv, "global_disable_outputs"):
            servo_drv.global_disable_outputs()
    except Exception:
        pass

def _softkill_init():
    """소프트킬 컨트롤러 초기화 + 콜백 연결."""
    global softkill, _softkill_killed

    # 환경변수로 핀/극성 설정 (없으면 기본값)
    btn   = int(os.getenv("GPIO_KILL_PIN", "-1"))
    red   = int(os.getenv("GPIO_LED_RED_PIN", "-1"))
    green = int(os.getenv("GPIO_LED_GREEN_PIN", "-1"))
    active_low      = os.getenv("GPIO_KILL_ACTIVE_LOW", "1") in ("1", "true", "True")
    led_active_high = os.getenv("GPIO_LED_ACTIVE_HIGH", "1") in ("1", "true", "True")

    def _on_change(killed: bool, reason: str):
        """버튼/원격으로 소프트킬 상태 변경 시 실제 장치/LED/MQTT 처리."""
        nonlocal btn, red, green
        global _softkill_killed
        _softkill_killed = bool(killed)

        if killed:
            # === OFF 진입 ===
            # 1) 핵심 장치 OFF
            try: pdrv.set_duty(0)
            except: pass
            try: pdrv.safe_init()      # EN 라인 LOW
            except: pass

            # 2) 팬/LED 안전 상태
            _apply_fans([0] * 8, 0)    # 아두이노 팬 OFF
            cols_logic = svc_leds.for_driver_colors_effective(softkill_killed=True, off_token="OFF")
            cols_hw    = _remap4(cols_logic, LED_HW_ORDER, fill="OFF")
            _ardu_send_leds(cols_hw)

            # 3) 서보: OE Enable → 홈 → OE Disable (정지자세로 수납)
            _servo_home_all(tag="softkill→OFF")
            _servo_disable_outputs()

        else:
            # === ON 복귀 ===
            try: pdrv.safe_init()
            except: pass
            try: pdrv.enable_forward()
            except: pass

            # 서보 OE Enable → 홈(기준자세)로 맞춘 뒤 동작 시작
            _servo_home_all(tag="softkill→ON")

            # LED는 최신 TSV 매핑 재적용(논리→물리 리맵 후 전송)
            cols_logic = svc_leds.for_driver_colors_effective(softkill_killed=False)
            cols_hw    = _remap4(cols_logic, LED_HW_ORDER, fill="W")
            _ardu_send_leds(cols_hw)

        # 대시보드/서버에 “라즈베리파이 액추에이터 전원 상태” 통지
        if mqttc:
            topic = f"control/hvac/{HVAC_ID}/power_actuator"
            mqttc.publish_json(topic, {"hvac_id": HVAC_ID, "power": ("off" if killed else "on")})

        _publish_status()  # 상태 한 번 발행

    # 컨트롤러 만들기 (기본: 부팅 시 GREEN=켜짐)
    softkill = SoftKillController(
        button_pin=btn,
        active_low=active_low,
        led_red_pin=red,
        led_green_pin=green,
        led_active_high=led_active_high,
        on_change=_on_change,
        initial_killed=False,   # 부팅=ON(GREEN)
    )
    # 내부 플래그 초기 동기화
    _softkill_killed = softkill.is_killed


def _driver_init() -> None:
    global servo_drv, ardu
    pdrv.safe_init()
    pdrv.enable_forward()
    pdrv.set_duty(0)

    try:
        # home=False로 만들고 아래에서 명시적으로 home_all() 실행
        servo_drv = ServoAPI(home=False)
        print("✅ Servo 준비 완료.")
        # === 부팅 직후: OE Enable → home_all() 수행 ===
        _servo_home_all(tag="startup")
    except Exception as e:
        print(f"⚠ Servo 초기화 생략: {e}")
        servo_drv = None

    if ArduinoFanLedClient is not None:
        try:
            ardu = ArduinoFanLedClient()
            if hasattr(ardu, "connect"):
                ardu.connect()
            print("✅ Arduino 연결 완료.")
        except Exception as e:
            print(f"⚠ Arduino 연결 생략(오류): {e}")

    print(f"✅ Drivers ready (BTS7960 MIN_ON={MIN_ON_DUTY_DEFAULT}%)")
    _softkill_init()  # ★


def _driver_safe_off() -> None:
    try:
        pdrv.set_duty(0)
    except Exception:
        pass
    if ardu:
        try:
            if hasattr(ardu, "set_fans"):
                ardu.set_fans([0, 0, 0, 0, 0])
            if hasattr(ardu, "set_leds"):
                ardu.set_leds(["W", "W", "W", "W"])
        except Exception:
            pass

# =====================================================
# 3) Helpers
# =====================================================
def _to_pwm_list(value: Any, max_len: int) -> List[int]:
    lst: List[int] = []
    if isinstance(value, (list, tuple)):
        for x in value:
            try:
                v = int(float(x))
            except Exception:
                v = 0
            lst.append(max(0, min(100, v)))
    elif value is not None:
        try:
            v = int(float(value))
        except Exception:
            v = 0
        lst = [max(0, min(100, v))]
    if len(lst) < max_len:
        lst += [0] * (max_len - len(lst))
    return lst[:max_len]


def _to_num_list(value: Any, max_len: int, as_float: bool = False) -> List[float | int]:
    """원본 보존용: 0~100 클램프 없이 숫자 변환만, 길이 보정"""
    lst: List[float | int] = []
    if isinstance(value, (list, tuple)):
        seq = list(value)
    elif value is None:
        seq = []
    else:
        seq = [value]
    for x in seq[:max_len]:
        try:
            num = float(x)
        except Exception:
            num = 0.0
        lst.append(num if as_float else int(num))
    if len(lst) < max_len:
        lst += [0.0 if as_float else 0] * (max_len - len(lst))
    return lst[:max_len]


def _extract_tsv4(data: dict) -> List[float]:
    if not isinstance(data, dict):
        return [0, 0, 0, 0]
    tsv = data.get("tsv")
    if isinstance(tsv, (list, tuple)):
        return list(tsv)[:4]
    if isinstance(tsv, dict):
        for k in ("tsv", "values", "slots"):
            v = tsv.get(k)
            if isinstance(v, (list, tuple)):
                return list(v)[:4]
    for k in ("values", "slots"):
        v = data.get(k)
        if isinstance(v, (list, tuple)):
            return list(v)[:4]
    return [0, 0, 0, 0]


def _airflow_word() -> str:
    return "on" if (any(state["fan_small_pwm"]) or state["fan_main_pwm"] > 0) else "off"


def _publish_status() -> None:
    if mqttc is None:
        return

    # 어떤 소스를 쓸지 선택
    if STATUS_USE_APPLIED:
        slot_internal = state["servo_internal"]
        slot_external = state["servo_external"]
        fan_small     = state["fan_small_pwm"]
        fan_main      = state["fan_main_pwm"]
    else:
        slot_internal = raw_cmd["servo_internal"]
        slot_external = raw_cmd["servo_external"]
        fan_small     = raw_cmd["fan_small_pwm"]
        fan_main      = raw_cmd["fan_main_pwm"]

    # 소비에너지 추정은 실제 적용값 기준
    energy_wh_30s = estimate_energy_wh_30s(
        peltier_pwm=state["peltier_pwm"],
        fan_small_pwms=state["fan_small_pwm"],
        fan_large_pwm=state["fan_main_pwm"],
        duration_sec=30.0,
        include_servos=True,
    )

    payload = {
        "hvac_id": HVAC_ID,
        "data": {
            "airflow_speed": _airflow_word(),
            "slot_internal": slot_internal,
            "slot_external": slot_external,
            "fan_intake_speed": fan_small[:4],
            "fan_main_speed": fan_main,
            "energy_temp_total": energy_wh_30s,
        },
    }
    mqttc.publish_json(TOPIC_STATUS_ALL, payload)

# ---- Arduino send helpers ----
def _ardu_send_fans(s1: int, s2: int, s3: int, s4: int, big: int) -> None:
    if not ardu:
        return
    try:
        if hasattr(ardu, "set_fans"):
            print(f"↪ Arduino.set_fans([{s1},{s2},{s3},{s4},{big}])")
            ardu.set_fans([s1, s2, s3, s4, big])
        elif hasattr(ardu, "send"):
            cmd = f"SETF {s1} {s2} {s3} {s4} {big}"
            print(f"↪ Arduino.send('{cmd}')")
            ardu.send(cmd)
        elif hasattr(ardu, "write"):
            cmd = f"SETF {s1} {s2} {s3} {s4} {big}\n"
            print(f"↪ Arduino.write('{cmd.strip()}')")
            ardu.write(cmd)
        else:
            print("⚠ Arduino 팬 전송 API를 찾지 못했습니다(set_fans/send/write).")
    except Exception as e:
        print(f"⚠ 팬 전송 실패: {e}")


def _ardu_send_leds(colors: List[str]) -> None:
    if not ardu:
        return
    try:
        if hasattr(ardu, "set_leds"):
            print(f"↪ Arduino.set_leds({colors})")
            ardu.set_leds(colors)
        elif hasattr(ardu, "send"):
            cmd = f"SETL {colors[0]} {colors[1]} {colors[2]} {colors[3]}"
            print(f"↪ Arduino.send('{cmd}')")
            ardu.send(cmd)
        elif hasattr(ardu, "write"):
            cmd = f"SETL {colors[0]} {colors[1]} {colors[2]} {colors[3]}\n"
            print(f"↪ Arduino.write('{cmd.strip()}')")
            ardu.write(cmd)
        else:
            print("⚠ Arduino LED 전송 API를 찾지 못했습니다(set_leds/send/write).")
    except Exception as e:
        print(f"⚠ LED 전송 실패: {e}")

# ---- Apply helpers ----
def _apply_fans(small8: List[int] | None = None, big: int | None = None) -> None:
    # 소형 4개가 들어오면: FanService로 '적용값' 산출 → 상태 갱신 → 전송
    if small8 is not None:
        # FanService는 소형 4개만 의미 있음
        payload = {
            "small_fan_pwm": list(small8)[:4],
            "large_fan_pwm": state["fan_main_pwm"] if big is None else big,
        }
        applied5 = svc_fans.preprocess(payload)     # [f1,f2,f3,f4,big] (소형=30..100 매핑)
        f1, f2, f3, f4, bigp = applied5

        # 상태(적용값) 반영: fan_small_pwm[0..3]만 갱신, 나머지 [4..7]은 기존 유지
        state["fan_small_pwm"][:4] = [f1, f2, f3, f4]
        state["fan_main_pwm"] = bigp

        _ardu_send_fans(f1, f2, f3, f4, bigp)

    # 대형만 바뀌면: 소형은 현재 '적용값' 그대로 사용하여 전송
    if small8 is None and big is not None:
        v = max(0, min(100, int(big)))
        state["fan_main_pwm"] = v
        f1, f2, f3, f4 = state["fan_small_pwm"][:4]   # 이미 적용값(매핑 후) 보관됨
        _ardu_send_fans(f1, f2, f3, f4, v)

def _apply_servos(internal4: List[float] | None, external4: List[float] | None) -> None:
    """
    서보 적용:
      - 부분 업데이트 시 빠진 쪽은 현재 state 값을 넣어 전처리하여
        svc_servo.state와의 일관성을 유지한다.
      - 드라이버 호출은 '요청된 쪽'이 이전 상태와 실제로 달라질 때만 수행한다.
    """
    if internal4 is None and external4 is None:
        return

    # 1) 전처리 입력 구성: 빠진 쪽은 현재 상태로 채움
    payload: dict[str, Any] = {
        "internal_servo": internal4 if internal4 is not None else state["servo_internal"],
        "external_servo": external4 if external4 is not None else state["servo_external"],
    }
    new_i, new_e = svc_servo.preprocess(payload)

    # 2) 변경 여부 판단(요청된 쪽만 비교)
    changed_i = internal4 is not None and not _angles_close(new_i, state["servo_internal"])
    changed_e = external4 is not None and not _angles_close(new_e, state["servo_external"])

    # 3) 드라이버 호출: 바뀐 쪽만
    if servo_drv:
        try:
            if changed_i and changed_e:
                servo_drv.set_both(new_i, new_e)
            elif changed_i:
                servo_drv.set_internal(new_i)
            elif changed_e:
                servo_drv.set_external(new_e)
            else:
                print("[Servo] unchanged → skip driver call")
        except Exception as e:
            print(f"⚠ Servo 반영 실패: {e}")

    # 4) 상태 동기화(요청된 쪽만 갱신)
    if internal4 is not None:
        state["servo_internal"] = new_i
    if external4 is not None:
        state["servo_external"] = new_e

def _apply_peltier(raw_pwm: Any) -> None:
    if raw_pwm is None:
        return
    try:
        data = {"peltier_pwm": int(float(raw_pwm))}
    except Exception:
        data = {"peltier_pwm": 0}
    applied = svc_peltier.preprocess(data)
    try:
        pdrv.set_duty(applied)
    except Exception as e:
        print(f"⚠ Peltier set_duty 실패: {e}")
    state["peltier_pwm"] = applied
    print(f"[Peltier] raw={svc_peltier.state.raw_duty} → applied={applied}")

def _apply_led_from_payload(payload: dict | list[float]) -> None:
    """
    들어온 TSV → (1) TSV 입력 정렬(LED_TSV_ORDER)
               → (2) LedService로 색상 산출
               → (3) LED 물리 채널 정렬(LED_HW_ORDER) → 아두이노 전송
    """
    # 1) payload 정규화
    if isinstance(payload, (list, tuple)):
        payload = {"tsv": list(payload)[:4]}

    # 2) TSV 4개 추출
    tsv_in = _extract_tsv4(payload)

    # 3) TSV 입력 정렬: 들어온 순서를 LedService가 기대하는 '논리 슬롯' 순서로
    tsv_logic = _remap4(tsv_in, LED_TSV_ORDER, fill=0)

    # 4) LedService로 색상 산출 (논리 슬롯 기준 색상 4개)
    colors_logic = svc_leds.preprocess({"tsv": tsv_logic})

    # 5) LED 물리 채널 정렬: 논리 슬롯 → 아두이노 실제 채널 순서
    colors_hw = _remap4(colors_logic, LED_HW_ORDER, fill="W")

    # 6) 상태 저장 및 전송 (소프트킬 반영)
    state["led_colors"] = colors_hw

    # 소프트킬 반영
    effective_logic = svc_leds.for_driver_colors_effective(
        softkill_killed=_softkill_killed,
        off_token="OFF",  # 필요시 "W"로 변경 가능
    )
    # effective는 논리 순서이므로, HW 순서로 다시 정렬
    effective_hw = _remap4(effective_logic, LED_HW_ORDER, fill="OFF")
    _ardu_send_leds(effective_hw)

    # 디버그 로그
    print(
        f"[LED] TSV_in={tsv_in} → logic={tsv_logic} → colors_logic={colors_logic} "
        f"→ colors_hw={colors_hw} → effective_hw={effective_hw}"
    )

def _angles_close(a: List[float], b: List[float], eps: float = SERVO_EPS_DEG) -> bool:
    if len(a) != 4 or len(b) != 4:
        return False
    return all(abs(float(x) - float(y)) <= eps for x, y in zip(a, b))

def _all_off() -> None:
    _apply_peltier(0)
    _apply_fans([0] * 8, 0)
    _ardu_send_leds(["W", "W", "W", "W"])

# =====================================================
# 3-1) 공용 인덱스 리맵 도구
# =====================================================
def _remap4(src_seq, index_map, *, fill=0):
    """
    길이 4 시퀀스 src_seq를 index_map에 맞춰 재배열.
    dst[i] = src_seq[index_map[i]]
    """
    out = []
    for src_idx in index_map:
        try:
            out.append(src_seq[src_idx])
        except Exception:
            out.append(fill)
    if len(out) < 4:
        out += [fill] * (4 - len(out))
    return out[:4]

# =====================================================
# 4) MQTT Handler
# =====================================================
def on_mqtt(topic: str, data: dict) -> None:
    try:
        if not isinstance(data, dict):
            return

        if topic.endswith("/value"):
            # ---- 원본(raw_cmd) 먼저 저장 (클램프 없이 형상만 보정) ----
            if "peltier_pwm" in data:
                try:
                    raw_cmd["peltier_pwm"] = int(float(data.get("peltier_pwm")))
                except Exception:
                    raw_cmd["peltier_pwm"] = 0

            if ("fan_small_pwm" in data) or ("small_fan_pwm" in data):
                raw_cmd["fan_small_pwm"] = _to_num_list(
                    data.get("fan_small_pwm") or data.get("small_fan_pwm"), 8, as_float=False
                )

            if ("fan_main_pwm" in data) or ("large_fan_pwm" in data):
                try:
                    raw_cmd["fan_main_pwm"] = int(float(data.get("fan_main_pwm") or data.get("large_fan_pwm")))
                except Exception:
                    raw_cmd["fan_main_pwm"] = 0

            if "internal_servo" in data:
                raw_cmd["servo_internal"] = _to_num_list(data.get("internal_servo"), 4, as_float=True)
            if "external_servo" in data:
                raw_cmd["servo_external"] = _to_num_list(data.get("external_servo"), 4, as_float=True)

            # ---- 소프트킬 중이면 하드웨어 적용만 건너뜀 ----
            if _softkill_killed:
                print("[SOFTKILL] /value 수신: raw만 갱신, 하드웨어 적용 차단")
                _publish_status()
                return

            # ---- 실제 적용 (팬은 small/big 동시반영으로 SETF 1회) ----
            _apply_peltier(data.get("peltier_pwm"))

            small = data.get("fan_small_pwm") or data.get("small_fan_pwm")
            big   = data.get("fan_main_pwm") or data.get("large_fan_pwm")
            if (small is not None) or (big is not None):
                _apply_fans(
                    small8=small if small is not None else None,
                    big=big if big is not None else None
                )

            _apply_servos(data.get("internal_servo"), data.get("external_servo"))

            _publish_status()

        elif topic.endswith("/tsv"):
            tsv4 = _extract_tsv4(data)
            _apply_led_from_payload({"tsv": tsv4})
            _publish_status()

        elif topic.endswith("/power_server"):
            pv = str(data.get("power", "")).strip().lower()
            want_off = pv in ("0", "false", "off")
            want_on  = pv in ("1", "true", "on")
            if softkill:
                if want_off:
                    softkill.set_state(True, emit=True, reason="mqtt")
                elif want_on:
                    softkill.set_state(False, emit=True, reason="mqtt")
            _publish_status()
    except Exception as e:
        print(f"[on_mqtt][Error] {e}")

# =====================================================
# 5) Shutdown
# =====================================================
def _handle_sigterm(signum, frame):
    print("\n🔚 SIGTERM/SIGINT received. Shutting down...")
    _shutdown.set()

# =====================================================
# 6) main
# =====================================================
def main():
    global mqttc

    _driver_init()

    mqttc = MQTTClient(
        BROKER_HOST,
        BROKER_PORT,
        publish_topics=TOPICS_PUB,
        subscribe_topics=TOPICS_SUB,
    )
    mqttc.set_message_handler(on_mqtt)
    mqttc.connect(keepalive=60)

    signal.signal(signal.SIGINT, _handle_sigterm)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    mode = "APPLIED(보정값)" if STATUS_USE_APPLIED else "RAW(수신값)"
    print(f"🚀 Running: broker={BROKER_HOST}:{BROKER_PORT} | HVAC_ID={HVAC_ID}")
    print(f"   * 상태 발행 모드: {mode}")
    print("   * /value: peltier/팬/서보, /tsv: LED, /power_server: on/off | 30초 Wh 추정 포함")

    try:
        while not _shutdown.is_set():
            time.sleep(0.5)
    finally:
        try:
            # ① 종료 직전 RP i 소프트킬 RED ON (대시보드에도 off 통지됨: on_change 콜백 내 publish)
            if softkill:
                softkill.set_state(True, emit=True, reason="shutdown")
                # softkill.cleanup()  # ← 라즈베리 LED까지 끄려면 사용 (기본은 RED 유지)
        except Exception:
            pass

        try:
            if mqttc:
                mqttc.disconnect()
        finally:
            # ② 장치 안전 OFF
            _driver_safe_off()
            # ③ Arduino 측 LED는 'OFF'로 유지하고 종료
            try:
                _ardu_send_leds(["OFF", "OFF", "OFF", "OFF"])
            except Exception:
                pass

        print("✅ Cleaned up. Bye.")

if __name__ == "__main__":
    main()
