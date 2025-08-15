from gpiozero import PWMOutputDevice, DigitalOutputDevice
from time import sleep

# ===== 4핀 팬 핀 설정 =====
FREQ_HZ_4PIN = 10000   # 4핀 팬 PWM은 보통 25kHz 권장
FAN_PWM_PIN = 20       # 속도 제어 PWM 핀
MOSFET_PIN = 21        # 전원 제어 MOSFET 핀

# PWM / MOSFET 객체 생성
fan_pwm = PWMOutputDevice(FAN_PWM_PIN, frequency=FREQ_HZ_4PIN)
mosfet_power = DigitalOutputDevice(MOSFET_PIN)

# 초기 전원 ON
mosfet_power.on()
print("✅ 4핀 팬 전원 ON (초기 상태)")

try:
    while True:
        print("\n=== 4핀 팬 제어 메뉴 ===")
        print("1: 팬 속도 조절")
        print("2: 팬 전원 ON")
        print("3: 팬 전원 OFF")
        print("-1: 종료")
        sel = int(input("메뉴 선택: "))

        if sel == -1:
            break

        elif sel == 1:
            duty = int(input("듀티(0~100): "))
            if 0 <= duty <= 100:
                fan_pwm.value = duty / 100.0
                print(f"✔ 4핀 팬 속도 {duty}%로 설정")
            else:
                print("⚠ 0~100 범위만 입력")

        elif sel == 2:
            mosfet_power.on()
            print("✅ 4핀 팬 전원 ON")

        elif sel == 3:
            mosfet_power.off()
            print("⛔ 4핀 팬 전원 OFF")

        else:
            print("⚠ 잘못된 선택입니다.")

except KeyboardInterrupt:
    pass

finally:
    fan_pwm.off()
    mosfet_power.off()
    print("✅ 4핀 팬 제어 종료")
