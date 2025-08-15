"""
services/fans.py
────────────────────────────────────────────────────────
- (Service Layer) 소형 팬 4개 + 대형 팬 1개 듀티 전처리 (클램핑 없음)
- 입력 예: {"small_fan_pwm": [5, 80, 0, 2], "large_fan_pwm": 90}
- 출력:
    • for_driver()       → [f1,f2,f3,f4,big]
    • to_arduino_cmd()   → "SETF f1 f2 f3 f4 big"
    • to_status()        → 상태 조각 딕셔너리

주의:
- 이 버전은 0~100 강제 클램핑을 하지 않습니다. 아두이노 펌웨어가 0..100을 가정하므로
  범위를 벗어난 값이 들어오면 그대로 전송됩니다.
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


# =====================================================
# 상태 모델
# =====================================================
@dataclass
class FanState:
    small: List[int] = field(default_factory=lambda: [0, 0, 0, 0])  # 소형 4개
    large: int = 0                                                  # 대형 1개


# =====================================================
# 서비스
# =====================================================
class FanService:
    """
    소형 4 + 대형 1 팬 듀티 전처리 서비스(클램핑 없음).
    - 길이만 4로 정규화(초과는 자르고, 부족은 0으로 패딩)
    - 값은 int로만 변환하여 그대로 유지
    """

    def __init__(self) -> None:
        self.state = FanState()

    def preprocess(self, payload: Mapping[str, Any]) -> List[int]:
        """
        Args:
            payload: {"small_fan_pwm":[..4], "large_fan_pwm": int}
        Returns:
            [f1,f2,f3,f4,big]  # 변환만 하고 조정/클램핑 없음
        """
        raw_small = payload.get("small_fan_pwm", [0, 0, 0, 0])
        raw_big   = payload.get("large_fan_pwm", 0)

        # 길이 4 맞추기
        small4 = list(raw_small)[:4]
        if len(small4) < 4:
            small4 += [0] * (4 - len(small4))

        # 정수 변환만 수행
        small = [_to_int(v, 0) for v in small4]
        big   = _to_int(raw_big, 0)

        # 상태 저장
        self.state.small = small
        self.state.large = big

        return small + [big]

    def for_driver(self) -> List[int]:
        """아두이노 브리지/드라이버로 바로 보낼 5개 배열."""
        return list(self.state.small) + [int(self.state.large)]

    def to_arduino_cmd(self) -> str:
        """프로토콜 문자열: 예) 'SETF 5 80 0 2 90'"""
        f1, f2, f3, f4 = self.state.small
        big = self.state.large
        return f"SETF {f1} {f2} {f3} {f4} {big}"

    def to_status(self) -> dict:
        """status/hvac/1/all 조립에 쓰는 상태 조각."""
        return {
            "fan_intake_speed": list(self.state.small),  # 소형 4
            "fan_main_speed": int(self.state.large),     # 대형 1
        }


# =====================================================
# (선택) 간단 테스트
# =====================================================
if __name__ == "__main__":
    sample = {"small_fan_pwm": [5, 80, 0, 2], "large_fan_pwm": 90}

    svc = FanService()
    svc.preprocess(sample)
    print("for_driver:", svc.for_driver())
    print("arduino  :", svc.to_arduino_cmd())
    print("status   :", svc.to_status())
