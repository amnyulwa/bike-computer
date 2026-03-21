"""
Bike Computer — GPX Data Logger

Writes ride data to a GPX 1.1 file under ~/rides/.
A new file is created on the first GPS fix each run.
A trackpoint is appended every LOG_INTERVAL_SEC seconds while a fix is held.
The file is flushed after every write so data survives an unclean shutdown
(power cut, crash).

GPX output format:
  <trkpt lat="..." lon="...">
    <ele>...</ele>           — barometric altitude (metres, more accurate)
    <time>...</time>         — UTC ISO-8601
    <extensions>
      <gps_alt>...</gps_alt>
      <temp_c>...</temp_c>
      <humidity>...</humidity>
      <voc_raw>...</voc_raw>
      <heading>...</heading>
      <speed_kmh>...</speed_kmh>
    </extensions>
  </trkpt>

The resulting file can be imported directly into Strava, Komoot, or any
GPX-aware app.  Extensions are ignored by most apps but kept for local analysis.
"""

import os
import threading
import time
import logging
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

import config

log = logging.getLogger(__name__)


def _fmt(value, precision: int = 6) -> str:
    """Format a float for XML, falling back to '0' for None."""
    if value is None:
        return "0"
    return f"{value:.{precision}f}"


class DataLogger(threading.Thread):
    """
    Background thread that periodically writes a GPX trackpoint.
    Starts logging once the first GPS fix is obtained.
    """

    GPX_HEADER = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="BikeComputer"\n'
        '     xmlns="http://www.topografix.com/GPX/1/1"\n'
        '     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        '     xsi:schemaLocation="http://www.topografix.com/GPX/1/1 '
        'http://www.topografix.com/GPX/1/1/gpx.xsd">\n'
        '  <trk>\n'
        '    <name>Bike Ride {name}</name>\n'
        '    <trkseg>\n'
    )
    GPX_FOOTER = "    </trkseg>\n  </trk>\n</gpx>\n"

    def __init__(self, state):
        super().__init__(daemon=True, name="LoggerThread")
        self._state = state
        self._stop_event = threading.Event()
        self._file = None
        self._filepath = None

    def stop(self):
        self._stop_event.set()

    def run(self):
        interval = config.LOG_INTERVAL_SEC
        while not self._stop_event.is_set():
            try:
                with self._state.lock:
                    fix      = self._state.gps_fix
                    lat      = self._state.gps_lat
                    lon      = self._state.gps_lon
                    gps_alt  = self._state.gps_altitude_m
                    baro_alt = self._state.baro_altitude_m
                    temp_c   = self._state.temperature_c
                    humidity = self._state.humidity_pct
                    voc_raw  = self._state.voc_raw
                    heading  = self._state.heading_deg
                    speed    = self._state.speed_kmh

                if fix and lat != 0.0:
                    if self._file is None:
                        self._open_file()
                    self._write_trkpt(
                        lat, lon, baro_alt, gps_alt,
                        temp_c, humidity, voc_raw, heading, speed,
                    )
            except Exception as exc:
                log.error("Logger error: %s", exc)

            time.sleep(interval)

    # ── file management ────────────────────────────────────────────────────────

    def _open_file(self):
        os.makedirs(config.RIDES_DIR, exist_ok=True)
        ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M")
        name = f"ride_{ts}.gpx"
        self._filepath = os.path.join(config.RIDES_DIR, name)
        self._file = open(self._filepath, "w", encoding="utf-8")  # noqa: WPS515
        header = self.GPX_HEADER.replace("{name}", ts)
        self._file.write(header)
        self._file.flush()
        log.info("Logging ride to %s", self._filepath)

    def close(self):
        """Write GPX footer and close the file cleanly."""
        if self._file is not None:
            try:
                self._file.write(self.GPX_FOOTER)
                self._file.flush()
                self._file.close()
                log.info("GPX file closed: %s", self._filepath)
            except Exception as exc:
                log.error("Error closing GPX file: %s", exc)
            finally:
                self._file = None

    # ── trackpoint writer ──────────────────────────────────────────────────────

    def _write_trkpt(
        self,
        lat: float,
        lon: float,
        baro_alt: float,
        gps_alt: float,
        temp_c: float,
        humidity: float,
        voc_raw: int,
        heading: float | None,
        speed_kmh: float,
    ):
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hdg_str = _fmt(heading, 1) if heading is not None else "0"

        trkpt = (
            f'      <trkpt lat="{_fmt(lat)}" lon="{_fmt(lon)}">\n'
            f'        <ele>{_fmt(baro_alt, 1)}</ele>\n'
            f'        <time>{xml_escape(utc_now)}</time>\n'
            f'        <extensions>\n'
            f'          <gps_alt>{_fmt(gps_alt, 1)}</gps_alt>\n'
            f'          <temp_c>{_fmt(temp_c, 2)}</temp_c>\n'
            f'          <humidity>{_fmt(humidity, 1)}</humidity>\n'
            f'          <voc_raw>{int(voc_raw)}</voc_raw>\n'
            f'          <heading>{hdg_str}</heading>\n'
            f'          <speed_kmh>{_fmt(speed_kmh, 2)}</speed_kmh>\n'
            f'        </extensions>\n'
            f'      </trkpt>\n'
        )
        self._file.write(trkpt)
        self._file.flush()   # survive power cuts
