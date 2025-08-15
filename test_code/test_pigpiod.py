import pigpio, time
pi = pigpio.pi()
print("OK" if pi.connected else "FAIL")   # OK 가 출력되어야 함
pi.stop()
