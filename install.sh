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
#   - adds narrowly-scoped sudoers rules for display sleep and clean shutdown
#   - sets the Pi to boot to the console (MarqueeMark draws the screen itself)
#   - installs and starts the systemd service
#   - reboots at the end (10 second countdown, Ctrl+C to cancel)
#
# Safe to re-run: this is also how you UPDATE. It re-downloads
# marqueemark.py, preserves any extra flags on your existing service,
# and leaves your art, calibration.json, and lastgame.json alone.
# On an update it restarts the service instead of rebooting.

set -euo pipefail

# Test builds can point at an exact commit without changing the normal public
# installer URL. This is especially useful for validating installer updates
# on hardware before merging them.
REPO_RAW="${MARQUEEMARK_REPO_RAW:-https://raw.githubusercontent.com/beastech/marqueemark/main}"
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
sudo mkdir -p "$INSTALL_DIR/art" "$INSTALL_DIR/bases" "$INSTALL_DIR/cache/mini-marquees"
sudo chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"

# Always try to fetch the latest version, so re-running this script is
# how you update. Download to a temp file first: a failed or partial
# download must never clobber a working install.
IS_UPDATE=0
[ -f "$SERVICE" ] && IS_UPDATE=1
BOOT_CONFIG_CHANGED=0
STARTUP_BOOT_PRE=""
SHUTDOWN_ANIMATION_STOP=""
SERVICE_STOP_TIMEOUT=5

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
install_builtin_base "electrocoin-alt-9-bottom-fixed.png"
install_builtin_base "neogeo-one-slot.png"
install_builtin_base "neogeo-two-slot-red-left-one-slot-scale-v12.png"
install_builtin_base "neogeo-four-slot-red-low-v5.png"
install_builtin_base "neogeo-six-slot-red-clean-final-v8.png"
install_builtin_base "ultrawide-viewport-test.png"

# ----------------------------------------------------------- permissions
say "Granting serial and display access"
sudo usermod -aG dialout,video,render,input "$USER_NAME"

say "Adding display-sleep and clean-shutdown sudoers rules"
SUDOERS_TMP="$(mktemp)"
printf '%s\n' \
  "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/tee /sys/class/graphics/fb0/blank" \
  "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff" \
  > "$SUDOERS_TMP"
if [ ! -x /usr/sbin/visudo ] || \
   ! sudo /usr/sbin/visudo -cf "$SUDOERS_TMP" >/dev/null; then
  rm -f "$SUDOERS_TMP"
  fail "Could not validate the MarqueeMark sudoers rules; no rules were installed."
fi
sudo install -m 440 "$SUDOERS_TMP" /etc/sudoers.d/marqueemark
rm -f "$SUDOERS_TMP"

# ----------------------------------------------------------- console boot
if command -v raspi-config >/dev/null; then
  say "Setting boot to console (MarqueeMark draws the screen directly)"
  sudo raspi-config nonint do_boot_behaviour B1 || true
else
  echo "raspi-config not found — skip console-boot step (set it manually if needed)."
fi

# Releases before this fix hid tty1 to prevent shutdown text appearing after
# MarqueeMark's final black frame.  That also removed the only local recovery
# path when networking or early boot failed.  Repair affected installations
# without replacing cmdline.txt, so unrelated options added later are kept.
CMDLINE_FILE=""
for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  if [ -f "$candidate" ]; then
    CMDLINE_FILE="$candidate"
    break
  fi
done
if [ -n "$CMDLINE_FILE" ] && \
   { [ -f "$CMDLINE_FILE.marqueemark-console-backup" ] || \
     [ -f "$CMDLINE_FILE.marqueemark-backup" ]; }; then
  if ! grep -qw 'console=tty1' "$CMDLINE_FILE"; then
    say "Restoring the local recovery console"
    sudo sed -i 's/$/ console=tty1/' "$CMDLINE_FILE"
    BOOT_CONFIG_CHANGED=1
  fi
  # These three arguments were added by the affected installer and could be
  # duplicated each time it ran. Remove every copy while retaining all other
  # arguments, including user-supplied video= modes.
  if grep -Eq '(^|[[:space:]])(loglevel=0|systemd\.show_status=false|vt\.global_cursor_default=0)([[:space:]]|$)' \
      "$CMDLINE_FILE"; then
    sudo sed -i \
      -e 's/[[:space:]]loglevel=0//g' \
      -e 's/[[:space:]]systemd.show_status=false//g' \
      -e 's/[[:space:]]vt.global_cursor_default=0//g' \
      "$CMDLINE_FILE"
    BOOT_CONFIG_CHANGED=1
  fi
  if ! systemctl is-enabled getty@tty1.service >/dev/null 2>&1; then
    BOOT_CONFIG_CHANGED=1
  fi
  sudo systemctl enable getty@tty1.service >/dev/null 2>&1 || true
  echo "  tty1 recovery login enabled; unrelated boot options were preserved."
