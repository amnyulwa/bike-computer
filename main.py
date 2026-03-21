"""
Bike Computer — Main Entry Point

Starts all background threads and drives the display loop.

Usage:
    python main.py [--simulate]

    --simulate   Run without real hardware; useful for layout testing on a
                 desktop PC.  Generates fake GPS + sensor data.

Thread map:
  GPSThread    — reads UART, parses NMEA, updates RideState
  SensorThread — polls I2C HAT sensors, updates RideState
  LoggerThread — appends GPX trackpoints every LOG_INTERVAL_SEC seconds
  Main thread  — renders dashboard to TFT at DISPLAY_FPS
"""

import argparse
import dataclasses
import logging
import math
import signal
import sys
import threading
import time

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


# ── Shared state ──────────────────────────────────────────────────────────────

@dataclasses.dataclass
class RideState:
    """
    Single source of truth for all live ride data.
    All fields are protected by `lock`; always acquire before reading or writing.
    """
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    # GPS
    gps_fix:        bool  = False
    gps_lat:        float = 0.0
    gps_lon:        float = 0.0
    gps_altitude_m: float = 0.0
    gps_satellites: int   = 0
    speed_kmh:      float = 0.0
    distance_km:    float = 0.0
    elapsed_sec:    int   = 0
    heading_deg:    float | None = None   # set by GPS COG or ICM20948

    # Environment sensors
    temperature_c:   float = 0.0
    humidity_pct:    float = 0.0
    pressure_hpa:    float = config.SEA_LEVEL_HPA
    baro_altitude_m: float = 0.0
    voc_raw:         int   = 0

    # Display
    units: str = config.DEFAULT_UNITS   # "metric" | "imperial"


# ── Simulation (no hardware) ──────────────────────────────────────────────────

class SimulatorThread(threading.Thread):
    """Generates synthetic ride data for UI testing without hardware."""

    def __init__(self, state):
        super().__init__(daemon=True, name="SimulatorThread")
        self._state = state
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        t = 0.0
        base_lat, base_lon = 51.5074, -0.1278   # London
        while not self._stop_event.is_set():
            t += 1.0
            with self._state.lock:
                self._state.gps_fix        = True
                self._state.gps_satellites = 8
                self._state.speed_kmh      = 18.0 + 6.0 * math.sin(t / 20)
                self._state.gps_lat        = base_lat + t * 0.00005
                self._state.gps_lon        = base_lon + t * 0.00003
                self._state.gps_altitude_m = 85.0 + 10.0 * math.sin(t / 40)
                self._state.distance_km   += self._state.speed_kmh / 3600
                self._state.elapsed_sec    = int(t)
                self._state.heading_deg    = (t * 3) % 360

                self._state.temperature_c   = 18.5 + math.sin(t / 60)
                self._state.humidity_pct    = 62.0
                self._state.pressure_hpa    = 1013.25 - t * 0.01
                self._state.baro_altitude_m = (
                    44330.0 * (1.0 - (self._state.pressure_hpa / 1013.25) ** 0.1903)
                )
                self._state.voc_raw = 18000 + int(5000 * math.sin(t / 30))

            time.sleep(1.0)


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Bike Computer")
    p.add_argument(
        "--simulate", action="store_true",
        help="Run with simulated data (no hardware required)"
    )
    return p.parse_args()


def main():
    args = parse_args()
    state = RideState()

    # ── Setup GPIO for touch IRQ (before display init) ────────────────────────
    if not args.simulate:
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(config.TOUCH_IRQ_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        except Exception as exc:
            log.warning("GPIO setup failed (not on Pi?): %s", exc)

    # ── Display ───────────────────────────────────────────────────────────────
    from display import Dashboard
    dash = Dashboard()
    if not args.simulate:
        try:
            dash.setup()
        except Exception as exc:
            log.error("Display init failed: %s", exc)
            sys.exit(1)
    else:
        log.info("Simulate mode: display output will be skipped (no hardware)")

    # ── Background threads ────────────────────────────────────────────────────
    threads = []

    if args.simulate:
        sim = SimulatorThread(state)
        sim.start()
        threads.append(sim)
    else:
        from gps_reader import GPSThread
        from sensors import SensorThread

        gps = GPSThread(state)
        gps.start()
        threads.append(gps)

        sensors = SensorThread(state)
        sensors.start()
        threads.append(sensors)

    from data_logger import DataLogger
    logger = DataLogger(state)
    if not args.simulate:
        logger.start()
        threads.append(logger)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    running = True

    def _shutdown(signum, frame):
        nonlocal running
        log.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Main display loop ─────────────────────────────────────────────────────
    frame_interval = 1.0 / config.DISPLAY_FPS
    touch_was_down = False

    log.info("Bike computer running — Ctrl-C to stop")

    while running:
        t0 = time.monotonic()

        # Touch detection → toggle units
        touch_now = dash.check_touch()
        if touch_now and not touch_was_down:
            dash.toggle_units()
            log.debug("Units toggled to %s", dash._units)
        touch_was_down = touch_now

        # Render frame
        try:
            dash.draw(state)
        except Exception as exc:
            log.error("Display draw error: %s", exc)

        # Maintain target FPS
        elapsed = time.monotonic() - t0
        time.sleep(max(0.0, frame_interval - elapsed))

    # ── Cleanup ───────────────────────────────────────────────────────────────
    log.info("Stopping threads…")
    for t in threads:
        if hasattr(t, "stop"):
            t.stop()
    for t in threads:
        t.join(timeout=3)

    logger.close()   # write GPX footer

    try:
        import RPi.GPIO as GPIO
        GPIO.cleanup()
    except Exception:
        pass

    log.info("Bike computer stopped.")


if __name__ == "__main__":
    main()
