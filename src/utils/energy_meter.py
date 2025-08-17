#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils/energy_meter.py
────────────────────────────────────────────────────────
- 하드웨어별 전류 모델을 이용해 '지금 상태로 30초 동안' 소모할 에너지(Wh)를 추정
- 서보는 5V, 나머지는 12V 계통으로 계산
- PWM은 선형 비례(0~100%) 가정

필요 시 아래 전역 변수(최대전류, 개수, 전압)를 수정하세요.
"""

from __future__ import annotations
from typing import Iterable

# ====== 전역 파라미터 (원하는 값으로 수정 가능) =============================

# 서보 (5V 계통)
SERVO_COUNT: int = 8
SERVO_CURRENT_A: float = 0.5          # 서보 1개당 고정 전류 (A)
SERVO_VOLTAGE_V: float = 5.0          # 서보 전압

# 소형 팬 (12V 계통)
FAN_SMALL_COUNT: int = 8
FAN_SMALL_CURRENT_A: float = 0.07     # 소형 팬 1개 최대 전류 (A)

# 대형 팬 (12V 계통)
FAN_LARGE_COUNT: int = 1
FAN_LARGE_CURRENT_A: float = 0.29     # 대형 팬 1개 최대 전류 (A)

# 펠티어 (12V 계통)
PELTIER_MAX_CURRENT_A: float = 6.0    # 펠티어 최대 전류 (A)
PELTIER_VOLTAGE_V: float = 12.0

# 펠티어 전용 쿨링 팬 (12V 계통)
PELTIER_FAN_COUNT: int = 1
PELTIER_FAN_CURRENT_A: float = 0.07   # 펠티어 쿨링팬 1개 최대 전류 (A)

# 공통 전압
BUS_12V: float = 12.0                 # 12V 계통 (팬/펠티어)
DURATION_SEC_DEFAULT: float = 30.0    # 기본 계산 구간(초)

# ==========================================================================


def _duty_to_ratio(pwm: int | float) -> float:
    """0~100(%) → 0.0~1.0, 범위 밖 입력은 클램프."""
    try:
        v = float(pwm)
    except Exception:
        v = 0.0
    if v < 0.0: v = 0.0
    if v > 100.0: v = 100.0
    return v / 100.0


def estimate_energy_wh_30s(
    peltier_pwm: int | float,
    fan_small_pwms: Iterable[int | float] | None = None,
    fan_large_pwm: int | float = 0,
    duration_sec: float = DURATION_SEC_DEFAULT,
    include_servos: bool = True,
) -> float:
    """
    현재 상태가 'duration_sec' 동안 유지된다고 가정할 때의 에너지(Wh) 추정값.

    Args:
        peltier_pwm: 펠티어 PWM(%) 0~100
        fan_small_pwms: 소형 팬 PWM 리스트(개수는 FAN_SMALL_COUNT와 무관, 주어진 개수만 반영)
        fan_large_pwm: 대형 팬 PWM(%)
        duration_sec: 적분 시간(초), 기본 30초
        include_servos: 서보(고정 전류) 포함 여부

    Returns:
        float: 추정 에너지(Wh)
    """
    t_h = max(0.0, float(duration_sec)) / 3600.0

    # 5V: 서보 (고정 전류 모델)
    p_5v = 0.0
    if include_servos and SERVO_COUNT > 0 and SERVO_CURRENT_A > 0:
        i_servo_total = SERVO_COUNT * SERVO_CURRENT_A
        p_5v = SERVO_VOLTAGE_V * i_servo_total  # W

    # 12V: 소형 팬들
    p_12v_fan_small = 0.0
    if fan_small_pwms:
        for pwm in fan_small_pwms:
            r = _duty_to_ratio(pwm)
            p_12v_fan_small += BUS_12V * (FAN_SMALL_CURRENT_A * r)

    # 12V: 대형 팬
    r_big = _duty_to_ratio(fan_large_pwm)
    p_12v_fan_big = BUS_12V * (FAN_LARGE_COUNT * FAN_LARGE_CURRENT_A * r_big)

    # 12V: 펠티어 (최대전류 * PWM 비례)
    r_pel = _duty_to_ratio(peltier_pwm)
    p_12v_peltier = PELTIER_VOLTAGE_V * (PELTIER_MAX_CURRENT_A * r_pel)

    # 12V: 펠티어 쿨링 팬 (펠티어 PWM 비례 가정)
    p_12v_pel_fan = BUS_12V * (PELTIER_FAN_COUNT * PELTIER_FAN_CURRENT_A * r_pel)

    p_total = p_5v + p_12v_fan_small + p_12v_fan_big + p_12v_peltier + p_12v_pel_fan  # W
    #e_wh = p_total * t_h
    e_wh = p_total
    return round(e_wh, 4)
