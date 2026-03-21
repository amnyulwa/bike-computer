"""
Bike Computer — GPS Reader
Runs in a background thread; reads NMEA sentences from the Adafruit Ultimate
GPS HAT via UART and updates the shared RideState.

Parses:
  GPRMC / GNRMC — speed (knots), lat, lon, course-over-ground, date/time
  GPGGA / GNGGA — altitude, fix quality, satellite count
"""

import math
import threading
import time
import logging

import pynmea2
import serial

import config

log = logging.getLogger(__name__)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in kilometres."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class GPSThread(threading.Thread):
    """
    Background thread that continuously reads the GPS UART and updates
    the shared RideState object.

    The thread is a daemon so it exits automatically when the main program ends.
    """

    # Minimum distance change (km) to count as movement and accumulate distance.
    # Filters out GPS drift while stationary.
    MIN_MOVE_KM = 0.003   # ~3 metres

    def __init__(self, state):
        super().__init__(daemon=True, name="GPSThread")
        self._state = state
        self._stop_event = threading.Event()

        # Internal tracking — not exposed until we have a valid fix
        self._prev_lat: float | None = None
        self._prev_lon: float | None = None
        self._start_time: float | None = None   # monotonic clock at first fix

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                self._run_serial()
            except serial.SerialException as exc:
                log.error("GPS serial error: %s — retrying in 3 s", exc)
                time.sleep(3)
            except Exception as exc:
                log.exception("Unexpected GPS error: %s", exc)
                time.sleep(3)

    def _run_serial(self):
        with serial.Serial(
            config.GPS_UART_PORT,
            config.GPS_BAUD_RATE,
            timeout=2,
        ) as port:
            log.info("GPS serial open on %s", config.GPS_UART_PORT)
            while not self._stop_event.is_set():
                raw = port.readline()
                if not raw:
                    continue
                try:
                    line = raw.decode("ascii", errors="replace").strip()
                    self._parse(line)
                except Exception:
                    pass   # malformed sentence — skip

    def _parse(self, sentence: str):
        try:
            msg = pynmea2.parse(sentence)
        except pynmea2.ParseError:
            return

        if isinstance(msg, (pynmea2.types.talker.RMC, pynmea2.types.talker.GGA)):
            self._handle_rmc_or_gga(msg)

    def _handle_rmc_or_gga(self, msg):
        with self._state.lock:
            if isinstance(msg, pynmea2.types.talker.RMC):
                # RMC: recommended minimum — speed, lat/lon, heading, date/time
                fix_ok = (
                    hasattr(msg, "status")
                    and msg.status == "A"   # A = active / valid
                    and msg.latitude is not None
                    and msg.longitude is not None
                )
                self._state.gps_fix = fix_ok

                if fix_ok:
                    lat = float(msg.latitude)
                    lon = float(msg.longitude)
                    self._state.gps_lat = lat
                    self._state.gps_lon = lon

                    # Speed: knots → km/h
                    if msg.spd_over_grnd is not None:
                        self._state.speed_kmh = float(msg.spd_over_grnd) * 1.852

                    # Course-over-ground as heading fallback
                    if msg.true_course is not None:
                        try:
                            cog = float(msg.true_course)
                            # Only override mag heading if ICM20948 hasn't provided one
                            if self._state.heading_deg is None:
                                self._state.heading_deg = cog
                        except (ValueError, TypeError):
                            pass

                    # Start elapsed timer on first fix
                    if self._start_time is None:
                        self._start_time = time.monotonic()
                    self._state.elapsed_sec = int(time.monotonic() - self._start_time)

                    # Accumulate distance
                    if self._prev_lat is not None:
                        d = _haversine_km(self._prev_lat, self._prev_lon, lat, lon)
                        if d >= self.MIN_MOVE_KM:
                            self._state.distance_km += d
                            self._prev_lat = lat
                            self._prev_lon = lon
                    else:
                        self._prev_lat = lat
                        self._prev_lon = lon

            elif isinstance(msg, pynmea2.types.talker.GGA):
                # GGA: fix quality and altitude
                fix_quality = int(msg.gps_qual) if msg.gps_qual else 0
                self._state.gps_fix = fix_quality > 0
                if fix_quality > 0 and msg.altitude is not None:
                    self._state.gps_altitude_m = float(msg.altitude)
                if msg.num_sats is not None:
                    try:
                        self._state.gps_satellites = int(msg.num_sats)
                    except (ValueError, TypeError):
                        pass
