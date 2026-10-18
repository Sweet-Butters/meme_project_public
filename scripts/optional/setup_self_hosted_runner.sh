#!/usr/bin/env bash
# Register this machine as a GitHub Actions self-hosted runner for
# Sweet-Butters/meme_project, install as a systemd user service that
# survives reboots (via loginctl enable-linger).
#
# Why self-hosted:
#   - YouTube blocks transcript scraping + sometimes Data API calls from
#     cloud IPs (GHA/AWS/GCP/Azure). Residential / WSL IPs are not blocked.
#   - GHA private repo minute quota (2000/month) becomes irrelevant — runs
#     consume zero GHA minutes when the job lands on a self-hosted runner.
#   - Tighter cron / lower latency possible without quota concerns.
#
# Prereqs (will fail clearly if missing):
#   - gh (GitHub CLI) authenticated  → gh auth login
#   - python3 + python3-venv (Ubuntu: sudo apt-get install -y python3 python3-venv)
#   - git, curl, tar  (usually preinstalled)
#
# After running:
#   - Verify: systemctl --user status gh-runner-meme.service
#   - Or:     gh api repos/Sweet-Butters/meme_project/actions/runners

set -euo pipefail

REPO="Sweet-Butters/meme_project"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner/meme_project}"
RUNNER_VERSION="${RUNNER_VERSION:-2.321.0}"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)-meme}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux,$(uname -m)}"
SERVICE_NAME="gh-runner-meme.service"

# --- 1. Prereq check ---
echo "→ Checking prerequisites..."
missing=()
for cmd in gh python3 git curl tar; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
done
if ! python3 -c "import venv" &>/dev/null; then
    missing+=("python3-venv (apt)")
fi
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: missing prerequisites: ${missing[*]}"
    echo "Ubuntu/WSL: sudo apt-get install -y python3 python3-venv git curl tar"
    echo "gh CLI:    https://cli.github.com/  then  gh auth login"
    exit 1
fi
if ! gh auth status &>/dev/null; then
    echo "ERROR: gh not authenticated. Run: gh auth login"
    exit 1
fi

# --- 2. Avoid duplicate runners with same name ---
if gh api "repos/$REPO/actions/runners" -q '.runners[].name' 2>/dev/null | grep -qx "$RUNNER_NAME"; then
    echo "Runner '$RUNNER_NAME' already registered on $REPO."
    echo "Delete via UI (Settings → Actions → Runners) or set RUNNER_NAME=... and re-run."
    exit 1
fi

# --- 3. Fetch fresh registration token (~1h validity) ---
echo "→ Fetching registration token..."
TOKEN=$(gh api -X POST "repos/$REPO/actions/runners/registration-token" -q .token)
if [[ -z "$TOKEN" ]]; then
    echo "ERROR: couldn't get registration token (check 'gh auth refresh -s repo,workflow')"
    exit 1
fi

# --- 4. Download + extract runner binary ---
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [[ ! -x ./config.sh ]]; then
    case "$(uname -m)" in
        x86_64)  ARCH="x64" ;;
        aarch64) ARCH="arm64" ;;
        *) echo "ERROR: unsupported arch $(uname -m)"; exit 1 ;;
    esac
    URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${ARCH}-${RUNNER_VERSION}.tar.gz"
    echo "→ Downloading runner v${RUNNER_VERSION} (${ARCH})..."
    curl -fsSL -o runner.tar.gz "$URL"
    tar xzf runner.tar.gz
    rm runner.tar.gz
fi

# --- 5. Configure ---
echo "→ Registering with GitHub as '$RUNNER_NAME'..."
./config.sh \
    --url "https://github.com/$REPO" \
    --token "$TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$RUNNER_LABELS" \
    --work "_work" \
    --unattended \
    --replace

# --- 6. Install as systemd user service ---
echo "→ Installing systemd user service '$SERVICE_NAME'..."
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/$SERVICE_NAME" <<EOF
[Unit]
Description=GitHub Actions self-hosted runner — Sweet-Butters/meme_project
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$RUNNER_DIR
ExecStart=$RUNNER_DIR/run.sh
Restart=on-failure
RestartSec=10
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user start "$SERVICE_NAME"

# --- 7. Suggest linger (required for service to run without login session) ---
if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
    echo ""
    echo "⚠  loginctl linger NOT enabled for $USER."
    echo "   The service will stop when you log out of WSL unless you run:"
    echo "       sudo loginctl enable-linger $USER"
    echo "   (one-time, persistent across reboots)"
fi

# --- 8. Status ---
sleep 3
echo ""
echo "=== Runner service status ==="
systemctl --user status "$SERVICE_NAME" --no-pager 2>/dev/null | head -12 || true
echo ""
echo "=== GitHub runner list ==="
gh api "repos/$REPO/actions/runners" -q '.runners[] | "  " + .name + " (" + .status + ")  labels: " + ([.labels[].name] | join(","))'
echo ""
echo "✓ Done. Workflows on this repo with 'runs-on: self-hosted' will now execute here."
echo "  Logs: journalctl --user -u $SERVICE_NAME -f"
echo "  Stop: systemctl --user stop $SERVICE_NAME"
echo "  Removal: ./config.sh remove --token \$(gh api -X POST repos/$REPO/actions/runners/remove-token -q .token)"
