#!/usr/bin/env bash
#
# MarqueeMark installer — https://github.com/beastech/marqueemark
#
# Run as your normal user (NOT root) on a fresh Raspberry Pi OS (64-bit):
#   curl -fsSL https://raw.githubusercontent.com/beastech/marqueemark/main/install.sh | bash
#
# What it does:
#   - installs dependencies (python3-serial, python3-pygame)
#   - creates /opt/marqueemark and downloads marqueemark.py
#   - grants your user serial + display access (dialout/video/render/input)
#   - adds the one-command sudoers rule used for display sleep
#   - sets the Pi to boot to the console (MarqueeMark draws the screen itself)
#   - installs and starts the systemd service
#
# Safe to re-run; it updates marqueemark.py and leaves your art,
# calibration.json, and lastgame.json alone.

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/beastech/marqueemark/main"
INSTALL_DIR="/opt/marqueemark"
SERVICE="/etc/systemd/system/marqueemark.service"

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- checks
[ "$(id -u)" -eq 0 ] && fail "Run as your normal user, not root (the script uses sudo where needed)."
command -v sudo >/dev/null || fail "sudo is required."
[ "$(uname -m)" = "aarch64" ] || fail "64-bit Raspberry Pi OS required (uname -m says: $(uname -m)). Reflash with the 64-bit image."

USER_NAME="$(id -un)"
say "Installing MarqueeMark for user: $USER_NAME"

# ----------------------------------------------------------- dependencies
say "Installing dependencies (this can take a minute)"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-serial python3-pygame

# ----------------------------------------------------------------- files
say "Setting up $INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR/art"
sudo chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"

say "Downloading marqueemark.py"
curl -fsSL "$REPO_RAW/marqueemark.py" -o "$INSTALL_DIR/marqueemark.py" \
  || fail "Could not download marqueemark.py — check your internet connection."

# ----------------------------------------------------------- permissions
say "Granting serial and display access"
sudo usermod -aG dialout,video,render,input "$USER_NAME"

say "Adding display-sleep sudoers rule (one specific command only)"
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/tee /sys/class/graphics/fb0/blank" \
  | sudo tee /etc/sudoers.d/marqueemark >/dev/null
sudo chmod 440 /etc/sudoers.d/marqueemark

# ----------------------------------------------------------- console boot
if command -v raspi-config >/dev/null; then
  say "Setting boot to console (MarqueeMark draws the screen directly)"
  sudo raspi-config nonint do_boot_behaviour B1 || true
else
  echo "raspi-config not found — skip console-boot step (set it manually if needed)."
fi

# -------------------------------------------------------------- rotation
# Panels mount in portrait; which value is right-side-up depends on which
# edge the ribbon cable exits. Default 90; calibration's 'p' preview will
# tell you if it should be 270 (art upside down = use the other value).
ROTATE=90
if [ -t 0 ]; then
  printf '\nPanel rotation [90/270/0/180] (default 90 — portrait): '
  read -r ans || true
  case "${ans:-}" in 90|270|0|180) ROTATE="$ans" ;; esac
fi
say "Using --rotate $ROTATE (edit $SERVICE later to change)"

# ---------------------------------------------------------------- service
say "Installing systemd service"
sudo tee "$SERVICE" >/dev/null <<UNIT
[Unit]
Description=MarqueeMark digital marquee
After=multi-user.target

[Service]
User=$USER_NAME
SupplementaryGroups=video render input dialout
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=SDL_AUDIODRIVER=dummy
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/marqueemark.py --rotate $ROTATE
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now marqueemark

# ------------------------------------------------------------------ done
HOST="$(hostname)"
cat <<DONE

=========================================================================
 MarqueeMark is installed and running.

 Next steps:
   1. ART:        open  http://$HOST.local:8080/admin  in a browser on
                  your network and drag in your marquee PNGs (MAME short
                  names: mslug.png, kof95.png, ... plus generic.png).
   2. CALIBRATE:  with the panel mounted (painter's tape first), run:
                    sudo systemctl stop marqueemark
                    cd $INSTALL_DIR
                    SDL_VIDEODRIVER=kmsdrm python3 marqueemark.py \\
                        --rotate $ROTATE --calibrate
                    sudo systemctl start marqueemark
   3. OBS:        add a Browser Source:  http://$HOST.local:8080/overlay

 Watch it live:   journalctl -u marqueemark -f
 NOTE: group changes need a log-out/log-in (or reboot) to take effect —
 a reboot now is the simplest way to finish:  sudo reboot
=========================================================================
DONE
