#!/usr/bin/env bash
# Manual re-deploy script — same steps as the GitHub Actions workflow.
# Run this on the Pi directly if you want to force a sync without a git push:
#   bash ~/Projects/bike-computer/scripts/deploy.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "==> Deploying bike-computer from $REPO_DIR"

cd "$REPO_DIR"

echo "--- git pull"
git pull origin main

echo "--- pip install"
pip3 install --break-system-packages -q -r requirements.txt

echo "--- restarting service"
sudo systemctl restart bike-computer

echo "--- status"
sudo systemctl status bike-computer --no-pager

echo "==> Deploy complete"
