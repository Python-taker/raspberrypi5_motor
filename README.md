# raspberrypi5_motor
2학기 공통 프로젝트 모터 담당 라즈베리파이 5 세팅 과정
=======
# SSAFY Motor Project (Raspberry Pi 5)

> 본 프로젝트는 Raspberry Pi 5 기반에서 `.venv` 가상환경을 사용하여 서보(PCA9685, I²C), 펠티어(BTS7960, PWM), 팬/LED(Arduino USB-시리얼 브리지)를 제어하고, 제어 명령 수신과 상태 발행을 MQTT로 수행하는 시스템입니다. 실사용 코드는 `src/`와 `arduino/` 하위에 위치합니다.

---

## 📑 목차

1. [프로젝트 개요](#프로젝트-개요)
2. [사전 요구사항](#-사전-요구사항)
3. [빠른 실행](#-빠른-실행)
4. [설치 및 환경 구성](#-설치-및-환경-구성)
5. [UART 문제 해결](#-uart-문제-해결)
6. [모듈 목록](#모듈-목록)
7. [하드웨어 연결 (라즈베리파이 기준)](#하드웨어-연결-라즈베리파이-기준)
8. [모듈 상세](#모듈-상세)

   * [PCA9685 (서보 드라이버)](#pca9685-서보-드라이버)
   * [BTS7960 (펠티어/모터 드라이버)](#bts7960-펠티어모터-드라이버)
   * [Arduino (팬/LED 스케치)](#arduino-팬led-스케치)
9. [서비스 레이어](#서비스-레이어)

   * [Fan Service (`src/actuators/services/fans.py`)](#fan-service-srcactuatorsservicesfanspy)
   * [LEDs Service (`src/actuators/services/leds.py`)](#leds-service-srcactuatorsservicesledspy)
   * [Peltier Service (`src/actuators/services/peltier.py`)](#peltier-service-srcactuatorsservicespeltierpy)
   * [Peltier+Temp Service (`src/actuators/services/peltier_with_temp.py`)](#peltiertemp-service-srcactuatorsservicespeltier_with_temppy)
   * [Servo Service (`src/actuators/services/servo.py`)](#servo-service-srcactuatorsservicesservopy)
   * [Main (`src/main.py`)](#main-srcmainpy)
10. [Config (`src/config.py`)](#config-srcconfigpy)
11. [MQTT Client (`src/mqtt_client.py`)](#mqtt-client-srcmqtt_clientpy)
12. [MQTT 파이프라인 예시](#mqtt-파이프라인-예시)
13. [백업 및 복원](#-백업-및-복원)
14. [자동 실행 (systemd)](#-자동-실행-systemd)
15. [실행 로그 예시](#-실행-로그-예시)
16. [주의사항](#-주의사항)
17. [부록 — 빠른 레퍼런스](#부록--빠른-레퍼런스)
18. [버튼 기반 제어(SoftKill/LED/Power)](#-버튼-기반-제어-SoftKill-LED-Power-)
19. [CLI (NO-MQTT) — main 로직 직접 시험](#CLI--NO-MQTT---main-로직-직접-시험)
21. [변경 로그](#변경-로그)

---

## 프로젝트 개요

**Raspberry Pi 5** 기반에서 `.venv` 가상환경을 사용하여 **PCA9685 서보(I²C)**, **BTS7960 (펠티어/모터 PWM)**, **Arduino(USB 시리얼) 기반 팬/LED**을 제어하고, 필요 시 **MQTT**로 제어 명령을 수신·상태를 발행합니다.

* **제어 대상**: 서보(최대 16ch), 펠티어(정방향 PWM), 소형 3핀 팬 **4대**, 4핀 PWM 대형 팬 **1대**, RGB LED **4개(공통 캐소드)**
* **통신 구조**: Pi↔PCA9685(I²C), Pi↔BTS7960(GPIO/PWM), Pi↔Arduino(USB CDC ACM), (옵션) Pi↔MQTT Broker
* **전원 구조**: 서보 5 V(고전류)·로직 3.3 V 분리, 펠티어 12 V, 공통 GND(스타 접지)

---

## ✅ 사전 요구사항

* **HW/OS**: Raspberry Pi 5, Raspberry Pi OS (Bookworm 권장)
* **Python**: 3.10+ (권장 3.11)
* **라즈베리파이 인터페이스 설정**

  * I²C **활성화**
  * UART: Arduino를 USB로 연결 시 자동 할당(`/dev/ttyACM0` 등)
  * (충돌 방지) **1‑Wire 비활성화** 또는 GPIO4 미사용
* **필수 패키지(예)**

  * `gpiozero`, `adafruit-circuitpython-pca9685`, `pyserial`, `paho-mqtt`, `python-dotenv`

---

## 🚀 빠른 실행

```bash
cd ssafy_project
python -m venv .venv && source .venv/bin/activate
pip install -U pip wheel
pip install gpiozero adafruit-circuitpython-pca9685 pyserial paho-mqtt python-dotenv

# 실사용 엔트리 포인트 실행
PYTHONPATH=src python -m src.main
```

> 하드웨어 제어 시 `sudo`가 필요할 수 있습니다.

---

## 📦 설치 및 환경 구성

### 1. 필수 패키지 설치

```bash
sudo apt update
sudo apt install python3-rpi.gpio python3-serial python3-smbus i2c-tools
```

### 2. 프로젝트 디렉토리 생성 및 구조

```bash
mkdir ~/ssafy_project
cd ~/ssafy_project
mkdir sensor_project scripts
```

```
ssafy_project/
├─ .venv/                            # Python 가상환경
├─ scripts/                          # 백업 및 의존성 관리 스크립트
│  ├─ backup.sh
│  ├─ freeze_deps.sh
│  └─ restore.sh
├─ arduino/
│  └─ multi_fan_led_serial_control.ino
├─ src/
│  ├─ __init__.py                     # (권장) src 패키지 루트 표시
│  ├─ main.py                         # 통합 제어 엔트리 (서보 홈/SoftKill/LED 리맵, 중복호출 억제)
│  ├─ mqtt_client.py
│  ├─ config.py
│  ├─ actuators/
│  │  ├─ drivers/
│  │  │  ├─ __init__.py               # (권장) 상위 패키지 고정
│  │  │  ├─ bts7960_peltier_pwm.py
│  │  │  ├─ pca9685_servo_module.py   # /OE 제어, home_all(), 미세조정 로직
│  │  │  └─ arduino_bridge.py
│  │  └─ services/
│  │     ├─ __init__.py               # (권장) 상위 패키지 고정
│  │     ├─ fans.py
│  │     ├─ leds.py
│  │     ├─ peltier.py
│  │     ├─ peltier_with_temp.py
│  │     └─ servo.py                  # 반전 없음, 전처리/상태 유지
│  ├─ controls/
│  │  └─ softkill.py                  # 물리 버튼/LED 제어 + on_change 콜백
│  ├─ scripts/
│  │  ├─ gpio_toggler.py              # (도움 스크립트) GPIO 토글 실험
│  │  └─ mqtt_sniff.py                # (도움 스크립트) MQTT 스니퍼
│  ├─ utils/
│  │  └─ energy_meter.py
│  └─ cli_actuator_test.py            # NO-MQTT CLI
├─ requirements.txt
├─ .env
├─ .gitignore
└─ README.md
```

### 3. 가상환경 생성 및 활성화

```bash
git clone <repo-url> && cd ssafy_project
python -m venv .venv && source .venv/bin/activate
```

### 4. pip 업그레이드 및 필수 라이브러리 설치

```bash
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt  # 있으면 사용, 없으면 빠른 실행의 목록 참고
```

### 5. **라즈베리파이 인터페이스 설정** (I²C 활성화, 1‑Wire 비활성 등)

### 6. **권한 설정** (시리얼 사용 시)

```bash
sudo usermod -aG dialout $USER && newgrp dialout
```

### 7. **환경 변수/설정 파일**: `src/config.py` (MQTT 브로커, 시리얼 포트 등)

---

## 🛠 UART 문제 해결

* 장치 인식 확인: `dmesg | grep -i tty` / `ls -l /dev/ttyACM*`
* 권한: 사용자 `dialout` 그룹 포함
* (선택) **udev 규칙**로 고정 심볼릭 링크(`/dev/arduino`) 생성
* 충돌 서비스: `ModemManager` 등 불필요 서비스 비활성화

> 센서 프로젝트의 UART 트러블슈팅 가이드와 동일한 절차로 적용 가능합니다. USB CDC(ACM) 장치(Arduino)는 `/dev/ttyACM*`로 인식됩니다.

---

## 모듈 목록

| 모듈                  | 파일                                              | 기능 설명                    | 연결           |
| ------------------- | ----------------------------------------------- | ------------------------ | ------------ |
| **PCA9685 Servo**   | `src/actuators/drivers/pca9685_servo_module.py` | 16채널 서보 PWM 제어, 각도↔펄스 변환, /OE  | I²C          |
| **BTS7960 Peltier** | `src/actuators/drivers/bts7960_peltier_pwm.py`  | 펠티어 정방향 PWM, EN/안전 시퀀스   | GPIO+PWM     |
| **Arduino Bridge**  | `src/actuators/drivers/arduino_bridge.py`       | USB 시리얼로 팬/LED 제어 송신     | USB CDC(ACM) |
| **Fans Service**    | `src/actuators/services/fans.py`                | 팬 제어 상위 래퍼/오케스트레이션       | 내부 호출        |
| **LEDs Service**    | `src/actuators/services/leds.py`                | RGB LED 색상 제어            | 내부 호출        |
| **Peltier Service** | `src/actuators/services/peltier.py`             | BTS7960 제어용 듀티 전처리       | 내부 호출        |
| **Peltier+Temp**    | `src/actuators/services/peltier_with_temp.py`   | 온도 기반 가중 보정 포함 듀티 전처리    | 내부 호출        |
| **Servo Service**   | `src/actuators/services/servo.py`               | 서보 각도 전처리(반전은 드라이버)      | I²C(간접)      |
| **SoftKill**        | `src/controls/softkill.py`                      | 물리 버튼/LED(R/G) 기반 소프트킬 컨트롤러    | GPIO   |
| **Main**            | `src/main.py`                                   | 엔트리, 서비스 구동              | —            |
| **MQTT Client**     | `src/mqtt_client.py`                            | 명령 수신/상태 발행              | TCP/MQTT     |
| **Energy Meter**    | `src/utils/energy_meter.py`                     | 30초 기준 소비전력 추정           | 내부 호출    |
| **Config**          | `src/config.py`                                 | 브로커/토픽/환경변수              | —            |
| **CLI Test**        | `src/cli_actuator_test.py`                      | MQTT 없이 main 로직 직접 시험     | 콘솔 상호작용(USB/I²C)  |

---

## 하드웨어 연결 (라즈베리파이 기준)

### 공통 원칙

* 공통 GND(스타 접지): 고전류(서보/펠티어/팬) 리턴은 PSU(–) 한 점에서 합류
* 각 보드 전원 핀 근처: **0.1 µF 세라믹** 디커플링, 전원 입력에는 **100–470 µF 전해** 병렬 권장
* **전원 레일 분리**: 서보(`V+`=5 V 고전류)와 로직(`VCC`=3.3 V)을 물리적으로 분리

### 라즈베리파이 GPIO 핀맵(본 프로젝트 사용분)

| 기능                     | 핀(BCM)                    | 비고                                    |
| ---------------------- | ------------------------- | ------------------------------------- |
| I²C SDA/SCL → PCA9685  | GPIO 2 / GPIO 3           | 3.3 V 풀업(보드 내장 시 생략 가능)               |
| BTS7960 R\_EN / R\_PWM | **GPIO 17** / **GPIO 18** | R\_PWM ≈ 1 kHz                        |
| BTS7960 L\_EN / L\_PWM | **GPIO 23** / **GPIO 24** | L\_PWM는 항상 LOW                        |
| SoftKill 버튼 입력      | **GPIO 10**               | active_low 옵션 지원                       |
| SoftKill LED (R/G)     | **GPIO 09** / **GPIO 11** | led_active_high 옵션 지원                 |
| PCA9685 /OE 제어       | **GPIO 22**               | 활성화 시 전체 채널 Enable/Disable          |
| 3.3 V 로직               | —                         | PCA9685 VCC(로직)                       |
| 5 V(서보)                | —                         | PCA9685 **V+** (외부 5 V, 1N5819 직렬 권장) |
| 12 V(펠티어)              | —                         | BTS7960 VMOTOR, 입력에 470 µF//0.1 µF    |


### 배선 다이어그램(개요)

```plaintext
[Raspberry Pi 5]
 ├─ I2C1 (GPIO2=SDA, GPIO3=SCL)
 │   └─ PCA9685 (VCC=3.3V, V+=5V 외부, /OE: GPIO22)
 │       └─ Servo CH0..7 (외부 5V, 공통 GND)
 │
 ├─ PWM/EN (GPIO17,18,23,24)
 │   └─ BTS7960
 │       ├─ R_EN=GPIO17, R_PWM=GPIO18 (~1kHz)
 │       ├─ L_EN=GPIO23, L_PWM=GPIO24(LOW 고정)
 │       └─ VMOTOR=12V → Peltier (+)  /  GND 공통
 ├─ GPIO(SoftKill)
 │   ├─ BUTTON (GPIO10, Active-Low)
 │   └─ LED R/G (GPIO9/11, Active-High)
 └─ USB (CDC/ACM)
     └─ Arduino (팬/LED 브리지)
         ├─ D9 → 4핀 PWM 대형 팬(25kHz, Active-Low, 오픈드레인)
         ├─ D3/D5/D6/D11 → 소형 팬 4ch (0~100%)
         └─ 다수 GPIO → 공통 캐소드 RGB LED 4개 (R/G/B 각 220–330Ω)
```

### 안정화/보호 부품 권장

* **PCA9685**: `V+` 라인 **1N5819 직렬** + `470 µF//0.1 µF`
* **BTS7960**: VMOTOR 입력 **470 µF//0.1 µF**, 필요 시 펠티어 단자에 100–220 µF 추가
* **I²C 라인**: 노이즈 우려 시 SDA/SCL 직렬 **220 Ω**
* **팬 4핀 PWM**: **오픈드레인**(2N7000 등)로 GND로만 당김 — 표준은 25 kHz/Active‑Low

---

## 모듈 상세

### PCA9685 (서보 드라이버)

* **전원**: `VCC=3.3 V`, `V+=외부 5 V` — 두 레일 **분리**(점퍼/브리지 제거)
* **보호/안정화**: `V+`에 **1N5819 직렬** + `V+–GND`에 **470 µF//0.1 µF**
* **I²C**: SDA/SCL에 3.3 V 풀업(보드 내장 가능), 라인당 **직렬 220 Ω** 권장
* **/OE**: 기본 HIGH(OFF) → 초기화 완료 후 LOW 활성화 (환경변수로 Enable/Disable 제어)
* **버스/주소**: `/dev/i2c-1`, 기본 `0x60` (*보드에 따라 `0x40`일 수 있음 — 스캔 권장*)

**핵심 함수**

| 함수                                                                    | 기능          | 비고                   |
| --------------------------------------------------------------------- | ----------- | -------------------- |
| `get_angle_from_pulse(pulse)`                                         | 펄스→실측 각도    | cubic 보간(`interp1d`) |
| `get_pulse_from_angle(angle)`                                         | 각도→펄스       | cubic 보간 후 `round`   |
| `init_pca9685(address=0x60, freq=50)`                                 | PCA9685 초기화 | Pi 5 버스 1 고정, 50 Hz  |
| `initialize_servo_system(home=True)`                                  | 전체 초기화      | 홈 스윕 옵션              |
| `safe_corrective_move(pwm, ch, positions, target_angle, move_min=15)` | **안전 이동**   | Δ펄스 작을 때 미세조정 시퀀스    |

**`ServoAPI` 요약**

* 내부 4ch(0\~3): **θ → (60-θ)** 반전 적용
* 외부 4ch(4\~7): 입력 그대로
* 스레드 락으로 동시 호출 보호, `home_*`, `set_internal/external/both()` 제공
* /OE 전체 Enable/Disable 지원(환경 변수로 핀/극성 지정)

---

### BTS7960 (펠티어/모터 드라이버)

* **핀(BCM)**: `R_EN=17`, `R_PWM=18`, `L_EN=23`, `L_PWM=24` (≈1 kHz)
* **EN 기본**: 부팅 시 `LOW`. 구동 시 `R_EN/L_EN=HIGH`, `L_PWM=LOW` 유지
* **권장**: 12 V 입력 **470 µF//0.1 µF**, 필요 시 펠티어 단자에 100–220 µF 추가
* **선택 보호**: 12 V 라인에 **1N5819 직렬**(역류/역극성 보호, 전압강하 감수)

**`PeltierAPI` 요약**

* `safe_init()` → `enable_forward()` → `set_duty(x)` / `ramp_to(y)`
* `stop()` 후 `close()` (**EN=LOW**) 권장

---

### Arduino (팬/LED 스케치)

* 5개 팬(소형 4 + 4핀 대형 1), 공통 캐소드 RGB LED 4개 제어
* 4핀 PWM 팬은 **25 kHz, Active‑Low(듀티 반전), 오픈드레인/오픈컬렉터 구동** 권장(예: 2N7000)
* 시리얼 프로토콜(개행 `\n` 종료):

  * `SETF f1 f2 f3 f4 big` → `ACK:SETF:...`
  * `SETL c1 c2 c3 c4` (R/G/B/W/OFF) → `ACK:SETL:...`
  * `SETALL f1 f2 f3 f4 big c1 c2 c3 c4` → `ACK:SETALL:...`
  * `GET?` → `DATA:STATE:F:...;L:...`

---

## 서비스 레이어

### Fan Service (`src/actuators/services/fans.py`)

**개요** — 소형 4 + 대형 1 팬의 듀티 **전처리 레이어**. 길이/타입만 정규화하고 **클램핑은 하지 않음**(테스트/디버깅 편의). 운영 환경에서는 상위 계층에서 0\~100 검증 권장.

* 입력 키: `small_fan_pwm`(list), `large_fan_pwm`(int)
* 정규화: 소형 팬 리스트 **길이 4 강제**(초과 버림·부족 0 패딩)
* 타입 변환: `int(float(x))`만 수행
* 상태 모델: `FanState(small: List[int], large: int)`

**공개 API**: `preprocess(payload) -> List[int]`, `for_driver() -> List[int]`, `to_arduino_cmd() -> str`, `to_status() -> dict`

---

### LEDs Service (`src/actuators/services/leds.py`)

**개요** — **TSV(-3..3)** 배열을 **LED 색상 4개**로 매핑.

* 규칙: `v <= cold_high → 'B'`, `cold_high < v < hot_low → 'W'`, `v >= hot_low → 'R'`
* 길이 4 강제(초과 버림·부족 0.0 패딩), `float()` 변환 실패 시 0.0 대체
* 기본 임계값: `cold_high=-0.5`, `hot_low=+0.5`
* LED_TSV_ORDER/LED_HW_ORDER로 입력/출력 채널 리맵 지원 (예: 대시보드 슬롯 순서 ≠ 하드 채널 순서일 때 매핑)
**공개 API**: `LedService(...).preprocess(payload) -> List[str]`, `for_driver()`, `to_arduino_cmd()`, `to_status()`

---

### Peltier Service (`src/actuators/services/peltier.py`)

**개요** — **펠티어 PWM 전처리·검증**. 상위 입력 `0..100`을 정책에 따라 **0은 OFF**, **1..100은 `MIN_ON..100` 균등 선형 매핑**.

* 기본 상수: `MIN_ON_DUTY_DEFAULT=50`, `MAX_DUTY_DEFAULT=100`
* 라운딩: `'floor' | 'round' | 'ceil'` (기본 `'floor'`)
* **클램핑 수행**: 입력을 0..100으로 제한

**정의**

```
raw ∈ [1..100] → mapped = MIN_ON + R((raw-1) * (100 - MIN_ON) / 99)
raw = 0 → 0
```

**공개 API**: `PeltierService(...).preprocess(payload) -> int`, `for_driver() -> int`, `to_status() -> dict`

---

### Peltier+Temp Service (`src/actuators/services/peltier_with_temp.py`)

**개요** — **펠티어 듀티 전처리 + 온도 기반 가중치 보정**.

1. `0..100` 입력을 \*\*0 또는 `MIN_ON..100`\*\*으로 선형 매핑(위 *Peltier Service* 동일).
2. **온도 편차 보정**: `delta_t = temp_avg - target_temp_avg`

   * `delta_t < 0` (추움): `base`를 **50(MIN\_ON)** 쪽으로 `w_cold`만큼 혼합 — **특수 규칙**: `base == MIN_ON`이면 **0으로 강제 OFF**
   * `delta_t > 0` (더움): `base`를 **100** 쪽으로 `w_hot`만큼 혼합
   * `base == 0`이면 **가중치로 켜지지 않음**(그대로 0)

* 가중치: `BIAS_WEIGHT_COLD_DEFAULT=0.5`, `BIAS_WEIGHT_HOT_DEFAULT=0.5` (0.0\~1.0)
* 혼합 함수: `mix(value, target, w) = (1-w)*value + w*target`

**상태 직렬화** (`to_status`) 예시 키

```json
{
  "peltier_pwm_cmd": <raw>,
  "peltier_pwm_base": <base_mapped>,
  "peltier_pwm_applied": <applied>,
  "temp_avg": <float>,
  "target_temp_avg": <float>,
  "delta_t": <float>
}
```

**공개 API**: `PeltierService(...).preprocess(payload) -> int`, `for_driver() -> int`, `to_status() -> dict`

> 구현 파일의 클래스명이 `PeltierService`이지만, **온도 가중 보정 포함 버전**이 `peltier_with_temp.py`에 있습니다. 동일 시그니처로 교체 가능.

---

### Servo Service (`src/actuators/services/servo.py`)

**개요** — **서보 각도 전처리·검증 레이어**. 내부 4ch/외부 4ch 배열을 **길이 정규화 → 숫자 변환 → 채널별 범위 클램프 → (선택) 반올림** 후 상태 유지.

> ❗ **중요**: **내부 4ch의 `60-θ` 반전은 드라이버(`ServoAPI`)가 수행**합니다. **서비스에서는 절대 반전하지 않습니다.**

* 입력 키: `internal_servo`(list\[float]), `external_servo`(list\[float])
* 길이 4 강제(초과 버림·부족 0.0 패딩), `float()` 실패 시 0.0
* 클램프 범위(기본): 내부 **0..60°**, 외부 **0..80°**
* 반올림 옵션: `round_to` 지정 시 `round(v, round_to)` 적용(예: `1`→0.1° 단위)

**공개 API**

| 시그니처                                                                                  | 기능          | 비고                                                   |
| ------------------------------------------------------------------------------------- | ----------- | ---------------------------------------------------- |
| `ServoService(internal_max_angles=[60]*4, external_max_angles=[80]*4, round_to=None)` | 서비스 생성자     | 채널별 최대각 커스텀 가능                                       |
| `preprocess(payload)`                                                                 | 정규화/클램프/반올림 | 반환 `(internal4, external4)` — **반전 없음**              |
| `for_driver()`                                                                        | 현재 상태 반환    | `ServoAPI.set_both()`에 바로 사용                         |
| `to_status()`                                                                         | 상태 조각 직렬화   | `{ "slot_internal": [...], "slot_external": [...] }` |

**예시**

```python
payload = {
  "internal_servo": [65, "12.3", -3, 30],  # → [60.0, 12.3, 0.0, 30.0]
  "external_servo": [50, 70, 999, "x"],    # → [50.0, 70.0, 80.0, 0.0]
}
svc = ServoService(round_to=1)
i4, e4 = svc.preprocess(payload)  # 반전 없음
ServoAPI().set_both(i4, e4)       # 내부 반전은 드라이버가 수행
```

---

### Main (`src/main.py`)

**개요 — 통합 제어 엔트리(최신)**

* 초기화 시:
   1. BTS7960 안전 초기화(EN=LOW→정방향 Enable, 듀티=0)
   2. ServoAPI OE Enable → home_all() 수행(부팅 기준자세)
   3. Arduino(팬/LED) 연결
   4. SoftKill 컨트롤러 초기화(물리 버튼/LED 연계), 콜백에서 안전 OFF/LED/서보 홈 일괄 처리

* 구독/발행:
   * 수신: .../value(펠티어/팬/서보), .../tsv(LED), .../power_server(SoftKill on/off)
   * 발행: status/hvac/{id}/all (옵션: STATUS_USE_APPLIED에 따라 raw vs applied)

* LED 채널 리맵: LED_TSV_ORDER(입력 정렬) → LED_HW_ORDER(물리채널 정렬)
SoftKill 시 LED는 강제 OFF 색상으로 운용 (OFF 토큰 사용)

* 서보 중복 호출 억제:
전처리(클램프/라운딩) 후 이전 적용 상태와 SERVO_EPS_DEG 이내면 드라이버 호출 스킵
(미세 떨림/과도 동작 억제)

* SoftKill 전환 시 동작:
   * OFF 진입: 펠티어 OFF, 팬 OFF, LED OFF, 서보 OE Enable → home_all() → OE Disable
   * ON 복귀: 펠티어 Enable, 서보 OE Enable → home_all(), 최신 LED 매핑 재적용

* 에너지 추정: utils/energy_meter.estimate_energy_wh_30s로 30초 가중치 기준 값 포함

> **CLI (NO-MQTT)**로 동일 로직을 바로 테스트 가능: src/cli_actuator_test.py

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py
────────────────────────────────────────────────────────
- 모터 라즈베리파이: (임시 버전) 펠티어만 제어
- MQTT 수신(control/hvac/{id}/value)의 peltier_pwm → 서비스 전처리 → BTS7960 드라이버 듀티 적용
- 온도 관련(tsv, temp_avg/target_temp_avg)은 **본 버전에서 무시**

필수 의존:
- config.py                     : 브로커/토픽 상수
- mqtt_client.py                : paho-mqtt 래퍼
- actuators/services/peltier.py : 펠티어 듀티 매핑 서비스(0→0, 1~100→MIN_ON..100)
- actuators/drivers/bts7960_peltier_pwm.py : BTS7960 제어(safe_init/enable_forward/set_duty)
"""

# =====================================================
# 0️⃣ Imports & Env
# =====================================================
import os
import sys
import time
import signal
import threading
from pathlib import Path

from dotenv import load_dotenv

# --- sys.path 보정: 이 파일(=src)의 절대경로를 import path에 추가 ---
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import (
    HVAC_ID,
    TOPIC_STATUS_ALL,
    TOPICS_SUB,
    TOPICS_PUB,
)
from mqtt_client import MQTTClient

# services / drivers 는 실제 폴더 위치에 맞게 임포트
from actuators.services.peltier import PeltierService, MIN_ON_DUTY_DEFAULT
from actuators.drivers import bts7960_peltier_pwm as pdrv  # safe_init(), enable_forward(), set_duty()

# =====================================================
# 1️⃣ 전역 (런타임 상태)
# =====================================================
load_dotenv()
BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

mqttc: MQTTClient | None = None
svc_peltier = PeltierService(min_on_duty=MIN_ON_DUTY_DEFAULT, rounding="floor")
_shutdown = threading.Event()


# =====================================================
# 2️⃣ 드라이버 초기화/해제
# =====================================================
def _driver_init() -> None:
    """
    BTS7960 초기 안전 상태 → 정방향 Enable.
    """
    pdrv.safe_init()
    pdrv.enable_forward()
    # 시작 시 듀티 0 보장
    pdrv.set_duty(0)
    print(f"✅ BTS7960 ready (MIN_ON={MIN_ON_DUTY_DEFAULT}%)")


def _driver_safe_off() -> None:
    """
    안전 종료: 듀티 0 → (모듈의 finally와 중복되어도 무해)
    """
    try:
        pdrv.set_duty(0)
    except Exception:
        pass


# =====================================================
# 3️⃣ 상태 발행 도우미
# =====================================================
def _publish_status(applied_duty: int) -> None:
    """
    팀 상태 스키마에 맞춰 최소 필드만 넣어 발행.
    - 추후 서보/팬/LED가 합류하면 data 키 아래 확장
    """
    if mqttc is None:
        return
    payload = {
        "hvac_id": HVAC_ID,
        "data": {
            "airflow_speed": "off",           # (임시) 나중에 팬 합류 시 반영
            "slot_internal": [0, 0, 0, 0],    # (임시)
            "slot_external": [0, 0, 0, 0],    # (임시)
            "fan_intake_speed": [0, 0, 0, 0], # (임시)
            "fan_main_speed": 0,              # (임시)
            # 서비스 to_status()와 동일 키 매핑(필요 시 조정)
            "energy_temp_total": applied_duty,
        },
    }
    mqttc.publish_json(TOPIC_STATUS_ALL, payload)


# =====================================================
# 4️⃣ MQTT 수신 핸들러 (임시: peltier만 처리)
#    mqtt_client.MQTTClient 는 handler(topic, data) 로 호출합니다.
# =====================================================
def on_mqtt(topic: str, data: dict) -> None:
    """
    - control/.../value 에서 peltier_pwm 추출 → 서비스 전처리 → 드라이버 듀티 적용
    - 그 외 토픽은 현재 무시(확장 예정)
    """
    try:
        if not isinstance(data, dict):
            return

        if topic.endswith("/value"):
            # 1) 서비스 전처리
            applied = svc_peltier.preprocess(data)  # 0 or MIN_ON..100

            # 2) 드라이버 적용
            pdrv.set_duty(applied)
            print(f"[Peltier] raw={svc_peltier.state.raw_duty} → applied={applied}")

            # 3) 상태 발행(간단)
            _publish_status(applied)

        # (tsv, power_server 등은 이번 버전에서 무시)

    except Exception as e:
        print(f"[on_mqtt][Error] {e}")


# =====================================================
# 5️⃣ 종료 처리
# =====================================================
def _handle_sigterm(signum, frame):
    print("\n🔚 SIGTERM/SIGINT received. Shutting down...")
    _shutdown.set()


# =====================================================
# 6️⃣ main
# =====================================================
def main():
    global mqttc

    # 드라이버 준비
    _driver_init()

    # MQTT 연결
    mqttc = MQTTClient(
        BROKER_HOST,
        BROKER_PORT,
        publish_topics=TOPICS_PUB,
        subscribe_topics=TOPICS_SUB,
    )
    mqttc.set_message_handler(on_mqtt)
    mqttc.connect(keepalive=60)

    # 종료 시그널 핸들링
    signal.signal(signal.SIGINT, _handle_sigterm)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    print(f"🚀 Running: broker={BROKER_HOST}:{BROKER_PORT} | HVAC_ID={HVAC_ID}")
    print("   * 임시 버전: peltier_pwm만 처리 (temp_avg/target_temp_avg 무시)")

    try:
        # 메인 루프는 단순 대기 (MQTT 콜백이 실질 업무 수행)
        while not _shutdown.is_set():
            time.sleep(0.5)
    finally:
        try:
            if mqttc:
                mqttc.disconnect()
        finally:
            _driver_safe_off()
        print("✅ Cleaned up. Bye.")


if __name__ == "__main__":
    main()
```

---

## Config (`src/config.py`)

**역할** — MQTT 토픽/환경변수 설정, 구독·발행 토픽 관리.

* **환경변수**

  * `HVAC_ID` (기본 1)
  * `MQTT_BROKER_HOST` (기본 `localhost`)
  * `MQTT_BROKER_PORT` (기본 `1883`)
  * `MQTT_KEEPALIVE` (기본 `60`)
  * `MQTT_QOS_DEFAULT` (기본 `0`)
  * STATUS_USE_APPLIED: 1|0 — 상태 발행 시 보정값/수신값 선택
  * LED_TSV_ORDER: 0,1,2,3 형태 — TSV 입력 슬롯→논리 슬롯 맵
  * LED_HW_ORDER: 0,1,2,3 형태 — 논리 슬롯→물리 LED 채널 맵
  * MIN_SMALL_FAN_ON: 소형 팬 최저 가동 듀티 (기본 30)
  * SERVO_EPS_DEG: 서보 동일 판정 허용오차(deg, 기본 0.2)
  * PCA9685_OE_GPIO: /OE 제어 GPIO 번호(미지정=-1)
  * PCA9685_OE_ACTIVE_LOW: 1|0 (기본 1=Active-Low)
  * PCA9685_OE_DEFAULT_ENABLE: 부팅시 Enable 여부(기본 0)
  * GPIO_KILL_PIN: SoftKill 버튼 GPIO(미지정=-1)
  * GPIO_KILL_ACTIVE_LOW: 1|0 (기본 1=Active-Low)=
  * GPIO_LED_RED_PIN / GPIO_LED_GREEN_PIN: 상태 LED GPIO(미지정=-1)
  * GPIO_LED_ACTIVE_HIGH: 1|0 (기본 1=Active-High)

* **토픽 상수**

  * `TOPIC_STATUS_ALL`      → `status/hvac/{HVAC_ID}/all`
  * `TOPIC_POWER_ACTUATOR` → `control/hvac/{HVAC_ID}/power_actuator`
  * `TOPIC_POWER_SERVER`    → `control/hvac/{HVAC_ID}/power_server`
  * `TOPIC_TSV`             → `control/hvac/{HVAC_ID}/tsv`
  * `TOPIC_VALUE`           → `control/hvac/{HVAC_ID}/value`

* **구독 리스트** (`TOPICS_SUB`)

  * `power_server`: { "power":"on|off" } → `SoftKill ON/OFF`
  * `tsv`: { , ,"tsv":[...4] } → `LED 상태(대시보드 버튼/값)`
  * `value`(제어값): `peltier_pwm`, `internal_servo`, `external_servo`, `small_fan_pwm[4]`, `large_fan_pwm`

* **발행 리스트** (`TOPICS_PUB`)

  * `status_all`(액추에이터 상태 종합)
  * `power_actuator`(라즈베리파이 측 전원 상태 회신)

**상태 페이로드 예** — `status/hvac/{HVAC_ID}/all`

```json
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
```

**명령 페이로드 예**

* `control/hvac/{HVAC_ID}/power_server` → `{ "power":"on|off" }`
* `control/hvac/{HVAC_ID}/tsv` → `{ "temp_avg":23.4, "target_temp_avg":25.0, "tsv":[1.0,0.0,-0.8,2.1] }`
* `control/hvac/{HVAC_ID}/value` →

```json
{
  "peltier_pwm": 5,
  "internal_servo": [45,45,44,6],
  "external_servo": [50,70,80,12],
  "small_fan_pwm": [5,80,0,2],
  "large_fan_pwm": 90
}
```

---

## MQTT Client (`src/mqtt_client.py`)

### 개요

`paho-mqtt`를 감싼 **간단 래퍼**입니다. 연결/구독/발행을 다루고, 팀 규약 토픽(`.../power_server`, `.../tsv`, `.../value`)의 **최신 JSON 스냅샷**을 보관합니다. 외부 콜백은 `(topic, data, msg)` 형식으로 받아서 **on\_message에서의 무거운 처리**를 피하고, 메인 로직은 콜백에서 분리해 구현할 수 있습니다.

**핵심 포인트**

1. `on_message`는 **파싱/캐시만 수행**(가볍게 유지)
2. 최신 스냅샷 접근은 **Lock 보호**로 일관성 보장
3. 메시지 핸들러는 **(topic, data, msg) → (topic, data) → (msg)** 순으로 하위 호환 호출
4. 발행 시 **화이트리스트(허용 토픽) 검사 후 경고만 표시**, 실제 퍼블리시는 항상 수행

### 빠른 사용 예 (`main.py` 연동)

```python
from config import BROKER_HOST, BROKER_PORT, TOPICS_SUB, TOPICS_PUB
from mqtt_client import MQTTClient

# 1) 클라이언트 준비 및 콜백 등록
mqttc = MQTTClient(BROKER_HOST, BROKER_PORT,
                   publish_topics=TOPICS_PUB,
                   subscribe_topics=TOPICS_SUB)

def on_mqtt(topic, data):  # 또는 (topic, data, msg)
    if topic.endswith('/value'):
        # data(dict) 처리...
        pass

mqttc.set_message_handler(on_mqtt)

# 2) 연결 및 자동 재연결 루프 시작
mqttc.connect(keepalive=60)

# 3) 발행 예시
mqttc.publish_json("status/hvac/1/all", {"hvac_id": 1, "data": {"ok": True}})

# 4) 스냅샷 접근
snap = mqttc.get_latest_snapshot()  # {"power":..., "tsv":..., "value":..., "last":...}

# 5) 종료 시 정리
mqttc.disconnect()
```

### 공개 API (요약)

| 메서드/속성   | 시그니처                                                                                                                      | 기능/비고                                                                                             |
| -------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 생성자      | `MQTTClient(broker_host, broker_port, *, publish_topics=None, subscribe_topics=None, client_id=None, clean_session=True)` | paho client 래핑. `publish_topics`/`subscribe_topics`는 **문자열 또는 `(topic, qos)`** 혼용 가능              |
| 핸들러 등록   | `set_message_handler(handler)`                                                                                            | 권장 시그니처: `handler(topic:str, data:dict, msg:mqtt.MQTTMessage)` — 하위호환: `(topic, data)` 또는 `(msg)` |
| 연결       | `connect(keepalive=60)`                                                                                                   | 브로커 접속 + `loop_start()`                                                                           |
| 종료       | `disconnect()`                                                                                                            | `loop_stop()` 후 disconnect                                                                        |
| 재구독      | `resubscribe(topics=None)`                                                                                                | 구독 목록 갱신 및 재구독(없으면 기존 목록 사용)                                                                      |
| 발행(raw)  | `publish_raw(topic, payload:str, qos=0, retain=False)`                                                                    | 화이트리스트 미포함 시 **경고 로그**만, 발행은 수행                                                                   |
| 발행(JSON) | `publish_json(topic, payload:dict, qos=0, retain=False)`                                                                  | `json.dumps(..., ensure_ascii=False)` 후 발행                                                        |
| 스냅샷      | `get_latest_snapshot() -> dict`                                                                                           | `{power, tsv, value, last}` 최신 JSON을 반환                                                           |
| 토픽별 최신   | `get_latest_by_topic(topic) -> dict`                                                                                      | 임의 토픽의 최신 payload(없으면 `{}`)                                                                       |
| 편의 접근자   | `get_latest_value() / get_latest_tsv() / get_latest_power_server()`                                                       | 팀 규약 접미사 기준 최신 JSON 반환                                                                            |

<details>
<summary><strong>mqtt_client.py (전체 소스 보기)</strong></summary>

```python
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
```

</details>

---

## MQTT 파이프라인 예시

**서보**

```
control/value → ServoService.preprocess() → (i4,e4) → ServoAPI.set_both(i4, e4)
```

**펠티어(기본 매핑)**

```
control/value → PeltierService.preprocess() → applied → PeltierAPI.set_duty(applied)
```

**펠티어(온도 가중)**

```
control/value(+ temp_avg/target_temp_avg) → peltier_with_temp.preprocess() → applied → PeltierAPI.set_duty(applied)
```

**팬/LED (Arduino)**

```
control/value → FanService.preprocess() → [f1..f4,big] → ArduinoFanLedBridge.set_fans()
control/tsv   → LedService.preprocess() → [c1..c4]    → ArduinoFanLedBridge.set_leds()
```

---

## 💾 백업 및 복원

```bash
# 의존성 동결
pip freeze > requirements.txt

# 백업 (소스/스케치/의존성)
tar czf backup_$(date +%F).tar.gz src arduino requirements.txt README.md

# 복원
tar xzf backup_YYYY-MM-DD.tar.gz
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 🔒 자동 실행 (systemd)

`/etc/systemd/system/ssafy-motor.service`

```ini
[Unit]
Description=SSAFY Motor Project Service
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/ssafy_project
Environment=PYTHONPATH=/home/pi/ssafy_project/src
ExecStart=/home/pi/ssafy_project/.venv/bin/python -m src.main
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

활성화

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ssafy-motor.service
sudo systemctl status ssafy-motor.service
```

---

## 📈 실행 로그 예시

```
[INFO] I2C scan: PCA9685 @ 0x40 detected
[INFO] Arduino /dev/ttyACM0 connected, 115200 baud
[INFO] BTS7960 EN=HIGH/HIGH, PWM=1kHz
[TX->Arduino] SETF 20 20 20 20 30
[RX<-Arduino] ACK:SETF:20,20,20,20,30
[TX->Arduino] SETL R G B OFF
[RX<-Arduino] ACK:SETL:R,G,B,OFF
[MQTT] Published motor/state/1 {...}
```

---

## ⚠️ 주의사항

* **전원/접지**: 공통 접지, 고전류 리턴은 PSU(–)에서 합류(스타 접지). GND 사이에 소자를 **직렬**로 넣지 않기
* **PCA9685**: `V+`와 `VCC` **절연**(점퍼 제거). `V+`에는 **1N5819 직렬** + **470 µF//0.1 µF** 권장
* **BTS7960**: 부팅 시 EN=LOW. 사용 중 정지 시 `PWM=0 → EN=LOW` 순서 준수
* **4핀 PWM 팬**: 오픈드레인 구동(2N7000 등) 권장, 25 kHz 표준
* **발열/케이블**: 펠티어/서보/팬 전류 고려 **굵고 짧게** 배선, 통풍 확보
* **핀 충돌**: GPIO4는 1‑Wire 기본 핀. 사용 시 1‑Wire 비활성화 또는 다른 핀 사용

---

## 부록 — 빠른 레퍼런스

* **Peltier 균등 매핑**

  ```python
  applied = MIN_ON + R((raw-1) * (100 - MIN_ON) / 99) if raw>0 else 0
  ```
* **Peltier 온도 가중 규칙 요약**

  * `delta_t<0` & `base==MIN_ON` → **0 강제**
  * `delta_t<0` → `mix(base, 50, w_cold)`
  * `delta_t>0` → `mix(base, 100, w_hot)`
  * `base==0`   → 0 유지
* **Servo 전처리 핵심**

  * 길이4 강제 → `float()` 변환 → 채널별 0..MAX 클램프 → (옵션) 반올림 → 상태 보관
  * 내부 `60-θ` **반전 금지**(드라이버가 수행)

---

## 버튼 기반 제어(SoftKill/LED/Power)

> 이 섹션은 새로 추가되었습니다. 물리 SoftKill 버튼과 대시보드의 LED 상태 버튼으로 Pi/Arduino를 제어하는 절차와 코드 흐름을 요약합니다.

1) SoftKill (물리 버튼)
* **GPIO 핀/극성**:
   `GPIO_KILL_PIN`, `GPIO_KILL_ACTIVE_LOW`
   `GPIO_LED_RED_PIN`, `GPIO_LED_GREEN_PIN`, `GPIO_LED_ACTIVE_HIGH`

* **구현 위치**: `src/controls/softkill.py` + `src/main.py` 의 `_softkill_init()`/콜백

* **상태 전환 동작**
    * **OFF 진입(killed=True)**
        1. 펠티어 듀티 0, EN LOW
        2. 팬 0, LED는 `OFF` 강제
        3. **서보 OE Enable** → `home_all()` → **OE Disable**
    * **ON 복귀(killed=False)**
        1. BTS7960 정방향 Enable
        2. **서보 OE Enable** → `home_all()`
        3. 최신 LED 매핑(TSV) 재적용
    * **브로커 통지**: `control/hvac/{id}/power_actuator` 로 `{"power":"on|off"}` 발행

2) **LED 상태 버튼(대시보드)**
* **토픽**: `control/hvac/{HVAC_ID}/tsv`
    페이로드: `{"tsv":[v1,v2,v3,v4]}` (`-3..+3` 범주)
* **매핑 규칙**: `LedService.preprocess()`로 `['B'|'W'|'R']*4` 생성
    → `LED_TSV_ORDER`로 입력 슬롯 정렬
    → `LED_HW_ORDER`로 물리 채널 정렬

* **SoftKill 연동**: killed=True 시 `OFF` 강제, 복귀 시 최신 색상으로 복원

---

## **CLI (NO-MQTT) — main 로직 직접 시험**

> 새로운 개발용 도구입니다. MQTT 브로커 없이 main.py의 동일 로직을 그대로 태워 하드웨어를 시험합니다.

* **파일**: `src/cli_actuator_test.py` 
* 작동 원리
    * `main._driver_init()`를 그대로 호출하여 **하드웨어 초기화**
    * `DummyPublisher`로 `status/.../all` 출력은 콘솔에 표시
    * 사용자의 CLI 명령을 `main.on_mqtt("<.../value|tsv|power_server>", payload)`로 **직접 투입**

* **명령 예**
```nginx
peltier 45
fans 30 40 0 0 60
servo-both 0 0 0 0  10 20 30 40
tsv 1.0 0 -1.2 0.6
power off
dump
q
```

* 종료 시 main과 동일한 안전 종료(SoftKill=OFF 통지, 펠티어/팬/LED 정리)

---

## **변경 로그**

**2025-08-18**

* **LED 상태 버튼**(대시보드 → `.../tsv`) 추가, **입력/출력 채널 리맵**(`LED_TSV_ORDER`, `LED_HW_ORDER`) 반영
* **SoftKill(물리 버튼)** 도입: 버튼/LED GPIO 연계, **OFF/ON 전환 시 서보** `home_all()` **시퀀스** 포함
* **서보 중복 호출 억제**: `SERVO_EPS_DEG` 도입 — 이전 상태와 차이가 미세할 경우 드라이버 호출 생략
* **PCA9685 /OE 제어** 옵션 추가: `PCA9685_OE_GPIO`, `PCA9685_OE_ACTIVE_LOW`, `PCA9685_OE_DEFAULT_ENABLE`
* **CLI (NO-MQTT)** 추가: `src/cli_actuator_test.py` — main 로직을 브로커 없이 직접 시험
* **상태 발행 모드**: `STATUS_USE_APPLIED`로 applied/raw 선택 가능
* **에너지 추정**: `utils/energy_meter.py` 기반 **30초 Wh** 추정치 상태에 포함
* 디렉토리/파일 구조 업데이트 반영 (`src/controls`, `src/utils` 등)