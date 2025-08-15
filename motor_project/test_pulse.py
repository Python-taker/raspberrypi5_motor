from Adafruit_PCA9685 import PCA9685
import Adafruit_GPIO.I2C as I2C

# Raspberry Pi 5용 I2C 버스 강제 설정
I2C.get_default_bus = lambda: 1

# PCA9685 초기화
pwm = PCA9685(address=0x60)
pwm.set_pwm_freq(50)  # 서보모터용 주파수

print("✅ 테스트 시작: '채널 PWM' 형식으로 입력 (예: 0 300)")
print("⛔ 종료: Ctrl+C\n")

try:
    while True:
        user_input = input("입력 > ").strip()
        if not user_input:
            continue

        parts = user_input.split()
        if len(parts) != 2:
            print("⚠️ 입력 형식 오류! 예: 0 300")
            continue

        try:
            channel = int(parts[0])
            pulse = int(parts[1])

            if not (0 <= channel <= 7):
                print("❗ 채널은 0~7 사이여야 합니다.")
                continue
            if not (70 <= pulse <= 700):
                print("⚠️ PWM 펄스는 보통 150~600 사이입니다. (현재 입력: {})".format(pulse))

            pwm.set_pwm(channel, 0, pulse)
            print(f"✔️ 채널 {channel} → 펄스 {pulse} 설정 완료")

        except ValueError:
            print("❗ 숫자만 입력해주세요.")

except KeyboardInterrupt:
    print("\n⛔ 종료됨 (Ctrl+C)")

finally:
    for ch in range(8):
        pwm.set_pwm(ch, 0, 0)
    print("✅ 모든 서보 PWM 신호 중지 완료.")
