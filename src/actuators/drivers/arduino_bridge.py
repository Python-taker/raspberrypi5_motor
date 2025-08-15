#!/usr/bin/env python3
"""
arduino_bridge.py
────────────────────────────────────────────────────────
- Raspberry Pi ↔ Arduino (USB 시리얼) 브리지
- 기능:
  1) 팬 5개 듀티 제어 (SETF)
  2) LED 4개 색상 제어 (SETL)  ※ 사용자 입력은 R/G/B/W/OFF, 내부 전송 시 B↔G 보정 가능
  3) 팬+LED 원샷 (SETALL)
  4) 상태 조회 (GET?)

프로토콜(아두이노 측과 합의):
  SETF f1 f2 f3 f4 big            → ACK:SETF:...
  SETL c1 c2 c3 c4                → ACK:SETL:...
  SETALL f1 f2 f3 f4 big c1 c2 c3 c4
                                  → ACK:SETALL:...
  GET?                            → DATA:STATE:ch1,ch2,ch3,ch4,big

!! 주의 사항 !!
1) pyserial 필요: pip install pyserial
2) 권한: dialout 그룹 추가 후 재로그인: sudo usermod -aG dialout $USER
3) 기본 포트 자동탐색(/dev/ttyACM*, /dev/ttyUSB*). 필요 시 명시적으로 포트 지정.

📌 호출 관계
- main.py에서 ArduinoFanLedBridge 클래스를 import하여 사용
- 본 파일 단독 실행 시 간단한 CLI 제공(fans/leds/all/get)
"""

import glob
import sys
import time
import serial
import threading
from typing import List, Optional, Sequence

# =====================================================
# 1️⃣ 유틸: 포트 자동 탐색
# =====================================================
def auto_find_port() -> Optional[str]:
    """
    /dev/ttyACM*, /dev/ttyUSB* 중 첫 번째를 반환.
    Returns:
        str | None
    """
    cands = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


