#!/usr/bin/env bash
# Installs STB as a systemd service for the given Linux user.
#
# Usage:
#   deploy/install.sh [username]
#
# Defaults to the current user if no username is given. Expects the repo to
# already be checked out at ~<username>/STB with a virtualenv at
# ~<username>/STB/.venv (see README.md "Setup").
set -euo pipefail

STB_USER="${1:-$USER}"
HOME_DIR=$(getent passwd "$STB_USER" | cut -d: -f6)

if [ -z "$HOME_DIR" ]; then
    echo "No such user: $STB_USER" >&2
    exit 1
fi

if [ ! -x "$HOME_DIR/STB/.venv/bin/python" ]; then
    echo "Expected a virtualenv at $HOME_DIR/STB/.venv - run the Setup steps in README.md first." >&2
    exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
sudo cp "$SCRIPT_DIR/stb@.service" /etc/systemd/system/stb@.service
sudo systemctl daemon-reload
sudo systemctl enable --now "stb@${STB_USER}.service"

echo "Installed and started stb@${STB_USER}.service"
echo "Check status: sudo systemctl status stb@${STB_USER}.service"
echo "Follow logs:  journalctl -u stb@${STB_USER}.service -f"
