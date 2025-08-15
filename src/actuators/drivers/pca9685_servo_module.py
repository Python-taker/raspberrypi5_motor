"""
pca9685_servo_module.py
────────────────────────────────────────────────────────
- Raspberry Pi 5 + Adafruit PCA9685 기반 서보모터 제어 모듈
- 실측 펄스/각도 보간표를 이용해 각도 ↔ 펄스를 상호 변환
- 안전 이동(safe_corrective_move), 초기화·호밍 루틴 제공

[라즈베리파이 5 핀 매핑(프로젝트 확정안)]
- I²C Bus: /dev/i2c-1 (강제 사용)
- SDA = GPIO2 (핀 3)
- SCL = GPIO3 (핀 5)
- PCA9685 VCC(논리) = 3.3V (핀 1)
- PCA9685 V+ (서보 전원) = 외부 5V 5A  ※ 본 모듈에서는 V+ 스위칭/측정 안 함
- (참고) 기본 I²C 주소는 코드에서 0x60으로 초기화, 보드 점퍼 설정에 따라 조정 가능

!! 주의 사항 !!
1. I²C 인터페이스 활성화 필요 (`raspi-config` → Interface → I2C)
2. Adafruit_PCA9685, Adafruit_GPIO, scipy 가상-환경(or 시스템)에 설치
3. pulse_values / actual_angles 은 환경에 따라 교정 가능

📌 호출 관계
- 별도 스크립트에서 `initialize_servo_system()` 호출 후 각 함수 사용
- 본 파일 단독 실행 시 아무 동작도 하지 않음 (CLI 없음)

### 외부 2번 3번 문제 있음
### 내부는 각도 60 - theta로 반전 시켜주어야 함. (60도가 완전히 닫힘, 0도가 완전히 열림)
"""
# =====================================================
# 0️⃣  IMPORTS & GLOBAL CONSTANTS
# =====================================================
from Adafruit_PCA9685 import PCA9685
import Adafruit_GPIO.I2C as I2C
from scipy.interpolate import interp1d
import time
from typing import List

# ───── 실측 기반 상수 ────────────────────────────────
PWM_HOME = 150              # 0 deg 기준 펄스
MOVE_MAXIMUM_ANGLE_LIST = [60, 60, 60, 60, 80, 80, 80, 80]
MOVE_MINIMUM_PULSE = 15     # 서보가 무시하지 않는 최소 이동 펄스

# =====================================================
# 1️⃣  실측 보간 데이터 (수정 금지: 로직 의존)
# =====================================================
pulse_values = [
    150, 155, 160, 165, 170, 175, 180, 185, 190, 195,
    200, 205, 210, 215, 220, 225, 230, 235, 240, 245,
    250, 255, 260, 265, 270, 275, 280, 285, 290, 295,
    300, 305, 310, 315, 320, 325, 330, 335, 340, 345,
    350, 355, 360, 365, 370, 375, 380, 385, 390, 395,
    400, 405, 410, 415, 420, 425, 430, 435, 440, 445,
    450, 455, 460, 465, 470, 475, 480, 485, 490, 495,
    500,
]

actual_angles = [
    0, 5.5, 7, 9, 11.5, 14.5, 16, 18.5, 21, 23.5,
    26.5, 28.5, 31, 33, 35, 37.5, 40, 42.5, 44.5, 46.5,
    48.5, 50.5, 52.5, 54.5, 56.5, 58.5, 60.5, 63, 64.5, 66,
    68.5, 70, 72, 74, 76.5, 78.5, 81, 83, 85.5, 87.5,
    89.5, 91.5, 93.5, 96, 98, 100.5, 102.5, 104.5, 106.5, 108.5,
    111, 113, 115, 117, 119, 121.5, 123.5, 126, 128, 130,
    132.5, 134.5, 137, 139, 141.5, 143.5, 145.5, 147.5, 150.5, 152.5,
    154.5,
]

