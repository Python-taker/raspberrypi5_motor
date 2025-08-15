from mqtt_client import MQTTClient
import time
import json
import os
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = os.getenv("MQTT_BROKER_HOST")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT"))

# 토픽 Sub
TOPICS_SUB_WITH_QOS = [
    ("actuator/1/all", 0),
    ("actuator/1/motors", 0)
]
# 토픽 Pub
TOPICS_PUB_WITH_QOS = [
    ("hvac/1/all", 0) 
]

mqtt_client = MQTTClient(BROKER_HOST, BROKER_PORT, TOPICS_SUB_WITH_QOS, TOPICS_PUB_WITH_QOS)
mqtt_client.connect()

# 예시: 5초마다 현재 상태를 hvac/all로 publish (실제론 실시간 값으로 교체)
for i in range(5):
    state = {
        "motors": [i%2, (i+1)%2, 1, 0, 1, 0, 1, 0],
        "fans": [1, 0, 1, i%2, (i+1)%2]
    }
    payload = json.dumps(state)
    for topic, qos in TOPICS_PUB_WITH_QOS:
        mqtt_client.publish(topic, payload, qos=qos)
    time.sleep(5)

# 메인 루프 (실전에서는 on_message에서 제어콜백만 붙이면 됨)
while True:
    time.sleep(1)
