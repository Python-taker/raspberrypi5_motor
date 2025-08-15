# uart_loopback_interactive.py
import serial, time

PORT = "/dev/serial0"
BAUD = 115200   # 기본 UART 속도

ser = serial.Serial(PORT, BAUD, timeout=0.8)
time.sleep(0.2)
ser.reset_input_buffer()

print("Loopback 모드: TXD0(GPIO14, pin8) ↔ RXD0(GPIO15, pin10) 연결되어 있어야 합니다.")
print("입력한 텍스트가 그대로 RX로 돌아오면 정상. 종료: q")
try:
    while True:
        s = input("> ")
        if s.lower() in ("q", "quit", "exit"):
            break
        ser.write((s + "\n").encode())
        line = ser.readline().decode(errors="ignore").rstrip("\r\n")
        print("RX:", line if line else "(timeout)")
finally:
    ser.close()
    print("⛔ 종료")
