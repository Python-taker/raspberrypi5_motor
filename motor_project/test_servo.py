# test_servo.py (RPi.GPIO 버전, pigpio 미사용)
import time, argparse
import RPi.GPIO as GPIO

def angle_to_duty(angle, min_us=500, max_us=2500, period_us=20000):
    angle = max(0, min(180, float(angle)))
    pulse = min_us + (max_us - min_us) * (angle / 180.0)
    return (pulse / period_us) * 100.0  # 50Hz에서 20ms 주기

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pin", type=int, default=18, help="BCM GPIO pin")
    parser.add_argument("--angle", type=float, default=90, help="0..180 deg")
    args = parser.parse_args()

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(args.pin, GPIO.OUT)

    pwm = GPIO.PWM(args.pin, 50)  # 50Hz
    pwm.start(0.0)
    try:
        duty = angle_to_duty(args.angle)
        pwm.ChangeDutyCycle(duty)
        time.sleep(0.6)
        # 신호 끊기(서보에 따라 필요/불필요)
        pwm.ChangeDutyCycle(0.0)
        time.sleep(0.05)
        print("✅ Done")
    finally:
        # 1) PWM을 먼저 완전히 정지
        try:
            pwm.stop()
        except Exception:
            pass
        # 2) 소멸자를 지금 실행시키도록 참조 제거(중요!)
        try:
            del pwm
        except Exception:
            pass
        # 3) 그 다음에 cleanup
        GPIO.cleanup()
