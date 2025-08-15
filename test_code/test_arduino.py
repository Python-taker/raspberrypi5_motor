#!/usr/bin/env python3
"""
led_controller.py
────────────────────────────────────────────────────────
- Raspberry Pi → Arduino USB 시리얼로 LED ON/OFF 명령 전송
- 프로토콜: 개행(\n)으로 끝나는 "ON" / "OFF" 텍스트
- 아두이노 응답: "ACK:ON" / "ACK:OFF" / "ERR:UNKNOWN_CMD"

⚠ 주의 사항
1) pip로 pyserial 설치 필요:  `pip install pyserial`
2) 포트는 보통 /dev/ttyACM0 또는 /dev/ttyUSB0
3) 권한 문제 시: `sudo usermod -aG dialout $USER` 후 재로그인

📌 호출 관계
- 단독 실행 가능 (CLI에서 on/off 전송 테스트)
"""

import sys
import time
import glob
import serial
from typing import Optional

# =====================================================
# 1️⃣ 시리얼 포트 자동 탐색
# /dev/ttyACM* 또는 /dev/ttyUSB* 후보를 스캔합니다.
# =====================================================
def auto_find_port() -> Optional[str]:
    """
    리눅스에서 흔한 아두이노 포트를 자동 탐색합니다.

    Returns:
        str | None: 찾으면 포트 문자열, 못 찾으면 None
    """
    candidates = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return candidates[0] if candidates else None


# =====================================================
# 2️⃣ 시리얼 연결 생성
# 지정 포트로 115200bps, 타임아웃 포함하여 연결합니다.
# =====================================================
def open_serial(port: str, baud: int = 115200, timeout: float = 2.0) -> serial.Serial:
    """
    시리얼 포트를 엽니다.

    Args:
        port (str): /dev/ttyACM0 등
        baud (int): 보드레이트 (기본 115200)
        timeout (float): 읽기 타임아웃(초)

    Returns:
        serial.Serial: 열린 시리얼 객체

    Raises:
        serial.SerialException: 포트 오픈 실패 시
    """
    ser = serial.Serial(port=port, baudrate=baud, timeout=timeout)
    # 초기 버퍼 정리
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


# =====================================================
# 3️⃣ 명령 전송 & 응답 수신 (버퍼 드레인 + 일치 검증)
# =====================================================
def send_command(ser: serial.Serial, cmd: str, retries: int = 2, wait_each: float = 0.02) -> str:
    """
    아두이노로 명령을 전송하고, 'ACK:<CMD>'가 나올 때까지 읽습니다.
    이전에 남아있던 ACK가 섞이지 않도록 전송 직전에 입력버퍼를 비웁니다.

    Args:
        ser (serial.Serial): 열린 시리얼 객체
        cmd (str): "ON" 또는 "OFF"
        retries (int): 재시도 횟수 (총 시도는 retries+1)
        wait_each (float): 전송 후 짧은 대기(초)

    Returns:
        str: 수신한 응답 문자열 (예: "ACK:ON")

    Raises:
        RuntimeError: 재시도 후에도 원하는 ACK를 못 받은 경우
    """
    cmd_norm = cmd.strip().upper()
    if cmd_norm not in ("ON", "OFF"):
        raise ValueError("cmd must be 'ON' or 'OFF'")

    expect = f"ACK:{cmd_norm}"

    for attempt in range(retries + 1):
        # 이전에 남아있던 데이터 제거 (핵심!)
        ser.reset_input_buffer()

        # 전송
        ser.write((cmd_norm + "\n").encode("ascii"))
        ser.flush()
        time.sleep(wait_each)  # 아주 짧게 대기 (아두이노가 처리/응답할 시간)

        # 이번 명령에 대한 ACK가 나올 때까지 타임아웃 내에서 반복 수신
        t_end = time.time() + ser.timeout if ser.timeout else time.time() + 2.0
        while time.time() < t_end:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue
            # 디버그가 필요하면 다음 줄 주석 해제
            # print(f"[DBG recv] {line}")
            if line == expect:
                return line
            # 다른 줄(READY, 이전 ACK 등)은 무시하고 계속 읽음

        # 여기까지 오면 이번 시도에서 원하는 ACK를 못 받음 → 재시도
        # 다음 루프에서 다시 전송
        # (필요시 짧은 대기 추가 가능)
        time.sleep(0.05)

    raise RuntimeError(f"Expected '{expect}' but did not receive it.")


# =====================================================
# 4️⃣ 초기 핸드셰이크(선택)
# 아두이노가 "READY"를 보낼 수 있으니 잠깐 대기합니다.
# =====================================================
def wait_ready(ser: serial.Serial, wait_sec: float = 2.0) -> None:
    """
    아두이노 리셋 직후 "READY" 등을 잠시 대기.

    Args:
        ser (serial.Serial): 열린 시리얼 객체
        wait_sec (float): 대기 시간(초)
    """
    t_end = time.time() + wait_sec
    while time.time() < t_end:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            # print(f"[ARDUINO] {line}")  # 디버그가 필요하면 주석 해제
            break


# =====================================================
# 5️⃣ 사용자 입력 루프
# 실행 후 터미널에서 on/off/quit 입력
# =====================================================
def main():
    """
    사용자 입력(ON/OFF)을 받아 LED를 제어합니다.
    """
    port = auto_find_port()
    if not port:
        print("ERROR: 아두이노 포트를 찾지 못했습니다. (/dev/ttyACM* 또는 /dev/ttyUSB*)")
        sys.exit(2)

    try:
        ser = open_serial(port)
    except Exception as e:
        print(f"ERROR: 포트 오픈 실패: {e}")
        sys.exit(3)

    try:
        wait_ready(ser, wait_sec=2.0)  # 선택적

        print(f"✅ 연결됨: {port}")
        print("명령을 입력하세요: on / off / quit")

        while True:
            try:
                cmd = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n프로그램을 종료합니다.")
                break

            if cmd in ("q", "quit", "exit"):
                print("프로그램을 종료합니다.")
                break

            if cmd not in ("on", "off"):
                print("⚠ 잘못된 입력입니다. on/off/quit 중에서 입력하세요.")
                continue

            try:
                resp = send_command(ser, cmd)
                print(f"아두이노 응답: {resp}")
                if cmd == "on" and resp != "ACK:ON":
                    print("⚠ 경고: 예상과 다른 응답")
                if cmd == "off" and resp != "ACK:OFF":
                    print("⚠ 경고: 예상과 다른 응답")
            except Exception as e:
                print(f"ERROR: 전송/수신 중 오류: {e}")

    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    # =====================================================
    # ▶ CLI 테스트 진입점
    # =====================================================
    main()