# 보간 함수 (numpy float64 반환)
interpolation_pulse_to_angle = interp1d(
    pulse_values, actual_angles, kind="cubic", fill_value="extrapolate"
)
interpolation_angle_to_pulse = interp1d(
    actual_angles, pulse_values, kind="cubic", fill_value="extrapolate"
)

# =====================================================
# 2️⃣  기초 변환/조회 함수
# =====================================================
def get_angle_from_pulse(pulse: int) -> float:
    """
    PWM 펄스를 실측 각도로 변환.

    Args:
        pulse (int): PWM 펄스값

    Returns:
        float: 실측 각도
    """
    return float(interpolation_pulse_to_angle(pulse))


def get_pulse_from_angle(angle: float) -> int:
    """
    실측 각도를 PWM 펄스로 변환(int).

    Args:
        angle (float): 각도

    Returns:
        int: 대응 PWM 펄스
    """
    return int(round(float(interpolation_angle_to_pulse(angle))))


# =====================================================
# 3️⃣  HW 초기화 및 상태 관리
# =====================================================
def init_pca9685(address: int = 0x60, freq: int = 50):
    """
    PCA9685 인스턴스를 초기화해 반환.

    Args:
        address (int): I²C 주소
        freq (int): PWM 주파수

    Returns:
        PCA9685: 제어 객체
    """
    I2C.get_default_bus = lambda: 1  # Pi 5 강제 버스
    pwm = PCA9685(address=address)
    pwm.set_pwm_freq(freq)
    print(f"✅ PCA9685 초기화 완료 (0x{address:X}, {freq} Hz)")
    return pwm


def init_channel_positions(num_channels: int = 8) -> List[int]:
    """
    채널별 현재 PWM 값을 저장할 리스트 초기화.

    Args:
        num_channels (int): 사용할 채널 수

    Returns:
        list[int]: 초기값 0으로 채워진 리스트
    """
    return [0] * num_channels


def initialize_servo_system(home: bool = True):
    """
    서보 시스템 전체 초기화 편의 함수.

    Args:
        home (bool): True → 모든 채널 초기 스윕 후 HOME.

    Returns:
        tuple: (pwm, channel_positions)
    """
    pwm = init_pca9685()
    channel_positions = init_channel_positions()
    if home:
        home_all_channels(pwm, channel_positions)
    return pwm, channel_positions


# =====================================================
# 4️⃣  서보 움직임 함수
# =====================================================
def get_current_pulse(channel: int, channel_positions: List[int]) -> int:
    """현재 저장된 채널 펄스 조회."""
    return channel_positions[channel]


def move_to_pulse(pwm, channel: int, channel_positions: List[int], pulse: int):
    """
    지정 펄스로 이동(0.3 s 대기).

    부작용:
        - 실제 PWM 출력
        - channel_positions 업데이트
    """
    target_pulse = int(round(pulse))
    pwm.set_pwm(channel, 0, target_pulse)
    channel_positions[channel] = target_pulse
    time.sleep(0.3)


def initialize_servo_position(pwm, channel: int, channel_positions: List[int]):
    """
    한 채널을 min↔max 왕복해 초기화(현재 위치 불명 시 사용).
    """
    minimum_pulse = PWM_HOME
    maximum_pulse = get_pulse_from_angle(MOVE_MAXIMUM_ANGLE_LIST[channel])

    move_to_pulse(pwm, channel, channel_positions, minimum_pulse)
    move_to_pulse(pwm, channel, channel_positions, maximum_pulse)
    move_to_pulse(pwm, channel, channel_positions, minimum_pulse)
    move_to_pulse(pwm, channel, channel_positions, maximum_pulse)


def go_to_home_position(pwm, channel: int, channel_positions: List[int]):
    """
    지정 채널을 HOME(150 pulse)으로 이동.
    """
    move_to_pulse(pwm, channel, channel_positions, PWM_HOME)
    print(f"🏠 채널 {channel} → HOME({PWM_HOME})")


