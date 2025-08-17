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
    STATUS_USE_APPLIED,   # ★ 추가
    LED_TSV_ORDER,   # ★ 추가
    LED_HW_ORDER,    # ★ 추가
)
from mqtt_client import MQTTClient

from actuators.services.peltier import PeltierService, MIN_ON_DUTY_DEFAULT
from actuators.services.servo import ServoService
from actuators.drivers import bts7960_peltier_pwm as pdrv
from actuators.drivers.pca9685_servo_module import ServoAPI

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

# =====================================================
# 1) Globals
# =====================================================
load_dotenv()
BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

mqttc: MQTTClient | None = None
svc_peltier = PeltierService(min_on_duty=MIN_ON_DUTY_DEFAULT, rounding="floor")
svc_servo   = ServoService()
svc_leds    = LedService()

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
def _driver_init() -> None:
    global servo_drv, ardu
    pdrv.safe_init()
    pdrv.enable_forward()
    pdrv.set_duty(0)

    try:
        servo_drv = ServoAPI(home=False)
        print("✅ Servo 준비 완료.")
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

    # 소비에너지 추정은 실제 적용값 기준(원한다면 플래그로 분기 가능)
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
    if small8 is not None:
        small8 = _to_pwm_list(small8, 8)
        state["fan_small_pwm"] = small8
        s1, s2, s3, s4 = small8[:4]
        bigp = state["fan_main_pwm"] if big is None else max(0, min(100, int(big)))
        _ardu_send_fans(s1, s2, s3, s4, bigp)

    if big is not None:
        v = max(0, min(100, int(big)))
        state["fan_main_pwm"] = v
        s1, s2, s3, s4 = state["fan_small_pwm"][:4]
        _ardu_send_fans(s1, s2, s3, s4, v)

def _apply_servos(internal4: List[float] | None, external4: List[float] | None) -> None:
    if internal4 is None and external4 is None:
        return
    payload: dict[str, Any] = {}
    if internal4 is not None:
        payload["internal_servo"] = internal4
    if external4 is not None:
        payload["external_servo"] = external4
    i4, e4 = svc_servo.preprocess(payload)
    if internal4 is not None:
        state["servo_internal"] = i4
    if external4 is not None:
        state["servo_external"] = e4
    if servo_drv:
        try:
            if internal4 is not None and external4 is not None:
                servo_drv.set_both(state["servo_internal"], state["servo_external"])
            elif internal4 is not None:
                servo_drv.set_internal(state["servo_internal"])
            elif external4 is not None:
                servo_drv.set_external(state["servo_external"])
        except Exception as e:
            print(f"⚠ Servo 반영 실패: {e}")

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

# ---- Apply helpers ----
def _apply_led_from_payload(payload: dict | list[float]) -> None:
    """
    들어온 TSV → (1) TSV 입력 정렬(LED_TSV_ORDER)
               → (2) LedService로 색상 산출
               → (3) LED 물리 채널 정렬(LED_HW_ORDER) → 아두이노 전송
    """
    # 1) payload 정규화
    if isinstance(payload, (list, tuple)):
        payload = {"tsv": list(payload)[:4]}

    # 2) TSV 4개 추출(원래 헬퍼 재사용)
    tsv_in = _extract_tsv4(payload)

    # 3) TSV 입력 정렬: 들어온 순서를 LedService가 기대하는 '논리 슬롯' 순서로
    tsv_logic = _remap4(tsv_in, LED_TSV_ORDER, fill=0)

    # 4) LedService로 색상 산출 (논리 슬롯 기준 색상 4개)
    colors_logic = svc_leds.preprocess({"tsv": tsv_logic})

    # 5) LED 물리 채널 정렬: 논리 슬롯 → 아두이노 실제 채널 순서
    colors_hw = _remap4(colors_logic, LED_HW_ORDER, fill="W")

    # 6) 상태 저장 및 전송
    state["led_colors"] = colors_hw
    _ardu_send_leds(colors_hw)

    # (선택) 디버그 로그
    print(f"[LED] TSV_in={tsv_in} → logic={tsv_logic} → colors_logic={colors_logic} → colors_hw={colors_hw}")

def _all_off() -> None:
    _apply_peltier(0)
    _apply_fans([0] * 8, 0)
    _ardu_send_leds(["W", "W", "W", "W"])

# =====================================================
# 3-1) 공용 인덱스 리맵 도구
# - dst[i] = src[index_map[i]]  (길이 4 전제, 방어적 보정)
# =====================================================
def _remap4(src_seq, index_map, *, fill=0):
    """
    길이 4 시퀀스 src_seq를 index_map에 맞춰 재배열.
    dst[i] = src_seq[index_map[i]]

    Args:
        src_seq: 원본 시퀀스(길이 4 가정, 짧아도 안전 처리)
        index_map: 0..3 순열(검증은 config에서 수행)
        fill: 범위 밖/결측 시 대체값

    Returns:
        list: 재배열된 길이 4 리스트
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

            # ---- 실제 적용(기존 로직) ----
            _apply_peltier(data.get("peltier_pwm"))
            small = data.get("fan_small_pwm") or data.get("small_fan_pwm")
            if small is not None:
                _apply_fans(small8=small)
            if "fan_main_pwm" in data or "large_fan_pwm" in data:
                _apply_fans(big=(data.get("fan_main_pwm") or data.get("large_fan_pwm")))
            _apply_servos(data.get("internal_servo"), data.get("external_servo"))

            _publish_status()

        elif topic.endswith("/tsv"):
            tsv4 = _extract_tsv4(data)
            _apply_led_from_payload({"tsv": tsv4})
            _publish_status()

        elif topic.endswith("/power_server"):
            pv = str(data.get("power", "")).strip().lower()
            is_off = pv in ("0", "false", "off") or pv == "" or pv is False or pv == 0
            is_on  = pv in ("1", "true", "on") or pv is True or pv == 1
            if is_off:
                _all_off()
            elif is_on:
                pass
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
            if mqttc:
                mqttc.disconnect()
        finally:
            _driver_safe_off()
        print("✅ Cleaned up. Bye.")

if __name__ == "__main__":
    main()
