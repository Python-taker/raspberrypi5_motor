#!/usr/bin/env python3
"""
multi_fan_led_cli.py
────────────────────────────────────────────────────────
- Raspberry Pi → Arduino USB 시리얼로
  1) 5개 팬 듀티(0~100) 제어
  2) 4개 LED 색상(R/G/B/W/OFF) 제어  ← 입력은 정상 의미로 받되 B↔G를 내부에서 보정 전송
  3) 원샷(SETALL) 및 상태 조회(GET?)
- 프로토콜:
    SETF f1 f2 f3 f4 big        → ACK:SETF:...
    SETL c1 c2 c3 c4            → ACK:SETL:...
    SETALL f1 f2 f3 f4 big c1 c2 c3 c4
                                → ACK:SETALL:...
    GET?                        → DATA:STATE:...

⚠ 준비:
    pip install pyserial
    (권한) sudo usermod -aG dialout $USER  (재로그인 필요)
"""

import sys, time, glob, serial
from typing import Optional

# =====================================================
# 1️⃣ 포트 탐색
# =====================================================
def auto_find_port() -> Optional[str]:
    c = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return c[0] if c else None

# =====================================================
# 2️⃣ 시리얼 오픈
# =====================================================
def open_serial(port: str, baud: int = 115200, timeout: float = 2.0) -> serial.Serial:
    ser = serial.Serial(port=port, baudrate=baud, timeout=timeout)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser

# =====================================================
# 3️⃣ READY 대기
# =====================================================
def wait_ready(ser: serial.Serial, wait_sec: float = 2.0) -> None:
    t_end = time.time() + wait_sec
    while time.time() < t_end:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            # print("[ARDUINO]", line)
            break

# =====================================================
# 4️⃣ 송수신 헬퍼 (버퍼 드레인 + prefix 매칭)
# =====================================================
def txrx_expect_prefix(ser: serial.Serial, cmd: str, expect_prefix: str,
                       retries: int = 2, wait_each: float = 0.02) -> str:
    for _ in range(retries + 1):
        ser.reset_input_buffer()
        ser.write((cmd.strip() + "\n").encode("ascii"))
        ser.flush()
        time.sleep(wait_each)

        t_end = time.time() + (ser.timeout or 2.0)
        while time.time() < t_end:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue
            # print("[DBG]", line)
            if line.startswith(expect_prefix):
                return line
        time.sleep(0.05)
    raise RuntimeError(f"No expected response for '{cmd}' (prefix='{expect_prefix}')")

# =====================================================
# 5️⃣ 입력 유틸
# =====================================================
def parse_five_ints(s: str):
    try:
        vals = list(map(int, s.split()))
        if len(vals) != 5: return None
        if any(v < 0 or v > 100 for v in vals): return None
        return vals
    except:
        return None

def parse_four_colors(s: str):
    allowed = {"R","G","B","W","OFF"}
    toks = s.upper().split()
    if len(toks) != 4: return None
    if any(t not in allowed for t in toks): return None
    return toks

def swap_b_g(colors):
    """사용자 의미와 실제 동작이 뒤바뀐 경우를 보정: B↔G 교환"""
    swapped = []
    for c in colors:
        if c == "B": swapped.append("G")
        elif c == "G": swapped.append("B")
        else: swapped.append(c)
    return swapped

# =====================================================
# 6️⃣ CLI
# =====================================================
def main():
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
        wait_ready(ser, 2.0)
        print(f"✅ 연결됨: {port}")
        print("명령 예시:")
        print("  fans   → 5개 듀티 입력 (예: 100 80 70 50 100)")
        print("  leds   → 4개 색상 입력 (예: R B G W | OFF 허용)  # 내부적으로 B↔G 보정 전송")
        print("  all    → 팬 + LED 한 번에 입력 후 SETALL")
        print("  get    → 현재 상태 조회")
        print("  quit   → 종료")

        while True:
            try:
                cmd = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n종료합니다.")
                break

            if cmd in ("q","quit","exit"):
                print("종료합니다.")
                break

            elif cmd == "fans":
                s = input("f1 f2 f3 f4 big (0~100): ").strip()
                vals = parse_five_ints(s)
                if not vals:
                    print("입력 오류: 예) 100 80 70 50 100 (모두 0~100)")
                    continue
                resp = txrx_expect_prefix(ser, f"SETF {' '.join(map(str, vals))}", "ACK:SETF:")
                print(resp)

            elif cmd == "leds":
                s = input("LED1 LED2 LED3 LED4 색상 (R/G/B/W/OFF): ").strip()
                cols = parse_four_colors(s)
                if not cols:
                    print("입력 오류: 예) R B G W  (또는 OFF 허용)")
                    continue
                cols_out = swap_b_g(cols)  # ★ B↔G 보정
                resp = txrx_expect_prefix(ser, f"SETL {' '.join(cols_out)}", "ACK:SETL:")
                print(resp)

            elif cmd == "all":
                sF = input("팬 5개 (0~100) → 예: 100 80 70 50 100 : ").strip()
                vals = parse_five_ints(sF)
                if not vals:
                    print("입력 오류(팬): 5개 정수 0~100")
                    continue
                sL = input("LED 4개 색상 (R/G/B/W/OFF) → 예: R B G W : ").strip()
                cols = parse_four_colors(sL)
                if not cols:
                    print("입력 오류(LED): 4개 색상 R/G/B/W/OFF")
                    continue
                cols_out = swap_b_g(cols)  # ★ B↔G 보정
                cmdline = f"SETALL {' '.join(map(str, vals))} {' '.join(cols_out)}"
                resp = txrx_expect_prefix(ser, cmdline, "ACK:SETALL:")
                print(resp)

            elif cmd == "get":
                resp = txrx_expect_prefix(ser, "GET?", "DATA:STATE:")
                print(resp)

            elif cmd == "":
                continue

            else:
                print("알 수 없는 명령입니다. fans / leds / all / get / quit")

    finally:
        try: ser.close()
        except: pass

if __name__ == "__main__":
    main()
