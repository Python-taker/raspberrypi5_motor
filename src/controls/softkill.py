#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
controls/softkill.py
- 토글형 푸시버튼 + 상태 LED(RED/GREEN)
- .env 자동 로드, 공통 캐소드/애노드 지원
- '눌림' 시에만 1회 토글(릴리즈/바운스 무시)
- 원격 제어 연동: apply_remote(power) 로 상태/LED 동기화
- 디버깅 토글: SOFTKILL_DEBUG(환경변수) 또는 생성자 debug=True 일 때만 로그 출력
"""

from __future__ import annotations
import os, sys, time, argparse
from typing import Callable, Optional

# ── .env 로드 ─────────────────────────────────────────
try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass

# ── GPIO(옵셔널) ─────────────────────────────────────
try:
    import RPi.GPIO as GPIO  # type: ignore
    _GPIO_OK = True
except Exception:
    _GPIO_OK = False

# ── 유틸 ─────────────────────────────────────────────
def _coerce_bool(x) -> bool:
    if isinstance(x, bool): return x
    return str(x).strip().lower() in ("1", "true", "y", "yes", "on", "t")

def _getenv_int(name: str, default: int) -> int:
    try: return int(os.getenv(name, str(default)))
    except Exception: return default

def _getenv_bool(name: str, default: bool) -> bool:
    return _coerce_bool(os.getenv(name, "1" if default else "0"))

def _lvl_name(v: int) -> str:
    return "HIGH" if v else "LOW"

def _func_name(pin: int) -> str:
    if not _GPIO_OK: return "N/A"
    try:
        f = GPIO.gpio_function(pin)
        if   f == GPIO.IN:   return "IN"
        elif f == GPIO.OUT:  return "OUT"
        elif f == GPIO.SPI:  return "SPI"
        elif f == GPIO.I2C:  return "I2C"
        elif f == GPIO.HARD_PWM: return "HARD_PWM"
        elif f == GPIO.SERIAL:   return "SERIAL"
        else: return str(f)
    except Exception:
        return "?"

def _coerce_on(v) -> bool:
    """원격 power 값을 '켜짐 여부'로 보수 없이 해석."""
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1","true","t","yes","y","on"):  return True
    if s in ("0","false","f","no","n","off"): return False
    try:
        return bool(int(float(s)))
    except Exception:
        return bool(v)


class SoftKillController:
    """
    killed=True  → RED ON,  GREEN OFF  (OFF 상태)
    killed=False → RED OFF, GREEN ON   (ON 상태)
    """
    def __init__(
        self,
        *,
        button_pin: int = -1,
        active_low: bool = True,
        debounce_ms: int = 120,
        led_red_pin: int = -1,
        led_green_pin: int = -1,
        led_active_high: bool = True,
        on_change: Optional[Callable[[bool, str], None]] = None,
        initial_killed: bool = False,
        debug: bool = False,  # ← 추가: 기본 False (로그 비활성)
    ) -> None:
        self.button_pin = int(button_pin)
        self.active_low = bool(active_low)
        self.debounce_ms = int(debounce_ms)
        self._debounce_s = max(0.0, self.debounce_ms / 1000.0)

        self.led_red_pin = int(led_red_pin)
        self.led_green_pin = int(led_green_pin)
        self.led_active_high = bool(led_active_high)

        self.on_change = on_change
        self._killed = bool(initial_killed)   # 기본 False → 부팅 시 GREEN

        # 디버그 토글(환경변수와 OR)
        self.debug = bool(debug) or _getenv_bool("SOFTKILL_DEBUG", False)

        # 버튼 레벨 정의
        if _GPIO_OK:
            self._pressed_level  = GPIO.LOW if self.active_low else GPIO.HIGH
            self._released_level = GPIO.HIGH if self.active_low else GPIO.LOW
        else:
            self._pressed_level, self._released_level = (0, 1) if self.active_low else (1, 0)
        self._last_toggle_t  = 0.0

        if _GPIO_OK:
            try:
                GPIO.setmode(GPIO.BCM)
                self._setup_leds()
                self._setup_button()
            except Exception as e:
                self._log(f"⚠ GPIO 초기화 실패: {e}")
        else:
            self._log("⚠ RPi.GPIO 미탑재 → NOOP 모드")

        # 초기 LED 반영(기본: GREEN ON)
        self._apply_leds(self._killed, verbose=True)
        if self.debug:
            self._dbg_dump(header="INITIAL")

    # ── logging helper ────────────────────────────────
    def _log(self, *args, **kwargs) -> None:
        if self.debug:
            print(*args, **kwargs)

    # ── GPIO setup ────────────────────────────────────
    def _setup_leds(self) -> None:
        if self.led_red_pin >= 0:
            GPIO.setup(self.led_red_pin, GPIO.OUT, initial=self._gpio_level(False))
        if self.led_green_pin >= 0:
            GPIO.setup(self.led_green_pin, GPIO.OUT, initial=self._gpio_level(False))
        self._blink_test()

    def _setup_button(self) -> None:
        if self.button_pin < 0:
            self._log("ℹ 버튼 핀 미설정 → 버튼 토글 비활성화")
            return
        pud = GPIO.PUD_UP if self.active_low else GPIO.PUD_DOWN
        GPIO.setup(self.button_pin, GPIO.IN, pull_up_down=pud)
        # BOTH로 감지하되, 콜백에서 '눌림' 레벨만 처리
        GPIO.add_event_detect(self.button_pin, GPIO.BOTH, callback=self._on_button_edge, bouncetime=self.debounce_ms)
        self._log(f"✅ Button BCM{self.button_pin} | active_low={self.active_low} "
                  f"(pressed={self._pressed_level==0 and 'LOW' or 'HIGH'}), debounce={self.debounce_ms}ms")

    # ── LED helpers ───────────────────────────────────
    def _gpio_level(self, on: bool) -> int:
        # ACTIVE_HIGH=True → on=HIGH, False=LOW
        # ACTIVE_HIGH=False(공통 캐소드) → on=LOW, False=HIGH
        if not self.led_active_high:
            on = not on
        return GPIO.HIGH if on else GPIO.LOW

    def _apply_leds(self, killed: bool, *, verbose: bool = False) -> None:
        if not _GPIO_OK:
            if verbose and self.debug:
                self._log(f"[LED] (NOOP) killed={killed}")
            return
        # 쓰기
        if self.led_red_pin >= 0:
            GPIO.output(self.led_red_pin, self._gpio_level(killed))
        if self.led_green_pin >= 0:
            GPIO.output(self.led_green_pin, self._gpio_level(not killed))
        if verbose and self.debug:
            # 읽어서 레벨까지 표시
            red_lvl   = GPIO.input(self.led_red_pin)   if self.led_red_pin   >= 0 else -1
            green_lvl = GPIO.input(self.led_green_pin) if self.led_green_pin >= 0 else -1
            self._log(f"[LED] {'KILLED/OFF (RED ON)' if killed else 'ON (GREEN ON)'} "
                      f"(active_high={self.led_active_high})")
            if self.led_red_pin >= 0:
                self._log(f"     RED   → pin BCM{self.led_red_pin} level={_lvl_name(red_lvl)} "
                          f"(logical {'ON' if killed else 'OFF'})")
            if self.led_green_pin >= 0:
                self._log(f"     GREEN → pin BCM{self.led_green_pin} level={_lvl_name(green_lvl)} "
                          f"(logical {'ON' if not killed else 'OFF'})")

    def _blink_test(self) -> None:
        if not _GPIO_OK: return
        try:
            if self.led_red_pin >= 0:
                GPIO.output(self.led_red_pin, self._gpio_level(True));  time.sleep(0.12)
                GPIO.output(self.led_red_pin, self._gpio_level(False))
            if self.led_green_pin >= 0:
                GPIO.output(self.led_green_pin, self._gpio_level(True)); time.sleep(0.12)
                GPIO.output(self.led_green_pin, self._gpio_level(False))
        except Exception as e:
            self._log(f"⚠ LED 자가진단 실패: {e}")

    # ── Debug dump ────────────────────────────────────
    def _dbg_dump(self, header: str = "STATUS") -> None:
        if not self.debug:
            return
        print(f"\n[DBG] ===== {header} =====")
        print(f"      killed={self._killed}  (GREEN when False, RED when True)")
        print(f"      LED_ACTIVE_HIGH={self.led_active_high}  (공통 캐소드라면 False)")
        print(f"      RED pin   = BCM{self.led_red_pin} (mode={_func_name(self.led_red_pin)})")
        print(f"      GREEN pin = BCM{self.led_green_pin} (mode={_func_name(self.led_green_pin)})")
        if _GPIO_OK:
            if self.led_red_pin >= 0:
                rl = GPIO.input(self.led_red_pin)
                print(f"      READ RED   level={_lvl_name(rl)}  → 물리 {('ON' if (rl==GPIO.HIGH if self.led_active_high else rl==GPIO.LOW) else 'OFF')}")
            if self.led_green_pin >= 0:
                gl = GPIO.input(self.led_green_pin)
                print(f"      READ GREEN level={_lvl_name(gl)}  → 물리 {('ON' if (gl==GPIO.HIGH if self.led_active_high else gl==GPIO.LOW) else 'OFF')}")
            if self.button_pin >= 0:
                bl = GPIO.input(self.button_pin)
                print(f"      BUTTON pin = BCM{self.button_pin} level={_lvl_name(bl)} (pressed_level={_lvl_name(self._pressed_level)})")
        print("[DBG] =====================\n")

    # ── State API ─────────────────────────────────────
    @property
    def is_killed(self) -> bool:
        return self._killed

    def set_state(self, killed: bool, *, reason: str = "", emit: bool = False) -> None:
        """외부(서버/MQTT 등)에서 상태를 지정할 때 사용. emit=True면 콜백도 호출"""
        new_state = bool(killed)
        if new_state == self._killed:
            # 이미 같은 상태여도 LED 재적용(미스매치 보정)
            self._apply_leds(new_state, verbose=True)
            return
        self._killed = new_state
        self._apply_leds(self._killed, verbose=True)
        if emit and self.on_change:
            self.on_change(self._killed, reason or "external")

    # ★ 원격 제어 진입점
    def apply_remote(self, power) -> None:
        """
        웹/MQTT 등 원격 명령을 소프트킬 상태로 반영.
        power: "on"/"off"/1/0/true/false ...
        - 내부 상태(_killed) 업데이트
        - LED를 강제로 재적용(이미 같은 상태여도 보정)
        - on_change 콜백 호출(reason='remote')
        """
        desired_on = _coerce_on(power)
        desired_killed = not desired_on
        if desired_killed != self._killed:
            self._killed = desired_killed
            self._apply_leds(self._killed, verbose=True)
            if self.on_change:
                self.on_change(self._killed, "remote")
        else:
            # 상태는 같지만 LED가 어긋났을 수 있으니 재적용
            self._apply_leds(self._killed, verbose=True)

    def toggle(self, *, reason: str = "button") -> None:
        self._killed = not self._killed
        self._apply_leds(self._killed, verbose=True)
        if self.on_change:
            self.on_change(self._killed, reason)

    # ── GPIO event ────────────────────────────────────
    def _on_button_edge(self, channel: int) -> None:
        if not _GPIO_OK or self.button_pin < 0:
            return
        now = time.monotonic()
        if (now - self._last_toggle_t) < self._debounce_s:
            return
        level = GPIO.input(self.button_pin)
        if level != self._pressed_level:
            return
        self._last_toggle_t = now
        self.toggle(reason="button")
        if self.debug:
            self._dbg_dump(header="AFTER BUTTON")

    # ── Helpers for CLI ───────────────────────────────
    def swap_colors(self) -> None:
        """RED/GREEN 핀 매핑을 스왑 (디버그용)"""
        self.led_red_pin, self.led_green_pin = self.led_green_pin, self.led_red_pin
        self._log(f"[DBG] Swap mapping → RED=BCM{self.led_red_pin}, GREEN=BCM{self.led_green_pin}")
        self._apply_leds(self._killed, verbose=True)
        if self.debug:
            self._dbg_dump(header="AFTER SWAP")

    # ── Cleanup ───────────────────────────────────────
    def cleanup(self) -> None:
        if _GPIO_OK:
            try:
                if self.led_red_pin >= 0:
                    GPIO.output(self.led_red_pin, self._gpio_level(False))
                if self.led_green_pin >= 0:
                    GPIO.output(self.led_green_pin, self._gpio_level(False))
            except Exception:
                pass
            try:
                GPIO.cleanup()
            except Exception:
                pass


# =====================================================================
# CLI
# =====================================================================
def _cli():
    ap = argparse.ArgumentParser(description="SoftKill controller (button toggle + status LEDs)")
    ap.add_argument("--button", type=int, default=_getenv_int("GPIO_KILL_PIN", -1))
    ap.add_argument("--active-low", type=_coerce_bool, default=_getenv_bool("GPIO_KILL_ACTIVE_LOW", True))
    ap.add_argument("--debounce", type=int, default=_getenv_int("GPIO_KILL_DEBOUNCE_MS", 120))
    ap.add_argument("--red", type=int, default=_getenv_int("GPIO_LED_RED_PIN", -1))
    ap.add_argument("--green", type=int, default=_getenv_int("GPIO_LED_GREEN_PIN", -1))
    ap.add_argument("--led-active-high", type=_coerce_bool, default=_getenv_bool("GPIO_LED_ACTIVE_HIGH", True))
    ap.add_argument("--init-killed", type=_coerce_bool, default=_getenv_bool("SOFTKILL_INIT_KILLED", False))
    ap.add_argument("--debug", type=_coerce_bool, default=True)  # CLI에서는 기본 True로 보이게
    args = ap.parse_args()

    def _on_change(killed: bool, reason: str):
        print(f"[CLI] → {'KILLED/OFF (RED ON)' if killed else 'ON (GREEN ON)'} (reason={reason})")

    print(f"[CFG] BTN BCM{args.button} (active_low={args.active_low}, debounce={args.debounce}ms,"
          f" pressed={'LOW' if args.active_low else 'HIGH'}) | "
          f"RED={args.red}, GREEN={args.green}, LED_ACTIVE_HIGH={args.led_active_high}, "
          f"init_killed={args.init_killed}")

    sk = SoftKillController(
        button_pin=args.button,
        active_low=bool(args.active_low),
        debounce_ms=args.debounce,
        led_red_pin=args.red,
        led_green_pin=args.green,
        led_active_high=bool(args.led_active_high),
        on_change=_on_change,
        initial_killed=bool(args.init_killed),   # 기본 False → GREEN부터 시작
        debug=bool(args.debug),                  # CLI에서는 디버그 기본 ON
    )

    print("키보드: t=토글(버튼), o=원격ON, f=원격OFF, p=원격토글, s=상태덤프, "
          "r=RED토글, g=GREEN토글, x=매핑스왑, 0=LED모두끄기, q=종료\n")

    try:
        while True:
            ch = sys.stdin.read(1)
            if not ch:
                time.sleep(0.05)
                continue
            c = ch.lower()
            if c == "t":
                sk.toggle(reason="cli")
                sk._dbg_dump(header="AFTER CLI TOGGLE")
            elif c == "o":   # 원격 ON 시뮬레이션
                sk.apply_remote("on")
                sk._dbg_dump(header="AFTER REMOTE ON")
            elif c == "f":   # 원격 OFF 시뮬레이션
                sk.apply_remote("off")
                sk._dbg_dump(header="AFTER REMOTE OFF")
            elif c == "p":   # 원격 토글 시뮬
                sk.apply_remote(not sk.is_killed)  # is_killed False→ON, True→OFF
                sk._dbg_dump(header="AFTER REMOTE TOGGLE")
            elif c == "s":
                sk._dbg_dump(header="MANUAL DUMP")
            elif c == "r" and _GPIO_OK and sk.led_red_pin >= 0:
                cur = GPIO.input(sk.led_red_pin)
                GPIO.output(sk.led_red_pin, GPIO.LOW if cur == GPIO.HIGH else GPIO.HIGH)
                print(f"[CLI] RED test toggle → {_lvl_name(GPIO.input(sk.led_red_pin))}")
            elif c == "g" and _GPIO_OK and sk.led_green_pin >= 0:
                cur = GPIO.input(sk.led_green_pin)
                GPIO.output(sk.led_green_pin, GPIO.LOW if cur == GPIO.HIGH else GPIO.HIGH)
                print(f"[CLI] GREEN test toggle → {_lvl_name(GPIO.input(sk.led_green_pin))}")
            elif c == "x":
                sk.swap_colors()
            elif c == "0" and _GPIO_OK:
                if sk.led_red_pin >= 0:   GPIO.output(sk.led_red_pin,   sk._gpio_level(False))
                if sk.led_green_pin >= 0: GPIO.output(sk.led_green_pin, sk._gpio_level(False))
                print("[CLI] LEDs OFF")
            elif c == "q":
                break
    except KeyboardInterrupt:
        pass
    finally:
        sk.cleanup()
        print("Bye.")

if __name__ == "__main__":
    _cli()
