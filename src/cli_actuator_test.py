#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli_actuator_test.py (NO-MQTT, call main.py logic directly)
────────────────────────────────────────────────────────
- MQTT 브로커 없이 main.py 내부 로직을 그대로 태우는 CLI
- 하는 일:
    • main._driver_init() 호출 → 실제 하드웨어 드라이버 초기화(+서보 홈)
    • main.mqttc 에 DummyPublisher 주입 → _publish_status() 출력 가시화
    • 명령을 main.on_mqtt(".../value|tsv|power_server", payload) 로 직접 투입
    • 종료 시 main 과 동일한 안전 종료 루틴 수행
"""

from __future__ import annotations

import sys
import json
import time
import signal
from pathlib import Path
from typing import Any, List

# --- sys.path 보정 (cli와 main이 같은 폴더라고 가정) ---
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# main.py를 모듈로 사용 (여기서 드라이버/서비스/상태/콜백 전부 재사용)
import main as app

# ===== Dummy MQTT Publisher (status/알림을 콘솔로 표시) =====
class DummyPublisher:
    def __init__(self) -> None:
        self.last = {}

    def publish_json(self, topic: str, payload: dict) -> None:
        self.last = {"topic": topic, "payload": payload}
        print(f"[STATUS-PUB] {topic} {json.dumps(payload, ensure_ascii=False)}")

    # 인터페이스 호환용 (실제 동작은 안 함)
    def set_message_handler(self, *_args, **_kwargs):
        pass
    def connect(self, *_args, **_kwargs):
        pass
    def disconnect(self):
        pass

# ===== 입력 파서 =====
def _ints(xs: List[str]) -> List[int]:
    out: List[int] = []
    for s in xs:
        try: out.append(int(float(s)))
        except: out.append(0)
    return out

def _floats(xs: List[str]) -> List[float]:
    out: List[float] = []
    for s in xs:
        try: out.append(float(s))
        except: out.append(0.0)
    return out

def _help() -> None:
    print(r"""
================= CLI (NO MQTT) =================
main.py의 on_mqtt 로직을 직접 호출해 테스트합니다.

명령어:
  peltier <duty>                       예) peltier 45
  fans <f1> <f2> <f3> <f4> <big>       예) fans 30 40 0 0 60
  fan-small <f1> <f2> <f3> <f4>        예) fan-small 10 10 0 0
  fan-main <big>                       예) fan-main 70
  servo-int  <4개 각도>                예) servo-int 0 10 20 30
  servo-ext  <4개 각도>                예) servo-ext 15 25 35 45
  servo-both <내부4> <외부4>           예) servo-both 0 0 0 0  10 20 30 40
  tsv <v1> <v2> <v3> <v4>              예) tsv 1.0 0 -1.2 0.6
  value <JSON>                         예) value {"peltier_pwm":55,"fan_main_pwm":70}
  power on|off                         예) power off
  status                                현재 상태를 강제로 출력
  dump                                  raw_cmd/state/softkill 덤프
  sleep <sec>                           잠깐 대기
  q|quit|exit                           종료
