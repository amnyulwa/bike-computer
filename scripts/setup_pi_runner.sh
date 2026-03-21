#!/usr/bin/env bash
# Bike Computer — Pi runner setup script
# Run this ON YOUR LOCAL MACHINE (not on the Pi).
# It SSHes into the Pi and configures everything needed for push-to-deploy.
#
# Usage:
#   bash scripts/setup_pi_runner.sh <RUNNER_TOKEN>
#
# Get your RUNNER_TOKEN from:
#   https://github.com/amnyulwa/bike-computer/settings/actions/runners/new
#   (select Linux / ARM)

set -euo pipefail

RUNNER_TOKEN="${1:-}"
PI_HOST="asm@10.0.0.111"
GITHUB_REPO="https://github.com/amnyulwa/bike-computer"
RUNNER_VERSION="2.321.0"   # update to latest from github.com/actions/runner/releases

if [ -z "$RUNNER_TOKEN" ]; then
    echo "Usage: $0 <RUNNER_TOKEN>"
    echo
    echo "Get your token from:"
    echo "  $GITHUB_REPO/settings/actions/runners/new"
    echo "  (select Linux → ARM)"
    exit 1
fi

echo "==> Setting up GitHub Actions self-hosted runner on $PI_HOST"
echo "    Repo: $GITHUB_REPO"
echo

# ── Run everything on the Pi over SSH ─────────────────────────────────────────
ssh "$PI_HOST" bash <<REMOTE_SCRIPT
set -euo pipefail

echo "--- Creating runner directory"
mkdir -p ~/actions-runner
cd ~/actions-runner

echo "--- Detecting architecture"
ARCH=\$(uname -m)
case "\$ARCH" in
    armv6l)  RUNNER_ARCH="arm"    ;;   # Pi Zero
    armv7l)  RUNNER_ARCH="arm"    ;;   # Pi 2/3/4 (32-bit OS)
    aarch64) RUNNER_ARCH="arm64"  ;;   # Pi 3/4/5 (64-bit OS)
    x86_64)  RUNNER_ARCH="x64"   ;;
    *)       echo "Unknown arch: \$ARCH"; exit 1 ;;
esac
echo "    Architecture: \$ARCH → runner arch: \$RUNNER_ARCH"

TARBALL="actions-runner-linux-\${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/\${TARBALL}"

echo "--- Downloading runner \${TARBALL}"
curl -L -o "\$TARBALL" "\$RUNNER_URL"
tar xzf "\$TARBALL"
rm "\$TARBALL"

echo "--- Configuring runner"
./config.sh \
    --url ${GITHUB_REPO} \
    --token ${RUNNER_TOKEN} \
    --name "raspberry-pi-zero" \
    --labels "self-hosted,raspberry-pi" \
    --work "_work" \
    --unattended

echo "--- Installing runner as systemd service"
sudo ./svc.sh install
sudo ./svc.sh start

echo "--- Runner service status"
sudo ./svc.sh status

echo
echo "--- Configuring sudoers for bike-computer service"
SUDOERS_ENTRY="asm ALL=(ALL) NOPASSWD: /bin/systemctl restart bike-computer, /bin/systemctl status bike-computer"
echo "\$SUDOERS_ENTRY" | sudo tee /etc/sudoers.d/bike-computer > /dev/null
sudo chmod 440 /etc/sudoers.d/bike-computer
echo "    Sudoers entry written."

echo
echo "==> Pi runner setup complete!"
echo "    Runner 'raspberry-pi-zero' is now listening for GitHub Actions jobs."
echo "    Check status: ssh $PI_HOST 'sudo ~/actions-runner/svc.sh status'"
REMOTE_SCRIPT

echo
echo "==> All done!"
echo
echo "Next steps:"
echo "  1. Push a change to main:"
echo "       git commit --allow-empty -m 'test deploy' && git push origin main"
echo "  2. Watch the run:"
echo "       https://github.com/amnyulwa/bike-computer/actions"
echo "  3. Check the Pi service:"
echo "       ssh $PI_HOST 'journalctl -u bike-computer -n 20'"
