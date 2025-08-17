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

새로 추가/보강
- kill():  PWM=0, R_EN/L_EN=LOW  → 완전 비활성(안전정지)
- resume(): R_EN/L_EN=HIGH, PWM=0 → 재활성 준비(구동은 0%부터)
- set_duty(): KILL 상태면 듀티를 무시(0 반환)하여 의도치 않은 재활성화를 방지

⚠ 주의
1) 전원 인가 전, 모든 EN을 LOW로 두는 초기화(safe_init) 필요.
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
# 2️⃣ 로우레벨 디바이스 & 상태
# =====================================================
_rpwm = PWMOutputDevice(R_PWM_PIN, frequency=FREQ_HZ, initial_value=0.0)
_lpwm = DigitalOutputDevice(L_PWM_PIN, initial_value=False)
_ren  = DigitalOutputDevice(R_EN_PIN,  initial_value=False)
_len  = DigitalOutputDevice(L_EN_PIN,  initial_value=False)

_lock = threading.Lock()
_state = {
    "killed": True,     # safe_init 시점엔 EN=LOW → killed=True
}

def _clip_percent(p: int) -> int:
    if p < 0: return 0
    if p > 100: return 100
    return int(p)

# =====================================================
# 3️⃣ 저수준 제어 함수 (모듈 전역)
# =====================================================
def safe_init() -> None:
    """
    초기 안전 상태:
    - PWM=0, 모든 EN LOW (전원 인가 직후 안전)
    """
    with _lock:
        _rpwm.value = 0.0
        _lpwm.off()
        _len.off()
        _ren.off()
        _state["killed"] = True
    sleep(0.02)

def enable_forward() -> None:
    """
    정방향 구동 준비:
    - ⚠ L_EN=HIGH (필수), L_PWM=LOW
    - R_EN=HIGH
    - 내부 상태 killed=False 로 전환
    """
    with _lock:
        _len.on()     # 동작 시 항상 HIGH
        _lpwm.off()   # 역방향 PWM은 항상 LOW
        _ren.on()     # 정방향 EN HIGH
        _state["killed"] = False
    sleep(0.01)

def set_duty(percent: int) -> int:
    """
    듀티(0~100%) 설정. 범위 밖은 클램프.
    - ⚠ killed=True 상태에서는 PWM 0만 유지하고 0을 반환(무시)
      → 의도치 않은 enable_forward() 호출로 재활성되는 것을 방지
    """
    p = _clip_percent(percent)
    with _lock:
        if _state["killed"]:
            # 킬 상태에서는 강제로 0 유지
            _rpwm.value = 0.0
            return 0
        # EN 보장(이미 enable_forward를 통해 HIGH 상태여야 정상)
        _rpwm.value = p / 100.0
        return p

def get_duty() -> int:
    """현재 PWM 듀티(정수 %)"""
    with _lock:
        return int(round(_rpwm.value * 100))

def kill() -> None:
    """
    소프트 킬(안전정지):
    - PWM=0
    - EN 라인 모두 LOW
    - state.killed=True
    """
    with _lock:
        _rpwm.value = 0.0
        sleep(0.01)
        _ren.off()
        _len.off()
        _lpwm.off()
        _state["killed"] = True

def resume() -> None:
    """
    소프트 킬 해제(재활성 준비):
    - EN 라인 HIGH로 재활성, 역PWM은 LOW
    - PWM은 0에서 시작 (사용자가 set_duty로 올려야 함)
    - state.killed=False
    """
    with _lock:
        _len.on()
        _lpwm.off()
        _ren.on()
        _rpwm.value = 0.0
        _state["killed"] = False
    sleep(0.01)

def is_killed() -> bool:
    with _lock:
        return bool(_state["killed"])