=================================================
""".strip())

# ===== on_mqtt 에 투입할 토픽 문자열 (endswith 로만 판별하므로 내용은 임의) =====
TOP_VALUE = f"control/hvac/{getattr(app, 'HVAC_ID', 1)}/value"
TOP_TSV   = f"control/hvac/{getattr(app, 'HVAC_ID', 1)}/tsv"
TOP_PWR   = f"control/hvac/{getattr(app, 'HVAC_ID', 1)}/power_server"

# ===== 한 줄 처리 =====
def handle(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    parts = s.split()
    cmd, args = parts[0].lower(), parts[1:]

    try:
        if cmd in ("q", "quit", "exit"):
            return False
        if cmd in ("h", "help", "?"):
            _help(); return True

        # ---- /value 계열 ----
        if cmd == "peltier" and len(args) == 1:
            duty = int(float(args[0]))
            app.on_mqtt(TOP_VALUE, {"peltier_pwm": duty})
            return True

        if cmd == "fans" and len(args) == 5:
            f1, f2, f3, f4, big = _ints(args)
            app.on_mqtt(TOP_VALUE, {"small_fan_pwm": [f1, f2, f3, f4], "fan_main_pwm": big})
            return True

        if cmd == "fan-small" and len(args) == 4:
            f1, f2, f3, f4 = _ints(args)
            app.on_mqtt(TOP_VALUE, {"small_fan_pwm": [f1, f2, f3, f4]})
            return True

        if cmd == "fan-main" and len(args) == 1:
            big = int(float(args[0]))
            app.on_mqtt(TOP_VALUE, {"fan_main_pwm": big})
            return True

        if cmd == "servo-int" and len(args) == 4:
            app.on_mqtt(TOP_VALUE, {"internal_servo": _floats(args)})
            return True

        if cmd == "servo-ext" and len(args) == 4:
            app.on_mqtt(TOP_VALUE, {"external_servo": _floats(args)})
            return True

        if cmd == "servo-both" and len(args) == 8:
            i = _floats(args[:4]); e = _floats(args[4:])
            app.on_mqtt(TOP_VALUE, {"internal_servo": i, "external_servo": e})
            return True

        if cmd == "value":
            # 공백 뒤 JSON 전체를 파싱
            js = s[len("value"):].strip()
            payload = json.loads(js) if js else {}
            if not isinstance(payload, dict):
                raise ValueError('JSON 오브젝트를 입력하세요. 예) value {"peltier_pwm":55}')
            app.on_mqtt(TOP_VALUE, payload)
            return True

        # ---- /tsv → LED ----
        if cmd == "tsv" and len(args) == 4:
            app.on_mqtt(TOP_TSV, {"tsv": _floats(args)})
            return True

        # ---- /power_server (softkill) ----
        if cmd == "power" and len(args) == 1:
            v = args[0].lower()
            if v not in ("on","off","1","0","true","false"):
                raise ValueError("power on|off 로 입력하세요.")
            app.on_mqtt(TOP_PWR, {"power": v})
            return True

        # ---- 상태/디버그 ----
        if cmd == "status":
            app._publish_status()  # DummyPublisher가 콘솔로 출력
            return True

        if cmd == "dump":
            print("raw_cmd :", getattr(app, "raw_cmd", {}))
            print("state   :", getattr(app, "state", {}))
            print("softkill:", getattr(app, "_softkill_killed", False))
            return True

        if cmd == "sleep" and len(args) == 1:
            sec = float(args[0]); print(f"(sleep {sec}s)"); time.sleep(sec); return True

        print("⚠ 알 수 없는 명령이거나 인자 개수가 맞지 않습니다. help 로 도움말을 보세요.")
    except Exception as e:
        print(f"❗ 오류: {e}")
    return True

# ===== 종료 핸들러 =====
def _on_signal(signum, frame):
    print("\n🔚 종료 신호 수신. 정리 중...")

# ===== main =====
def main():
    # 1) main 드라이버 초기화(+서보 홈) & Dummy MQTT 주입
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    print("⚙️  드라이버 초기화(main._driver_init) ...")
    app._driver_init()             # main과 동일: 펠티어/서보/아두이노 초기화 + 서보 home_all()
    app.mqttc = DummyPublisher()   # 상태 발행을 콘솔로 보기 위함

    print("✅ 준비 완료. 하드웨어는 main.py와 동일 로직으로 제어됩니다.")
    _help()

    # 2) REPL
    try:
        while True:
            line = input("> ")
            if not handle(line):
                break
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        # main.py 와 동일한 종료 루틴
        try:
            if getattr(app, "softkill", None):
                app.softkill.set_state(True, emit=True, reason="shutdown")
        except Exception:
            pass
        try:
            app._driver_safe_off()
            try:
                if hasattr(app, "_ardu_send_leds"):
                    app._ardu_send_leds(["OFF","OFF","OFF","OFF"])  # LED를 OFF로 유지
            except Exception:
                pass
        finally:
            print("✅ Clean exit.")

if __name__ == "__main__":
    main()
