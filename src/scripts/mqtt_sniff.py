#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/mqtt_sniff.py
────────────────────────────────────────────────────────
- MQTT 모든 메시지(기본 '#')를 구독해서 콘솔에 출력하는 스니퍼
- JSON이면 pretty-print, JSON이 아니어도 RAW 그대로 출력
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Tuple
from dotenv import load_dotenv
import os

# --- sys.path 보정: src를 import path에 추가 ---
SRC_DIR = Path(__file__).resolve().parents[1]  # .../src
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# --- .env 불러오기 ---
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH)

BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

from mqtt_client import MQTTClient  # noqa: E402


def _parse_topics(arg: str | None) -> List[Tuple[str, int]]:
    """
    콤마로 나눈 토픽 문자열을 (topic, qos) 리스트로 변경.
    인자가 없으면 기본 ['#'].
    """
    if not arg:
        return [("#", 0)]
    items = [s.strip() for s in arg.split(",") if s.strip()]
    return [(t, 0) for t in (items or ["#"])]

def _handler(topic: str, data: dict | None, msg) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    raw = msg.payload.decode(errors="ignore")

    print("\n" + "=" * 80)
    print(f"[{ts}] TOPIC: {topic}")
    print("- RAW ---------------------------------------------------------------")
    print(raw)

    # JSON이면 예쁘게도 출력
    try:
        obj = json.loads(raw) if raw else None
        if isinstance(obj, dict) or isinstance(obj, list):
            print("- JSON --------------------------------------------------------------")
            print(json.dumps(obj, ensure_ascii=False, indent=2))
    except Exception:
        pass
    print("=" * 80)

def main():
    ap = argparse.ArgumentParser(description="MQTT Sniffer (print all incoming messages)")
    ap.add_argument(
        "--topics",
        help="구독 토픽들(콤마 구분). 기본값: '#'",
        default=None,
    )
    args = ap.parse_args()
    sub_topics = _parse_topics(args.topics)

    print(f"[Sniffer] Broker = {BROKER_HOST}:{BROKER_PORT}")
    print(f"[Sniffer] Subscribe = {[t for t, _ in sub_topics]}")

    mqttc = MQTTClient(
        BROKER_HOST,
        BROKER_PORT,
        publish_topics=None,
        subscribe_topics=sub_topics,
    )
    mqttc.set_message_handler(_handler)
    mqttc.connect(keepalive=60)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[Sniffer] Stopping...")
    finally:
        mqttc.disconnect()
        print("[Sniffer] Bye.")

if __name__ == "__main__":
    main()