def home_all_channels(pwm, channel_positions: List[int]):
    """
    모든 채널을 초기 스윕 + HOME.
    """
    for channel in range(len(channel_positions)):
        initialize_servo_position(pwm, channel, channel_positions)
        go_to_home_position(pwm, channel, channel_positions)


def recalibrate_home_position(
    pwm, channel: int, channel_positions: List[int], warmup_pulses: List[int] = [155, 160, 165]
):
    """
    진동형 보정을 수행해 HOME 정확도 향상.

    Args:
        warmup_pulses (list[int]): 홈 근처에서 왕복할 펄스 리스트
    """
    #initialize_servo_position(pwm, channel, channel_positions)
    go_to_home_position(pwm, channel, channel_positions)
    for pulse in warmup_pulses:
        move_to_pulse(pwm, channel, channel_positions, pulse)
    go_to_home_position(pwm, channel, channel_positions)
    print(f"🔧 채널 {channel} → HOME 재보정 완료")


# =====================================================
# 5️⃣  고급 보정 로직 (변경 금지)
# =====================================================
def find_max_distance(now_position: int, compare_position_A: int, compare_position_B: int):
    dist_A = abs(now_position - compare_position_A)
    dist_B = abs(now_position - compare_position_B)
    return -1 if dist_A > dist_B else 1


def generate_fallback_sequence(target_pulse: int, minimum_pulse: int, maximum_pulse: int) -> list:
    """
    특수 분기에 해당되지 않을 때 사용할 예비 시퀀스.
    """
    return [minimum_pulse, maximum_pulse, minimum_pulse, maximum_pulse, target_pulse]


