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

BROKER_HOST: str = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT: int = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_KEEPALIVE: int = int(os.getenv("MQTT_KEEPALIVE", "60"))

QOS_DEFAULT: int = int(os.getenv("MQTT_QOS_DEFAULT", "0"))

# ✅ 상태 발행 시, 보정된 값(적용값)을 보낼지 여부 (기본 True)
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
TOPIC_TSV_STATUS      = f"status/hvac/{HVAC_ID}/tsv"
TOPIC_VALUE           = f"control/hvac/{HVAC_ID}/value"

# =====================================================
# 3️⃣ MQTT 구독/발행 리스트
# =====================================================
TOPICS_SUB = [
    (TOPIC_POWER_SERVER, QOS_DEFAULT),
    (TOPIC_TSV,          QOS_DEFAULT),
    (TOPIC_TSV_STATUS,   QOS_DEFAULT),
    (TOPIC_VALUE,        QOS_DEFAULT),
]

TOPICS_PUB = [
    (TOPIC_STATUS_ALL,     QOS_DEFAULT),
    (TOPIC_POWER_ACTUATOR, QOS_DEFAULT),
]

# =====================================================
# 4️⃣ LED 인덱스 매핑
# =====================================================
def _parse_order_map(env_name: str, default_csv: str = "0,1,2,3") -> list[int]:
    raw = os.getenv(env_name, default_csv).replace(" ", "")
    try:
        arr = [int(x) for x in raw.split(",") if x != ""]
    except Exception:
        arr = [0, 1, 2, 3]
    if len(arr) != 4 or sorted(arr) != [0, 1, 2, 3]:
        print(f"[config][Warn] {env_name}={raw} 가 유효하지 않음 → [0,1,2,3] 사용")
        arr = [0, 1, 2, 3]
    return arr

LED_TSV_ORDER: list[int] = _parse_order_map("LED_TSV_ORDER", "0,1,2,3")
LED_HW_ORDER:  list[int] = _parse_order_map("LED_HW_ORDER",  "2,0,3,1")

# =====================================================
# 5️⃣ 소프트-Kill 버튼 & 상태 LED 핀
# =====================================================
# 버튼: GPIO_KILL_PIN (토글 동작)
GPIO_KILL_PIN: int = int(os.getenv("GPIO_KILL_PIN", "-1"))  # -1이면 비활성화
GPIO_KILL_ACTIVE_LOW: bool = os.getenv("GPIO_KILL_ACTIVE_LOW", "1") in ("1", "true", "True")
GPIO_KILL_DEBOUNCE_MS: int = int(os.getenv("GPIO_KILL_DEBOUNCE_MS", "120"))

# LED: 공통 캐소드/애노드 여부에 따라 ACTIVE_HIGH 조정
GPIO_LED_RED_PIN: int   = int(os.getenv("GPIO_LED_RED_PIN", "-1"))
GPIO_LED_GREEN_PIN: int = int(os.getenv("GPIO_LED_GREEN_PIN", "-1"))
GPIO_LED_ACTIVE_HIGH: bool = os.getenv("GPIO_LED_ACTIVE_HIGH", "1") in ("1", "true", "True")
# ACTIVE_HIGH=True → HIGH=켜짐 / False → LOW=켜짐

# =====================================================
# __all__ 공개 API
# =====================================================
__all__ = [
    # 브로커/공통
    "BROKER_HOST", "BROKER_PORT", "MQTT_KEEPALIVE",
    "HVAC_ID", "QOS_DEFAULT", "STATUS_USE_APPLIED",
    # 토픽
    "TOPIC_STATUS_ALL", "TOPIC_POWER_ACTUATOR",
    "TOPIC_POWER_SERVER", "TOPIC_TSV", "TOPIC_TSV_STATUS", "TOPIC_VALUE",
    # 리스트
    "TOPICS_SUB", "TOPICS_PUB",
    # LED 위치 보정
    "LED_TSV_ORDER", "LED_HW_ORDER",
    # 소프트킬/LED
    "GPIO_KILL_PIN", "GPIO_KILL_ACTIVE_LOW", "GPIO_KILL_DEBOUNCE_MS",
    "GPIO_LED_RED_PIN", "GPIO_LED_GREEN_PIN", "GPIO_LED_ACTIVE_HIGH",
]