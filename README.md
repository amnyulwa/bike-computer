# Bike Computer

A GPS-connected bike computer built on a Raspberry Pi Zero. Displays real-time speed, distance, altitude, heading, temperature, air quality, and GPS coordinates on a 2.4" TFT touchscreen. Logs every ride as a GPX file importable into Strava or Komoot.

## Hardware

| Component | Interface | Purpose |
|---|---|---|
| Raspberry Pi Zero (with headers) | — | Main compute unit |
| Adafruit Ultimate GPS HAT | UART (`/dev/serial0`) | Location, speed, altitude |
| Waveshare Environment Sensor HAT | I2C | Temperature, humidity, pressure, VOC, IMU |
| Adafruit 2.4" ILI9341 TFT + Touch | SPI0 | Display and touch input |

The GPS HAT and Waveshare HAT stack directly onto the Pi's 40-pin header using stacking headers. The TFT connects via jumper wires to the passthrough pins.

## Wiring Diagrams

All diagrams are in [`diagrams/`](diagrams/) and can be opened in any browser.

| File | Shows |
|---|---|
| [`bike_computer_system_overview.svg`](diagrams/bike_computer_system_overview.svg) | Block diagram of all components and protocols |
| [`bike_computer_gpio_pinmap.svg`](diagrams/bike_computer_gpio_pinmap.svg) | Full 40-pin header map with used pins highlighted |
| [`bike_computer_stacking_diagram.svg`](diagrams/bike_computer_stacking_diagram.svg) | Physical side-view of the HAT stack and TFT wiring |
| [`bike_computer_tft_wiring.svg`](diagrams/bike_computer_tft_wiring.svg) | Wire-by-wire TFT connection detail |

### TFT Pin Connections

| TFT Pin | Pi GPIO | Header Pin |
|---|---|---|
| VIN | 3.3V | Pin 17 |
| GND | GND | Pin 20 |
| CLK | GPIO11 (SCLK) | Pin 23 |
| MOSI | GPIO10 | Pin 19 |
| MISO | GPIO9 | Pin 21 |
| CS | GPIO8 (CE0) | Pin 24 |
| DC | GPIO25 | Pin 22 |
| RST | GPIO27 | Pin 13 |
| LITE | 3.3V | Pin 17 |
| T_CS | GPIO7 (CE1) | Pin 26 |
| T_IRQ | GPIO17 | Pin 11 |

## Dashboard

Single-screen layout (320×240, landscape):

```
┌──────────────────────────────────────┐
│  12.4 km/h              01:23:45     │  speed · elapsed time
├──────────────────────────────────────┤
│  GPS ▲ 342m   BARO ▲ 344m   ↗ NW   │  GPS alt · baro alt · heading
├──────────────────────────────────────┤
│  DIST  14.2 km    TEMP 18.2°C  68%  │  distance · temperature · humidity
├──────────────────────────────────────┤
│  VOC 18432        ● FIX  8 sat      │  air quality · GPS fix status
│  51.50740°N  0.12780°E              │  coordinates
└──────────────────────────────────────┘
```

**Touch anywhere** on the screen to toggle between metric (km/h, km, °C) and imperial (mph, mi, °F).

## Software

| File | Purpose |
|---|---|
| `main.py` | Entry point, main display loop, `RideState` dataclass |
| `config.py` | All pin numbers, I2C addresses, and tuning constants |
| `gps_reader.py` | GPS UART thread — parses NMEA, accumulates distance |
| `sensors.py` | I2C sensor thread — BME280, ICM20948, SGP40, LPS22HB |
| `display.py` | ILI9341 driver and dashboard renderer (PIL) |
| `data_logger.py` | GPX file writer, flushes every 5 seconds |

### Architecture

Four concurrent threads share a single `RideState` object protected by a `threading.Lock`:

```
GPSThread    ─── UART /dev/serial0 ──→ speed, lat/lon, altitude, distance
SensorThread ─── I2C bus 1 ─────────→ temp, humidity, pressure, VOC, heading
LoggerThread ─── ~/rides/*.gpx ──────→ trackpoint every 5 s (when fix held)
Main thread  ─── ILI9341 SPI ────────→ dashboard render at 2 fps
```

## Installation

### 1. First-time Pi setup

Run once on a fresh Raspberry Pi OS Lite install:

```bash
sudo bash install.sh
sudo reboot
```

This enables SPI, I2C, and UART; installs all Python packages; copies fonts; creates the `~/rides/` directory; and installs a systemd service that starts the bike computer on boot.

### 2. Run manually

```bash
python3 main.py
```

### 3. Simulate without hardware

Test the dashboard layout on any machine — generates fake GPS and sensor data:

```bash
python3 main.py --simulate
```

### 4. Service management

```bash
sudo systemctl start bike-computer     # start
sudo systemctl stop bike-computer      # stop
sudo systemctl status bike-computer    # check status
journalctl -u bike-computer -f         # live logs
```

## Configuration

Edit [`config.py`](config.py) to adjust behaviour:

```python
SEA_LEVEL_HPA = 1013.25   # update with local QNH for accurate baro altitude
DEFAULT_UNITS = "metric"  # "metric" or "imperial"
DISPLAY_FPS   = 2         # lower if Pi feels sluggish
LOG_INTERVAL_SEC = 5      # GPX trackpoint write interval
```

### Compass calibration

The ICM20948 magnetometer heading is affected by nearby metal (mounting bolts, phone, etc.). For accurate compass readings, do a figure-8 calibration and update the offsets in `config.py`:

```python
MAG_OFFSET_X = 0.0   # replace with (max_x + min_x) / 2
MAG_OFFSET_Y = 0.0   # replace with (max_y + min_y) / 2
```

Until calibrated, the heading falls back to GPS course-over-ground when moving.

## Ride Data

GPX files are saved to `~/rides/ride_YYYY-MM-DD_HH-MM.gpx` on first GPS fix. Each trackpoint includes:

- Latitude, longitude
- Barometric altitude (metres)
- UTC timestamp
- Temperature, humidity, VOC index, compass heading, speed

Import into **Strava**: Dashboard → Upload activity → select the `.gpx` file
Import into **Komoot**: Profile → Tours → Import GPX

## Auto-Deploy (push-to-deploy)

The Pi polls GitHub every 60 seconds via a systemd timer. When new commits are pushed to `main`, the Pi pulls the update and restarts the bike-computer service automatically.

```
git push origin main
# → Pi picks up changes within 60 seconds
```

### Setup (one-time)

```bash
# On your local machine:
bash scripts/setup_pi_sync.sh
```

### Monitor sync

```bash
# Watch sync logs live on the Pi:
ssh asm@10.0.0.111 'journalctl -t bike-sync -f'

# Force an immediate sync:
ssh asm@10.0.0.111 'sudo systemctl start bike-sync.service'
```

> **Note:** The GitHub Actions self-hosted runner does not support ARMv6 (Pi Zero). The polling timer is used instead.

## Dependencies

```
adafruit-blinka
adafruit-circuitpython-rgb-display
adafruit-circuitpython-bme280
adafruit-circuitpython-sgp40
adafruit-circuitpython-lps2x
pillow
pyserial
pynmea2
smbus2
RPi.GPIO
```

Install with:
```bash
pip install -r requirements.txt
```
