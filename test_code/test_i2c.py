# test_pca9685_probe.py
import os
from Adafruit_GPIO import I2C as _I2C
_I2C.get_default_bus = lambda: int(os.getenv("I2C_BUS", "1"))
from Adafruit_PCA9685 import PCA9685
addr = int(os.getenv("PCA9685_ADDR", "0x40"), 16)
pwm = PCA9685(address=addr)
print("OK:", hex(addr))
