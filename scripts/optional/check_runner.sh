#!/usr/bin/env bash
# Quick health check for the self-hosted runner.
# Usage: bash scripts/check_runner.sh
set -euo pipefail

REPO="Sweet-Butters/meme_project"
SERVICE="gh-runner-meme.service"

echo "=== systemd service ==="
if systemctl --user is-active "$SERVICE" &>/dev/null; then
    echo "  active ✓"
else
    echo "  NOT active ✗"
    systemctl --user status "$SERVICE" --no-pager 2>/dev/null | head -8 || true
fi

echo ""
echo "=== GitHub view ==="
gh api "repos/$REPO/actions/runners" \
    -q '.runners[] | "  " + .name + " (" + .status + ")  " + (if .busy then "BUSY" else "idle" end) + "  labels: " + ([.labels[].name] | join(","))' \
    2>/dev/null || echo "  (couldn't reach GitHub — check 'gh auth status')"

echo ""
echo "=== last 10 log lines ==="
journalctl --user -u "$SERVICE" -n 10 --no-pager 2>/dev/null | tail -10 || echo "  (no journal access)"
