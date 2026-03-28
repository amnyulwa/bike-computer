"""
Bike Computer — I2C Sensor Thread
Polls the Waveshare Sense HAT (B) sensors at SENSOR_POLL_HZ and updates
the shared RideState.

Sensors on the Sense HAT (B) — SKU HIPI73-1:
  SHTC3    (0x70) — temperature, relative humidity
  LPS22HB  (0x5C) — barometric pressure → altitude
  ICM20948 (0x68) — 9-DOF IMU; magnetometer used for compass heading
  TCS34725 (0x29) — colour sensor; clear channel used for ambient lux

Barometric altitude formula:
    altitude = 44330 * (1 - (P / P0)^0.1903)
where P0 = config.SEA_LEVEL_HPA (update with local QNH for best accuracy).

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
import adafruit_shtc3
import adafruit_tcs34725
from adafruit_lps2x import LPS22

import config

log = logging.getLogger(__name__)

# ── ICM20948 register map (relevant subset) ───────────────────────────────────
_ICM_BANK_SEL            = 0x7F
_ICM_PWR_MGMT_1          = 0x06
_ICM_USER_CTRL           = 0x03
_ICM_I2C_MST_CTRL        = 0x01
_ICM_MAG_HXL             = 0x11
_ICM_I2C_MST_EN          = 0x20
_AK09916_ADDR            = 0x0C
_AK09916_CNTL2           = 0x31
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

    def _write(self, reg: int, value: int):
        self._bus.write_i2c_block_data(self._addr, reg, [value])

    def _read(self, reg: int, length: int = 1) -> bytes:
        return bytes(self._bus.read_i2c_block_data(self._addr, reg, length))

    def _bank(self, bank: int):
        self._write(_ICM_BANK_SEL, bank << 4)

    def _init(self):
        self._bank(0)
        self._write(_ICM_PWR_MGMT_1, 0x01)   # wake, auto-select clock
        time.sleep(0.1)
        ctrl = self._read(_ICM_USER_CTRL)[0]
        self._write(_ICM_USER_CTRL, ctrl | _ICM_I2C_MST_EN)
        self._bank(3)
        self._write(_ICM_I2C_MST_CTRL, 0x07)  # 400 kHz I2C master clock
        self._mag_write(_AK09916_CNTL2, _AK09916_MODE_CONT_100HZ)
        time.sleep(0.01)
        log.info("ICM20948 initialised, magnetometer active")

    def _mag_write(self, reg: int, value: int):
        self._bank(3)
        self._write(0x03, _AK09916_ADDR & ~0x80)
        self._write(0x04, reg)
        self._write(0x06, value)
        self._write(0x05, 0x81)

    def _mag_read(self, reg: int, length: int) -> bytes:
        self._bank(3)
        self._write(0x03, _AK09916_ADDR | 0x80)
        self._write(0x04, reg)
        self._write(0x05, 0x80 | length)
        time.sleep(0.01)
        self._bank(0)
        return self._read(0x3B, length)

    def heading_degrees(self) -> float:
        """
        Magnetic heading in degrees (0 = North, 90 = East).
        NOTE: raw magnetic heading — add local magnetic declination if needed.
        Update config.MAG_OFFSET_X/Y after performing a figure-8 calibration.
        """
        data = self._mag_read(_ICM_MAG_HXL, 6)
        x, y, z = struct.unpack_from("<hhh", data)
        mx = x * 0.15 - config.MAG_OFFSET_X
        my = y * 0.15 - config.MAG_OFFSET_Y
        heading = math.degrees(math.atan2(my, mx))
        return heading + 360.0 if heading < 0 else heading


class SensorThread(threading.Thread):
    """
    Background thread that polls all I2C sensors on the Sense HAT (B) and
    writes results into the shared RideState.
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
        i2c = board.I2C()
        self._bus = smbus2.SMBus(1)

        # SHTC3 — temperature + humidity (primary)
        self._shtc3 = adafruit_shtc3.SHTC3(i2c)
        log.info("SHTC3 initialised at 0x%02x", config.SHTC3_I2C_ADDR)

        # LPS22HB — barometric pressure → altitude
        self._lps = LPS22(i2c, address=config.LPS22HB_I2C_ADDR)
        log.info("LPS22HB initialised at 0x%02x", config.LPS22HB_I2C_ADDR)

        # ICM20948 — 9-DOF IMU for compass heading
        try:
            self._icm = ICM20948(self._bus, address=config.ICM20948_I2C_ADDR)
        except Exception as exc:
            log.warning("ICM20948 not found (%s) — heading will use GPS COG", exc)
            self._icm = None

        # TCS34725 — colour sensor, clear channel used for lux
        try:
            self._tcs = adafruit_tcs34725.TCS34725(i2c)
            self._tcs.integration_time = 50   # ms
            self._tcs.gain = 4
            log.info("TCS34725 initialised at 0x%02x", config.TCS34725_I2C_ADDR)
        except Exception as exc:
            log.warning("TCS34725 not found (%s)", exc)
            self._tcs = None

        log.info("I2C sensor init complete")

    def _poll_loop(self, interval: float):
        while not self._stop_event.is_set():
            t_start = time.monotonic()
            self._read_all()
            elapsed = time.monotonic() - t_start
            time.sleep(max(0.0, interval - elapsed))

    def _read_all(self):
        # ── SHTC3: temperature + humidity ─────────────────────────────────────
        try:
            temp_c, humidity = self._shtc3.measurements
        except Exception as exc:
            log.debug("SHTC3 read error: %s", exc)
            return

        # ── LPS22HB: pressure → barometric altitude ───────────────────────────
        try:
            pressure = self._lps.pressure
        except Exception as exc:
            log.debug("LPS22HB read error: %s", exc)
            return

        baro_alt = _baro_altitude(pressure)

        # ── ICM20948: compass heading (optional) ──────────────────────────────
        heading = None
        if self._icm is not None:
            try:
                heading = self._icm.heading_degrees()
            except Exception as exc:
                log.debug("ICM20948 read error: %s", exc)

        # ── TCS34725: ambient lux from clear channel (optional) ───────────────
        lux = 0.0
        if self._tcs is not None:
            try:
                lux = self._tcs.lux or 0.0
            except Exception as exc:
                log.debug("TCS34725 read error: %s", exc)

        with self._state.lock:
            self._state.temperature_c   = temp_c
            self._state.humidity_pct    = humidity
            self._state.pressure_hpa    = pressure
            self._state.baro_altitude_m = baro_alt
            self._state.lux             = lux
            if heading is not None:
                self._state.heading_deg = heading