def perform_micro_adjustment(target_pulse: int, current_pulse: int, maximum_pulse: int, move_minimum_pulse_set: int) -> list:
    """
    너무 가까운 거리일 때 서보모터가 반응하지 않는 문제를 보정하기 위한 보조 펄스 시퀀스를 생성합니다.
    반환값: [중간_보정_펄스1, 중간_보정_펄스2, ..., 최종_목표_펄스]
    """
    minimum_pulse = PWM_HOME
    adjustment_sequence = []
    offset_pulse = 10
    move_minimum_pulse = min(move_minimum_pulse_set, offset_pulse)
        
    ## 타겟과 현재가 모두 max에 가까운 경우 (기본)
    if ((abs(target_pulse - maximum_pulse) < move_minimum_pulse) and (abs(current_pulse - maximum_pulse) < move_minimum_pulse)):
        #### AND 타겟과 현재가 모두 min에도 가까운 경우
        if ((abs(target_pulse - minimum_pulse) < move_minimum_pulse) and (abs(current_pulse - minimum_pulse) < move_minimum_pulse)):
            target_direction = find_max_distance(target_pulse, minimum_pulse, maximum_pulse)
            current_direction = find_max_distance(current_pulse, minimum_pulse, maximum_pulse)
            ### 타겟과 현재가 모두 max에 더 가까운 경우 (minimum 거리가 긴 경우)
            if (target_direction == -1 and current_direction == -1):
                adjustment_sequence = [minimum_pulse, target_pulse]
            
            ### 타겟은 max에 더 가깝고 (minimum 거리가 길고), 현재는 min에 더 가까운 경우 (maximum 거리가 긴 경우)
            elif (target_direction == -1 and current_direction == 1):
                adjustment_sequence = [maximum_pulse, minimum_pulse, target_pulse]
            
            ### 타겟과 현재가 모두 min에 더 가까운 경우 (maximum 거리가 긴 경우)
            elif (target_direction == 1 and current_direction == 1):
                adjustment_sequence = [maximum_pulse, target_pulse]
            
            ### 타겟은 min에 더 가깝고 (maximum 거리가 길고), 현재는 max에 더 가까운 경우 (minimum 거리가 긴 경우)
            elif (target_direction == 1 and current_direction == -1):
                adjustment_sequence = [minimum_pulse, maximum_pulse, target_pulse]
            
            ### 모두 해당하지 않는 경우 (이는 오류에 해당할 듯)
            else:
                adjustment_sequence = generate_fallback_sequence(target_pulse, minimum_pulse, maximum_pulse)
                
        #### AND 타겟만 min에도 가까운 경우    
        elif (abs(target_pulse - minimum_pulse) < move_minimum_pulse):
            target_direction = find_max_distance(target_pulse, minimum_pulse, maximum_pulse)
            ### 타겟이 max에 더 가깝고 (minimum 거리가 길고)
            if (target_direction == -1):
                adjustment_sequence = [minimum_pulse, target_pulse]
            ### 타겟이 min에 더 가깝고 (maximum 거리가 길고)
            elif (target_direction == 1):
                adjustment_sequence = [minimum_pulse, maximum_pulse, target_pulse]
            ### 모두 해당하지 않는 경우 (이는 오류에 해당할 듯)
            else:
                adjustment_sequence = generate_fallback_sequence(target_pulse, minimum_pulse, maximum_pulse)
        
        #### AND 현재만 min에도 가까운 경우       
        elif (abs(current_pulse - minimum_pulse) < move_minimum_pulse):
            current_direction = find_max_distance(current_pulse, minimum_pulse, maximum_pulse)
            ### 현재가 max에 더 가깝고 (minimum 거리가 길고)
            if (current_direction == -1):
                adjustment_sequence = [minimum_pulse, target_pulse]
            ### 현재가 min에 더 가깝고 (maximum 거리가 길고)
            elif (current_direction == 1):
                adjustment_sequence = [maximum_pulse, minimum_pulse, target_pulse]
            ### 모두 해당하지 않는 경우 (이는 오류에 해당할 듯)
            else:
                adjustment_sequence = generate_fallback_sequence(target_pulse, minimum_pulse, maximum_pulse)
                
        #### 그 외의 경우
        else:
            adjustment_sequence = [minimum_pulse, target_pulse]
    
    ## 타겟과 현재가 모두 min에 가까운 경우 (기본)
    elif ((abs(target_pulse - minimum_pulse) < move_minimum_pulse) and (abs(current_pulse - minimum_pulse) < move_minimum_pulse)):
        #### AND 타겟만 max에도 가까운 경우
        if (abs(target_pulse - maximum_pulse) < move_minimum_pulse):
            target_direction = find_max_distance(target_pulse, minimum_pulse, maximum_pulse)
            ### 타겟이 max에 더 가깝고 (minimum 거리가 길고)
            if (target_direction == -1):
                adjustment_sequence = [maximum_pulse, minimum_pulse, target_pulse]
            ### 타겟이 min에 더 가깝고 (maximum 거리가 길고)
            elif (target_direction == 1):
                adjustment_sequence = [maximum_pulse, target_pulse]
            ### 모두 해당하지 않는 경우 (이는 오류에 해당할 듯)
            else:
                adjustment_sequence = generate_fallback_sequence(target_pulse, minimum_pulse, maximum_pulse)
        
        #### AND 현재만 max에도 가까운 경우
        elif (abs(current_pulse - maximum_pulse) < move_minimum_pulse):
            current_direction = find_max_distance(current_pulse, minimum_pulse, maximum_pulse)
            ### 현재가 max에 더 가깝고 (minimum 거리가 길고)
            if (current_direction == -1):
                adjustment_sequence = [minimum_pulse, maximum_pulse, target_pulse]
            ### 현재가 min에 더 가깝고 (maximum 거리가 길고)
            elif (current_direction == 1):
                adjustment_sequence = [maximum_pulse, target_pulse]
            ### 모두 해당하지 않는 경우 (이는 오류에 해당할 듯)
            else:
                adjustment_sequence = generate_fallback_sequence(target_pulse, minimum_pulse, maximum_pulse)
                
        #### 그 외의 경우
        else:
            adjustment_sequence = [maximum_pulse, target_pulse]
        
    ## 타겟이 max에 가까운 경우
    elif (abs(target_pulse - maximum_pulse) < move_minimum_pulse):
        #### AND 현재가 min에 가까운 경우
        if (abs(current_pulse - minimum_pulse) < move_minimum_pulse):
            adjustment_sequence = [maximum_pulse, minimum_pulse, target_pulse]
        #### 그 외의 경우
        else:
            adjustment_sequence = [minimum_pulse, target_pulse]
    
    ## 타겟이 min에 가까운 경우
    elif (abs(target_pulse - minimum_pulse) < move_minimum_pulse):
        #### AND 현재가 max에 가까운 경우
        if (abs(current_pulse - maximum_pulse) < move_minimum_pulse):
            adjustment_sequence = [minimum_pulse, maximum_pulse, target_pulse]
        #### 그 외의 경우
        else:
            adjustment_sequence = [maximum_pulse, target_pulse]
            
    ## 타겟과 현재가 모두 같은 경우
    elif (current_pulse == target_pulse):
        adjustment_sequence = [minimum_pulse, target_pulse] 
        
    ## 그 외의 경우
    else:
        adjustment_sequence = [minimum_pulse,target_pulse]
        
    return adjustment_sequence 

