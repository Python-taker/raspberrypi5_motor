#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli_actuator_test.py
────────────────────────────────────────────────────────
- MQTT 없이 로컬에서 액추에이터들을 각각 단독으로 테스트하는 CLI
  1) Peltier PWM (BTS7960)
  2) Servos (PCA9685) - 내부 4ch / 외부 4ch / 동시
  3) Fans (Arduino via USB) - small 4 + large 1
  4) TSV → LED 색상 매핑(Blue/White/Red) 후 Arduino에 전송
"""

import sys
import time
from pathlib import Path
from typing import List

# --- sys.path 보정 ---
SRC_DIR = Path(__file__).resolve().parent  # .../src
ROOT_DIR = SRC_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# ── 드라이버/서비스 임포트 ─────────────────────────────
from actuators.services.peltier import PeltierService, MIN_ON_DUTY_DEFAULT
from actuators.drivers.pca9685_servo_module import ServoAPI
from actuators.drivers import bts7960_peltier_pwm as pdrv

# Arduino Fan/LED 드라이버: arduino_bridge 우선, 없으면 구명칭으로 폴백
try:
    from actuators.drivers.arduino_bridge import ArduinoFanLedBridge as ArduinoFanLedClient
except ModuleNotFoundError:
    from actuators.drivers.arduino_fan_led import ArduinoFanLedClient  # 파일명이 다를 경우


# =====================================================
# 0️⃣ 공통 유틸
# =====================================================
def _parse_int_list(prompt: str, n: int) -> List[int]:
    while True:
        try:
            vals = list(map(int, input(prompt).strip().split()))
            if len(vals) != n:
                print(f"⚠ {n}개 정수를 공백으로 입력하세요.")
                continue
            return vals
        except ValueError:
            print("⚠ 숫자만 입력하세요.")

def _parse_float_list(prompt: str, n: int) -> List[float]:
    while True:
        try:
            vals = list(map(float, input(prompt).strip().split()))
            if len(vals) != n:
                print(f"⚠ {n}개 실수를 공백으로 입력하세요.")
                continue
            return vals
        except ValueError:
            print("⚠ 숫자만 입력하세요.")

def _press_enter():
    try:
        input("↩ Enter 를 눌러 메뉴로 돌아가기...")
    except KeyboardInterrupt:
        pass


# =====================================================
# 1️⃣ Peltier(펠티어) 테스트
# =====================================================
def test_peltier(svc: PeltierService):
    print("\n[Peltier] 0은 OFF, 1~100은 서비스 매핑(MIN_ON~100) 후 적용됩니다.")
    while True:
        s = input("듀티(0~100, 종료:q): ").strip().lower()
        if s in ("q", "quit", "exit"):
            break
        try:
            raw = int(s)
        except ValueError:
            print("⚠ 숫자를 입력하세요.")
            continue
        applied = svc.preprocess({"peltier_pwm": raw})
        pdrv.set_duty(applied)
        print(f"raw={raw} → applied={applied}")


# =====================================================
# 2️⃣ Servo(서보) 테스트
#   - 내부 4ch: 입력 θ → (60-θ) 반전 (드라이버 내부 처리)
#   - 외부 4ch: 입력 그대로
# =====================================================
def test_servo_internal(servo: ServoAPI):
    print("\n[Servo] 내부 4채널 각도를 입력하세요. 예) 0 10 20 30")
    angles = _parse_float_list("internal(4개, deg): ", 4)
    servo.set_internal(angles)
    print("✓ 내부 적용 완료")
    _press_enter()

def test_servo_external(servo: ServoAPI):
    print("\n[Servo] 외부 4채널 각도를 입력하세요. 예) 15 25 35 45")
    angles = _parse_float_list("external(4개, deg): ", 4)
    servo.set_external(angles)
    print("✓ 외부 적용 완료")
    _press_enter()

def test_servo_both(servo: ServoAPI):
    print("\n[Servo] 내부/외부 각도 4개씩 입력. 예) 내부: 0 0 0 0 / 외부: 10 20 30 40")
    i = _parse_float_list("internal(4개, deg): ", 4)
    e = _parse_float_list("external(4개, deg): ", 4)
    servo.set_both(i, e)
    print("✓ 내부+외부 동시 적용 완료")
    _press_enter()


# =====================================================
# 3️⃣ Fan(팬) 테스트 (Arduino)
#   - small_fan_pwm = 4개, large_fan_pwm = 1개 → SETF f1 f2 f3 f4 big
# =====================================================
def test_fans(ardu: ArduinoFanLedClient | None):
    if not ardu:
        print("⚠ Arduino 미연결 상태입니다. USB 연결/권한 확인 후 다시 시도하세요.")
        _press_enter()
        return
    print("\n[Fans] 작은 팬 4개 + 대형 1개 듀티(0~100)를 입력. 예) 100 80 70 50 90")
    vals = _parse_int_list("f1 f2 f3 f4 big: ", 5)
    ack = ardu.set_fans(vals)
    print("→", ack)
    _press_enter()


# =====================================================
# 4️⃣ TSV → LED 매핑 테스트 (Arduino)
#   - 규칙: tsv ∈ [-3,3]
#       tsv < -0.5  → Blue
#       -0.5~0.5    → White
#       tsv > 0.5   → Red
#   - 4개 TSV를 받아 4개 LED 색으로 전송
# =====================================================
def _tsv_to_color(v: float) -> str:
    if v < -0.5:
        return "B"
    if v > 0.5:
        return "R"
    return "W"

def test_led_from_tsv(ardu: ArduinoFanLedClient | None):
    if not ardu:
        print("⚠ Arduino 미연결 상태입니다. USB 연결/권한 확인 후 다시 시도하세요.")
        _press_enter()
        return
    print("\n[LED] TSV 4개(-3~3)를 입력하세요. 예) 1.0 0.0 -1.2 0.6")
    tsv = _parse_float_list("tsv[4]: ", 4)
    cols = [_tsv_to_color(v) for v in tsv]  # ['R'|'B'|'W']
    print("→ LED Colors:", cols)
    ack = ardu.set_leds(cols)
    print("→", ack)
    _press_enter()


# =====================================================
# 5️⃣ 메뉴 루프
# =====================================================
MENU = """
================= CLI Actuator Test =================
 1) Peltier PWM (BTS7960)
 2) Servo Internal  (4ch)
 3) Servo External  (4ch)
 4) Servo Both      (8ch)
 5) Fans            (small x4 + large x1)
 6) LED from TSV    (4개의 TSV → LED 색 매핑 전송)
 7) Arduino GET?    (현재 상태 읽기)
 q) Quit