# =====================================================
# 2️⃣ 브리지 클래스
# =====================================================
class ArduinoFanLedBridge:
    """
    팬 5개 + LED 4개 제어를 위한 시리얼 브리지.

    Args:
        port (str | None): 시리얼 포트. None이면 auto_find_port() 시도
        baud (int): 보드레이트 (기본 115200)
        timeout (float): 읽기 타임아웃 (초)
        swap_bg (bool): True면 LED 전송 시 B↔G 보정

    Methods:
        connect() / close()
        set_fans([f1,f2,f3,f4,big]) -> str(ACK)
        set_leds([c1,c2,c3,c4]) -> str(ACK)   # c∈{R,G,B,W,OFF}
        set_all(fans, leds) -> str(ACK)
        get_state() -> dict  # {"ch1":int,...,"big":int,"raw":"..."}
    """

    # =================================================
    # 2-1️⃣ 생성자
    # =================================================
    def __init__(self,
                 port: Optional[str] = None,
                 baud: int = 115200,
                 timeout: float = 2.0,
                 swap_bg: bool = True) -> None:
        self.port = port or auto_find_port()
        self.baud = baud
        self.timeout = timeout
        self.swap_bg = swap_bg
        self.ser: Optional[serial.Serial] = None
        self._lock = threading.Lock()

    # =================================================
    # 2-2️⃣ 연결/종료
    # =================================================
    def connect(self) -> None:
        """
        시리얼 오픈 및 READY 대기(최대 timeout 내).
        Raises:
            RuntimeError: 포트 미발견/오픈 실패/READY 미수신
        """
        if not self.port:
            raise RuntimeError("Arduino 포트를 찾지 못했습니다. (/dev/ttyACM* 또는 /dev/ttyUSB*)")

        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        except Exception as e:
            raise RuntimeError(f"포트 오픈 실패: {e}")

        # 초기 버퍼 정리
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        # READY 대기
        t_end = time.time() + self.timeout
        while time.time() < t_end:
            line = self._readline()
            if line:
                # print(f"[READY?] {line}")
                break
        # READY 미수신이어도 이후 프로토콜 동작에 문제 없으면 패스.
        # 필요시 위 조건 강화 가능.

    def close(self) -> None:
        """시리얼 닫기(있으면)."""
        with self._lock:
            try:
                if self.ser:
                    self.ser.close()
            finally:
                self.ser = None

    # =================================================
    # 2-3️⃣ 공개 API
    # =================================================
    def set_fans(self, fans: Sequence[int]) -> str:
        """
        팬 5개 듀티 적용.
        Args:
            fans: [f1,f2,f3,f4,big], 각 0~100
        Returns:
            str: "ACK:SETF:..." 원문
        """
        self._require_conn()
        vals = self._validate_fans(fans)
        cmd = f"SETF {' '.join(map(str, vals))}"
        return self._txrx_expect_prefix(cmd, "ACK:SETF:")

    def set_leds(self, colors: Sequence[str]) -> str:
        """
        LED 4개 색상 적용. 입력은 정상 의미(R/G/B/W/OFF)로 받고,
        self.swap_bg=True면 전송 직전에 B↔G 보정.
        Args:
            colors: [c1,c2,c3,c4], 각 in {"R","G","B","W","OFF"} (대소문자 무시)
        Returns:
            str: "ACK:SETL:..." 원문
        """
        self._require_conn()
        cols = self._validate_leds(colors)
        cols_out = self._swap_b_g(cols) if self.swap_bg else cols
        cmd = f"SETL {' '.join(cols_out)}"
        return self._txrx_expect_prefix(cmd, "ACK:SETL:")

    def set_all(self, fans: Sequence[int], colors: Sequence[str]) -> str:
        """
        팬+LED 동시 적용.
        Returns:
            str: "ACK:SETALL:..." 원문
        """
        self._require_conn()
        vals = self._validate_fans(fans)
        cols = self._validate_leds(colors)
        cols_out = self._swap_b_g(cols) if self.swap_bg else cols
        cmd = f"SETALL {' '.join(map(str, vals))} {' '.join(cols_out)}"
        return self._txrx_expect_prefix(cmd, "ACK:SETALL:")

    def get_state(self) -> dict:
        """
        아두이노 상태 조회.
        Returns:
            dict: {"ch1":int,"ch2":int,"ch3":int,"ch4":int,"big":int,"raw":str}
        Raises:
            RuntimeError: 응답 파싱 실패
        """
        self._require_conn()
        raw = self._txrx_expect_prefix("GET?", "DATA:STATE:")
        try:
            payload = raw.split("DATA:STATE:")[1].strip()
            nums = list(map(int, payload.split(",")))
            if len(nums) != 5:
                raise ValueError("expected 5 ints")
            return {"ch1": nums[0], "ch2": nums[1], "ch3": nums[2], "ch4": nums[3], "big": nums[4], "raw": raw}
        except Exception as e:
            raise RuntimeError(f"상태 파싱 실패: {e} (raw='{raw}')")

    # =================================================
    # 2-4️⃣ MQTT value payload 적용(편의)
    # =================================================
    def apply_from_value_payload(self, value: dict, led_colors: Optional[Sequence[str]] = None) -> str:
        """
        팀 규약의 'control/.../value' JSON에서 팬 값을 추출하여 적용.
        value 예:
            {
              "peltier_pwm": 5,
              "internal_servo": [45,45,44,6],
              "external_servo": [50,70,80,12],
              "small_fan_pwm": [5,80,0,2],
              "large_fan_pwm": 90
            }
        Args:
            value: dict
            led_colors: 필요 시 동시에 보낼 LED 배열 ["R","G","B","W"/"OFF"] x4
        Returns:
            str: ACK 원문 (SETF 또는 SETALL)
        """
        small = value.get("small_fan_pwm", [0, 0, 0, 0])
        big = value.get("large_fan_pwm", 0)
        fans = list(map(int, small))[:4] + [int(big)]
        if led_colors is None:
            return self.set_fans(fans)
        else:
            return self.set_all(fans, led_colors)

    # =================================================
    # 2-5️⃣ 내부 헬퍼
    # =================================================
    def _require_conn(self) -> None:
        if not self.ser:
            raise RuntimeError("시리얼 연결이 없습니다. connect() 먼저 호출하세요.")

    def _readline(self) -> str:
        """타임아웃까지 한 줄 읽어 개행 제거."""
        assert self.ser
        return self.ser.readline().decode(errors="ignore").strip()

    def _txrx_expect_prefix(self, cmd: str, expect_prefix: str, retries: int = 2, wait_each: float = 0.02) -> str:
        """
        버퍼 드레인 후 cmd 전송 → expect_prefix로 시작하는 응답 수신.
        """
        assert self.ser
        with self._lock:
            for _ in range(retries + 1):
                # 이전 찌꺼기 제거
                self.ser.reset_input_buffer()
                # 전송
                self.ser.write((cmd.strip() + "\n").encode("ascii"))
                self.ser.flush()
                time.sleep(wait_each)
                # 수신 대기
                t_end = time.time() + (self.ser.timeout or 2.0)
                while time.time() < t_end:
                    line = self._readline()
                    if not line:
                        continue
                    # print(f"[DBG] {line}")
                    if line.startswith(expect_prefix):
                        return line
                time.sleep(0.05)
        raise RuntimeError(f"'{cmd}' 전송에 대한 '{expect_prefix}' 응답이 없습니다.")

    @staticmethod
    def _validate_fans(fans: Sequence[int]) -> List[int]:
        if not isinstance(fans, (list, tuple)) or len(fans) != 5:
            raise ValueError("fans는 길이 5의 정수 리스트여야 합니다. (f1 f2 f3 f4 big)")
        vals = []
        for v in fans:
            iv = int(v)
            if iv < 0 or iv > 100:
                raise ValueError("팬 듀티는 0~100 범위여야 합니다.")
            vals.append(iv)
        return vals

    @staticmethod
    def _validate_leds(colors: Sequence[str]) -> List[str]:
        allowed = {"R", "G", "B", "W", "OFF"}
        if not isinstance(colors, (list, tuple)) or len(colors) != 4:
            raise ValueError("colors는 길이 4의 리스트여야 합니다. (R/G/B/W/OFF)")
        out = []
        for c in colors:
            token = str(c).upper()
            if token not in allowed:
                raise ValueError(f"허용되지 않은 색상: {c}")
            out.append(token)
        return out

    @staticmethod
    def _swap_b_g(colors: Sequence[str]) -> List[str]:
        """하드웨어 배선 문제로 실제 B/G가 뒤집혀 있을 때 보정"""
        out = []
        for c in colors:
            if c == "B":
                out.append("G")
            elif c == "G":
                out.append("B")
            else:
                out.append(c)
        return out


