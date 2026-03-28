"""
Bike Computer — Display Driver & Dashboard Renderer

Drives the Adafruit 2.4" ILI9341 TFT (320×240) over SPI0.
Each frame a full PIL image is composed and pushed to the display.

Dashboard layout (landscape, 320×240):
┌──────────────────────────────────────┐
│  ██ 12.4 km/h         01:23:45      │  row A — speed (large) + elapsed time
├──────────────────────────────────────┤
│  GPS ▲ 342m  BARO ▲ 344m   ↗ 278° │  row B — altitudes + heading
├──────────────────────────────────────┤
│  DIST  14.2 km    TEMP 18.2°C  68% │  row C — distance + temperature + humidity
├──────────────────────────────────────┤
│  VOC 45         ● FIX  8 sat        │  row D — VOC index + GPS status
│  51.5074°N  0.1278°E                │  row E — coordinates
└──────────────────────────────────────┘

Touch anywhere → toggle metric / imperial units.
"""

import os
import math
import logging

from PIL import Image, ImageDraw, ImageFont

import config

log = logging.getLogger(__name__)

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  10,  20)    # near-black background
C_LABEL     = (120, 120, 140)   # muted grey for labels
C_VALUE     = (220, 220, 220)   # white-ish for values
C_SPEED     = (80,  220, 100)   # green for the primary speed readout
C_ALT       = (100, 200, 255)   # light blue — altitude
C_HEADING   = (255, 210, 80)    # amber — compass
C_DIST      = (200, 200, 80)    # yellow-green — distance
C_TEMP      = (255, 140, 60)    # warm orange — temperature
C_HUMID     = (80,  180, 255)   # sky blue — humidity
C_VOC_OK    = (80,  200, 80)    # green VOC
C_VOC_WARN  = (255, 200, 50)    # amber VOC
C_VOC_BAD   = (255, 80,  80)    # red VOC
C_FIX_OK    = (50,  255, 80)    # GPS fix indicator
C_FIX_NONE  = (200, 50,  50)
C_DIVIDER   = (40,  40,  60)
C_UNIT      = (90,  90, 110)    # small unit suffix