fi

# ----------------------------------------------------------- boot splash
# Keep the stock, recoverable boot presentation unless the owner explicitly
# enables the Neo Geo theme. The choice persists in /opt across later updates.
# Set MARQUEEMARK_NEO_GEO_SPLASH=1 to enable or =0 to disable it.
SPLASH_MARKER="$INSTALL_DIR/neo_geo_splash.enabled"
RECOVERY_GETTY="getty@tty1.service"
case "${MARQUEEMARK_NEO_GEO_SPLASH:-}" in
  1|true|yes|on) touch "$SPLASH_MARKER" ;;
  0|false|no|off) rm -f "$SPLASH_MARKER" ;;
esac

install_startup_splash() {
  if ! command -v plymouth-set-default-theme >/dev/null; then
    echo "  Plymouth is not installed — skip optional Neo Geo startup splash."
    return
  fi

  local theme_dir="/usr/share/plymouth/themes/marqueemark-startup"
  local theme_file script_file image_file
  theme_file="$(mktemp)"
  script_file="$(mktemp)"
  image_file="$(mktemp)"
  if ! curl -fsSL "$REPO_RAW/plymouth/marqueemark-startup/marqueemark-startup.plymouth" -o "$theme_file" || \
     ! curl -fsSL "$REPO_RAW/plymouth/marqueemark-startup/marqueemark-startup.script" -o "$script_file" || \
     ! curl -fsSL "$REPO_RAW/plymouth/marqueemark-startup/splash.png" -o "$image_file"; then
    rm -f "$theme_file" "$script_file" "$image_file"
    echo "  could not download Neo Geo startup splash — keep the current boot theme."
    return
  fi

  say "Installing the optional Neo Geo startup splash"
  sudo install -d -m 755 "$theme_dir"
  sudo install -m 644 "$theme_file" "$theme_dir/marqueemark-startup.plymouth"
  sudo install -m 644 "$script_file" "$theme_dir/marqueemark-startup.script"
  sudo install -m 644 "$image_file" "$theme_dir/splash.png"
  rm -f "$theme_file" "$script_file" "$image_file"
  sudo plymouth-set-default-theme -R marqueemark-startup
  BOOT_CONFIG_CHANGED=1
}

configure_cloud_console_output() {
  # Raspberry Pi OS cloud-init units duplicate boot-stage output to tty1.
  # That text remains in the console buffer and flashes when KMS is released
  # at shutdown. Keep it in journald for diagnostics while leaving the tty1
  # login itself enabled as a recovery path.
  local unit dropin_dir dropin_file
  for unit in cloud-config cloud-final cloud-init-local cloud-init-main cloud-init-network; do
    dropin_dir="/etc/systemd/system/$unit.service.d"
    dropin_file="$dropin_dir/marqueemark-console.conf"
    sudo install -d -m 755 "$dropin_dir"
    printf '%s\n' '[Service]' 'StandardOutput=journal' 'StandardError=journal' \
      | sudo tee "$dropin_file" >/dev/null
  done
  BOOT_CONFIG_CHANGED=1
}

