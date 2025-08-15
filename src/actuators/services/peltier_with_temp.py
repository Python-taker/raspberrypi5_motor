"""
services/peltier.py
────────────────────────────────────────────────────────
- (Service Layer) 펠티어 PWM 전처리·검증 + 온도기반 가중치 보정
- 입력 0..100을 다음 규칙으로 변환:
  • 0        → 0 (OFF)
  • 1..100   → MIN_ON..100 으로 선형 매핑(균등 분포)
- 온도 보정:
  • temp_avg < target_temp_avg  (추움) → 50(MIN_ON)에 가까워지도록 가중
      - 특수 규칙: 가중치 적용 전 값이 정확히 50(MIN_ON)이면 0으로 강제 OFF
  • temp_avg > target_temp_avg  (더움) → 100에 가까워지도록 가중
  • base가 0이면(OFF) 가중치로 켜지지 않음 (0 유지)

전역 상수(프로젝트 정책에 맞게 조정):
- MIN_ON_DUTY_DEFAULT = 50      # 40/30 등으로 변경 가능
- MAX_DUTY_DEFAULT    = 100
- BIAS_WEIGHT_COLD_DEFAULT = 0.5  # 0.0~1.0 (추울 때 50쪽으로 끌어당기는 비율)
- BIAS_WEIGHT_HOT_DEFAULT  = 0.5  # 0.0~1.0 (더울 때 100쪽으로 끌어당기는 비율)

선형 매핑(균등):
- raw ∈ [1..100] → mapped = MIN_ON + R( (raw-1) * (100 - MIN_ON) / 99 )
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

# =====================================================
# 0️⃣ 전역 상수
# =====================================================
MIN_ON_DUTY_DEFAULT = 50
MAX_DUTY_DEFAULT    = 100

# 온도 가중치 (0.0=가중치 없음, 1.0=완전 타겟 값으로 점프)
BIAS_WEIGHT_COLD_DEFAULT = 0.5   # 추울 때 50으로 끌어당김
BIAS_WEIGHT_HOT_DEFAULT  = 0.5   # 더울 때 100으로 끌어당김

# =====================================================
# 유틸
# =====================================================
def _to_int(x: Any, fallback: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return fallback

def _to_float(x: Any, fallback: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return fallback

def _clamp(v: int, lo: int, hi: int) -> int:
    return hi if v > hi else lo if v < lo else v

def _mix(value: float, target: float, w: float) -> float:
    """value를 target 쪽으로 w(0~1)만큼 당김."""
    if w <= 0.0: return value
    if w >= 1.0: return target
    return (1.0 - w) * value + w * target

# =====================================================
# 상태 모델
# =====================================================
@dataclass
class PeltierState:
    raw_duty: int = 0            # 수신 원본(0..100 밖일 수도)
    base_mapped: int = 0         # 0 또는 MIN_ON..100 (온도 가중 전)
    applied_duty: int = 0        # 가중치 반영 후 최종 적용
    temp_avg: float = 0.0
    target_temp_avg: float = 0.0
    delta_t: float = 0.0         # temp_avg - target_temp_avg

# =====================================================
# 서비스
# =====================================================
class PeltierService:
    """
    펠티어 듀티 전처리:
      1) 0..100 입력 → 0 또는 MIN_ON..100 선형 매핑
      2) 온도 편차 기반 가중치 보정(추우면 50쪽, 더우면 100쪽)
      3) 특수 규칙: base가 정확히 50 & 추움 → 0으로 강제
      4) base가 0이면 가중치로 켜지지 않음(그대로 0)
    """

    def __init__(
        self,
        *,
        min_on_duty: int = MIN_ON_DUTY_DEFAULT,
        max_duty: int = MAX_DUTY_DEFAULT,
        rounding: str = "floor",             # 'floor' | 'round' | 'ceil'
        bias_weight_cold: float = BIAS_WEIGHT_COLD_DEFAULT,
        bias_weight_hot: float  = BIAS_WEIGHT_HOT_DEFAULT,
    ) -> None:
        if rounding not in ("floor", "round", "ceil"):
            raise ValueError("rounding 은 'floor' | 'round' | 'ceil' 중 하나여야 합니다.")
        if not (0 <= min_on_duty <= max_duty <= 100):
            raise ValueError("0 <= min_on_duty <= max_duty <= 100 이어야 합니다.")
        if not (0.0 <= bias_weight_cold <= 1.0 and 0.0 <= bias_weight_hot <= 1.0):
            raise ValueError("bias weights must be in [0.0, 1.0].")

        self._min_on = int(min_on_duty)
        self._max = int(max_duty)
        self._rounding = rounding
        self._w_cold = float(bias_weight_cold)
        self._w_hot  = float(bias_weight_hot)

        self.state = PeltierState()

    # -------------------------------------------------
    # 전처리 (payload 예: {"peltier_pwm": 5, "temp_avg": 22.3, "target_temp_avg": 24.0})
    # -------------------------------------------------
    def preprocess(self, payload: Mapping[str, Any]) -> int:
        """
        1) peltier_pwm → base(0 또는 MIN_ON..100)
        2) temp_avg / target_temp_avg → delta 계산
        3) delta<0(추움): base==MIN_ON이면 0으로 강제, 아니면 50쪽으로 가중
           delta>0(더움): 100쪽으로 가중
           delta==0: 가중치 없음
        4) applied 저장 후 반환
        """
        # 입력값 파싱
        raw = _clamp(_to_int(payload.get("peltier_pwm", 0), 0), 0, 100)
        temp_avg = _to_float(payload.get("temp_avg", 0.0), 0.0)
        target   = _to_float(payload.get("target_temp_avg", 0.0), 0.0)
        delta_t  = temp_avg - target

        # 1) 기본 매핑
        base = self._map_zero_or_linear(raw)

        # 2) 온도 가중치 적용
        applied = base
        if base == 0:
            # OFF는 가중치로 켜지지 않음
            applied = 0
        else:
            if delta_t < 0:
                # 추움: 50쪽으로 끌기
                if base == self._min_on:
                    # 특수 규칙: base가 정확히 50이고 추우면 0으로 강제 OFF
                    applied = 0
                else:
                    applied = int(round(_mix(base, self._min_on, self._w_cold)))
            elif delta_t > 0:
                # 더움: 100쪽으로 끌기
                applied = int(round(_mix(base, self._max, self._w_hot)))
            # delta_t == 0 → 변화 없음

        # 상태 저장
        self.state.raw_duty = raw
        self.state.base_mapped = int(base)
        self.state.applied_duty = int(applied)
        self.state.temp_avg = float(temp_avg)
        self.state.target_temp_avg = float(target)
        self.state.delta_t = float(delta_t)

        return self.state.applied_duty

    # -------------------------------------------------
    # 상태 직렬화(상태 토픽 조각)
    # -------------------------------------------------
    def to_status(self) -> dict:
        return {
            "peltier_pwm_cmd": self.state.raw_duty,
            "peltier_pwm_base": self.state.base_mapped,
            "peltier_pwm_applied": self.state.applied_duty,
            "temp_avg": self.state.temp_avg,
            "target_temp_avg": self.state.target_temp_avg,
            "delta_t": self.state.delta_t,
        }

    # -------------------------------------------------
    # 드라이버 전달값
    # -------------------------------------------------
    def for_driver(self) -> int:
        """드라이버(PeltierAPI.set_duty)에 바로 전달 가능한 듀티."""
        return int(self.state.applied_duty)

    # -------------------------------------------------
    # 내부: 선형 매핑(균등)
    # -------------------------------------------------
    def _map_zero_or_linear(self, raw: int) -> int:
        """
        0 → 0
        1..100 → MIN_ON..100 선형 매핑
          mapped = MIN_ON + R( (raw-1) * (100 - MIN_ON) / 99 )
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
    svc = PeltierService(
        min_on_duty=50,
        bias_weight_cold=0.6,
        bias_weight_hot=0.4,
        rounding="floor",
    )

    samples = [
        {"peltier_pwm": 0,  "temp_avg": 23.0, "target_temp_avg": 25.0},  # base=0 → 0 유지
        {"peltier_pwm": 50, "temp_avg": 23.0, "target_temp_avg": 25.0},  # base=50 & 추움 → 0 강제
        {"peltier_pwm": 75, "temp_avg": 23.0, "target_temp_avg": 25.0},  # 추움 → 50쪽으로
        {"peltier_pwm": 60, "temp_avg": 28.0, "target_temp_avg": 25.0},  # 더움 → 100쪽으로
        {"peltier_pwm": 100,"temp_avg": 25.0, "target_temp_avg": 25.0},  # 평형 → 변화 없음
    ]
    for p in samples:
        out = svc.preprocess(p)
        print(p, "=>", out, "| status:", svc.to_status())