def safe_corrective_move(
    pwm, channel: int, channel_positions: List[int], target_angle: float, move_minimum_pulse=MOVE_MINIMUM_PULSE
):
    """
    최소 펄스 무시 현상을 피하면서 타깃 각도로 이동.

    로직:
        1) 목표 각도를 펄스로 변환·경계 클램프
        2) Δ펄스 >= MOVE_MINIMUM_PULSE → 직접 이동
        3) Δ펄스 <  ...              → perform_micro_adjustment() 시퀀스 이동
    """
    target_pulse = get_pulse_from_angle(target_angle)
    current_pulse = get_current_pulse(channel, channel_positions)
    maximum_pulse = get_pulse_from_angle(MOVE_MAXIMUM_ANGLE_LIST[channel])
    start_pulse = current_pulse

    # 경계 클램프
    if target_pulse > maximum_pulse:
        print(
            f"⚠️ CH{channel}: 목표펄스 {target_pulse} > 최대허용 {maximum_pulse} → 클램프"
        )
        target_pulse = maximum_pulse
    elif target_pulse < PWM_HOME:
        print(
            f"⚠️ CH{channel}: 목표펄스 {target_pulse} < 최소허용 {PWM_HOME} → 클램프"
        )
        target_pulse = PWM_HOME

    if target_pulse - current_pulse >= move_minimum_pulse:
        move_to_pulse(pwm, channel, channel_positions, target_pulse)

    elif current_pulse - target_pulse >= move_minimum_pulse:
        #pulse = 150 + (current_pulse - target_pulse)  # 원본 로직 유지
        #move_to_pulse(pwm, channel, channel_positions, pulse)
        recalibrate_home_position(pwm, channel, channel_positions)
        move_to_pulse(pwm, channel, channel_positions, target_pulse)

    else:
        pulse_list = perform_micro_adjustment(
            target_pulse, current_pulse, maximum_pulse, move_minimum_pulse
        )
        for pulse in pulse_list:
            if (start_pulse > pulse) and (pulse != PWM_HOME) and (pulse != maximum_pulse):
                recalibrate_home_position(pwm, channel, channel_positions)
            move_to_pulse(pwm, channel, channel_positions, pulse)

# =====================================================
# 6️⃣  MAIN 인터페이스 (메인에서 바로 쓰는 래퍼)
#   - 내부 슬롯(4ch)은 각도 반전: target = 60 - theta
#   - 외부 슬롯(4ch)은 입력 각도를 그대로 적용
#   - 채널 매핑: 0~3=internal, 4~7=external
# =====================================================

INTERNAL_CHANNELS = [0, 1, 2, 3]
EXTERNAL_CHANNELS = [4, 5, 6, 7]

