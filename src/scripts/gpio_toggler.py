#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/gpio_toggler.py
────────────────────────────────────────────────────────
- 지정한 GPIO 핀을 LOW 3초 / HIGH 3초로 무한 반복 토글
- 기본 핀: BCM 9 (GPIO9)
- 공통 캐소드 LED라면 LOW=켜짐, HIGH=꺼짐 이므로 '켜짐 3초 → 꺼짐 3초'가 됨.
- Ctrl+C 로 종료 시 깔끔하게 정리

사용:
  python -m src.scripts.gpio_toggler
  python -m src.scripts.gpio_toggler --pin 9 --low 3 --high 3
"""

import time
import argparse
import sys

try:
    import RPi.GPIO as GPIO
except Exception as e:
    print("RPi.GPIO를 불러올 수 없습니다. 이 스크립트는 라즈베리파이에서 실행하세요.")
    print("에러:", e)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="GPIO LOW/HIGH 토글러")
    ap.add_argument("--pin", type=int, default=9, help="BCM 핀 번호 (기본 9)")
    ap.add_argument("--low", type=float, default=3.0, help="LOW 유지 시간(초) (기본 3)")
    ap.add_argument("--high", type=float, default=3.0, help="HIGH 유지 시간(초) (기본 3)")
    args = ap.parse_args()

    pin = args.pin
    t_low = max(0.0, float(args.low))
    t_high = max(0.0, float(args.high))

    print(f"[CFG] BCM{pin}을 LOW {t_low:.2f}s ↔ HIGH {t_high:.2f}s 로 무한 반복합니다.")
    print("     공통 캐소드 LED일 경우 LOW=켜짐, HIGH=꺼짐 입니다.")
    print("Ctrl+C 로 종료")

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)  # 시작은 HIGH

    try:
        while True:
            GPIO.output(pin, GPIO.LOW)
            print(f"[{time.strftime('%H:%M:%S')}] BCM{pin} = LOW")
            time.sleep(t_low)

            GPIO.output(pin, GPIO.HIGH)
            print(f"[{time.strftime('%H:%M:%S')}] BCM{pin} = HIGH")
            time.sleep(t_high)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            GPIO.output(pin, GPIO.LOW)  # 테스트 끝나면 꺼놓고 싶으면 HIGH/LOW 바꿔도 됨
        except Exception:
            pass
        GPIO.cleanup()
        print("\n정리 완료. Bye.")

if __name__ == "__main__":
    main()
