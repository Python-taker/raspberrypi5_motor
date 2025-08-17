# src/services/leds.py
"""
services/leds.py
────────────────────────────────────────────────────────
- TSV(-3..3) → LED 색상(R/B/W) 매핑 + 원시 TSV 값(실수) 전송 지원
- 지원 입력 형태:
  A) {"tsv":[...]}
  B) {"values":[...]}, {"slots":[...]}
  C) {"tsv":{"tsv":[...], "temp_avg":..., "target_temp_avg":...}}  ← 중첩형

출력:
  • for_driver_colors()   → ["R"|"B"|"W"] * 4
  • for_driver_values()   → [float] * 4 (클램프/패딩 완료)
  • to_arduino_cmd_colors()  → "SETL C1 C2 C3 C4"
  • to_arduino_cmd_values()  → "SETT v1 v2 v3 v4" (소수 2자리)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, List, Sequence

# =====================================================
# 상수(필요시 프로젝트 정책에 맞게 조정)
# =====================================================
TSV_MIN = -3.0
TSV_MAX =  3.0
TSV_COLD_HIGH = -0.5   # 이하 → Blue
TSV_HOT_LOW   =  0.5   # 이상 → Red

# =====================================================
# 유틸
# =====================================================
def _to_float(x: Any, fallback: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return fallback

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _extract_tsv4(payload: Mapping[str, Any]) -> List[float]:
    """
    다양한 TSV 페이로드를 4개 float 리스트로 정규화.
    - A) {"tsv":[...]}
    - B) {"values":[...]}, {"slots":[...]}
    - C) {"tsv":{"tsv":[...], "temp_avg":..., "target_temp_avg":...}}
    """
    arr: Sequence[Any] | None = None

    if not isinstance(payload, Mapping):
        return [0.0, 0.0, 0.0, 0.0]

    # A) 상위 tsv가 리스트
    tsv = payload.get("tsv")
    if isinstance(tsv, (list, tuple)):
        arr = tsv
    # C) 상위 tsv가 dict이고 그 안에 tsv/values/slots가 리스트
    elif isinstance(tsv, Mapping):
        for k in ("tsv", "values", "slots"):
            v = tsv.get(k)
            if isinstance(v, (list, tuple)):
                arr = v
                break
    # B) 상위 values/slots
    if arr is None:
        for k in ("values", "slots"):
            v = payload.get(k)
            if isinstance(v, (list, tuple)):
                arr = v
                break

    if arr is None:
        arr = [0.0, 0.0, 0.0, 0.0]

    # 길이 4 강제 + float 변환 + [-3,3] 클램프
    out = [_clamp(_to_float(v, 0.0), TSV_MIN, TSV_MAX) for v in list(arr)[:4]]
    if len(out) < 4:
        out += [0.0] * (4 - len(out))
    return out[:4]

# =====================================================
# 상태 모델
# =====================================================
@dataclass
class LedState:
    raw_tsv: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    colors:  List[str]   = field(default_factory=lambda: ["W", "W", "W", "W"])
    temp_avg: float = 0.0
    target_temp_avg: float = 0.0

# =====================================================
# 서비스
# =====================================================
class LedService:
    """
    TSV(4개) → 색상(4개) 매핑 + 원시 TSV 전송을 위한 서비스.
    """
    def __init__(
        self,
        *,
        cold_high: float = TSV_COLD_HIGH,
        hot_low: float = TSV_HOT_LOW,
        tsv_min: float = TSV_MIN,
        tsv_max: float = TSV_MAX,
    ) -> None:
        if not (cold_high < hot_low):
            raise ValueError("cold_high < hot_low 여야 합니다. (예: -0.5 < 0.5)")
        self._cold_high = float(cold_high)
        self._hot_low   = float(hot_low)
        self._tsv_min   = float(tsv_min)
        self._tsv_max   = float(tsv_max)
        self.state = LedState()

    # -------------------------------------------------
    # 전처리: payload → raw_tsv(클램프) → colors
    # -------------------------------------------------
    def preprocess(self, payload: Mapping[str, Any]) -> List[str]:
        tsv4 = _extract_tsv4(payload)
        # 보조 필드(temp_avg 등)도 가능하면 보관(상위/중첩 둘 다 탐색)
        temp_avg = payload.get("temp_avg", 0.0)
        target_temp_avg = payload.get("target_temp_avg", 0.0)
        if isinstance(payload.get("tsv"), Mapping):
            inner = payload["tsv"]
            temp_avg = inner.get("temp_avg", temp_avg)
            target_temp_avg = inner.get("target_temp_avg", target_temp_avg)

        colors = [self._map_tsv_to_color(v) for v in tsv4]

        self.state.raw_tsv = tsv4
        self.state.colors = colors
        self.state.temp_avg = _to_float(temp_avg, 0.0)
        self.state.target_temp_avg = _to_float(target_temp_avg, 0.0)
        return colors

    # -------------------------------------------------
    # 드라이버/브리지 전달
    # -------------------------------------------------
    def for_driver_colors(self) -> List[str]:
        return list(self.state.colors)

    def for_driver_values(self) -> List[float]:
        return list(self.state.raw_tsv)

    # -------------------------------------------------
    # 아두이노 프로토콜 문자열
    # -------------------------------------------------
    def to_arduino_cmd_colors(self) -> str:
        # 예: "SETL R W B R"
        c1, c2, c3, c4 = self.state.colors
        return f"SETL {c1} {c2} {c3} {c4}"

    def to_arduino_cmd_values(self) -> str:
        # 예: "SETT 1.20 0.00 -1.20 2.50"
        v1, v2, v3, v4 = self.state.raw_tsv
        return f"SETT {v1:.2f} {v2:.2f} {v3:.2f} {v4:.2f}"

    # -------------------------------------------------
    # 상태 직렬화(상태 토픽 조각)
    # -------------------------------------------------
    def to_status(self) -> dict:
        return {
            "led_colors": list(self.state.colors),
            "tsv": list(self.state.raw_tsv),
            "temp_avg": self.state.temp_avg,
            "target_temp_avg": self.state.target_temp_avg,
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
