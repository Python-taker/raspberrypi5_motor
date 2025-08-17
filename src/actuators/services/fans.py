"""
services/fans.py
────────────────────────────────────────────────────────
- (Service Layer) 소형 팬 4개 + 대형 팬 1개 듀티 전처리
- 요구사항 반영:
    • 소형 4개(3핀, MOSFET): 0 → 0 고정, 1..100 → 선형 매핑 30..100
    • 대형 1개(4핀, PWM): 0..100 그대로 사용(안전상 0..100 클램프)
- 입력 예: {"small_fan_pwm": [5, 80, 0, 2], "large_fan_pwm": 90}
- 출력:
    • for_driver()       → [f1,f2,f3,f4,big]   (적용값: 소형은 매핑 후, 대형은 클램프)
    • to_arduino_cmd()   → "SETF f1 f2 f3 f4 big"  (적용값)
    • to_status()        → {"fan_intake_speed":[...], "fan_main_speed": int}  (적용값)

비고:
- 소형팬 선형 매핑은 1→MIN_SMALL_ON, 100→100 이 되도록 99구간 균등 스케일.
- 입력은 int로 변환 후 처리. 안전상 대형/소형 모두 0..100 범위로 클램프.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, List


# =====================================================
# 유틸
# =====================================================
def _to_int(x: Any, fallback: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return fallback

def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


# =====================================================
# 상태 모델
# =====================================================
@dataclass
class FanState:
    # 원본(수신 그대로, int 변환만)
    small_raw: List[int] = field(default_factory=lambda: [0, 0, 0, 0])  # 소형 4개
    large_raw: int = 0                                                  # 대형 1개
    # 적용(드라이버로 전송되는 값: 소형은 매핑, 대형은 클램프)
    small_applied: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    large_applied: int = 0


# =====================================================
# 서비스
# =====================================================
class FanService:
    """
    소형 4 + 대형 1 팬 듀티 전처리 서비스.
    - 소형: 0 → 0, 1..100 → 선형 매핑 30..100 (기본값, 변경 가능)
    - 대형: 0..100 클램프(그대로 사용)
    """
    def __init__(self, *, min_small_on: int = 30) -> None:
        if not (0 <= min_small_on <= 100):
            raise ValueError("min_small_on must be within 0..100")
        self.min_small_on = int(min_small_on)
        self.state = FanState()

    # ---------- 내부: 소형 듀티 매핑 ----------
    def _map_small_duty(self, v: int) -> int:
        """
        0 → 0
        1..100 → 선형 매핑(min_small_on..100)
        """
        v = _clamp(v, 0, 100)
        if v == 0:
            return 0
        if v == 100:
            return 100
        # 1..99 를 0..98 로 정규화 후 스케일, 1→min_small_on, 100→100
        # 등가: min_small_on + (v-1) * (100 - min_small_on) / 99
        num = (v - 1) * (100 - self.min_small_on)
        mapped = self.min_small_on + round(num / 99.0)
        return _clamp(mapped, 0, 100)

    # ---------- 공개 API ----------
    def preprocess(self, payload: Mapping[str, Any]) -> List[int]:
        """
        Args:
            payload: {"small_fan_pwm":[..4], "large_fan_pwm": int}
        Returns:
            [f1,f2,f3,f4,big]  # 드라이버 전송용 '적용값'(소형은 매핑, 대형은 클램프)
        """
        raw_small = payload.get("small_fan_pwm", [0, 0, 0, 0])
        raw_big   = payload.get("large_fan_pwm", 0)

        # 길이 4 맞추기
        small4 = list(raw_small)[:4]
        if len(small4) < 4:
            small4 += [0] * (4 - len(small4))

        # 정수 변환 → 원본 저장
        small_raw = [_to_int(v, 0) for v in small4]
        big_raw   = _to_int(raw_big, 0)
        self.state.small_raw = small_raw
        self.state.large_raw = big_raw

        # 적용값 계산
        small_applied = [self._map_small_duty(_clamp(v, 0, 100)) for v in small_raw]
        big_applied   = _clamp(big_raw, 0, 100)

        self.state.small_applied = small_applied
        self.state.large_applied = big_applied

        return small_applied + [big_applied]

    def for_driver(self) -> List[int]:
        """아두이노 브리지/드라이버로 바로 보낼 5개 배열(적용값)."""
        return list(self.state.small_applied) + [int(self.state.large_applied)]

    def to_arduino_cmd(self) -> str:
        """프로토콜 문자열(적용값): 예) 'SETF 30 84 0 30 90'"""
        f1, f2, f3, f4 = self.state.small_applied
        big = self.state.large_applied
        return f"SETF {f1} {f2} {f3} {f4} {big}"

    def to_status(self) -> dict:
        """
        status/hvac/.../all 조립에 쓰는 상태 조각(적용값 반영).
        - 소형은 매핑 후 값
        - 대형은 0..100 클램프 값
        """
        return {
            "fan_intake_speed": list(self.state.small_applied),  # 소형 4 (매핑 후)
            "fan_main_speed": int(self.state.large_applied),     # 대형 1 (클램프)
        }

    # (선택) 원본 상태도 보고 싶을 때
    def to_status_raw_debug(self) -> dict:
        """디버깅용: 원본 수신값 그대로(클램프/매핑 전)."""
        return {
            "fan_intake_speed_raw": list(self.state.small_raw),
            "fan_main_speed_raw": int(self.state.large_raw),
        }


# =====================================================
# (선택) 간단 테스트
# =====================================================
if __name__ == "__main__":
    sample = {"small_fan_pwm": [1, 80, 0, 2], "large_fan_pwm": 90}

    svc = FanService(min_small_on=30)
    applied = svc.preprocess(sample)
    print("for_driver (applied):", svc.for_driver(), "| returned:", applied)
    print("arduino  (applied)  :", svc.to_arduino_cmd())
    print("status   (applied)  :", svc.to_status())
    print("status raw (debug)  :", svc.to_status_raw_debug())
