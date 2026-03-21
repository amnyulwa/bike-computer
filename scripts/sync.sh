#!/usr/bin/env bash
# Bike Computer — Git sync script (runs on the Pi via systemd timer)
# Checks if origin/main has new commits; if so, pulls and restarts the service.
set -euo pipefail

REPO_DIR="/home/asm/Projects/bike-computer"
SERVICE="bike-computer"
LOG_TAG="bike-sync"

log() { logger -t "$LOG_TAG" "$*"; }

cd "$REPO_DIR"

# Fetch silently — only network call, no local changes yet
git fetch origin main --quiet 2>&1 | logger -t "$LOG_TAG" || {
    log "git fetch failed — no network?"
    exit 0
}

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Already up to date ($LOCAL)"
    exit 0
fi

log "New commits detected: $LOCAL → $REMOTE"
log "Pulling…"
git pull origin main --quiet

log "Updating dependencies…"
pip3 install --break-system-packages -q -r requirements.txt

log "Restarting $SERVICE…"
sudo systemctl restart "$SERVICE"

log "Deploy complete ($(git rev-parse --short HEAD))"
