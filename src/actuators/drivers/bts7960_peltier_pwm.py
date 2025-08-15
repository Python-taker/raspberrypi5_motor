#!/usr/bin/env python3
"""
bts7960_peltier_pwm.py
────────────────────────────────────────────────────────
- Raspberry Pi 5 + BTS7960로 펠티어(정방향 전용) PWM 제어
- 핀 매핑(BCM): R_EN=GPIO17, R_PWM=GPIO18, L_EN=GPIO23, L_PWM=GPIO24
- 정책:
    • 동작 시 정방향만 사용(R_EN=HIGH)
    • 역방향 PWM은 항상 LOW
    • ⚠ 실제 작동 특성상 L_EN도 HIGH 여야 함 (init 시에만 LOW)
      → enable_forward()가 R_EN/L_EN을 모두 HIGH로 설정

⚠ 주의
1) 전원 인가 전, 모든 EN을 LOW로 두는 초기화 필요(safe_init).
2) 12V 전원과 GND는 스타접지, RPi와 GND 공통.
3) root 또는 gpio 그룹 권한 필요할 수 있음.
"""

from gpiozero import PWMOutputDevice, DigitalOutputDevice
from time import sleep
import threading
from typing import Optional

# =====================================================
# 1️⃣ 핀 맵 (BCM 기준)
# =====================================================
R_EN_PIN  = 17    # 정방향 Enable
R_PWM_PIN = 18    # 정방향 PWM (권장: 1 kHz)
L_EN_PIN  = 23    # 역방향 Enable (동작 시 HIGH 유지)
L_PWM_PIN = 24    # 역방향 PWM (항상 LOW)

FREQ_HZ = 1000    # BTS7960 + 펠티어 권장 범위 내

# =====================================================
# 2️⃣ 로우레벨 디바이스 (모듈 전역 인스턴스)
# =====================================================
rpwm = PWMOutputDevice(R_PWM_PIN, frequency=FREQ_HZ, initial_value=0.0)
lpwm = DigitalOutputDevice(L_PWM_PIN, initial_value=False)
ren  = DigitalOutputDevice(R_EN_PIN,  initial_value=False)
len_ = DigitalOutputDevice(L_EN_PIN,  initial_value=False)

def safe_init():
    """
    초기 안전 상태:
    - PWM=0, 모든 EN LOW (전원 인가 직후 안전)
    """
    rpwm.value = 0.0
    lpwm.off()
    len_.off()
    ren.off()
    sleep(0.05)

def enable_forward():
    """
    정방향 구동 준비:
    - ⚠ L_EN=HIGH (필수), L_PWM=LOW
    - R_EN=HIGH
    """
    len_.on()     # 동작 시 항상 HIGH
    lpwm.off()    # 역방향 PWM은 항상 LOW
    ren.on()      # 정방향 EN HIGH
    sleep(0.02)

def set_duty(percent: int) -> int:
    """
    듀티(0~100%) 설정. 범위 밖은 클램프.
    """
    if percent < 0:   percent = 0
    if percent > 100: percent = 100
    rpwm.value = percent / 100.0
    return percent

# =====================================================
# 3️⃣ 고수준 래퍼 (MAIN에서 바로 사용)
# =====================================================
class PeltierAPI:
    """
    BTS7960 기반 펠티어 정방향 PWM 제어 래퍼.

    Methods:
        enable_forward()           → EN 라인 HIGH, 역PWM LOW
        set_duty(percent:int)      → 0..100 %
        ramp_to(target:int, ...)   → 부드러운 램핑
        apply_from_payload(value)  → {"peltier_pwm": int} 적용
        stop()                     → 듀티 0%
        close()                    → 안전 종료
    """
    def __init__(self, freq_hz: int = FREQ_HZ):
        self._lock = threading.Lock()
        # 모듈 전역 디바이스 재사용
        safe_init()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def enable_forward(self):
        with self._lock:
            enable_forward()

    def set_duty(self, percent: int) -> int:
        with self._lock:
            enable_forward()              # EN 보장
            applied = set_duty(percent)
            return applied

    def ramp_to(self, target: int, step: int = 5, interval: float = 0.02) -> int:
        """
        현재 듀티에서 target까지 부드럽게 램핑.
        """
        if target < 0: target = 0
        if target > 100: target = 100
        with self._lock:
            enable_forward()
            current = int(round(rpwm.value * 100))
            if current == target:
                return target

            s = step if target > current else -abs(step)
            for d in range(current, target, s):
                rpwm.value = max(0.0, min(1.0, d / 100.0))
                sleep(interval)
            rpwm.value = target / 100.0
            return target

    def apply_from_payload(self, value: dict) -> int:
        """
        MQTT value payload에서 'peltier_pwm' 추출·적용.
        예: {"peltier_pwm": 5, ...}
        """
        try:
            duty = int(value.get("peltier_pwm", 0))
        except Exception:
            duty = 0
        return self.set_duty(duty)

    def stop(self):
        with self._lock:
            rpwm.value = 0.0

    def close(self):
        with self._lock:
            try:
                rpwm.value = 0.0
                sleep(0.02)
                ren.off()
                len_.off()
                lpwm.off()
                # rpwm.close()  # 전역 재사용 시 닫지 않음 (필요하면 주석 해제)
            except Exception:
                pass

# =====================================================
# 4️⃣ CLI ─ 단독 테스트
# =====================================================
def main():
    safe_init()
    print("✅ 초기화 완료: PWM=0, EN=LOW")

    api = PeltierAPI()
    api.enable_forward()
    print("⚙️  EN=HIGH (R_EN/L_EN), 역PWM=LOW")

    try:
        while True:
            raw = input("듀티 0~100 | ramp: r 70 | 종료:-1 > ").strip()
            if raw == "-1":
                break
            if raw.lower().startswith("r "):
                try:
                    tgt = int(raw.split()[1])
                    api.ramp_to(tgt)
                    print(f"✔️ 램프 완료: {tgt}%")
                except Exception:
                    print("❗ ramp 사용법: r 70")
                continue
            try:
                duty = int(raw)
            except ValueError:
                print("❗ 숫자(0~100) 또는 'r 70' 형태로 입력하세요.")
                continue
            applied = api.set_duty(duty)
            print(f"✔️ 현재 듀티비: {applied}%")
            sleep(0.05)

    except KeyboardInterrupt:
        print("\n⛔ 사용자 강제 종료")

    finally:
        api.close()
        print("✅ PWM 0%, EN OFF, GPIO 정리 완료")

if __name__ == "__main__":
    main()
