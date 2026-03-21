#!/usr/bin/env bash
# Bike Computer — One-shot setup script
# Run once on a fresh Raspberry Pi OS Lite install:
#   chmod +x install.sh && sudo ./install.sh
#
# What this does:
#   1. Enables UART (for GPS), SPI (for TFT), I2C (for sensors) via raspi-config
#   2. Disables the Linux serial console (so GPS can use /dev/serial0)
#   3. Installs system packages
#   4. Installs Python dependencies
#   5. Copies DejaVu fonts into the project fonts/ directory
#   6. Creates the ~/rides output directory
#   7. Installs a systemd service so the bike computer starts on boot

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_USER="${SUDO_USER:-pi}"
HOME_DIR="$(eval echo "~$INSTALL_USER")"

echo "==> Bike Computer Setup"
echo "    Project dir : $SCRIPT_DIR"
echo "    Install user: $INSTALL_USER"
echo

# ── 1. Raspberry Pi interface configuration ───────────────────────────────────
echo "==> Enabling SPI, I2C, UART…"
raspi-config nonint do_spi 0        # enable SPI
raspi-config nonint do_i2c 0        # enable I2C
raspi-config nonint do_serial_hw 0  # enable UART hardware
raspi-config nonint do_serial_cons 1 # disable serial console (free the UART)

# ── 2. System packages ────────────────────────────────────────────────────────
echo "==> Installing system packages…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    python3-setuptools \
    python3-wheel \
    libgpiod2 \
    fonts-dejavu-core \
    i2c-tools \
    git

# ── 3. Python dependencies ────────────────────────────────────────────────────
echo "==> Installing Python packages…"
pip3 install --break-system-packages -r "$SCRIPT_DIR/requirements.txt"

# ── 4. Copy fonts ─────────────────────────────────────────────────────────────
echo "==> Copying DejaVu fonts…"
FONT_DST="$SCRIPT_DIR/fonts"
mkdir -p "$FONT_DST"

for src in \
    /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
    /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf; do
    if [ -f "$src" ]; then
        cp -n "$src" "$FONT_DST/" && echo "    Copied: $(basename "$src")"
    fi
done

# ── 5. Rides output directory ─────────────────────────────────────────────────
echo "==> Creating rides directory…"
mkdir -p "$HOME_DIR/rides"
chown "$INSTALL_USER:$INSTALL_USER" "$HOME_DIR/rides"

# ── 6. Systemd service ────────────────────────────────────────────────────────
echo "==> Installing systemd service…"
SERVICE_FILE="/etc/systemd/system/bike-computer.service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Bike Computer
After=multi-user.target

[Service]
Type=simple
User=$INSTALL_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable bike-computer.service

echo
echo "==> Setup complete!"
echo
echo "    To start now:          sudo systemctl start bike-computer"
echo "    To check status:       sudo systemctl status bike-computer"
echo "    To view logs:          journalctl -u bike-computer -f"
echo "    To run manually:       python3 $SCRIPT_DIR/main.py"
echo "    To test without HW:    python3 $SCRIPT_DIR/main.py --simulate"
echo
echo "    IMPORTANT: Reboot required for UART/SPI/I2C changes to take effect."
echo "    Run: sudo reboot"