if [ -f "$SPLASH_MARKER" ]; then
  install_startup_splash
  configure_cloud_console_output
  # agetty redraws its prompt whenever tty1 becomes active, including when
  # SDL releases KMS during shutdown. Keep local recovery on tty2 so the
  # presentation tty can remain black without removing console access.
  RECOVERY_GETTY="getty@tty2.service"
  STARTUP_BOOT_PRE="ExecStartPre=/bin/sh -c 'if [ \"\$(/usr/bin/systemctl is-system-running 2>/dev/null)\" = starting ]; then /usr/bin/touch /run/marqueemark/system-boot; fi'"
  SHUTDOWN_ANIMATION_STOP="ExecStop=/bin/sh -c 'if [ -e /run/marqueemark/cabinet-poweroff ]; then /bin/rm -f /run/marqueemark/cabinet-poweroff; elif [ \"\$(/usr/bin/systemctl is-system-running 2>/dev/null)\" = stopping ]; then /bin/kill -USR1 \"\$MAINPID\"; /bin/sleep 11; fi'"
  SERVICE_STOP_TIMEOUT=15
  sudo systemctl enable --now getty@tty2.service >/dev/null 2>&1 || true
  sudo systemctl disable --now getty@tty1.service >/dev/null 2>&1 || true
  # A user who explicitly selects the cabinet presentation also opts into a
  # quiet handoff. These options suppress routine kernel/systemd chatter
  # without removing local console recovery on tty2.
  if [ -n "$CMDLINE_FILE" ]; then
    for QUIET_ARG in loglevel=0 systemd.show_status=false vt.global_cursor_default=0; do
      if ! grep -qw "$QUIET_ARG" "$CMDLINE_FILE"; then
        sudo sed -i "s/$/ $QUIET_ARG/" "$CMDLINE_FILE"
        BOOT_CONFIG_CHANGED=1
      fi
    done
  fi
else
  sudo systemctl enable --now getty@tty1.service >/dev/null 2>&1 || true
  if command -v plymouth-set-default-theme >/dev/null && \
     [ "$(plymouth-set-default-theme 2>/dev/null || true)" = "marqueemark-startup" ]; then
    for SAFE_PLYMOUTH_THEME in pix spinner text; do
      if [ -d "/usr/share/plymouth/themes/$SAFE_PLYMOUTH_THEME" ]; then
        say "Restoring the standard Plymouth boot theme"
        sudo plymouth-set-default-theme -R "$SAFE_PLYMOUTH_THEME"
        BOOT_CONFIG_CHANGED=1
        break
      fi
    done
  fi
  for CLOUD_UNIT in cloud-config cloud-final cloud-init-local cloud-init-main cloud-init-network; do
    sudo rm -f "/etc/systemd/system/$CLOUD_UNIT.service.d/marqueemark-console.conf"
  done
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
  DISPLAY_LAYOUT="mini"
  if [ -t 0 ]; then
    echo
    echo "Which display are you configuring?"
    echo "  1) Mini Marquee (original portrait display)"
    echo "  2) Ultrawide Marquee (wide digital display)"
    read -r -p "Choose 1 or 2 [1]: " DISPLAY_CHOICE
    case "$DISPLAY_CHOICE" in
      2) DISPLAY_LAYOUT="ultrawide" ;;
      *) DISPLAY_LAYOUT="mini" ;;
    esac
  else
    echo "  non-interactive install: defaulting to Mini Marquee"
    echo "  (change later from Admin, or use --layout ultrawide in the service)"
  fi
  RUN_ARGS="--rotate $ROTATE --layout $DISPLAY_LAYOUT"
fi

# ---------------------------------------------------------------- service
say "Installing systemd service"
sudo tee "$SERVICE" >/dev/null <<UNIT
[Unit]
Description=MarqueeMark digital marquee
After=$RECOVERY_GETTY

[Service]
User=$USER_NAME
SupplementaryGroups=video render input dialout
RuntimeDirectory=marqueemark
RuntimeDirectoryMode=0755
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=SDL_AUDIODRIVER=dummy
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=$INSTALL_DIR
$STARTUP_BOOT_PRE
ExecStart=/usr/bin/python3 $INSTALL_DIR/marqueemark.py $RUN_ARGS
# systemd considers getty started before agetty has necessarily printed its
# banner. Wait until both it and MarqueeMark have settled, then reset the
# underlying tty buffer while KMS owns the visible display.
ExecStartPost=+/bin/sh -c 'sleep 2; printf "\\x1bc\\x1b[2J\\x1b[H" > /dev/tty1'
$SHUTDOWN_ANIMATION_STOP
# Pygame restores the text console while exiting, so blank only after its
# process is gone. Plymouth then owns a black reboot/shutdown background.
ExecStopPost=-/bin/sh -c 'printf 1 | /usr/bin/sudo -n /usr/bin/tee /sys/class/graphics/fb0/blank >/dev/null'
Restart=always
RestartSec=3
TimeoutStopSec=$SERVICE_STOP_TIMEOUT
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
  if [ "$BOOT_CONFIG_CHANGED" -eq 1 ]; then
    echo "  Boot recovery settings changed; reboot to apply and verify them: sudo reboot"
  else
    echo "  No reboot needed. Watch it with: journalctl -u marqueemark -f"
  fi
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
