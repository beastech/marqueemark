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
#   - installs a starter art/generic.png (fallback marquee) if missing
#   - grants your user serial + display access (dialout/video/render/input)
#   - adds the one-command sudoers rule used for display sleep
#   - sets the Pi to boot to the console (MarqueeMark draws the screen itself)
#   - installs and starts the systemd service
#   - reboots at the end (10 second countdown, Ctrl+C to cancel)
#
# Safe to re-run: this is also how you UPDATE. It re-downloads
# marqueemark.py, preserves any extra flags on your existing service,
# and leaves your art, calibration.json, and lastgame.json alone.
# On an update it restarts the service instead of rebooting.

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
sudo apt-get install -y -qq python3-serial python3-pygame python3-pil

# ----------------------------------------------------------------- files
say "Setting up $INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR/art" "$INSTALL_DIR/bases"
sudo chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"

# Always try to fetch the latest version, so re-running this script is
# how you update. Download to a temp file first: a failed or partial
# download must never clobber a working install.
IS_UPDATE=0
[ -f "$SERVICE" ] && IS_UPDATE=1

say "Downloading marqueemark.py"
TMP_PY="$(mktemp)"
if curl -fsSL "$REPO_RAW/marqueemark.py" -o "$TMP_PY" && [ -s "$TMP_PY" ]; then
  if [ -f "$INSTALL_DIR/marqueemark.py" ] \
     && cmp -s "$TMP_PY" "$INSTALL_DIR/marqueemark.py"; then
    echo "  already up to date"
  else
    mv "$TMP_PY" "$INSTALL_DIR/marqueemark.py"
    echo "  updated $INSTALL_DIR/marqueemark.py"
  fi
  rm -f "$TMP_PY"
elif [ -f "$INSTALL_DIR/marqueemark.py" ]; then
  rm -f "$TMP_PY"
  say "Download failed, keeping the copy already in $INSTALL_DIR"
  echo "  (check your internet connection if you were expecting an update)"
else
  rm -f "$TMP_PY"
  fail "Could not download marqueemark.py from $REPO_RAW/marqueemark.py
  This usually means the repo is private/unpublished, or you are offline.
  Workaround: copy marqueemark.py into $INSTALL_DIR yourself, then re-run."
fi

INSTALL_VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$INSTALL_DIR/marqueemark.py" | head -n 1)"
[ -n "$INSTALL_VERSION" ] && echo "  installed version: $INSTALL_VERSION"

# Title labels for MAME-style artwork names. This is optional at runtime
# (the admin page falls back to readable filenames), but updating it here
# makes selectors show proper game titles such as "Tecmo World Soccer '96".
say "Downloading game title labels"
TMP_TITLES="$(mktemp)"
if curl -fsSL "$REPO_RAW/game_titles.json" -o "$TMP_TITLES" && [ -s "$TMP_TITLES" ]; then
  mv "$TMP_TITLES" "$INSTALL_DIR/game_titles.json"
else
  rm -f "$TMP_TITLES"
  echo "  could not download title labels; filename labels will be used"
fi

# ------------------------------------------------------- starter artwork
# A fallback marquee so the panel shows something on first boot instead
# of a black rectangle. Only installed if the user has none: an update
# must never overwrite artwork they chose themselves.
if [ ! -f "$INSTALL_DIR/art/generic.png" ]; then
  say "Installing starter fallback marquee (art/generic.png)"
  TMP_PNG="$(mktemp)"
  if curl -fsSL "$REPO_RAW/art/generic.png" -o "$TMP_PNG" && [ -s "$TMP_PNG" ]; then
    mv "$TMP_PNG" "$INSTALL_DIR/art/generic.png"
    echo "  you can replace it any time from the admin page"
  else
    rm -f "$TMP_PNG"
    echo "  could not download it, skipping (the marquee will be blank until"
    echo "   you add art/generic.png from the admin page)"
  fi
else
  echo "  keeping your existing art/generic.png"
fi

