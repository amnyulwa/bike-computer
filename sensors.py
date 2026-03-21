"""
Bike Computer — I2C Sensor Thread
Polls the Waveshare Environment Sensor HAT sensors at SENSOR_POLL_HZ and
updates the shared RideState.

Sensors on the HAT:
  BME280   (0x76) — temperature, relative humidity, barometric pressure
  LPS22HB  (0x5C) — barometric pressure (backup / cross-check)
  ICM20948 (0x68) — 9-DOF IMU; we use the magnetometer for compass heading
  SGP40    (0x59) — raw VOC gas sensor (Sensirion algorithm gives VOC index)

Barometric altitude is derived from pressure using the international
barometric formula:
    altitude = 44330 * (1 - (P / P0)^0.1903)
where P0 is sea-level pressure from config.SEA_LEVEL_HPA.

ICM20948 compass note:
  Raw magnetometer readings include hard-iron distortion from nearby metals.
  For accurate heading, perform a figure-8 calibration and update
  config.MAG_OFFSET_X / MAG_OFFSET_Y with the min/max averages.
"""

import math
import struct
import threading
import time
import logging

import board
import busio
import adafruit_bme280.basic as adafruit_bme280
import adafruit_sgp40
from adafruit_lps2x import LPS22

import config

log = logging.getLogger(__name__)

# ── ICM20948 register map (relevant subset) ───────────────────────────────────
_ICM_BANK_SEL        = 0x7F
_ICM_WHO_AM_I        = 0x00   # bank 0
_ICM_PWR_MGMT_1      = 0x06   # bank 0
_ICM_USER_CTRL       = 0x03   # bank 0
_ICM_I2C_MST_CTRL    = 0x01   # bank 3
_ICM_MAG_CTRL2       = 0x31   # AK09916 magnetometer control
_ICM_MAG_ST1         = 0x10   # AK09916 status 1
_ICM_MAG_HXL         = 0x11   # AK09916 X low byte
_ICM_I2C_MST_EN      = 0x20
_AK09916_ADDR        = 0x0C   # magnetometer slave address
_AK09916_CNTL2       = 0x31
_AK09916_MODE_CONT_100HZ = 0x08


def _baro_altitude(pressure_hpa: float) -> float:
    """Convert pressure (hPa) to altitude (m) above sea level."""
    return 44330.0 * (1.0 - (pressure_hpa / config.SEA_LEVEL_HPA) ** 0.1903)


class ICM20948:
    """
    Minimal smbus2-based driver for the ICM20948 IMU.
    Only the magnetometer (AK09916) path is used here for compass heading.
    """

    def __init__(self, i2c_bus, address=config.ICM20948_I2C_ADDR):
        self._bus = i2c_bus
        self._addr = address
        self._init()

    # ── low-level helpers ──────────────────────────────────────────────────────

    def _write(self, reg: int, value: int):
        self._bus.write_i2c_block_data(self._addr, reg, [value])

    def _read(self, reg: int, length: int = 1) -> bytes:
        return bytes(self._bus.read_i2c_block_data(self._addr, reg, length))

    def _bank(self, bank: int):
        self._write(_ICM_BANK_SEL, bank << 4)

    # ── initialisation ─────────────────────────────────────────────────────────

    def _init(self):
        self._bank(0)
        # Wake chip, auto-select clock
        self._write(_ICM_PWR_MGMT_1, 0x01)
        time.sleep(0.1)

        # Enable I2C master so we can talk to the AK09916 magnetometer
        self._bank(0)
        ctrl = self._read(_ICM_USER_CTRL)[0]
        self._write(_ICM_USER_CTRL, ctrl | _ICM_I2C_MST_EN)

        self._bank(3)
        self._write(_ICM_I2C_MST_CTRL, 0x07)  # 400 kHz I2C master clock

        # Configure AK09916 for continuous 100 Hz measurement
        self._mag_write(_AK09916_CNTL2, _AK09916_MODE_CONT_100HZ)
        time.sleep(0.01)
        log.info("ICM20948 initialised, magnetometer active")

    def _mag_write(self, reg: int, value: int):
        """Write to the AK09916 magnetometer via ICM20948 I2C master."""
        self._bank(3)
        self._write(0x03, _AK09916_ADDR & ~0x80)  # SLV0_ADDR write
        self._write(0x04, reg)                      # SLV0_REG
        self._write(0x06, value)                    # SLV0_DO
        self._write(0x05, 0x81)                     # SLV0_CTRL: enable, 1 byte

    def _mag_read(self, reg: int, length: int) -> bytes:
        """Read from AK09916 via ICM20948 I2C master."""
        self._bank(3)
        self._write(0x03, _AK09916_ADDR | 0x80)    # SLV0_ADDR read
        self._write(0x04, reg)                       # SLV0_REG
        self._write(0x05, 0x80 | length)             # SLV0_CTRL: enable, N bytes
        time.sleep(0.01)
        self._bank(0)
        return self._read(0x3B + 0 * 8, length)     # EXT_SLV_SENS_DATA_00

    # ── public API ─────────────────────────────────────────────────────────────

    def read_magnetometer(self) -> tuple[float, float, float]:
        """
        Return (x, y, z) magnetometer readings in µT.
        The AK09916 sensitivity is 0.15 µT/LSB.
        """
        data = self._mag_read(_ICM_MAG_HXL, 6)
        x, y, z = struct.unpack_from("<hhh", data)
        scale = 0.15
        return x * scale, y * scale, z * scale

    def heading_degrees(self) -> float:
        """
        Magnetic heading in degrees (0 = North, 90 = East).
        Apply hard-iron offsets from config before computing atan2.

        NOTE: This is the raw magnetic heading, not true north.
        Magnetic declination correction is not applied here — add your local
        declination to the result if needed.
        """
        mx, my, _ = self.read_magnetometer()
        mx -= config.MAG_OFFSET_X
        my -= config.MAG_OFFSET_Y
        heading = math.degrees(math.atan2(my, mx))
        if heading < 0:
            heading += 360.0
        return heading


