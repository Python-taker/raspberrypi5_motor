from Adafruit_PCA9685 import PCA9685
import Adafruit_GPIO.I2C as I2C
from scipy.interpolate import interp1d
import time

# 📌 I2C 버스 강제 설정 (Raspberry Pi 5용)
I2C.get_default_bus = lambda: 1

# 📌 PCA9685 초기화
pwm = PCA9685(address=0x60)
pwm.set_pwm_freq(50)  # 50Hz (서보모터용)

# 📌 보간용 데이터: 사용자 입력각 ↔ 실측 회전각
input_angles = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45,
                50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 105]

actual_angles = [0.0, 7.1, 14.3, 20.5, 26.7, 32.0, 37.3, 43.5,
                 48.7, 54.0, 58.1, 63.3, 68.5, 73.7, 78.9, 84.0,
                 88.1, 95.3, 100.5, 105.7, 111.9, 116.0]

# 📌 정방향 보간: 입력각 → 실측각
forward_interp_func = interp1d(input_angles, actual_angles, kind='linear', fill_value="extrapolate")

# 📌 실측각 → PWM 펄스 변환
def actual_angle_to_pwm(angle: float) -> int:
    return int(150 + (angle / 180.0) * 450)

# 📌 사용자 목표 입력각 → 실측각 보정 → PWM 변환
def compute_pwm_from_input_angle(user_angle: float) -> int:
    actual_angle = float(forward_interp_func(user_angle))  # 입력 → 실측 보정
    return actual_angle_to_pwm(actual_angle)

# 📌 각 채널별 현재 위치 (입력 기준 각도)
channel_positions = {ch: 0.0 for ch in range(8)}

# 📌 메인 루프
if __name__ == "__main__":
    print("✅ '채널 각도' 입력 (예: 3 45) → 0도 기준 목표 각도 지정")
    print("🔄 '0' 입력 → 원점 복귀 및 현재 위치 초기화\n")

    try:
        while True:
            try:
                user_input = input("입력 > ").strip()
                if not user_input:
                    continue

                parts = user_input.split()
                if len(parts) != 2:
                    print("⚠️ 형식 오류! '채널 각도'")
                    continue

                channel, angle = int(parts[0]), int(parts[1])

                if not (0 <= channel <= 7):
                    print("❗ 채널은 0~7 사이여야 합니다.")
                    continue
                if not (0 <= angle <= 105):
                    print("❗ 각도는 0~105도 사이여야 합니다.")
                    continue

                if angle == 0:
                    pwm.set_pwm(channel, 0, actual_angle_to_pwm(0.0))
                    channel_positions[channel] = 0.0
                    print(f"🔄 채널 {channel} → 원점 복귀 → 펄스 150")
                    continue

                current_position = channel_positions[channel]
                target_position = float(angle)

                pulse = compute_pwm_from_input_angle(target_position)
                pwm.set_pwm(channel, 0, pulse)

                delta = target_position - current_position
                print(f"✔️ 채널 {channel} → 현재 {current_position:.1f}도 → "
                      f"목표 {target_position:.1f}도 → Δ {delta:+.1f}도 → 펄스 {pulse}")

                channel_positions[channel] = target_position

            except ValueError:
                print("❗ 숫자만 입력해주세요 (예: 0 45)")

    except KeyboardInterrupt:
        print("\n⛔ 종료됨 (Ctrl+C)")
    finally:
        for ch in range(8):
            pwm.set_pwm(ch, 0, 0)
        print("✅ 모든 서보 PWM 신호 중지 완료.")
