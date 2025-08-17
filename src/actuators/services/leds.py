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
  • for_driver_colors()              → ["R"|"B"|"W"] * 4  (소프트킬 미적용, 순수 매핑)
  • for_driver_colors_effective(...) → ["R"|"B"|"W"|"OFF"] * 4  (소프트킬 적용 결과)
  • for_driver_values()              → [float] * 4 (클램프/패딩 완료)
  • to_arduino_cmd_colors()          → "SETL C1 C2 C3 C4"  (소프트킬 미적용)
  • to_arduino_cmd_colors_effective(...) → "SETL C1 C2 C3 C4" (소프트킬 적용)
  • to_arduino_cmd_values()          → "SETT v1 v2 v3 v4" (소수 2자리)

소프트킬 연동:
  - softkill가 "killed=True"일 때 LED를 강제로 끄려면
      colors = svc.for_driver_colors_effective(softkill_killed=True, off_token="OFF")  # or "W"
    또는
      cmd = svc.to_arduino_cmd_colors_effective(softkill_killed=True, off_token="OFF")
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
    colors:  List[str]   = field(default_factory=lambda: ["W", "W", "W", "W"])   # 순수 매핑 결과(R/B/W)
    effective_colors: List[str] = field(default_factory=lambda: ["W", "W", "W", "W"])  # 소프트킬 적용 반영(R/B/W/OFF)
    temp_avg: float = 0.0
    target_temp_avg: float = 0.0

# =====================================================
# 서비스
# =====================================================
class LedService:
    """
    TSV(4개) → 색상(4개) 매핑 + 원시 TSV 전송을 위한 서비스.
    소프트킬(킬스위치) 적용 시에는 effective_colors를 사용.
    """
    def __init__(
        self,
        *,
        cold_high: float = TSV_COLD_HIGH,
        hot_low: float = TSV_HOT_LOW,
        tsv_min: float = TSV_MIN,
        tsv_max: float = TSV_MAX,
        default_off_token: str = "OFF",   # "OFF" 또는 "W" 권장
    ) -> None:
        if not (cold_high < hot_low):
            raise ValueError("cold_high < hot_low 여야 합니다. (예: -0.5 < 0.5)")
        self._cold_high = float(cold_high)
        self._hot_low   = float(hot_low)
        self._tsv_min   = float(tsv_min)
        self._tsv_max   = float(tsv_max)
        self._default_off_token = str(default_off_token).upper()
        if self._default_off_token not in {"OFF", "W"}:
            raise ValueError("default_off_token은 'OFF' 또는 'W'만 허용됩니다.")
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
        self.state.effective_colors = list(colors)  # 기본은 동일, 소프트킬 적용 시 별도 오버라이드
        self.state.temp_avg = _to_float(temp_avg, 0.0)
        self.state.target_temp_avg = _to_float(target_temp_avg, 0.0)
        return colors

    # -------------------------------------------------
    # 드라이버/브리지 전달 (소프트킬 미적용: 기존 호환)
    # -------------------------------------------------
    def for_driver_colors(self) -> List[str]:
        """순수 매핑(R/B/W) — 기존 코드와 100% 호환."""
        return list(self.state.colors)

    def for_driver_values(self) -> List[float]:
        return list(self.state.raw_tsv)

    # -------------------------------------------------
    # 드라이버/브리지 전달 (소프트킬 적용)
    # -------------------------------------------------
    def for_driver_colors_effective(self, *, softkill_killed: bool, off_token: str | None = None) -> List[str]:
        """
        소프트킬 적용 결과를 반환.
        softkill_killed=True → [off_token]*4
        softkill_killed=False → 순수 매핑 colors
        """
        token = (off_token or self._default_off_token).upper()
        if token not in {"OFF", "W"}:
            raise ValueError("off_token은 'OFF' 또는 'W'만 허용됩니다.")
        eff = [token, token, token, token] if softkill_killed else list(self.state.colors)
        self.state.effective_colors = eff
        return eff

    # -------------------------------------------------
    # 아두이노 프로토콜 문자열
    # -------------------------------------------------
    def to_arduino_cmd_colors(self) -> str:
        # 예: "SETL R W B R"  (소프트킬 미적용)
        c1, c2, c3, c4 = self.state.colors
        return f"SETL {c1} {c2} {c3} {c4}"

    def to_arduino_cmd_colors_effective(self, *, softkill_killed: bool, off_token: str | None = None) -> str:
        # 예: "SETL OFF OFF OFF OFF"  (소프트킬 적용 시)
        cols = self.for_driver_colors_effective(softkill_killed=softkill_killed, off_token=off_token)
        c1, c2, c3, c4 = cols
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
            "led_colors_effective": list(self.state.effective_colors),
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
