"""
config.py
────────────────────────────────────────────────────────
- MQTT 토픽/환경변수 설정
- 구독/발행 토픽 목록 관리 (라즈베리파이 ↔ 서버)

!! 주의 사항 !!
- HVAC_ID는 기본 1이며, 필요 시 환경변수 HVAC_ID로 재정의 가능
- QoS는 팀 규약대로 기본 0 사용 (변경 시 QOS_DEFAULT만 바꾸면 일괄 반영)
- 브로커 호스트/포트는 환경변수 MQTT_BROKER_HOST / MQTT_BROKER_PORT 사용

📌 페이로드 규약(요약)
- Pub
  1) status/hvac/{HVAC_ID}/all
     {
       "hvac_id": 1,
       "data": {
         "airflow_speed": "low|medium|high|off",
         "slot_internal": [0,0,0,0],
         "slot_external": [0,0,0,0],
         "fan_intake_speed": [0,0,0,0],
         "fan_main_speed": 0,
         "energy_temp_total": 0
       }
     }
  2) control/hvac/{HVAC_ID}/power_actuator
     {"hvac_id": 1, "power": "on|off"}

- Sub
  1) control/hvac/{HVAC_ID}/power_server
     {"power":"on|off"}
  2) control/hvac/{HVAC_ID}/tsv  또는  status/hvac/{HVAC_ID}/tsv   ← ✅ 둘 다 수신
     {
       "temp_avg": 23.4,
       "target_temp_avg": 25.0,
       "tsv": [1.0, 0.0, -0.8, 2.1]      # 길이 4 벡터
     }
  3) control/hvac/{HVAC_ID}/value
     {
       "peltier_pwm": 5,
       "internal_servo": [45,45,44,6],
       "external_servo": [50,70,80,12],
       "small_fan_pwm": [5,80,0,2],
       "large_fan_pwm": 90
     }
"""

import os

# =====================================================
# 1️⃣ 공통 설정 (환경변수)
# =====================================================
HVAC_ID: int = int(os.getenv("HVAC_ID", "1"))

# MQTT 브로커(메인에서 필요 시 import 해서 사용)
BROKER_HOST: str = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT: int = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_KEEPALIVE: int = int(os.getenv("MQTT_KEEPALIVE", "60"))

# QoS 기본값 (팀 규약상 0)
QOS_DEFAULT: int = int(os.getenv("MQTT_QOS_DEFAULT", "0"))

# ✅ 상태 발행 시, 적용값을 보낼지 여부 (기본 True)
STATUS_USE_APPLIED: bool = os.getenv("STATUS_USE_APPLIED", "true").strip().lower() in (
    "1", "true", "t", "yes", "y", "on"
)

# =====================================================
# 2️⃣ 토픽 문자열
# =====================================================
TOPIC_STATUS_ALL      = f"status/hvac/{HVAC_ID}/all"
TOPIC_POWER_ACTUATOR  = f"control/hvac/{HVAC_ID}/power_actuator"

TOPIC_POWER_SERVER    = f"control/hvac/{HVAC_ID}/power_server"
TOPIC_TSV             = f"control/hvac/{HVAC_ID}/tsv"
TOPIC_TSV_STATUS      = f"status/hvac/{HVAC_ID}/tsv"   # ← 추가: 일부 발행측이 status/.../tsv 사용
TOPIC_VALUE           = f"control/hvac/{HVAC_ID}/value"

# =====================================================
# 3️⃣ MQTT 구독(Subscribe) 토픽 리스트
#    paho-mqtt Client.subscribe([(topic, qos), ...]) 형태와 호환
# =====================================================
TOPICS_SUB = [
    (TOPIC_POWER_SERVER, QOS_DEFAULT),  # 서버 전원 제어: {"power":"on|off"}
    (TOPIC_TSV,          QOS_DEFAULT),  # TSV + temp_avg/target_temp_avg + 벡터 (control)
    (TOPIC_TSV_STATUS,   QOS_DEFAULT),  # TSV + ... (status)  ← 추가
    (TOPIC_VALUE,        QOS_DEFAULT),  # 제어값: 팬/서보/펠티어 등
]

# =====================================================
# 4️⃣ MQTT 발행(Publish) 토픽 리스트
# =====================================================
TOPICS_PUB = [
    (TOPIC_STATUS_ALL,     QOS_DEFAULT),  # 액추에이터 상태값 종합
    (TOPIC_POWER_ACTUATOR, QOS_DEFAULT),  # 라즈베리파이 측 전원 상태 회신
]


# =====================================================
# 1-추가) LED 인덱스 매핑 (ENV로 관리)
#  - LED_TSV_ORDER:  들어온 TSV의 인덱스 → "논리 슬롯" 인덱스
#  - LED_HW_ORDER:   논리 슬롯 인덱스 → "아두이노 물리 채널" 인덱스
#  예: "2,0,3,1"  ← 길이 4, 0~3의 순열이어야 함
# =====================================================
def _parse_order_map(env_name: str, default_csv: str = "0,1,2,3") -> list[int]:
    """
    '0,1,2,3' 형태 ENV를 [0,1,2,3] 리스트로 파싱.
    - 길이 != 4, 중복/범위 오류가 있으면 identity로 대체.
    """
    raw = os.getenv(env_name, default_csv).replace(" ", "")
    try:
        arr = [int(x) for x in raw.split(",") if x != ""]
    except Exception:
        arr = [0, 1, 2, 3]
    if len(arr) != 4 or sorted(arr) != [0, 1, 2, 3]:
        print(f"[config][Warn] {env_name}={raw} 가 유효한 순열이 아님 → [0,1,2,3] 사용")
        arr = [0, 1, 2, 3]
    return arr

LED_TSV_ORDER: list[int] = _parse_order_map("LED_TSV_ORDER", "0,1,2,3")
LED_HW_ORDER:  list[int] = _parse_order_map("LED_HW_ORDER",  "2,0,3,1")

__all__ = [
    # 브로커/공통
    "BROKER_HOST", "BROKER_PORT", "MQTT_KEEPALIVE",
    "HVAC_ID", "QOS_DEFAULT",
    "STATUS_USE_APPLIED",                     # ★ 추가
    # 토픽 상수
    "TOPIC_STATUS_ALL", "TOPIC_POWER_ACTUATOR",
    "TOPIC_POWER_SERVER", "TOPIC_TSV", "TOPIC_TSV_STATUS", "TOPIC_VALUE",
    "TOPIC_POWER_SERVER", "TOPIC_TSV", "TOPIC_VALUE",
    # 리스트
    "TOPICS_SUB", "TOPICS_PUB",
    # LED 위치 보정
    "LED_TSV_ORDER", "LED_HW_ORDER",
]