class SensorThread(threading.Thread):
    """
    Background thread that polls all I2C sensors on the Waveshare HAT and
    writes the results into the shared RideState.
    """

    def __init__(self, state):
        super().__init__(daemon=True, name="SensorThread")
        self._state = state
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        interval = 1.0 / config.SENSOR_POLL_HZ
        while not self._stop_event.is_set():
            try:
                self._init_sensors()
                self._poll_loop(interval)
            except Exception as exc:
                log.error("Sensor init/poll error: %s — retrying in 5 s", exc)
                time.sleep(5)

    def _init_sensors(self):
        import smbus2
        self._i2c = board.I2C()
        self._bus = smbus2.SMBus(1)

        self._bme = adafruit_bme280.Adafruit_BME280_I2C(
            self._i2c, address=config.BME280_I2C_ADDR
        )
        self._bme.sea_level_pressure = config.SEA_LEVEL_HPA

        try:
            self._lps = LPS22(self._i2c, address=config.LPS22HB_I2C_ADDR)
        except Exception:
            log.warning("LPS22HB not found — using BME280 pressure only")
            self._lps = None

        self._sgp = adafruit_sgp40.SGP40(self._i2c)

        try:
            self._icm = ICM20948(self._bus, address=config.ICM20948_I2C_ADDR)
        except Exception:
            log.warning("ICM20948 not found — heading will use GPS COG")
            self._icm = None

        log.info("I2C sensors initialised")

    def _poll_loop(self, interval: float):
        while not self._stop_event.is_set():
            t_start = time.monotonic()
            self._read_all()
            elapsed = time.monotonic() - t_start
            time.sleep(max(0.0, interval - elapsed))

    def _read_all(self):
        try:
            temp_c    = self._bme.temperature
            humidity  = self._bme.relative_humidity
            pressure  = self._bme.pressure          # hPa
        except Exception as exc:
            log.debug("BME280 read error: %s", exc)
            return

        # Prefer LPS22HB for pressure if available (slightly more accurate)
        if self._lps is not None:
            try:
                pressure = self._lps.pressure
            except Exception:
                pass

        baro_alt = _baro_altitude(pressure)

        # SGP40 VOC index (requires temperature + humidity compensation)
        voc_raw = 0
        try:
            voc_raw = self._sgp.measure_raw(
                temperature=temp_c, relative_humidity=humidity
            )
        except Exception as exc:
            log.debug("SGP40 read error: %s", exc)

        # ICM20948 compass heading
        heading = None
        if self._icm is not None:
            try:
                heading = self._icm.heading_degrees()
            except Exception as exc:
                log.debug("ICM20948 read error: %s", exc)

        with self._state.lock:
            self._state.temperature_c  = temp_c
            self._state.humidity_pct   = humidity
            self._state.pressure_hpa   = pressure
            self._state.baro_altitude_m = baro_alt
            self._state.voc_raw        = voc_raw
            if heading is not None:
                self._state.heading_deg = heading
