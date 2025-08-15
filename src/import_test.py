#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_path_test.py
────────────────────────────────────────────────────────
- 패키지/모듈 임포트가 정상 동작하는지 빠르게 점검하는 스모크 테스트
- 하드웨어 의존 모듈(bts7960_peltier_pwm 등)은 기본 건너뜀
  (환경변수 IMPORT_HARDWARE=1 로 켜면 시도)

실행 예:
  python src/import_path_test.py
  # 또는 프로젝트 루트에서
  python -m src.import_path_test
"""

from __future__ import annotations
import os
import sys
from pathlib import Path
import importlib
import traceback

# 0) 경로 보정: src 와 그 상위(프로젝트 루트) 모두 sys.path 에 추가
SRC_DIR = Path(__file__).resolve().parent           # .../src
ROOT_DIR = SRC_DIR.parent                           # 프로젝트 루트
for p in (str(SRC_DIR), str(ROOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

print("=== PYTHON ENV ===")
print("python:", sys.version.split()[0])
print("cwd   :", Path.cwd())
print("file  :", Path(__file__).resolve())
print("sys.path[0..3]:", sys.path[:3])
print()

# 1) 패키지 __init__.py 존재 여부 안내(권장)
def _exists(rel):
    return (SRC_DIR / rel).exists()

checks = [
    ("actuators/__init__.py",      _exists("actuators/__init__.py")),
    ("actuators/services/__init__.py", _exists("actuators/services/__init__.py")),
    ("actuators/drivers/__init__.py",  _exists("actuators/drivers/__init__.py")),
]
print("=== PACKAGE INIT CHECKS ===")
for name, ok in checks:
    print(f"{name:<35} : {'OK' if ok else 'MISSING (권장: 빈 파일 추가)'}")
print()

# 2) 임포트 테스트 대상
HARDWARE = os.getenv("IMPORT_HARDWARE", "0") == "1"

targets = [
    # services
    ("actuators.services.peltier",              "PeltierService, MIN_ON_DUTY_DEFAULT"),
    ("actuators.services.servo",                "ServoService"),
    ("actuators.services.leds",                 None),  # 있을 수도/없을 수도
    ("actuators.services.fans",                 None),  # 있을 수도/없을 수도

    # drivers (하드웨어 없는 것부터)
    ("actuators.drivers.pca9685_servo_module",  "ServoAPI"),
    ("actuators.drivers.arduino_bridge",          "ArduinoFanLedBridge"),  # 오타 탐지용 의도적 케이스
    ("actuators.drivers.arduino_bridge",        "ArduinoFanLedBridge"),
]

if HARDWARE:
    targets.append(("actuators.drivers.bts7960_peltier_pwm", None))
else:
    print("※ 하드웨어 의존 모듈 임포트는 기본 건너뜁니다. (IMPORT_HARDWARE=1 로 활성화)\n")

# 3) 임포트 실행
def try_import(mod_name: str, members: str | None):
    print(f"[IMPORT] {mod_name}  ({'+' + members if members else 'module only'})")
    try:
        mod = importlib.import_module(mod_name)
        print("  └─ OK: module imported.")
        if members:
            for m in [s.strip() for s in members.split(",")]:
                try:
                    getattr(mod, m)
                    print(f"     └─ attr OK: {m}")
                except Exception as e:
                    print(f"     └─ attr FAIL: {m}  -> {type(e).__name__}: {e}")
        return True
    except Exception as e:
        print(f"  └─ FAIL: {type(e).__name__}: {e}")
        # 힌트: 흔한 원인 메시지
        if "No module named" in str(e):
            print("     ↳ 힌트: sys.path 에 src 가 들어갔는지, 패키지 경로가 정확한지 확인하세요.")
        if "GPIO" in str(e) or "No such device" in str(e):
            print("     ↳ 하드웨어 모듈은 라즈베리파이/GPIO 환경에서만 임포트가 성공할 수 있어요.")
        # 상세 스택은 옵션
        # traceback.print_exc()
        return False

print("=== IMPORT SMOKE TEST ===")
ok_all = True
for mod_name, members in targets:
    ok = try_import(mod_name, members)
    ok_all = ok_all and ok
    print()

print("=== SUMMARY ===")
print("RESULT:", "PASS" if ok_all else "SOME FAILURES (위 로그 참고)")
