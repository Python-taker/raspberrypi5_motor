#!/usr/bin/env python3
"""
mqtt_client.py
────────────────────────────────────────────────────────
- paho-mqtt 래퍼: 연결/구독/발행 + 토픽별 최신 JSON 스냅샷 보관
- 팀 규약(config.py)의 (topic, qos) 리스트와 호환

핵심 포인트
1) on_message에서는 파싱/캐시만 수행 (가벼움 유지)
2) latest_* 접근은 Lock으로 일관성 보장 (스냅샷 반환)
3) 외부 핸들러는 (topic, data, msg) → (topic, data) → (msg) 순으로 호환 호출
4) 발행은 화이트리스트 검사 후 로그 경고, 실제 퍼블리시는 항상 수행

사용 예 (main.py)
────────────────────────────────────────────────────────
from config import BROKER_HOST, BROKER_PORT, TOPICS_SUB, TOPICS_PUB
from mqtt_client import MQTTClient

mqttc = MQTTClient(BROKER_HOST, BROKER_PORT, publish_topics=TOPICS_PUB, subscribe_topics=TOPICS_SUB)
mqttc.set_message_handler(on_mqtt)  # def on_mqtt(topic, data, msg): ...
mqttc.connect(keepalive=60)

# 상태 발행
mqttc.publish_json("status/hvac/1/all", {"hvac_id": 1, "data": {...}})
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import paho.mqtt.client as mqtt


# =====================================================
# 1️⃣ 유틸: 토픽 리스트 정규화
#   - 문자열 or (topic, qos) → (topic, qos) 형태로 변환
# =====================================================
def _normalize_topics(
    topics: Optional[Sequence[Union[str, Tuple[str, int]]]]
) -> List[Tuple[str, int]]:
    if not topics:
        return []
    norm: List[Tuple[str, int]] = []
    for t in topics:
        if isinstance(t, tuple):
            topic = str(t[0])
            qos = int(t[1]) if len(t) > 1 else 0
            norm.append((topic, qos))
        else:
            norm.append((str(t), 0))
    return norm


# =====================================================
# 2️⃣ MQTTClient 래퍼
# =====================================================
class MQTTClient:
    """
    paho-mqtt 간단 래퍼

    Args:
        broker_host: 브로커 호스트
        broker_port: 브로커 포트
        publish_topics: 발행 화이트리스트 [(topic, qos), ...]
        subscribe_topics: 구독 목록 [(topic, qos), ...]

    Attributes:
        latest_by_topic: Dict[str, dict]  # 토픽별 최신 JSON
        latest_power/value/tsv: 각 규약 토픽의 최신 JSON
        latest_control_data: 가장 마지막으로 수신한 JSON (아무 토픽)
    """

    # -------------------------------------------------
    # 생성자
    # -------------------------------------------------
    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        *,
        publish_topics: Optional[Sequence[Union[str, Tuple[str, int]]]] = None,
        subscribe_topics: Optional[Sequence[Union[str, Tuple[str, int]]]] = None,
        client_id: Optional[str] = None,
        clean_session: bool = True,
    ) -> None:
        # paho client
        self.client = mqtt.Client(client_id=client_id, clean_session=clean_session)
        self.broker_host = broker_host
        self.broker_port = broker_port

        # 토픽 목록 정규화
        self.publish_topics: List[Tuple[str, int]] = _normalize_topics(publish_topics)
        self.subscribe_topics: List[Tuple[str, int]] = _normalize_topics(subscribe_topics)

        # 최신 상태 보관
        self._lock = threading.Lock()
        self.latest_by_topic: Dict[str, dict] = {}
        self.latest_power: Optional[dict] = None            # .../power_server
        self.latest_tsv: Optional[dict] = None              # .../tsv
        self.latest_value: Optional[dict] = None            # .../value
        self.latest_control_data: Optional[dict] = None     # 마지막으로 수신한 JSON

        # 외부 핸들러 (권장 시그니처: handler(topic:str, data:dict, msg:mqtt.MQTTMessage))
        self.message_handler: Optional[Callable[..., None]] = None

        # paho 콜백 연결
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        # (선택) 재연결 backoff
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    # -------------------------------------------------
    # 핸들러 등록
    # -------------------------------------------------
    def set_message_handler(self, handler: Callable[..., None]) -> None:
        """
        수신 메시지 핸들러 등록.

        권장 시그니처:
            def handler(topic: str, data: dict, msg: mqtt.MQTTMessage): ...

        하위 호환:
            - (topic, data)
            - (msg)
        """
        self.message_handler = handler

    # -------------------------------------------------
    # 연결/종료
    # -------------------------------------------------
    def connect(self, keepalive: int = 60) -> None:
        """브로커 연결 및 네트워크 루프 시작"""
        self.client.connect(self.broker_host, self.broker_port, keepalive=keepalive)
        self.client.loop_start()

    def disconnect(self) -> None:
        """네트워크 루프 중지 및 브로커 연결 종료"""
        try:
            self.client.loop_stop()
        finally:
            try:
                self.client.disconnect()
            except Exception:
                pass

    # -------------------------------------------------
    # 구독/발행 API
    # -------------------------------------------------
    def resubscribe(self, topics: Optional[Sequence[Union[str, Tuple[str, int]]]] = None) -> None:
        """구독 목록 갱신 및 재구독"""
        if topics is not None:
            self.subscribe_topics = _normalize_topics(topics)
        if self.subscribe_topics:
            self.client.subscribe(self.subscribe_topics)
            print(f"[MQTT] Subscribed: {[t for t, _ in self.subscribe_topics]}")

    def publish_raw(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        """
        문자열 payload 발행. 화이트리스트 미포함시 경고만 하고 발행은 수행.
        """
        if self.publish_topics:
            allowed = {t for t, _ in self.publish_topics}
            if topic not in allowed:
                print(f"[MQTT][Warn] {topic} not in publish whitelist. Publishing anyway.")
        print(f"[MQTT] PUB: {topic} (QoS={qos}, retain={retain}) → {payload}")
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def publish_json(self, topic: str, payload: dict, qos: int = 0, retain: bool = False) -> None:
        """dict → JSON 직렬화 후 발행"""
        self.publish_raw(topic, json.dumps(payload, ensure_ascii=False), qos=qos, retain=retain)

    # -------------------------------------------------
    # 최신 스냅샷/헬퍼
    # -------------------------------------------------
    def get_latest_snapshot(self) -> Dict[str, Optional[dict]]:
        """
        토픽 분류별 최신값 스냅샷 반환.
        Returns:
            {
              "power": {...} | None,
              "tsv": {...} | None,
              "value": {...} | None,
              "last": {...} | None
            }
        """
        with self._lock:
            return {
                "power": self.latest_power,
                "tsv": self.latest_tsv,
                "value": self.latest_value,
                "last": self.latest_control_data,
            }

    def get_latest_by_topic(self, topic: str) -> dict:
        """해당 토픽의 최신 파싱 payload (없으면 빈 dict)"""
        with self._lock:
            return dict(self.latest_by_topic.get(topic, {}))

    # 편의 접근자 (팀 규약 토픽 접미사 기준)
    def get_latest_value(self) -> dict:
        with self._lock:
            return dict(self.latest_value or {})

    def get_latest_tsv(self) -> dict:
        with self._lock:
            return dict(self.latest_tsv or {})

    def get_latest_power_server(self) -> dict:
        with self._lock:
            return dict(self.latest_power or {})

    # =====================================================
    # 3️⃣ 내부 paho 콜백
    # =====================================================
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[MQTT] Connected to {self.broker_host}:{self.broker_port}")
            if self.subscribe_topics:
                client.subscribe(self.subscribe_topics)
                print(f"[MQTT] Subscribed: {[t for t, _ in self.subscribe_topics]}")
        else:
            print(f"[MQTT][Error] Connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            print(f"[MQTT][Warn] Unexpected disconnect (rc={rc})")

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        payload_str = msg.payload.decode(errors="ignore")
        print(f"[MQTT] SUB: {msg.topic} → {payload_str}")

        data: Optional[dict[str, Any]] = None
        try:
            data = json.loads(payload_str) if payload_str else {}
        except Exception as e:
            print(f"[MQTT][Error] JSON parse failed: {e}")

        # 최신값 갱신
        with self._lock:
            if data is not None:
                self.latest_control_data = data
                self.latest_by_topic[msg.topic] = data
                if msg.topic.endswith("/power_server"):
                    self.latest_power = data
                elif msg.topic.endswith("/tsv"):
                    self.latest_tsv = data
                elif msg.topic.endswith("/value"):
                    self.latest_value = data

        # 외부 핸들러 호출 (우선순위: (topic, data, msg) → (topic, data) → (msg))
        if self.message_handler:
            try:
                self.message_handler(msg.topic, data, msg)  # 권장
                return
            except TypeError:
                pass
            try:
                self.message_handler(msg.topic, data)       # 하위 호환
                return
            except TypeError:
                pass
            try:
                self.message_handler(msg)                   # 구형
            except Exception as e:
                print(f"[MQTT][Handler Error] {e}")
