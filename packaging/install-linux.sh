#!/usr/bin/env bash
set -euo pipefail

SCRIPT_SRC="$(dirname "$(readlink -f "$0")")/autoping.py"

install_pkg() {
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip iputils-ping
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip iputils-ping
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed python iputils-ping
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper -n install python3 python3-pip iputils-ping
  else
    echo "Unsupported package manager" >&2
    exit 1
  fi
}

install_pkg
sudo cp "$SCRIPT_SRC" /usr/local/bin/autoping
sudo chmod +x /usr/local/bin/autoping
echo "AutoPing installed. Run: autoping ping 1.1.1.1 -c 4 -W 800"