def _clamp_angle_for_channel(ch: int, angle: float) -> float:
    """채널별 최대 각도 테이블에 맞춰 0..max 범위로 클램프."""
    max_deg = float(MOVE_MAXIMUM_ANGLE_LIST[ch])
    if angle < 0.0: return 0.0
    if angle > max_deg: return max_deg
    return angle

def _apply_angle(pwm, channel: int, positions: list, angle_deg: float) -> None:
    """안전 보정 이동(safe_corrective_move)로 1채널 적용."""
    angle_deg = _clamp_angle_for_channel(channel, angle_deg)
    safe_corrective_move(pwm, channel, positions, angle_deg)

import threading

class ServoAPI:
    """
    PCA9685 서보 8채널 제어의 얇은 고수준 래퍼.
    - 내부 4ch: 입력 각도 θ → (60 - θ)로 반전 적용
    - 외부 4ch: 입력 각도 그대로 적용
    """
    def __init__(self, address: int = 0x60, freq: int = 50, home: bool = True):
        """
        Args:
            address: I²C 주소(기본 0x60)
            freq: PWM 주파수(기본 50Hz)
            home: True면 초기 스윕+HOME 수행
        """
        # initialize_servo_system(home=home) 대신 주소/주파수 인자 반영
        self.pwm = init_pca9685(address=address, freq=freq)
        self.positions = init_channel_positions()
        if home:
            home_all_channels(self.pwm, self.positions)

        # 멀티스레드 제어 대비
        self._lock = threading.Lock()

    # -------------------------------------------------
    # 내부/외부 일괄 적용 (각각 4개)
    # -------------------------------------------------
    def set_internal(self, internal_angles: list[float]) -> None:
        """
        내부 슬롯 4개(채널 0~3) 적용.
        - 입력 각도 θ(0~60)를 (60-θ)로 반전 후 적용
        """
        if not isinstance(internal_angles, (list, tuple)) or len(internal_angles) != 4:
            raise ValueError("internal_angles는 길이 4의 리스트여야 합니다.")
        with self._lock:
            for i, ch in enumerate(INTERNAL_CHANNELS):
                theta_in = float(internal_angles[i])
                theta_target = 60.0 - theta_in          # ★ 반전 규칙
                _apply_angle(self.pwm, ch, self.positions, theta_target)

    def set_external(self, external_angles: list[float]) -> None:
        """
        외부 슬롯 4개(채널 4~7) 적용.
        - 입력 각도를 있는 그대로 적용
        """
        if not isinstance(external_angles, (list, tuple)) or len(external_angles) != 4:
            raise ValueError("external_angles는 길이 4의 리스트여야 합니다.")
        with self._lock:
            for i, ch in enumerate(EXTERNAL_CHANNELS):
                theta_target = float(external_angles[i])
                _apply_angle(self.pwm, ch, self.positions, theta_target)

    def set_both(self, internal_angles: list[float], external_angles: list[float]) -> None:
        """내부 4개 + 외부 4개를 한 번에 적용 (단일 락으로 일관성 보장)."""
        if (not isinstance(internal_angles, (list, tuple)) or len(internal_angles) != 4 or
            not isinstance(external_angles, (list, tuple)) or len(external_angles) != 4):
            raise ValueError("internal_angles/external_angles는 각각 길이 4의 리스트여야 합니다.")
        with self._lock:
            for i, ch in enumerate(INTERNAL_CHANNELS):
                theta_in = float(internal_angles[i])
                theta_target = 60.0 - theta_in
                _apply_angle(self.pwm, ch, self.positions, theta_target)
            for i, ch in enumerate(EXTERNAL_CHANNELS):
                theta_target = float(external_angles[i])
                _apply_angle(self.pwm, ch, self.positions, theta_target)

    # -------------------------------------------------
    # 편의 함수
    # -------------------------------------------------
    def home_channel(self, ch: int) -> None:
        with self._lock:
            go_to_home_position(self.pwm, ch, self.positions)

    def home_all(self) -> None:
        with self._lock:
            home_all_channels(self.pwm, self.positions)

    def close(self) -> None:
        """PCA9685는 별도 close가 없어도 되지만, 필요 시 확장용."""
        try:
            pass  # Adafruit_PCA9685는 명시적 종료 API 없음
        except Exception:
            pass

