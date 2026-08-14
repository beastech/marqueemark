# MarqueeMark

**A digital mini-marquee for the Neo Geo MVS.** MarqueeMark replaces a mini
marquee card with a small LCD panel that *always shows the correct game* —
it listens to a TerraOnion NeoSD Pro flash cart over USB and switches the
marquee art the instant you load or change a game. It also serves a live
"now playing" overlay for OBS so your stream always shows what's running.

No original hardware is modified. The panel mounts to the back of the
marquee plexi the same way the paper cards did, and everything is
reversible.

## Features

- **Automatic game detection** via the NeoSD Pro's USB serial interface
  (a previously undocumented protocol — see [How it works](#how-it-works))
- **Instant art switching** with a smooth fade, including when swapping
  virtual slots on the cart
- **Power-cycle aware**: the cart always auto-boots Flash Slot 1 after
  power-off, and MarqueeMark shows the right art within seconds of the
  cabinet powering on — before you touch anything
- **Display sleep**: when the cabinet turns off, the panel's backlight
  shuts down too; it wakes automatically with the cab
- **Interactive calibration**: align the image to your marquee window with
  arrow keys — including a tilt adjustment (0.1° steps) that squares up a
  slightly crooked panel mount without re-taping
- **OBS stream overlay**: a browser source URL that shows the current
  game's mini-marquee art in the corner of your stream, updating live
- Runs headless as a systemd service; survives crashes, USB unplugs, and
  power cycles

## Hardware

| Part | Notes |
|---|---|
| [8" IPS LCD panel kit (1024x768, HDMI driver board)](https://amzn.to/4g3kyUs) | Chimei Innolux HJ080IA-01E class. Active area covers the standard 4.44" x 5.44" mini-marquee window with overscan to hide the bezel. Includes its own USB-to-barrel power lead, which can run from one of the Pi's USB-A ports. |
| [CanaKit Raspberry Pi 4 Starter Kit (2 GB)](https://amzn.to/3SnVsro) | One box covers the Pi 4 Model B, the correct micro-HDMI display cable, a proper 3.5A USB-C power supply, a 32 GB microSD card, case, heatsinks, and an inline power switch. 2 GB of RAM is plenty — the Pi only renders images. Reflash the included SD card per step 1 (skip the pre-loaded image), and skip installing the fan: heatsinks alone are enough for this workload, and a fan just pulls cabinet dust through the case. |
| [Double-sided mounting tape](https://amzn.to/45NlqaV) | Final panel mounting to the back of the marquee plexi. |
| [Painter's tape](https://amzn.to/4wI9rHr) | Temporary mounting while you align and calibrate — commit to the strong tape only after calibration looks right. |
| [USB-A to Micro-USB cable, 5 ft](https://amzn.to/4c5oxi0) | Pi to the NeoSD Pro's USB port (the cart uses Micro-USB). |
| TerraOnion NeoSD Pro | The flash cart. MarqueeMark reads its game announcements; it does not modify the cart in any way. |

*The hardware links above are Amazon affiliate links — buying through them
supports this project at no cost to you.*

**Marquee art is not included** (it's copyrighted). Mini-marquee art packs
using MAME short-name file naming (`mslug.png`, `kof95.png`, ...) are
available to registered users at EmuMovies. Drop the PNGs into the `art/`
folder, and add a `generic.png` (a generic Neo Geo marquee) which is used
as the fallback image.

## How it works

The NeoSD Pro's USB port appears as a standard serial device
(`/dev/ttyACM0`, STM32 CDC, VID 0483 PID 5740) powered by the cartridge
slot — it enumerates when the cab is on and vanishes when it's off.

Whenever a game is loaded (menu load, RAM load, or virtual-slot switch),
the cart spontaneously broadcasts a 61-byte frame. As far as we know this
protocol was previously undocumented; it was reverse-engineered for this
project in July 2026:

```
offset 0-2    magic 99 88 3A
offset 3-4    u16 LE  zero-based menu slot index
                      (Flash Slots 1-4 announce as 0-3, RAM slot as 4)
offset 5-6    u16 LE  game's index in the SD card library list
offset 7-8    u16 LE  NGH catalog number, BCD-encoded (0x0269 = NGH-269)
offset 9-10   reserved (zero)
offset 11-43  short name  — 33-byte field, null-terminated, stale bytes
                            after the terminator (reused buffer)
offset 44-60  full title  — 17-byte field, may be truncated with NO
                            terminator
```

Notes: RAM loads may announce twice (MarqueeMark dedupes). The RAM slot's
contents are destroyed at power-off and the cart always auto-boots Flash
Slot 1 — MarqueeMark tracks what lives in each flash slot so the marquee
is correct from power-on. The cart never speaks during auto-boot, which is
why that tracking exists.

MarqueeMark itself is one Python file: a serial listener, a pygame
renderer that draws directly to the display (no desktop needed), a small
state store, and an HTTP/SSE server for the OBS overlay.

## Installation

### Quick install (recommended)

1. Download **Raspberry Pi Imager** from
   [raspberrypi.com/software](https://www.raspberrypi.com/software)
   (Windows, macOS, and Linux) and install it.
2. Open Imager and set:
   - **Choose Device** → Raspberry Pi 4
   - **Choose OS** → **Raspberry Pi OS (64-bit)** (the top recommended
     option once a device is picked — this specific 64-bit build is
     required)
   - **Choose Storage** → your microSD card
3. Before writing, click the settings gear (OS customisation) and set a
   **hostname** (e.g. `marquee`), **enable SSH**, and add your **Wi-Fi**
   credentials. This is what lets you reach the Pi headless — no
   keyboard, mouse, or monitor needed for setup.
4. Write the image, boot the Pi, and SSH in
   (`ssh <username>@marquee.local`).
5. Run:

```bash
curl -fsSL https://raw.githubusercontent.com/beastech/marqueemark/main/install.sh | bash
```

You'll be asked for the panel's rotation (90 or 270 — just pick the
default for now; see the note below). The script installs everything,
sets up the service, and prints your next steps.

6. **Reboot before doing anything else**: `sudo reboot`. This isn't
   optional — the Pi needs to boot to the console (not the desktop) for
   the display driver to work, and this is also when your new permissions
   take effect. Trying the web interface before this reboot will fail
   with a "connection refused" error.

After it comes back up, add your art (see
[Managing marquee art](#managing-marquee-art)), then continue with
[Mounting and calibration](#5-mounting-and-calibration) to size and
align the image once the panel is physically in place.

**If the image is upside down:** rotation only has two valid values for
a portrait-mounted panel, 90 and 270 — they're the same orientation
flipped, depending on which edge the panel's ribbon cable exits. If the
default (90) comes out upside down, swap it:

```bash
sudo systemctl edit --full marqueemark
```

Change `--rotate 90` to `--rotate 270` (or vice versa) on the
`ExecStart` line, save, then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart marqueemark
```

**To resize or reposition the image** (it doesn't fill the window
correctly, or needs to move): this is what calibration mode is for —
jump to [Mounting and calibration](#5-mounting-and-calibration). In
short: stop the service, run
`SDL_VIDEODRIVER=kmsdrm python3 marqueemark.py --rotate <90 or 270> --calibrate`,
use the arrow keys to position and `+`/`-` to resize (proportions are
locked automatically), `s` to save, `q` to quit, then start the service
again.

That's the whole install; the sections below describe the same steps
manually, for reference or customized setups.

### 1. Operating system

Flash **Raspberry Pi OS (64-bit)** with Raspberry Pi Imager. In the
imager's settings, set a hostname (e.g. `marquee`), enable SSH, and add
your Wi-Fi credentials so the Pi is reachable headless from first boot.

Boot the Pi, SSH in, and update:

```bash
sudo apt update && sudo apt full-upgrade -y
```

Set the Pi to boot to the console (no desktop — MarqueeMark draws to the
screen directly):

```bash
sudo raspi-config nonint do_boot_behaviour B1
```

### 2. Dependencies and files

```bash
sudo apt install -y python3-serial python3-pygame
sudo mkdir -p /opt/marqueemark/art
sudo chown -R $USER:$USER /opt/marqueemark
```

Copy `marqueemark.py` into `/opt/marqueemark/`. Art can be added later
from any browser via the built-in art manager (see
[Managing marquee art](#managing-marquee-art)) — no file-transfer tools
needed.

Give your user serial and display access (log out and back in after):

```bash
sudo usermod -aG dialout,video,render,input $USER
```

Allow the display-sleep feature to control panel power (one specific
command only — this is not general sudo access):

```bash
echo "$USER ALL=(root) NOPASSWD: /usr/bin/tee /sys/class/graphics/fb0/blank" \
  | sudo tee /etc/sudoers.d/marqueemark
sudo chmod 440 /etc/sudoers.d/marqueemark
```

### 3. Connect the hardware

- USB cable: Pi → NeoSD Pro's USB port. The cart only powers up with the
  cabinet, so don't worry if nothing appears until the cab is on.
- HDMI: Pi → panel driver board. Panel's power: driver board's
  USB-to-barrel cable → one of the Pi's USB-A ports.
- With the cab on, verify the cart enumerates:

```bash
ls /dev/ttyACM0
```

### 4. First run and rotation

The panel mounts in portrait, so the output must be rotated. Find your
rotation (90 or 270 depending on which edge the ribbon cable exits):

```bash
cd /opt/marqueemark
SDL_VIDEODRIVER=kmsdrm python3 marqueemark.py --rotate 90
```

Load a game on the NeoSD — art should appear. If it's upside down, use
`--rotate 270` instead. Ctrl+C to stop.

### 5. Mounting and calibration

1. Mount the panel behind the marquee window with **painter's tape**
   first. Bias it so live pixels overhang the window opening on all four
   edges.
2. Run calibration (keys are typed in your SSH terminal — no keyboard
   needs to be attached to the Pi):

```bash
SDL_VIDEODRIVER=kmsdrm python3 marqueemark.py --rotate 90 --calibrate
```

3. **Arrow keys** move the test pattern — put its top-left corner in the
   window's top-left corner. **`+` / `-`** resize it; the aspect ratio is
   locked to the real 4.44" x 5.44" mini-marquee card, so proportions are
   always correct. Size it to slightly overfill the opening.
4. If the mount is slightly crooked, square the image with the tilt keys:
   **`,` / `.`** (0.1°) and **`<` / `>`** (0.5°).
5. **`p`** previews with real art, **`t`** cycles the nudge step
   (5/1/20 px), **`s`** saves, **`q`** quits.
6. When it looks perfect, commit the panel with the double-sided tape and
   re-run calibration for a final touch-up if needed.

### 6. Run as a service

Create `/etc/systemd/system/marqueemark.service`:

```ini
[Unit]
Description=MarqueeMark digital marquee
After=multi-user.target

[Service]
User=YOUR_USERNAME
SupplementaryGroups=video render input dialout
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=SDL_AUDIODRIVER=dummy
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=/opt/marqueemark
ExecStart=/usr/bin/python3 /opt/marqueemark/marqueemark.py --rotate 90
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

(Replace `YOUR_USERNAME` and the rotation value with yours.) Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now marqueemark
```

Watch it live:

```bash
journalctl -u marqueemark -f
```

From now on the Pi boots straight into MarqueeMark. Power the cab on and
the marquee shows the Flash Slot 1 game automatically; switch games and it
follows; power the cab off and the panel blanks, then sleeps its
backlight ~10 seconds later.

## OBS stream overlay

MarqueeMark serves a transparent overlay page showing the current game's
mini-marquee art in the bottom-right corner, updating live.

In OBS: **Add → Browser Source**, URL
`http://YOUR_PI_HOSTNAME.local:8080/overlay`, width 1920, height 1080.
That's it. When no game is identified, the overlay shows `generic.png`.

Also available: `http://...:8080/current` returns the current game as
JSON, if you want to build your own integrations.

## Managing marquee art

With the service running, open **`http://YOUR_PI_HOSTNAME.local:8080/admin`**
from any browser on your network (PC or phone). Drag and drop your PNG
files onto the page — it shows every installed marquee as a thumbnail,
lets you delete files, and warns you if `generic.png` (the fallback
image) is missing.

Files must be named by MAME short name (`mslug.png`, `kof95.png`, ...);
only PNGs are accepted, and names are sanitized automatically. Because
the art lives on the Pi's Linux partition, this page is also the easiest
path from a Windows PC — no SD-card readers or SFTP tools required.

## Command-line options

| Option | Default | Purpose |
|---|---|---|
| `--port` | `/dev/ttyACM0` | NeoSD serial device |
| `--art` | `./art` | Art folder |
| `--rotate` | `0` | Output rotation: 0 / 90 / 180 / 270 |
| `--calibrate` | — | Interactive window calibration, then exit |
| `--idle` | `blank` | With no cart link: `blank` (dark, dies with the cab) or `generic` (stays lit — for a slot that usually holds a real cartridge) |
| `--keep-awake` | off | Never sleep the panel on link loss |
| `--http-port` | `8080` | Overlay server port |

## Troubleshooting

- **No `/dev/ttyACM0`**: the cart is slot-powered — the cab must be on.
  Check `dmesg | tail` for the "NeoSD Virtual Com Port" enumeration.
- **Permission denied on the serial port**: your user isn't in `dialout`
  (re-login after `usermod`).
- **Service runs but no journal output**: `PYTHONUNBUFFERED=1` is missing
  from the unit.
- **Art doesn't restore after a power cycle**: the slot map has to see
  each flash slot announced once — cycle through your virtual slots one
  time to seed it. Also confirm `/opt/marqueemark` is owned by the service
  user (root-owned files block `lastgame.json`).
- **Panel never sleeps**: verify the sudoers rule, and test your driver
  board manually: `echo 4 | sudo tee /sys/class/graphics/fb0/blank`
  should put it into standby (`echo 0` wakes it). Boards that show a
  permanent "NO SIGNAL" box instead can't use this feature — run with
  `--keep-awake`.
- **Wrong-direction arrow keys in calibration**: your `--rotate` value is
  flipped 180° from the panel's mounted orientation — use the other
  portrait value (90 ↔ 270). Press `p` first; if the sample art is
  upside down, that's the sign.

## Limitations & roadmap

- Detection requires a NeoSD Pro. With a real MVS cartridge in the slot
  the marquee shows the generic art (`--idle generic`) — automatic
  detection for real carts is the v2 goal.
- One panel/window per Pi HDMI output today; the Pi 4's dual HDMI makes a
  two-window build possible and it's on the roadmap.
- The NeoSD protocol here is unofficial and could change in future
  TerraOnion firmware. Firmware 1.07 behavior is what's documented above.

## Credits

Built by Britt at [Gamesboro](https://gamesboro.net). The NeoSD Pro USB
announcement protocol was reverse-engineered on real hardware for this
project. Not affiliated with or endorsed by TerraOnion or SNK.
