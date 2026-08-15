#!/usr/bin/env bash
# Set ReciFi up on a fresh Raspberry Pi OS Lite install (Zero 2 W).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "==> system packages"
sudo apt-get update
# Pillow wheels exist for armv7/aarch64, but libopenjp2/libtiff are still
# needed at runtime; libusb + bluez cover the two transports.
sudo apt-get install -y python3-venv python3-dev libopenjp2-7 libtiff6 \
    libusb-1.0-0 bluez fonts-dejavu-core avahi-daemon

echo "==> virtualenv"
python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip wheel
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo "==> permissions"
sudo usermod -aG lp,bluetooth "$USER_NAME"
# Let a non-root user claim a vendor-class USB printer through libusb.
sudo tee /etc/udev/rules.d/99-recifi.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{bInterfaceClass}=="07", MODE="0660", GROUP="lp"
SUBSYSTEM=="usbmisc", KERNEL=="lp[0-9]*", MODE="0660", GROUP="lp"
EOF
sudo udevadm control --reload-rules

echo "==> service"
sed -e "s|/home/pi/ReciFi|$REPO_DIR|g" \
    -e "s|^User=pi|User=$USER_NAME|" -e "s|^Group=pi|Group=$USER_NAME|" \
    -e "s|/home/pi/.local|/home/$USER_NAME/.local|" \
    "$REPO_DIR/deploy/recifi.service" | sudo tee /etc/systemd/system/recifi.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now recifi

echo
echo "Done. UI: http://$(hostname).local:8080"
echo "Group changes need a re-login (or reboot) to take effect."
echo "Logs: journalctl -u recifi -f"
