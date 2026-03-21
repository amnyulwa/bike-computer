#!/usr/bin/env bash
# Bike Computer — Pi polling sync setup
# Run this from YOUR LOCAL MACHINE terminal (not from Claude).
# It SSHes into the Pi and sets up everything needed for push-to-deploy via
# a 60-second systemd timer (workaround for GitHub Actions runner not supporting ARMv6).
#
# Usage:  bash scripts/setup_pi_sync.sh

set -euo pipefail

PI_HOST="asm@10.0.0.111"
REPO_DIR="/home/asm/Projects/bike-computer"
GITHUB_REPO="git@github.com:amnyulwa/bike-computer.git"

echo "==> Setting up polling sync on $PI_HOST"
echo

# ── 1. Generate a deploy key on the Pi ───────────────────────────────────────
echo "--- Generating SSH deploy key on Pi (if not already present)"
ssh "$PI_HOST" '
    if [ ! -f ~/.ssh/github_deploy_ed25519 ]; then
        ssh-keygen -t ed25519 -C "pi-bike-computer-deploy" \
            -f ~/.ssh/github_deploy_ed25519 -N ""
        echo "KEY_GENERATED"
    else
        echo "KEY_EXISTS"
    fi
    cat ~/.ssh/github_deploy_ed25519.pub
' 2>&1 | tee /tmp/pi_deploy_key_output.txt

DEPLOY_PUBKEY=$(grep "^ssh-ed25519" /tmp/pi_deploy_key_output.txt)

if grep -q "KEY_GENERATED" /tmp/pi_deploy_key_output.txt; then
    echo
    echo "========================================================"
    echo " ACTION REQUIRED — Add this deploy key to GitHub:"
    echo "========================================================"
    echo
    echo "  1. Go to:"
    echo "     https://github.com/amnyulwa/bike-computer/settings/keys/new"
    echo
    echo "  2. Title:  raspberry-pi-zero"
    echo "     Allow write access: NO (read-only is enough)"
    echo
    echo "  3. Paste this key:"
    echo
    echo "     $DEPLOY_PUBKEY"
    echo
    echo "  4. Click 'Add deploy key'"
    echo
    read -r -p "Press Enter once you have added the key to GitHub… "
fi

# ── 2. Configure SSH on Pi to use deploy key for GitHub ──────────────────────
echo "--- Configuring SSH on Pi for GitHub"
ssh "$PI_HOST" "
    mkdir -p ~/.ssh
    chmod 700 ~/.ssh

    # Add GitHub host key to known_hosts
    ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null

    # Write SSH config entry for github.com using deploy key
    if ! grep -q 'bike-computer-deploy' ~/.ssh/config 2>/dev/null; then
        cat >> ~/.ssh/config <<'SSHCONF'

Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_deploy_ed25519
    IdentitiesOnly yes
SSHCONF
        chmod 600 ~/.ssh/config
        echo 'SSH config updated'
    else
        echo 'SSH config already set'
    fi

    # Test auth
    ssh -T git@github.com 2>&1 || true
"

# ── 3. Clone or update the repo on the Pi ────────────────────────────────────
echo "--- Setting up repo on Pi at $REPO_DIR"
ssh "$PI_HOST" "
    if [ -d '$REPO_DIR/.git' ]; then
        echo 'Repo already exists — updating remote URL to SSH'
        git -C '$REPO_DIR' remote set-url origin '$GITHUB_REPO'
        git -C '$REPO_DIR' pull origin main
    else
        mkdir -p '$REPO_DIR'
        git clone '$GITHUB_REPO' '$REPO_DIR'
    fi
"

# ── 4. Make sync.sh executable on the Pi ─────────────────────────────────────
echo "--- Setting permissions on sync.sh"
ssh "$PI_HOST" "chmod +x '$REPO_DIR/scripts/sync.sh'"

# ── 5. Install systemd timer + service unit ───────────────────────────────────
echo "--- Installing systemd timer"
ssh "$PI_HOST" "sudo tee /etc/systemd/system/bike-sync.service > /dev/null" <<'UNIT'
[Unit]
Description=Bike Computer Git Sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=asm
ExecStart=/home/asm/Projects/bike-computer/scripts/sync.sh
UNIT

ssh "$PI_HOST" "sudo tee /etc/systemd/system/bike-sync.timer > /dev/null" <<'TIMER'
[Unit]
Description=Bike Computer Git Sync — every 60 seconds

[Timer]
OnBootSec=30
OnUnitActiveSec=60
Unit=bike-sync.service

[Install]
WantedBy=timers.target
TIMER

# ── 6. Sudoers for sync script ────────────────────────────────────────────────
echo "--- Configuring sudoers"
ssh "$PI_HOST" '
    echo "asm ALL=(ALL) NOPASSWD: /bin/systemctl restart bike-computer, /bin/systemctl status bike-computer" \
        | sudo tee /etc/sudoers.d/bike-computer > /dev/null
    sudo chmod 440 /etc/sudoers.d/bike-computer
'

# ── 7. Enable and start the timer ────────────────────────────────────────────
echo "--- Enabling and starting bike-sync.timer"
ssh "$PI_HOST" "
    sudo systemctl daemon-reload
    sudo systemctl enable bike-sync.timer
    sudo systemctl start bike-sync.timer
    sudo systemctl list-timers bike-sync.timer --no-pager
"

echo
echo "==> Done! The Pi will now check for updates every 60 seconds."
echo
echo "Verify it's working:"
echo "  ssh $PI_HOST 'journalctl -t bike-sync -n 20 -f'"
echo
echo "To force an immediate sync:"
echo "  ssh $PI_HOST 'sudo systemctl start bike-sync.service'"
