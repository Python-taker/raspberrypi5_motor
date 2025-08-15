#!/usr/bin/env python3
"""
bts7960_peltier_pwm.py
────────────────────────────────────────────────────────
- Raspberry Pi 5 + BTS7960로 펠티어(정방향만) PWM 제어
- 사용자 지정 핀 매핑 반영:
    R_EN=GPIO4,  R_PWM=GPIO18,  L_EN=GPIO23,  L_PWM=GPIO24
- 기본 정책: 정방향만 사용(R_EN=HIGH), 역방향은 항상 비활성(L_EN=LOW, L_PWM=LOW)
- 실제 작동 방법 : 정방향만 사용하더라도 무조건 L_EN은 켜주어야 함.
- 따라서 L_EN 은 역방향 사용 여부와 관계없이 무조건 HIGH 가 되어야 펠티어 소자가 작동함.

⚠ 주의 사항
1) 배선 전원 투입 전, EN 핀들은 기본 LOW 상태여야 안전합니다.
2) 펠티어는 저항성 부하이므로 플라이백 다이오드는 불필요합니다.
3) 전원(12V)와 GND는 스타접지, Pi와 공통 GND 유지.
"""

from gpiozero import PWMOutputDevice, DigitalOutputDevice
from time import sleep

# =====================================================
# 1️⃣ 핀 맵 (BCM 기준)
# 사용자 제공 매핑: R_EN=4, R_PWM=18, L_EN=23, L_PWM=24
# =====================================================
R_EN_PIN  = 17    # 정방향 Enable
R_PWM_PIN = 18   # 정방향 PWM (권장: 1 kHz)
L_EN_PIN  = 23   # 역방향 Enable (항상 HIGH)
L_PWM_PIN = 24   # 역방향 PWM (항상 LOW)

FREQ_HZ = 1000   # BTS7960 + 펠티어 권장 범위 내

# =====================================================
# 2️⃣ 디바이스 객체 생성
# 역방향은 DigitalOutputDevice로 상시 LOW 고정
# =====================================================
rpwm = PWMOutputDevice(R_PWM_PIN, frequency=FREQ_HZ, initial_value=0.0)
lpwm = DigitalOutputDevice(L_PWM_PIN, initial_value=False)
ren  = DigitalOutputDevice(R_EN_PIN,  initial_value=False)
len_ = DigitalOutputDevice(L_EN_PIN,  initial_value=False)

def safe_init():
    """
    초기 안전 상태 설정:
    - 모든 신호 LOW
    - 역방향 완전 비활성(L_EN=LOW, L_PWM=LOW)
    - 정방향만 Enable(필요 시점에만 R_EN=HIGH, L_EN=HIGH)
    """
    rpwm.value = 0.0
    lpwm.off()
    len_.off()    # 역방향 EN 끔
    ren.off()     # 정방향 EN도 일단 끔
    sleep(0.05)

def enable_forward():
    """
    정방향만 사용:
    - R_EN=HIGH, L_EN=HIGH
    - L_PWM=LOW 유지
    """
    len_.on()
    lpwm.off()
    ren.on()
    sleep(0.02)

def set_duty(percent: int):
    """
    듀티(0~100%) 설정. 범위 밖 값은 클램핑.
    """
    if percent < 0:   percent = 0
    if percent > 100: percent = 100
    rpwm.value = percent / 100.0
    return percent

def main():
    # 초기 안전 상태
    safe_init()
    print("✅ 초기화 완료: 모든 라인 LOW, 역방향 비활성")

    # 정방향 사용 준비
    enable_forward()
    print("⚙️  정방향 EN=HIGH (R_EN), 역방향 EN=LOW (L_EN)")

    try:
        while True:
            try:
                duty = int(input("PWM Duty (0~100, 종료:-1): ").strip())
            except ValueError:
                print("❗ 숫자를 입력하세요.")
                continue

            if duty == -1:
                break

            duty = set_duty(duty)
            print(f"✔️ 현재 듀티비: {duty}%")
            sleep(0.05)

    except KeyboardInterrupt:
        print("\n⛔ 사용자 강제 종료됨 (Ctrl+C)")

    finally:
        # 안전 종료: 듀티 0 → EN LOW → 자원 해제
        rpwm.value = 0.0
        sleep(0.02)
        ren.off()
        len_.off()
        lpwm.off()
        rpwm.close()
        print("✅ PWM 0%, EN OFF, GPIO 정리 완료")

if __name__ == "__main__":
    main()
