"""
Bike Computer — Configuration
All hardware pin numbers, I2C addresses, and tunable constants in one place.
"""

# ── TFT Display (ILI9341) ────────────────────────────────────────────────────
TFT_CS_PIN  = 8    # GPIO8  — SPI0 CE0
TFT_DC_PIN  = 25   # GPIO25 — Data/Command
TFT_RST_PIN = 27   # GPIO27 — Reset
TFT_WIDTH   = 320
TFT_HEIGHT  = 240
TFT_ROTATION = 90  # landscape

# ── Touchscreen (XPT2046 on same SPI bus) ────────────────────────────────────
TOUCH_CS_PIN  = 7   # GPIO7  — SPI0 CE1
TOUCH_IRQ_PIN = 17  # GPIO17 — interrupt (active low)

# ── GPS UART ──────────────────────────────────────────────────────────────────
GPS_UART_PORT = "/dev/serial0"
GPS_BAUD_RATE = 9600

# ── I2C Sensor Addresses ──────────────────────────────────────────────────────
BME280_I2C_ADDR   = 0x76   # Waveshare HAT — temp / humidity / pressure
LPS22HB_I2C_ADDR  = 0x5C   # Waveshare HAT — barometric pressure (backup)
ICM20948_I2C_ADDR = 0x68   # Waveshare HAT — 9-DOF IMU (accel/gyro/mag)
SGP40_I2C_ADDR    = 0x59   # Waveshare HAT — VOC gas sensor

# ── Barometric altitude reference ─────────────────────────────────────────────
# Standard atmosphere sea-level pressure in hPa.
# For better accuracy, update this to your local QNH from a weather service.
SEA_LEVEL_HPA = 1013.25

# ── Timing ────────────────────────────────────────────────────────────────────
SENSOR_POLL_HZ   = 1    # how often to read I2C sensors (Hz)
DISPLAY_FPS      = 2    # display refresh rate (frames/sec)
LOG_INTERVAL_SEC = 5    # GPX trackpoint write interval (seconds)

# ── Logging ───────────────────────────────────────────────────────────────────
import os
RIDES_DIR = os.path.expanduser("~/rides")

# ── Units ─────────────────────────────────────────────────────────────────────
# "metric"  → km/h, km, °C
# "imperial" → mph, miles, °F
DEFAULT_UNITS = "metric"

# ── ICM20948 magnetometer hard-iron calibration offsets ──────────────────────
# These are rough defaults; run a figure-8 calibration and update these values
# for accurate compass heading.  See sensors.py for details.
MAG_OFFSET_X = 0.0
MAG_OFFSET_Y = 0.0
