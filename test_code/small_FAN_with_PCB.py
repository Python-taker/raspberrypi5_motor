from gpiozero import PWMOutputDevice
from time import sleep

# BCM 번호 예시 (충돌 안 나는 핀 선택)
PWM_PINS = [5, 6, 13, 19]
FREQ_HZ = 1000

# PWMOutputDevice 객체 생성
pwm_channels = [PWMOutputDevice(pin, frequency=FREQ_HZ) for pin in PWM_PINS]

try:
    while True:
        duty = int(input("듀티(0~100, -1 종료): "))
        if duty == -1:
            break
        if 0 <= duty <= 100:
            value = duty / 100.0
            for pwm in pwm_channels:
                pwm.value = value
            print(f"✔ {len(pwm_channels)}채널 모두 듀티 {duty}%로 설정")
        else:
            print("⚠ 0~100 범위 입력")

except KeyboardInterrupt:
    pass
finally:
    for pwm in pwm_channels:
        pwm.off()
    print("PWM 모두 종료")