=====================================================
"""

def main():
    # ── 드라이버 초기화 ──
    # Peltier
    pdrv.safe_init()
    pdrv.enable_forward()
    pdrv.set_duty(0)
    svc = PeltierService(min_on_duty=MIN_ON_DUTY_DEFAULT, rounding="floor")

    # Servo
    servo = ServoAPI(home=False)  # 필요 시 home=True로 부팅시 홈 스윕

    # Arduino(Fan/LED)
    ardu: ArduinoFanLedClient | None = None
    try:
        ardu = ArduinoFanLedClient()
        # arduino_bridge 기반 클래스는 connect()가 필요
        if hasattr(ardu, "connect"):
            ardu.connect()
        print("✅ Arduino 연결 완료.")
    except Exception as e:
        print(f"⚠ Arduino 연결 생략(오류): {e}")
        ardu = None

    print("✅ 초기화 완료. 장치 준비됨.\n")
    while True:
        try:
            print(MENU)
            sel = input("> ").strip().lower()
            if sel in ("q", "quit", "exit"):
                break
            elif sel == "1":
                test_peltier(svc)
            elif sel == "2":
                test_servo_internal(servo)
            elif sel == "3":
                test_servo_external(servo)
            elif sel == "4":
                test_servo_both(servo)
            elif sel == "5":
                test_fans(ardu)
            elif sel == "6":
                test_led_from_tsv(ardu)
            elif sel == "7":
                if ardu:
                    try:
                        print("→", ardu.get_state())
                    except AttributeError:
                        print("⚠ 드라이버에 get_state()가 없습니다.")
                else:
                    print("⚠ Arduino 미연결 상태입니다.")
                _press_enter()
            elif sel == "":
                continue
            else:
                print("⚠ 메뉴를 다시 선택하세요.")
        except KeyboardInterrupt:
            print("\n⛔ 사용자 종료")
            break
        except Exception as e:
            print(f"❗ 오류: {e}")

    # ── 종료 처리 ──
    try:
        pdrv.set_duty(0)
    except Exception:
        pass
    print("✅ 종료: Peltier PWM=0, 기타 리소스 정리 완료.")

if __name__ == "__main__":
    main()