def _load_fonts(font_dir: str):
    """
    Try to load DejaVuSans from font_dir, then system fonts, then fall back to
    the PIL default (which has no size control but always works).
    """
    candidates = [
        os.path.join(font_dir, "DejaVuSans-Bold.ttf"),
        os.path.join(font_dir, "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]

    def try_load(size):
        for path in candidates:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    return {
        "speed":  try_load(48),
        "large":  try_load(28),
        "medium": try_load(18),
        "small":  try_load(13),
        "tiny":   try_load(11),
    }


class Dashboard:
    """
    Manages the ILI9341 display and renders the bike computer dashboard.
    Call `setup()` once, then `draw(state)` each frame.
    """

    def __init__(self):
        self._disp   = None
        self._fonts  = None
        self._units  = config.DEFAULT_UNITS   # "metric" | "imperial"
        self._width  = config.TFT_WIDTH
        self._height = config.TFT_HEIGHT

    # ── initialisation ─────────────────────────────────────────────────────────

    def setup(self):
        """Initialise the SPI display.  Must be called from the main thread."""
        import board
        import busio
        import digitalio
        from adafruit_rgb_display import ili9341

        spi  = busio.SPI(clock=board.SCLK, MOSI=board.MOSI, MISO=board.MISO)
        cs   = digitalio.DigitalInOut(board.CE0)
        dc   = digitalio.DigitalInOut(getattr(board, f"D{config.TFT_DC_PIN}"))
        rst  = digitalio.DigitalInOut(getattr(board, f"D{config.TFT_RST_PIN}"))

        self._disp = ili9341.ILI9341(
            spi,
            cs=cs,
            dc=dc,
            rst=rst,
            baudrate=24_000_000,
            width=self._width,
            height=self._height,
            rotation=config.TFT_ROTATION,
        )

        font_dir = os.path.join(os.path.dirname(__file__), "fonts")
        self._fonts = _load_fonts(font_dir)
        log.info("Display initialised (%dx%d)", self._width, self._height)

    # ── unit helpers ───────────────────────────────────────────────────────────

    def toggle_units(self):
        self._units = "imperial" if self._units == "metric" else "metric"

    def _speed(self, kmh: float) -> tuple[str, str]:
        if self._units == "metric":
            return f"{kmh:.1f}", "km/h"
        return f"{kmh * 0.621371:.1f}", "mph"

    def _distance(self, km: float) -> tuple[str, str]:
        if self._units == "metric":
            return f"{km:.2f}", "km"
        return f"{km * 0.621371:.2f}", "mi"

    def _temperature(self, celsius: float) -> tuple[str, str]:
        if self._units == "metric":
            return f"{celsius:.1f}", "°C"
        return f"{celsius * 9/5 + 32:.1f}", "°F"

    @staticmethod
    def _elapsed(seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def _heading_arrow(deg: float) -> str:
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        idx = int((deg + 22.5) / 45) % 8
        return dirs[idx]

    @staticmethod
    def _voc_colour(raw: int):
        # SGP40 raw ticks: lower = cleaner air (typical indoor: 20000–25000)
        if raw < 20000:
            return (80, 200, 80)    # green — clean
        if raw < 35000:
            return (255, 200, 50)   # amber — moderate
        return (255, 80, 80)        # red — poor

    # ── drawing helpers ────────────────────────────────────────────────────────

    def _text(self, draw, xy, text, font_key, fill):
        draw.text(xy, text, font=self._fonts[font_key], fill=fill)

    def _label_value(self, draw, lx, ly, label, value, unit,
                     lcolour=C_LABEL, vcolour=C_VALUE, ucolour=C_UNIT,
                     lfont="tiny", vfont="medium", ufont="tiny"):
        self._text(draw, (lx, ly), label, lfont, lcolour)
        self._text(draw, (lx, ly + 14), value, vfont, vcolour)
        if unit:
            # right-align unit suffix next to value
            vw = self._fonts[vfont].getlength(value)
            self._text(draw, (lx + vw + 3, ly + 17), unit, ufont, ucolour)

    # ── main draw call ─────────────────────────────────────────────────────────

    def draw(self, state):
        """Compose one full dashboard frame and push it to the TFT."""
        img  = Image.new("RGB", (self._width, self._height), BG)
        draw = ImageDraw.Draw(img)

        with state.lock:
            speed_kmh    = state.speed_kmh
            distance_km  = state.distance_km
            elapsed_sec  = state.elapsed_sec
            gps_alt      = state.gps_altitude_m
            baro_alt     = state.baro_altitude_m
            heading_deg  = state.heading_deg
            temperature  = state.temperature_c
            humidity     = state.humidity_pct
            lux          = state.lux
            lat          = state.gps_lat
            lon          = state.gps_lon
            gps_fix      = state.gps_fix
            satellites   = state.gps_satellites

        W, H = self._width, self._height

        # ── Row A: speed (big) + elapsed time ─────────────────────────────────
        spd_str, spd_unit = self._speed(speed_kmh)
        self._text(draw, (8, 6), spd_str, "speed", C_SPEED)
        spd_w = int(self._fonts["speed"].getlength(spd_str))
        self._text(draw, (8 + spd_w + 4, 24), spd_unit, "small", C_UNIT)

        time_str = self._elapsed(elapsed_sec)
        self._text(draw, (W - 4 - int(self._fonts["large"].getlength(time_str)), 12),
                   time_str, "large", C_VALUE)

        # divider
        y_div1 = 66
        draw.line([(0, y_div1), (W, y_div1)], fill=C_DIVIDER, width=1)

        # ── Row B: GPS altitude | baro altitude | heading ─────────────────────
        y2 = y_div1 + 4
        self._label_value(draw, 6, y2, "GPS ALT",
                          f"{gps_alt:.0f}", "m", vcolour=C_ALT)
        self._label_value(draw, 116, y2, "BARO ALT",
                          f"{baro_alt:.0f}", "m", vcolour=C_ALT)
        hdg_str = (f"{heading_deg:.0f}°  {self._heading_arrow(heading_deg)}"
                   if heading_deg is not None else "---")
        self._label_value(draw, 226, y2, "HEADING",
                          hdg_str, "", vcolour=C_HEADING)

        y_div2 = y2 + 44
        draw.line([(0, y_div2), (W, y_div2)], fill=C_DIVIDER, width=1)

        # ── Row C: distance | temperature | humidity ──────────────────────────
        y3 = y_div2 + 4
        dist_str, dist_unit = self._distance(distance_km)
        self._label_value(draw, 6, y3, "DIST", dist_str, dist_unit, vcolour=C_DIST)

        temp_str, temp_unit = self._temperature(temperature)
        self._label_value(draw, 130, y3, "TEMP", temp_str, temp_unit, vcolour=C_TEMP)

        self._label_value(draw, 236, y3, "HUMID",
                          f"{humidity:.0f}", "%", vcolour=C_HUMID)

        y_div3 = y3 + 44
        draw.line([(0, y_div3), (W, y_div3)], fill=C_DIVIDER, width=1)

        # ── Row D: Ambient lux | GPS fix status ───────────────────────────────
        y4 = y_div3 + 4
        self._label_value(draw, 6, y4, "LIGHT",
                          f"{lux:.0f}", "lux", vcolour=(200, 200, 80))

        # Fix indicator dot + satellite count
        fix_col  = C_FIX_OK if gps_fix else C_FIX_NONE
        fix_text = "FIX" if gps_fix else "NO FIX"
        dot_x, dot_y = 180, y4 + 18
        draw.ellipse([(dot_x - 6, dot_y - 6), (dot_x + 6, dot_y + 6)], fill=fix_col)
        self._text(draw, (dot_x + 10, y4 + 11), fix_text, "small", fix_col)
        self._text(draw, (dot_x + 10, y4 + 24), f"{satellites} sat", "tiny", C_LABEL)

        y_div4 = y4 + 42
        draw.line([(0, y_div4), (W, y_div4)], fill=C_DIVIDER, width=1)

        # ── Row E: GPS coordinates ─────────────────────────────────────────────
        y5 = y_div4 + 3
        if gps_fix and lat != 0.0:
            lat_str = f"{abs(lat):.5f}°{'N' if lat >= 0 else 'S'}"
            lon_str = f"{abs(lon):.5f}°{'E' if lon >= 0 else 'W'}"
            coord_str = f"{lat_str}  {lon_str}"
        else:
            coord_str = "Acquiring GPS fix…"
        self._text(draw, (6, y5), coord_str, "small", C_LABEL)

        # ── Units toggle hint (bottom-right corner) ────────────────────────────
        unit_hint = f"[{self._units.upper()}]"
        uw = int(self._fonts["tiny"].getlength(unit_hint))
        self._text(draw, (W - uw - 4, H - 14), unit_hint, "tiny", C_DIVIDER)

        # Push to hardware
        if self._disp is not None:
            self._disp.image(img)

        return img   # returned so tests/simulators can inspect without hardware

    # ── touch handling ─────────────────────────────────────────────────────────

    def check_touch(self) -> bool:
        """
        Poll the XPT2046 touch controller.
        Returns True if the screen is currently being touched.

        The XPT2046 uses SPI; a full driver is non-trivial on pure Python,
        so we poll the T_IRQ line (active-low) as a simple touch detector.
        """
        try:
            import RPi.GPIO as GPIO
            return not GPIO.input(config.TOUCH_IRQ_PIN)   # active low
        except Exception:
            return False