# =====================================================
# 7️⃣ CLI ─ 단독 테스트용 진입점
# -----------------------------------------------------
# • initialize_servo_system() 으로 HW / 상태 초기화
# • 숫자 메뉴로 각 기능 단일 테스트
#   1) go_to_home_position
#   2) home_all_channels
#   3) recalibrate_home_position
#   4) safe_corrective_move
# • q / Q / Ctrl-C 로 종료
# =====================================================

def _prompt_int(msg: str, lo: int, hi: int) -> int:
    """범위 내 정수를 받을 때까지 반복 입력."""
    while True:
        try:
            val = int(input(msg).strip())
            if lo <= val <= hi:
                return val
            print(f"⚠️  {lo}~{hi} 사이 정수만 입력하세요.")
        except ValueError:
            print("⚠️  숫자를 입력하세요.")


def _prompt_float(msg: str, lo: float, hi: float) -> float:
    """범위 내 실수를 받을 때까지 반복 입력."""
    while True:
        try:
            val = float(input(msg).strip())
            if lo <= val <= hi:
                return val
            print(f"⚠️  {lo}~{hi} 사이 숫자만 입력하세요.")
        except ValueError:
            print("⚠️  숫자를 입력하세요.")


def _print_menu() -> None:
    print(
        "\n==== Servo Test Menu ====\n"
        " 1) go_to_home_position\n"
        " 2) home_all_channels\n"
        " 3) recalibrate_home_position\n"
        " 4) safe_corrective_move\n"
        " q) quit (또는 Ctrl+C)\n"
        "========================="
    )


def main() -> None:
    """
    CLI 진입점.
    initialize_servo_system() 이후 사용자 입력으로 각 함수 단독 테스트.
    """
    pwm, channel_positions = initialize_servo_system(home=True)

    try:
        while True:
            _print_menu()
            choice = input("메뉴 선택 > ").strip().lower()

            if choice in ("q", "quit", "exit"):
                print("👋 종료합니다.")
                break

            # ① go_to_home_position
            if choice == "1":
                ch = _prompt_int("채널(0~7) > ", 0, 7)
                go_to_home_position(pwm, ch, channel_positions)

            # ② home_all_channels
            elif choice == "2":
                home_all_channels(pwm, channel_positions)

            # ③ recalibrate_home_position
            elif choice == "3":
                ch = _prompt_int("채널(0~7) > ", 0, 7)
                # 기본 warm-up 시퀀스 사용
                recalibrate_home_position(pwm, ch, channel_positions)

            # ④ safe_corrective_move
            elif choice == "4":
                ch = _prompt_int("채널(0~7) > ", 0, 7)
                ang = _prompt_float("목표 각도(0~105) > ", 0.0, 105.0)
                safe_corrective_move(pwm, ch, channel_positions, ang)

            else:
                print("⚠️  메뉴 번호를 다시 선택하세요.")

    except KeyboardInterrupt:
        print("\n👋 Ctrl+C 감지, 프로그램 종료.")


# ── 단독 실행 시 main()만 호출 ────────────────────────
if __name__ == "__main__":
    main()

# =====================================================
# 8️⃣  예시 사용 (main.py에서)
# -----------------------------------------------------
# from drivers.pca9685_servo_module import ServoAPI
# servo = ServoAPI(home=True)
# servo.set_internal([0, 10, 20, 30])     # 내부: 실제로는 [60,50,40,30]로 적용
# servo.set_external([15, 25, 35, 45])    # 외부: 그대로 적용
# servo.set_both([0,0,0,0], [0,0,0,0])    # 동시 적용
# servo.home_all()
# =====================================================