"""
services/leds.py
────────────────────────────────────────────────────────
- (Service Layer) TSV(-3..3) → LED 색상(R/B/W) 매핑
- sub 토픽 payload 예:
  {"temp_avg": 0.0, "target_temp_avg": 0.0, "tsv": [1.0, 0.0, -1.2, 2.5]}

매핑 규칙(기본):
  v <= COLD_HIGH (기본 -0.5)  → "B"  (Blue = 춥다)
  COLD_HIGH < v < HOT_LOW     → "W"  (White = 쾌적)
  v >= HOT_LOW  (기본  0.5)   → "R"  (Red = 덥다)

출력:
  • for_driver()      → ["R"|"B"|"W"|...]*4
  • to_arduino_cmd()  → "SETL C1 C2 C3 C4"
  • to_status()       → 상태 조각 (원하면 status/hvac/1/all에 포함)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, List

# =====================================================
# 0️⃣ 임계값 전역 상수(프로젝트 전역 정책에 맞게 조정)
# =====================================================
TSV_COLD_HIGH = -0.5   # 이하이면 춥다 → Blue
TSV_HOT_LOW   =  0.5   # 이상이면 덥다 → Red

# =====================================================
# 유틸
# =====================================================
def _to_float(x: Any, fallback: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return fallback

# =====================================================
# 상태 모델
# =====================================================
@dataclass
class LedState:
    raw_tsv: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    colors:  List[str]   = field(default_factory=lambda: ["W", "W", "W", "W"])  # 기본 쾌적=흰색

# =====================================================
# 서비스
# =====================================================
class LedService:
    """
    TSV(4개) → LED 색상(4개) 매핑 서비스.
    - 길이를 4로 맞춤(초과 자르고, 부족하면 0.0으로 패딩)
    - 색상 토큰: 'R','B','W' (필요시 'OFF'도 확장 가능)
    """
    def __init__(self, *, cold_high: float = TSV_COLD_HIGH, hot_low: float = TSV_HOT_LOW) -> None:
        if not (cold_high < hot_low):
            raise ValueError("cold_high < hot_low 여야 합니다. (예: -0.5 < 0.5)")
        self._cold_high = float(cold_high)
        self._hot_low   = float(hot_low)
        self.state = LedState()

    # -------------------------------------------------
    # 전처리: TSV → 색상 배열
    # -------------------------------------------------
    def preprocess(self, payload: Mapping[str, Any]) -> List[str]:
        """
        Args:
            payload: {"tsv":[...] } 를 포함한 dict
        Returns:
            colors: 길이 4 리스트 (각 원소는 "R"/"B"/"W")
        """
        raw = payload.get("tsv", [0.0, 0.0, 0.0, 0.0])

        tsv4 = list(raw)[:4]
        if len(tsv4) < 4:
            tsv4 += [0.0] * (4 - len(tsv4))

        tsv_f = [_to_float(v, 0.0) for v in tsv4]
        colors = [self._map_tsv_to_color(v) for v in tsv_f]

        self.state.raw_tsv = tsv_f
        self.state.colors  = colors
        return colors

    # -------------------------------------------------
    # 아두이노 전송 문자열
    # -------------------------------------------------
    def to_arduino_cmd(self) -> str:
        """
        Arduino 프로토콜 문자열 생성:
          예) "SETL R W B R"
        """
        c1, c2, c3, c4 = self.state.colors
        return f"SETL {c1} {c2} {c3} {c4}"

    # -------------------------------------------------
    # 드라이버/브리지 전달용
    # -------------------------------------------------
    def for_driver(self) -> List[str]:
        """시리얼 브리지에 바로 넘길 수 있는 색상 토큰 배열."""
        return list(self.state.colors)

    # -------------------------------------------------
    # 상태 직렬화(상태 토픽 조각)
    # -------------------------------------------------
    def to_status(self) -> dict:
        """status/hvac/1/all 조립에 쓸 상태 조각(예시)."""
        return {
            "led_colors": list(self.state.colors),
            "tsv": list(self.state.raw_tsv),
        }

    # -------------------------------------------------
    # 내부 매핑
    # -------------------------------------------------
    def _map_tsv_to_color(self, v: float) -> str:
        if v <= self._cold_high:
            return "B"  # Blue: 춥다
        if v >= self._hot_low:
            return "R"  # Red: 덥다
        return "W"      # White: 쾌적
