"""
services/peltier.py
────────────────────────────────────────────────────────
- (Service Layer) 펠티어 PWM 전처리·검증 로직
- 입력 0..100을 다음 규칙으로 변환:
  • 0        → 0 (OFF)
  • 1..100   → MIN_ON..100 으로 선형 매핑(균등 분포)

전역 상수:
- MIN_ON_DUTY_DEFAULT: 50  # ← 40/30 등으로 바꾸면 즉시 반영됨

선형 매핑 정의(정수 라운딩 모드 선택 가능):
- raw ∈ [1..100] → mapped = MIN_ON + R( (raw-1) * (100 - MIN_ON) / 99 )
  (raw=1 → MIN_ON, raw=100 → 100, 구간에 균등 분포)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

# =====================================================
# 0️⃣ 전역 상수 (원하면 여기서 바로 바꾸세요)
# =====================================================
MIN_ON_DUTY_DEFAULT = 50   # ← 40 또는 30으로 바꿔도 동작 (예: 40, 30)
MAX_DUTY_DEFAULT    = 100

# =====================================================
# 유틸
# =====================================================
def _to_int(x: Any, fallback: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return fallback

def _clamp(v: int, lo: int, hi: int) -> int:
    return hi if v > hi else lo if v < lo else v

# =====================================================
# 상태 모델
# =====================================================
@dataclass
class PeltierState:
    raw_duty: int = 0         # 수신 원본(0..100 밖일 수도 있음)
    applied_duty: int = 0     # 실제 적용할 값: 0 또는 MIN_ON..100

# =====================================================
# 서비스
# =====================================================
class PeltierService:
    """
    펠티어 듀티 전처리 서비스.
    - 0..100 입력 → 0은 0, 1..100은 MIN_ON..100 선형 매핑
    """

    def __init__(
        self,
        *,
        min_on_duty: int = MIN_ON_DUTY_DEFAULT,
        max_duty: int = MAX_DUTY_DEFAULT,
        rounding: str = "floor",   # 'floor' | 'round' | 'ceil'
    ) -> None:
        if rounding not in ("floor", "round", "ceil"):
            raise ValueError("rounding 은 'floor' | 'round' | 'ceil' 중 하나여야 합니다.")
        if not (0 <= min_on_duty <= max_duty <= 100):
            raise ValueError("0 <= min_on_duty <= max_duty <= 100 이어야 합니다.")
        self._min_on = int(min_on_duty)
        self._max = int(max_duty)
        self._rounding = rounding
        self.state = PeltierState()

    # -------------------------------------------------
    # 전처리
    # -------------------------------------------------
    def preprocess(self, payload: Mapping[str, Any]) -> int:
        """
        MQTT value payload에서 'peltier_pwm'을 추출해 정책을 적용한 듀티 계산.
        Returns: applied_duty(int): 0 또는 MIN_ON..100
        """
        raw = _clamp(_to_int(payload.get("peltier_pwm", 0), 0), 0, 100)
        applied = self._map_zero_or_linear(raw)
        self.state.raw_duty = raw
        self.state.applied_duty = applied
        return applied

    # -------------------------------------------------
    # 상태 직렬화(상태 토픽 조각)
    # -------------------------------------------------
    def to_status(self) -> dict:
        return {
            "peltier_pwm_cmd": self.state.raw_duty,
            "peltier_pwm_applied": self.state.applied_duty,
            # 필요 시 프로젝트 스키마에 맞게 수정/삭제
            "energy_temp_total": self.state.applied_duty,
        }

    # -------------------------------------------------
    # 드라이버 전달값
    # -------------------------------------------------
    def for_driver(self) -> int:
        """드라이버(PeltierAPI.set_duty)에 바로 전달 가능한 듀티."""
        return int(self.state.applied_duty)

    # -------------------------------------------------
    # 내부 매핑 정책(균등 선형)
    # -------------------------------------------------
    def _map_zero_or_linear(self, raw: int) -> int:
        """
        0 → 0
        1..100 → MIN_ON..100 선형 매핑
          mapped = MIN_ON + R( (raw-1) * (100 - MIN_ON) / 99 )
          (R은 rounding 모드)
        """
        if raw <= 0:
            return 0

        span = self._max - self._min_on  # 0..100
        if span <= 0:
            # MIN_ON==MAX이면 항상 그 값으로 고정
            return self._max

        # (raw-1)/99 로 1→0, 100→1 정규화 → 균등 분포
        inc = (raw - 1) * (span / 99.0)  # 1..100 → 0..span

        if self._rounding == "floor":
            step = math.floor(inc)
        elif self._rounding == "ceil":
            step = math.ceil(inc)
        else:
            step = round(inc)

        mapped = self._min_on + step
        # 안전 클램프
        if mapped < self._min_on: mapped = self._min_on
        if mapped > self._max:    mapped = self._max
        return int(mapped)

# =====================================================
# (선택) 간단 테스트
# =====================================================
if __name__ == "__main__":
    # 전역 상수만 바꿔도 동작(예: MIN_ON_DUTY_DEFAULT=40)
    for min_on in (50, 40, 30):
        print(f"\n=== MIN_ON={min_on} (rounding=floor) ===")
        svc = PeltierService(min_on_duty=min_on, rounding="floor")
        tests = [0, 1, 2, 25, 50, 75, 99, 100]
        for t in tests:
            applied = svc.preprocess({"peltier_pwm": t})
            print(f"raw={t:>3} → applied={applied}")