# =====================================================
# 4️⃣ 고수준 래퍼 (MAIN에서 바로 사용)
# =====================================================
class PeltierAPI:
    """
    BTS7960 기반 펠티어 정방향 PWM 제어 래퍼.

    Methods:
        enable_forward()           → EN 라인 HIGH, 역PWM LOW (killed=False)
        set_duty(percent:int)      → 0..100 % (killed=True면 0 고정)
        ramp_to(target:int, ...)   → 부드러운 램핑 (killed=True면 무시)
        apply_from_payload(value)  → {"peltier_pwm": int} 적용
        stop()                     → 듀티 0%
        kill()                     → PWM 0 + EN LOW (killed=True)
        resume()                   → EN HIGH + PWM 0 (killed=False)
        close()                    → 안전 종료
        is_killed()                → bool
        get_duty()                 → int (0~100)
    """
    def __init__(self, freq_hz: int = FREQ_HZ):
        self._lock = threading.Lock()
        # 모듈 전역 디바이스 재사용
        safe_init()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---- 상태/제어 ----
    def enable_forward(self) -> None:
        with self._lock:
            enable_forward()

    def set_duty(self, percent: int) -> int:
        with self._lock:
            return set_duty(percent)

    def get_duty(self) -> int:
        with self._lock:
            return get_duty()

    def is_killed(self) -> bool:
        with self._lock:
            return is_killed()

    def ramp_to(self, target: int, step: int = 5, interval: float = 0.02) -> int:
        """
        현재 듀티에서 target까지 부드럽게 램핑.
        - killed=True면 아무 것도 하지 않고 0 반환
        """
        t = _clip_percent(target)
        with self._lock:
            if is_killed():
                # 킬 상태에서는 동작 금지
                _rpwm.value = 0.0
                return 0

            current = int(round(_rpwm.value * 100))
            if current == t:
                return t

            s = step if t > current else -abs(step)
            for d in range(current, t, s):
                _rpwm.value = max(0.0, min(1.0, d / 100.0))
                sleep(interval)
            _rpwm.value = t / 100.0
            return t

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

    def stop(self) -> None:
        with self._lock:
            _rpwm.value = 0.0

    def kill(self) -> None:
        with self._lock:
            kill()

    def resume(self) -> None:
        with self._lock:
            resume()

    def close(self) -> None:
        with self._lock:
            try:
                # 안전 종료: PWM 0 → EN OFF
                _rpwm.value = 0.0
                sleep(0.02)
                _ren.off()
                _len.off()
                _lpwm.off()
                _state["killed"] = True
                # _rpwm.close()  # 전역 재사용 시 닫지 않음 (필요하면 주석 해제)
            except Exception:
                pass

# =====================================================
# 5️⃣ CLI ─ 단독 테스트
# =====================================================
def main():
    safe_init()
    print("✅ 초기화 완료: PWM=0, EN=LOW (killed=True)")

    api = PeltierAPI()
    api.resume()   # 테스트 편의상 EN=HIGH, PWM=0으로 시작
    print("⚙️  resume() → EN=HIGH (R_EN/L_EN), 역PWM=LOW, PWM=0, killed=False")

    try:
        while True:
            raw = input("듀티 0~100 | r 70(램프) | k(킬) | u(재개) | e(EN강제) | d(듀티확인) | x(종료) > ").strip()
            if raw.lower() in ("x", "q", "exit"):
                break
            if raw.lower().startswith("r "):
                try:
                    tgt = int(raw.split()[1])
                    applied = api.ramp_to(tgt)
                    print(f"✔️ ramp → {applied}% (killed={api.is_killed()})")
                except Exception:
                    print("❗ ramp 사용법: r 70")
                continue
            if raw.lower() == "k":
                api.kill()
                print("⛔ kill() → EN LOW, PWM 0, killed=True")
                continue
            if raw.lower() == "u":
                api.resume()
                print("✅ resume() → EN HIGH, PWM 0, killed=False")
                continue
            if raw.lower() == "e":
                api.enable_forward()
                print("🔓 enable_forward() → EN HIGH (killed=False)")
                continue
            if raw.lower() == "d":
                print(f"ℹ️ duty={api.get_duty()}%, killed={api.is_killed()}")
                continue

            try:
                duty = int(raw)
            except ValueError:
                print("❗ 숫자(0~100) 또는 'r 70' / k / u / e / d / x")
                continue
            applied = api.set_duty(duty)
            print(f"✔️ set_duty → {applied}% (killed={api.is_killed()})")
            sleep(0.03)

    except KeyboardInterrupt:
        print("\n⛔ 사용자 강제 종료")

    finally:
        api.close()
        print("✅ PWM 0%, EN OFF, GPIO 정리 완료")

if __name__ == "__main__":
    main()