# Built-in cabinet layouts are application assets, not user-selected game
# artwork. Add a newly introduced template on update if it is missing, while
# never replacing an existing local copy (so users may still customise it).
install_builtin_base() {
  local asset="$1" tmp
  if [ -f "$INSTALL_DIR/art/$asset" ]; then
    echo "  keeping existing built-in base: $asset"
    return
  fi
  tmp="$(mktemp)"
  if curl -fsSL "$REPO_RAW/art/$asset" -o "$tmp" && [ -s "$tmp" ]; then
    mv "$tmp" "$INSTALL_DIR/art/$asset"
    echo "  installed built-in base: $asset"
  else
    rm -f "$tmp"
    echo "  could not download built-in base: $asset"
  fi
}
say "Installing built-in marquee templates"
install_builtin_base "electrocoin-base.png"
install_builtin_base "neogeo-one-slot.png"
install_builtin_base "ultrawide-viewport-test.png"

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
# Panels mount in portrait, so only 90/270 are meaningful; they are the
# same orientation flipped, depending on which edge the panel's ribbon
# cable exits. We always start at 90 and let the admin page's "Flip 180"
# button handle the other case: it saves the rotation into
# calibration.json, which overrides this value from then on. No prompt
# here on purpose, most people have not mounted the panel yet at this
# point and could not answer the question anyway.
ROTATE=90

# On an update, keep any extra flags the user added to ExecStart
# (--idle generic, --keep-awake, --http-port, a flipped --rotate, etc.)
# so reinstalling never silently reverts their configuration.
EXTRA_ARGS=""
if [ -f "$SERVICE" ]; then
  EXISTING="$(grep -m1 '^ExecStart=' "$SERVICE" 2>/dev/null || true)"
  EXTRA_ARGS="${EXISTING#*marqueemark.py}"
  EXTRA_ARGS="$(echo "$EXTRA_ARGS" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
fi
if [ -n "$EXTRA_ARGS" ]; then
  RUN_ARGS="$EXTRA_ARGS"
  say "Keeping your existing options: $RUN_ARGS"
else
  RUN_ARGS="--rotate $ROTATE"
fi

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
ExecStart=/usr/bin/python3 $INSTALL_DIR/marqueemark.py $RUN_ARGS
Restart=always
RestartSec=3
TimeoutStopSec=5
KillMode=control-group

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now marqueemark

# ------------------------------------------------------------------ done
HOST="$(hostname)"
if [ "$IS_UPDATE" -eq 1 ]; then
cat <<UPDONE

=========================================================================
 MarqueeMark updated.
   ART + CALIBRATION:  http://$HOST.local:8080/admin
=========================================================================
UPDONE
else
cat <<DONE

=========================================================================
 MarqueeMark is installed.

 A reboot is required to finish: the Pi must boot to the console instead
 of the desktop for the display to work, and your new permissions take
 effect then too. The web interface will not respond until after it.

 After the reboot, everything happens in your browser:
   ART + CALIBRATION:  http://$HOST.local:8080/admin
   OBS BROWSER SOURCE: http://$HOST.local:8080/overlay

 (If http://$HOST.local does not resolve, use the Pi's IP address:
  run  hostname -I  to see it.)

 Watch the service live:  journalctl -u marqueemark -f
=========================================================================
DONE
fi

# An update does not need a reboot: the console-boot setting and group
# memberships are already in place from the first install, so restarting
# the service is enough and far less disruptive.
if [ "$IS_UPDATE" -eq 1 ]; then
  say "Update complete, restarting the service"
  sudo systemctl restart marqueemark
  echo "  No reboot needed. Watch it with: journalctl -u marqueemark -f"
  exit 0
fi

# First install: reboot with a countdown so it is automatic but never a
# surprise. Read from /dev/tty rather than stdin, since this script is
# normally run via "curl ... | bash" where stdin is the pipe.
if [ -e /dev/tty ] && (: >/dev/tty) 2>/dev/null; then
  if [ -n "${SSH_CONNECTION:-}" ]; then
    echo "You are connected over SSH: this connection will drop. Reconnect"
    echo "in about 30 seconds."
  fi
  echo
  for i in 10 9 8 7 6 5 4 3 2 1; do
    printf '\rRebooting in %2d seconds... (press Ctrl+C to cancel and reboot later) ' "$i"
    sleep 1
  done
  echo
  sudo reboot
else
  echo "No terminal detected, skipping the automatic reboot."
  echo "Finish the install by running:  sudo reboot"
fi