# =====================================================
# 3️⃣ 단독 실행용 간단 CLI
#    (테스트/디버깅 용. main.py 에서는 클래스만 사용)
# =====================================================
def _parse_five_ints(s: str) -> Optional[List[int]]:
    try:
        vals = list(map(int, s.split()))
        if len(vals) != 5: return None
        if any(v < 0 or v > 100 for v in vals): return None
        return vals
    except Exception:
        return None

def _parse_four_colors(s: str) -> Optional[List[str]]:
    toks = s.upper().split()
    if len(toks) != 4: return None
    allowed = {"R","G","B","W","OFF"}
    if any(t not in allowed for t in toks): return None
    return toks

def main():
    try:
        bridge = ArduinoFanLedBridge(swap_bg=True)  # 하드웨어 B/G 뒤집힘 보정
        bridge.connect()
    except Exception as e:
        print(f"[ERR] 연결 실패: {e}")
        sys.exit(2)

    print(f"✅ 연결됨: {bridge.port}")
    print("명령: fans / leds / all / get / quit")
    print("  fans  → 5개 듀티 (예: 100 80 70 50 100)")
    print("  leds  → 4개 색상 (예: R B G W | OFF 허용)  # 내부 B↔G 보정")
    print("  all   → 팬+LED 동시")
    print("  get   → 상태 조회")

    try:
        while True:
            try:
                cmd = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n종료합니다.")
                break

            if cmd in ("q", "quit", "exit"):
                print("종료합니다.")
                break

            elif cmd == "fans":
                s = input("f1 f2 f3 f4 big (0~100): ").strip()
                vals = _parse_five_ints(s)
                if not vals:
                    print("입력 오류: 예) 100 80 70 50 100 (0~100)")
                    continue
                try:
                    print(bridge.set_fans(vals))
                except Exception as e:
                    print(f"[ERR] {e}")

            elif cmd == "leds":
                s = input("LED1 LED2 LED3 LED4 (R/G/B/W/OFF): ").strip()
                cols = _parse_four_colors(s)
                if not cols:
                    print("입력 오류: 예) R B G W  (또는 OFF)")
                    continue
                try:
                    print(bridge.set_leds(cols))
                except Exception as e:
                    print(f"[ERR] {e}")

            elif cmd == "all":
                sF = input("팬 5개 (0~100): ").strip()
                vals = _parse_five_ints(sF)
                if not vals:
                    print("입력 오류(팬): 5개 정수 0~100")
                    continue
                sL = input("LED 4개 (R/G/B/W/OFF): ").strip()
                cols = _parse_four_colors(sL)
                if not cols:
                    print("입력 오류(LED): 4개 색상")
                    continue
                try:
                    print(bridge.set_all(vals, cols))
                except Exception as e:
                    print(f"[ERR] {e}")

            elif cmd == "get":
                try:
                    st = bridge.get_state()
                    print(st)
                except Exception as e:
                    print(f"[ERR] {e}")

            elif cmd == "":
                continue

            else:
                print("알 수 없는 명령입니다. fans / leds / all / get / quit")

    finally:
        bridge.close()


if __name__ == "__main__":
    main()
