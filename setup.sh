#!/usr/bin/env bash
#
# IamBusy setup — creates the virtualenv, installs dependencies and writes a
# blank schedule_config.py.  Safe to re-run: existing config and data are kept.
#
#   ./setup.sh                 install + configure (asks a few questions)
#   ./setup.sh --service       also install a systemd service (needs sudo)
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
APP_DIR="$(pwd)"

NAME="" START="" PORT="" SERVICE=0 ASSUME_YES=0

usage() {
    cat <<USAGE
Usage: ./setup.sh [options]

  --name NAME       display name shown in the UI
  --start DATE      first day of week 1, YYYY-MM-DD (default: this week's Monday)
  --port PORT       port to listen on (default: 2026)
  --service         install and start a systemd service (needs sudo)
  -y, --yes         don't ask questions, use defaults for anything not given
  -h, --help        show this help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)    NAME="${2:?--name needs a value}"; shift 2 ;;
        --start)   START="${2:?--start needs a value}"; shift 2 ;;
        --port)    PORT="${2:?--port needs a value}"; shift 2 ;;
        --service) SERVICE=1; shift ;;
        -y|--yes)  ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

[[ -t 0 ]] || ASSUME_YES=1

ask() {  # ask VAR "Prompt" "default"
    local var="$1" prompt="$2" default="$3" reply=""
    [[ -n "${!var}" ]] && return
    if [[ $ASSUME_YES -eq 0 ]]; then
        read -r -p "$prompt [$default]: " reply
    fi
    printf -v "$var" '%s' "${reply:-$default}"
}

# ── 1. Python + virtualenv ───────────────────────────────────
command -v python3 >/dev/null || { echo "python3 not found — install Python 3.9+ first." >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
    || { echo "Python 3.9+ is required (found $(python3 -V))." >&2; exit 1; }

if [[ ! -x .venv/bin/python ]]; then
    echo "==> Creating virtualenv in .venv"
    python3 -m venv .venv || {
        echo "Could not create the virtualenv (on Debian/Ubuntu: sudo apt install python3-venv)." >&2
        exit 1
    }
fi
echo "==> Installing dependencies"
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt

# ── 2. Blank config ──────────────────────────────────────────
if [[ -f schedule_config.py ]]; then
    echo "==> schedule_config.py already exists — keeping it"
else
    ask NAME  "Your name" "${USER:-Me}"
    ask START "First day of week 1 (YYYY-MM-DD)" \
        "$(python3 -c 'from datetime import date, timedelta as t; d = date.today(); print(d - t(days=d.weekday()))')"
    ask PORT  "Port" "2026"

    NAME="$NAME" START="$START" PORT="$PORT" .venv/bin/python - <<'PY'
import os
import re
import sys
from datetime import date

name, start, port = os.environ["NAME"], os.environ["START"], os.environ["PORT"]
try:
    d = date.fromisoformat(start)
except ValueError:
    sys.exit(f"Invalid start date {start!r} — expected YYYY-MM-DD.")
if not port.isdigit() or not 1 <= int(port) <= 65535:
    sys.exit(f"Invalid port {port!r}.")

values = {
    "USER_NAME": repr(name),
    "ACADEMIC_WEEK1_START": f"date({d.year}, {d.month}, {d.day})",
    "APP_PORT": str(int(port)),
}
text = open("schedule_config.example.py", encoding="utf-8").read()
for key, value in values.items():
    text, n = re.subn(
        rf"^({key}\b[^=\n]*= ).*$", lambda m: m.group(1) + value, text, flags=re.M,
    )
    if n != 1:
        sys.exit(f"Could not set {key} in schedule_config.example.py")
with open("schedule_config.py", "w", encoding="utf-8") as fh:
    fh.write(text)
PY
    echo "==> Wrote schedule_config.py (blank schedule)"
fi

PORT="$(.venv/bin/python -c 'import schedule_config as c; print(getattr(c, "APP_PORT", 2026))')"
HOST="$(.venv/bin/python -c 'import schedule_config as c; print(getattr(c, "APP_HOST", "0.0.0.0"))')"

# ── 3. Database ──────────────────────────────────────────────
.venv/bin/python -c 'import app' >/dev/null
COUNT="$(.venv/bin/python -c 'import db; print(len(db.get_all_activities()))')"
echo "==> Database ready ($COUNT activities)"

# ── 4. systemd service (optional) ────────────────────────────
if [[ $SERVICE -eq 1 ]]; then
    command -v systemctl >/dev/null || { echo "systemd not found — start the app manually instead." >&2; exit 1; }
    UNIT=/etc/systemd/system/iambusy.service
    INSTALL_UNIT=1
    if [[ -e $UNIT ]]; then
        reply=""
        [[ $ASSUME_YES -eq 0 ]] && read -r -p "$UNIT already exists. Replace it? [y/N]: " reply
        if [[ $reply =~ ^[Yy] ]]; then
            sudo cp "$UNIT" "$UNIT.bak"
            echo "==> Old unit saved as $UNIT.bak"
        else
            INSTALL_UNIT=0
            echo "==> $UNIT already exists — leaving it untouched"
        fi
    fi
    if [[ $INSTALL_UNIT -eq 1 ]]; then
        echo "==> Installing $UNIT (sudo)"
        sudo tee "$UNIT" >/dev/null <<UNIT_EOF
[Unit]
Description=IamBusy schedule
After=network.target

[Service]
User=$(id -un)
Group=$(id -gn)
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/gunicorn --workers 2 --bind $HOST:$PORT app:app
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT_EOF
        sudo systemctl daemon-reload
        sudo systemctl enable iambusy.service
        sudo systemctl restart iambusy.service
        echo "==> Service running — it restarts on crash and starts at boot"
        echo "    Status: systemctl status iambusy    Logs: journalctl -u iambusy -f"
    fi
fi

# ── Done ─────────────────────────────────────────────────────
echo
echo "All set."
if [[ $SERVICE -eq 0 ]]; then
    echo "  Start it:       .venv/bin/gunicorn --bind $HOST:$PORT app:app"
    echo "  Run at boot:    ./setup.sh --service"
fi
echo "  Open:           http://localhost:$PORT"
echo "  Add activities: http://localhost:$PORT/manage"
