"""
services/servo.py
────────────────────────────────────────────────────────
- (Service Layer) 서보 각도 전처리·검증 로직만 담당
- 내부 4ch / 외부 4ch 입력을 클램프(범위 제한)하고 상태를 유지
- ❗ 내부 채널의 60-θ 반전은 '드라이버(ServoAPI)'가 수행함. 여기서는 절대 반전하지 않음.

📌 입력(MQTT 'value' 페이로드 일부 예)
{
  "internal_servo": [45, 45, 44, 6],
  "external_servo": [50, 70, 80, 12]
}

📌 출력(드라이버로 바로 전달 가능)
- 내부: [0..60] 범위로 클램프된 각도 리스트 4개
- 외부: [0..80] 범위로 클램프된 각도 리스트 4개

!! 주의 사항 !!
1) 본 모듈은 하드웨어 I/O를 수행하지 않습니다(순수 로직).
2) 길이가 4보다 길면 잘라내고, 짧으면 0.0으로 패딩합니다.
3) 숫자형 문자열도 float로 변환을 시도합니다. 변환 실패 시 0.0으로 처리합니다.

📌 사용 예 (main/controller에서)
from services.servo import ServoService
svc = ServoService()
i_angles, e_angles = svc.preprocess(payload_value)  # 반전 없음
# drivers.pca9685_servo_module.ServoAPI().set_both(i_angles, e_angles)  # 내부는 드라이버가 60-θ 처리
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple, Mapping, Any


# =====================================================
# 0️⃣ 상수/유틸
# =====================================================
INTERNAL_LEN = 4
EXTERNAL_LEN = 4
INTERNAL_MAX_ANGLES: List[float] = [60.0, 60.0, 60.0, 60.0]
EXTERNAL_MAX_ANGLES: List[float] = [80.0, 80.0, 80.0, 80.0]


def _clamp(v: float, lo: float, hi: float) -> float:
    """단일 값 클램프."""
    return hi if v > hi else lo if v < lo else v


def _to_float(x: Any, fallback: float = 0.0) -> float:
    """숫자/문자열을 float로 변환 시도, 실패 시 fallback."""
    try:
        return float(x)
    except Exception:
        return fallback


def _normalize_list(
    values: Sequence[Any],
    required_len: int,
    *,
    pad_value: float = 0.0,
) -> List[Any]:
    """
    길이를 정확히 required_len으로 맞춤.
    - 길면 잘라냄
    - 짧으면 pad_value로 패딩
    """
    lst = list(values) if values is not None else []
    if len(lst) >= required_len:
        return lst[:required_len]
    # pad
    return lst + [pad_value] * (required_len - len(lst))


# =====================================================
# 1️⃣ 상태 모델
# =====================================================
@dataclass
class ServoState:
    """현재(서비스 관점) 내부/외부 각도 상태"""
    internal: List[float] = field(default_factory=lambda: [0.0] * INTERNAL_LEN)
    external: List[float] = field(default_factory=lambda: [0.0] * EXTERNAL_LEN)


# =====================================================
# 2️⃣ 서비스 본체
# =====================================================
class ServoService:
    """
    서보 각도 전처리 서비스.
    - 내부/외부 각도를 범위에 맞게 클램프하여 저장/반환
    - ❗ 내부 60-θ 반전은 드라이버에서 수행
    """

    # -------------------------------------------------
    # 2-1️⃣ 생성자
    # -------------------------------------------------
    def __init__(
        self,
        internal_max_angles: Sequence[float] = INTERNAL_MAX_ANGLES,
        external_max_angles: Sequence[float] = EXTERNAL_MAX_ANGLES,
        round_to: int | None = None,
    ) -> None:
        """
        Args:
            internal_max_angles: 내부 4ch의 최대 허용 각도(기본 60)
            external_max_angles: 외부 4ch의 최대 허용 각도(기본 80)
            round_to: 소수점 반올림 자리수(예: 1 → 0.1 단위), None이면 반올림 안 함
        """
        if len(internal_max_angles) != INTERNAL_LEN:
            raise ValueError("internal_max_angles는 길이 4여야 합니다.")
        if len(external_max_angles) != EXTERNAL_LEN:
            raise ValueError("external_max_angles는 길이 4여야 합니다.")

        self._imax = [float(x) for x in internal_max_angles]
        self._emax = [float(x) for x in external_max_angles]
        self._round_to = round_to
        self.state = ServoState()

    # -------------------------------------------------
    # 2-2️⃣ 공용 API: 전처리
    # -------------------------------------------------
    def preprocess(self, payload: Mapping[str, Any]) -> Tuple[List[float], List[float]]:
        """
        MQTT value payload에서 내부/외부 각도를 추출하여 클램프 후 상태 저장.

        Args:
            payload: {"internal_servo":[...4], "external_servo":[...4]} 포함한 dict

        Returns:
            (internal_angles, external_angles)  # 반전 없음(드라이버에서 60-θ 수행)
        """
        raw_internal = payload.get("internal_servo", [0, 0, 0, 0])
        raw_external = payload.get("external_servo", [0, 0, 0, 0])

        i_norm = _normalize_list(raw_internal, INTERNAL_LEN, pad_value=0.0)
        e_norm = _normalize_list(raw_external, EXTERNAL_LEN, pad_value=0.0)

        # 숫자화 → 채널별 클램프 → (선택) 반올림
        internal = []
        for idx, val in enumerate(i_norm):
            v = _to_float(val, 0.0)
            v = _clamp(v, 0.0, self._imax[idx])
            if self._round_to is not None:
                v = round(v, self._round_to)
            internal.append(v)

        external = []
        for idx, val in enumerate(e_norm):
            v = _to_float(val, 0.0)
            v = _clamp(v, 0.0, self._emax[idx])
            if self._round_to is not None:
                v = round(v, self._round_to)
            external.append(v)

        # 상태 저장
        self.state.internal = internal
        self.state.external = external
        return internal, external

    # -------------------------------------------------
    # 2-3️⃣ 상태 직렬화(상태 토픽용)
    # -------------------------------------------------
    def to_status(self) -> dict:
        """
        상태 발행용(예: status/hvac/1/all) 조각을 반환.
        프로젝트 스키마에 맞춰 키만 맞추면 됨.
        """
        return {
            "slot_internal": self.state.internal,  # 내부 4ch (반전 적용 전)
            "slot_external": self.state.external,  # 외부 4ch
        }

    # -------------------------------------------------
    # 2-4️⃣ 헬퍼: 드라이버 호출 직전 값 반환
    # -------------------------------------------------
    def for_driver(self) -> Tuple[List[float], List[float]]:
        """
        드라이버(ServoAPI.set_both)에 곧바로 전달 가능한 값 반환.
        ❗ 내부 반전(60-θ)은 드라이버가 수행.
        """
        return list(self.state.internal), list(self.state.external)


# =====================================================
# 3️⃣ (선택) 간단 테스트
# =====================================================
if __name__ == "__main__":
    # 예제 payload
    payload = {
        "internal_servo": [65, "12.3", -3, 30],  # → [60.0, 12.3, 0.0, 30.0]
        "external_servo": [50, 70, 999, "x"],    # → [50.0, 70.0, 80.0, 0.0]
    }

    svc = ServoService(round_to=1)
    i, e = svc.preprocess(payload)
    print("[internal]", i)
    print("[external]", e)
    print("[status]", svc.to_status())
    # 드라이버에서는 내부만 60-θ로 적용됨:
    # drivers.ServoAPI().set_both(i, e)
