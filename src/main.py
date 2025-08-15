#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py
────────────────────────────────────────────────────────
- 모터 라즈베리파이: (임시 버전) 펠티어만 제어
- MQTT 수신(control/hvac/{id}/value)의 peltier_pwm → 서비스 전처리 → BTS7960 드라이버 듀티 적용
- 온도 관련(tsv, temp_avg/target_temp_avg)은 **본 버전에서 무시**

필수 의존:
- config.py                     : 브로커/토픽 상수
- mqtt_client.py                : paho-mqtt 래퍼
- actuators/services/peltier.py : 펠티어 듀티 매핑 서비스(0→0, 1~100→MIN_ON..100)
- actuators/drivers/bts7960_peltier_pwm.py : BTS7960 제어(safe_init/enable_forward/set_duty)
"""

# =====================================================
# 0️⃣ Imports & Env
# =====================================================
import os
import sys
import time
import signal
import threading
from pathlib import Path

from dotenv import load_dotenv

# --- sys.path 보정: 이 파일(=src)의 절대경로를 import path에 추가 ---
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import (
    HVAC_ID,
    TOPIC_STATUS_ALL,
    TOPICS_SUB,
    TOPICS_PUB,
)
from mqtt_client import MQTTClient

# services / drivers 는 실제 폴더 위치에 맞게 임포트
from actuators.services.peltier import PeltierService, MIN_ON_DUTY_DEFAULT
from actuators.drivers import bts7960_peltier_pwm as pdrv  # safe_init(), enable_forward(), set_duty()

# =====================================================
# 1️⃣ 전역 (런타임 상태)
# =====================================================
load_dotenv()
BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

mqttc: MQTTClient | None = None
svc_peltier = PeltierService(min_on_duty=MIN_ON_DUTY_DEFAULT, rounding="floor")
_shutdown = threading.Event()


# =====================================================
# 2️⃣ 드라이버 초기화/해제
# =====================================================
def _driver_init() -> None:
    """
    BTS7960 초기 안전 상태 → 정방향 Enable.
    """
    pdrv.safe_init()
    pdrv.enable_forward()
    # 시작 시 듀티 0 보장
    pdrv.set_duty(0)
    print(f"✅ BTS7960 ready (MIN_ON={MIN_ON_DUTY_DEFAULT}%)")


def _driver_safe_off() -> None:
    """
    안전 종료: 듀티 0 → (모듈의 finally와 중복되어도 무해)
    """
    try:
        pdrv.set_duty(0)
    except Exception:
        pass


# =====================================================
# 3️⃣ 상태 발행 도우미
# =====================================================
def _publish_status(applied_duty: int) -> None:
    """
    팀 상태 스키마에 맞춰 최소 필드만 넣어 발행.
    - 추후 서보/팬/LED가 합류하면 data 키 아래 확장
    """
    if mqttc is None:
        return
    payload = {
        "hvac_id": HVAC_ID,
        "data": {
            "airflow_speed": "off",           # (임시) 나중에 팬 합류 시 반영
            "slot_internal": [0, 0, 0, 0],    # (임시)
            "slot_external": [0, 0, 0, 0],    # (임시)
            "fan_intake_speed": [0, 0, 0, 0], # (임시)
            "fan_main_speed": 0,              # (임시)
            # 서비스 to_status()와 동일 키 매핑(필요 시 조정)
            "energy_temp_total": applied_duty,
        },
    }
    mqttc.publish_json(TOPIC_STATUS_ALL, payload)


# =====================================================
# 4️⃣ MQTT 수신 핸들러 (임시: peltier만 처리)
#    mqtt_client.MQTTClient 는 handler(topic, data) 로 호출합니다.
# =====================================================
def on_mqtt(topic: str, data: dict) -> None:
    """
    - control/.../value 에서 peltier_pwm 추출 → 서비스 전처리 → 드라이버 듀티 적용
    - 그 외 토픽은 현재 무시(확장 예정)
    """
    try:
        if not isinstance(data, dict):
            return

        if topic.endswith("/value"):
            # 1) 서비스 전처리
            applied = svc_peltier.preprocess(data)  # 0 or MIN_ON..100

            # 2) 드라이버 적용
            pdrv.set_duty(applied)
            print(f"[Peltier] raw={svc_peltier.state.raw_duty} → applied={applied}")

            # 3) 상태 발행(간단)
            _publish_status(applied)

        # (tsv, power_server 등은 이번 버전에서 무시)

    except Exception as e:
        print(f"[on_mqtt][Error] {e}")


# =====================================================
# 5️⃣ 종료 처리
# =====================================================
def _handle_sigterm(signum, frame):
    print("\n🔚 SIGTERM/SIGINT received. Shutting down...")
    _shutdown.set()


# =====================================================
# 6️⃣ main
# =====================================================
def main():
    global mqttc

    # 드라이버 준비
    _driver_init()

    # MQTT 연결
    mqttc = MQTTClient(
        BROKER_HOST,
        BROKER_PORT,
        publish_topics=TOPICS_PUB,
        subscribe_topics=TOPICS_SUB,
    )
    mqttc.set_message_handler(on_mqtt)
    mqttc.connect(keepalive=60)

    # 종료 시그널 핸들링
    signal.signal(signal.SIGINT, _handle_sigterm)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    print(f"🚀 Running: broker={BROKER_HOST}:{BROKER_PORT} | HVAC_ID={HVAC_ID}")
    print("   * 임시 버전: peltier_pwm만 처리 (temp_avg/target_temp_avg 무시)")

    try:
        # 메인 루프는 단순 대기 (MQTT 콜백이 실질 업무 수행)
        while not _shutdown.is_set():
            time.sleep(0.5)
    finally:
        try:
            if mqttc:
                mqttc.disconnect()
        finally:
            _driver_safe_off()
        print("✅ Cleaned up. Bye.")


if __name__ == "__main__":
    main()
