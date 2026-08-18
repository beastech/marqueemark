# MarqueeMark

**A digital mini-marquee for the Neo Geo MVS using NeoSD Pro.** MarqueeMark replaces a mini
marquee card with a small LCD panel that *always shows the correct game*:
it listens to a TerraOnion NeoSD Pro flash cart over USB and switches the
marquee art the instant you load or change a game. It also serves a live
"now playing" overlay for OBS so your stream always shows what's running.

No original hardware is modified. The panel mounts to the back of the
marquee plexi the same way the paper cards did, and everything is
reversible.

## Features

- **Automatic game detection** via the NeoSD Pro's USB serial interface
  (a previously undocumented protocol, see [How it works](#how-it-works))
- **Instant art switching** with a smooth fade, including when swapping
  virtual slots on the cart
- **Power-cycle aware**: the cart always auto-boots Flash Slot 1 after
  power-off, and MarqueeMark shows the right art within seconds of the
  cabinet powering on, before you touch anything
- **Display sleep**: when the cabinet turns off, the panel's backlight
  shuts down too; it wakes automatically with the cab
- **Calibration from your browser**: position, resize, tilt, and flip the
  image from the admin page on any phone or PC while watching the panel
  update live. No SSH, no keyboard, no Linux required. Proportions are
  locked to the real mini-marquee card, so the image can never be
  stretched.
- **OBS stream overlay**: a browser source URL that shows the current
  game's mini-marquee art in the corner of your stream, updating live
- **Browser art manager**: drag and drop your marquee PNGs onto a web page
  to install them
- Runs headless as a systemd service; survives crashes, USB unplugs, and
  power cycles

## Hardware

| Part | Notes |
|---|---|
| [8" IPS LCD panel kit (1024x768, HDMI driver board)](https://amzn.to/4g3kyUs) | Chimei Innolux HJ080IA-01E class. Active area covers the standard 4.44" x 5.44" mini-marquee window with overscan to hide the bezel. Includes its own USB-to-barrel power lead, which can run from one of the Pi's USB-A ports. |
| [CanaKit Raspberry Pi 4 Starter Kit (2 GB)](https://amzn.to/3SnVsro) | One box covers the Pi 4 Model B, the correct micro-HDMI display cable, a proper 3.5A USB-C power supply, a 32 GB microSD card, case, heatsinks, and an inline power switch. 2 GB of RAM is plenty; the Pi only renders images. Reflash the included SD card per step 1 (skip the pre-loaded image), and skip installing the fan: heatsinks alone are enough for this workload, and a fan just pulls cabinet dust through the case. |
| [Double-sided mounting tape](https://amzn.to/45NlqaV) | Final panel mounting to the back of the marquee plexi. |
| [Painter's tape](https://amzn.to/4wI9rHr) | Temporary mounting while you align and calibrate; commit to the strong tape only after calibration looks right. |
| [USB-A to Micro-USB cable, 5 ft](https://amzn.to/4c5oxi0) | Pi to the NeoSD Pro's USB port (the cart uses Micro-USB). |
| TerraOnion NeoSD Pro | The flash cart. MarqueeMark reads its game announcements; it does not modify the cart in any way. |

*The hardware links above are Amazon affiliate links, buying through them
supports this project at no cost to you.*

**Marquee art is not included** Mini-marquee art packs
using MAME short-name file naming (`mslug.png`, `kof95.png`, ...) are
available to registered users at EmuMovies.

https://emumovies.com/files/file/1628-neo-geo-mvs-marquee-pack-mini/

Add the PNGs from the built-in art manager page. 
I include a "generic.png" image of the Gamesboro logo
so that you can align your image before downloading the pack.

## How it works

The NeoSD Pro's USB port appears as a standard serial device
(`/dev/ttyACM0`, STM32 CDC, VID 0483 PID 5740) powered by the cartridge
slot. It enumerates when the cab is on and vanishes when it's off.

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
offset 11-43  short name  - 33-byte field, null-terminated, stale bytes
                            after the terminator (reused buffer)
offset 44-60  full title  - 17-byte field, may be truncated with NO
                            terminator
```

Notes: RAM loads may announce twice (MarqueeMark dedupes). The RAM slot's
contents are destroyed at power-off and the cart always auto-boots Flash
Slot 1. MarqueeMark tracks what lives in each flash slot so the marquee
is correct from power-on. The cart never speaks during auto-boot, which is
why that tracking exists.

MarqueeMark itself is one Python file: a serial listener, a pygame
renderer that draws directly to the display (no desktop needed), a small
state store, and an HTTP server for the admin page and OBS overlay.

## Installation (quick install)

Three steps: flash the card, run one command, reboot. You do not need to
know Linux, and after the reboot everything else happens in a web
browser. This is the recommended path for everyone; a manual,
step-by-step version is documented further down under
[Manual installation](#manual-installation) for reference or customized
setups.

### Step 1: flash the SD card

1. Download **Raspberry Pi Imager** from
   [raspberrypi.com/software](https://www.raspberrypi.com/software)
   (Windows, macOS, and Linux) and install it.
2. Open Imager and set:
   - **Choose Device**: Raspberry Pi 4
   - **Choose OS**: **Raspberry Pi OS (64-bit)** (the top recommended
     option once a device is picked; this specific 64-bit build is
     required)
   - **Choose Storage**: your microSD card
3. Before writing, click the settings gear (OS customisation) and set a
   **hostname** (`marquee` is used in the examples below), **enable
   SSH**, and add your **Wi-Fi** credentials. Setting the hostname here
   is worth doing either way: it's what makes `marquee.local` work later.
4. Write the image.

### Step 2: run the installer

Pick whichever is easier for you. Both end up in the same place.

**Option A: with a keyboard and monitor (easiest, no SSH)**

Do this at a desk *before* taping the panel into your marquee, so the
screen is in its normal landscape orientation and the desktop is
readable. Once the panel is mounted in portrait the desktop will be
sideways, which is survivable but unpleasant to type against.

1. Connect the panel (or any HDMI monitor), a USB keyboard, and power up
   the Pi. It boots to the desktop.
2. If you didn't set Wi-Fi in Imager, connect now using the network icon
   in the taskbar.
3. Press **Ctrl+Alt+T** to open a terminal.
4. Type the install command:

```bash
curl -fsSL https://raw.githubusercontent.com/beastech/marqueemark/main/install.sh | bash
```

If you'd rather not type that by hand, open Chromium on the Pi, go to
this project's GitHub page, copy the command, and paste it into the
terminal with **Ctrl+Shift+V** (in a Linux terminal, plain Ctrl+V does
not paste).

The installer reboots the Pi when it finishes, so expect the desktop to
disappear and the marquee software to take over the screen.

**Option B: headless over SSH**

Boot the Pi with no monitor attached, then from another computer:

```bash
ssh <username>@marquee.local
```

Once connected, run the install command:

```bash
curl -fsSL https://raw.githubusercontent.com/beastech/marqueemark/main/install.sh | bash
```

### Step 3: let it reboot

When the installer finishes it counts down from 10 and reboots the Pi
automatically. This is required, not cosmetic: the Pi has to boot to the
console instead of the desktop for the display to work, and your new
permissions take effect at the same time. If you press Ctrl+C to cancel
the countdown, run `sudo reboot` yourself before using the web interface,
or it will refuse the connection.

If you installed over SSH, your connection will drop during the reboot.
Reconnect after about 30 seconds, or just move to your browser; you're
done with the terminal either way.

You are never asked about the panel's rotation. If the image comes out
upside down, one button on the admin page fixes it (see
[Calibrating the image](#calibrating-the-image)).

That's the whole install. Everything from here happens in a browser on
any device on your network: open **`http://marquee.local:8080/admin`** to
add art and calibrate the image. See
[Using the admin page](#using-the-admin-page) below.

Can't reach `marquee.local`? Use the Pi's IP address instead. Run
`hostname -I` on the Pi to see it, then browse to
`http://THAT_ADDRESS:8080/admin`. Note that if you never set a hostname
in Imager, the default is `raspberrypi.local`, not `marquee.local`.

## Updating MarqueeMark

To update to the latest version, run the same install command again:

```bash
curl -fsSL https://raw.githubusercontent.com/beastech/marqueemark/main/install.sh | bash
```

On a re-run the installer downloads the current version, keeps any
options you added to the service (such as `--idle generic` or
`--keep-awake`), and restarts the service instead of rebooting. Your art,
calibration, and slot history are left untouched.

## Keeping the Pi updated

The installer deliberately does not upgrade your operating system. A full
upgrade can take 10 to 20 minutes on a Pi, can stop to ask questions
mid-install, and isn't needed for MarqueeMark to run. That choice is left
to you.

This Pi will likely sit on your network for years, though, so it's worth
keeping patched. To bring it up to date once, whenever you like:

```bash
sudo apt update && sudo apt full-upgrade -y
```

To have it install security updates automatically from then on, one
command sets it up:

```bash
sudo apt install -y unattended-upgrades
```

On Raspberry Pi OS that enables daily security updates with no further
configuration. It runs quietly in the background and won't interrupt the
marquee.

### A note on network security

MarqueeMark's admin page has no password. Anyone who can reach the Pi on
your network can upload art, delete files, and change the calibration.
That's a deliberate trade for ease of setup, and it's fine on a normal
home network.

Do not port-forward this device or expose port 8080 to the internet. It
is designed to be reached only from inside your own network.

## Using the admin page

Open **`http://YOUR_PI_HOSTNAME.local:8080/admin`** from any browser on
your network, phone or PC. Everything you need after installation lives
here, and none of it requires SSH.

Note: the bare address (`http://YOUR_PI_HOSTNAME.local:8080/`) serves the
OBS overlay, not the admin page. Include `/admin`.

### Adding marquee art

Drag and drop your PNG files onto the drop zone. The page shows every
installed marquee as a thumbnail, lets you delete files, and warns you if
`generic.png` (the fallback image) is missing.

Files must be named by MAME short name (`mslug.png`, `kof95.png`, ...);
only PNGs are accepted, and names are sanitized automatically. Because
the art lives on the Pi's Linux partition, this page is also the easiest
path from a Windows PC: no SD-card readers or SFTP tools required.

If you're not sure what a game's short name is, you don't have to look it
up: load the game on the NeoSD Pro and MarqueeMark logs the exact name it
wants (`journalctl -u marqueemark -n 5`), or just watch which art fails to
appear.

### Calibrating the image

Mount the panel behind the marquee window with **painter's tape** first,
positioned so live pixels overhang the window opening on all four edges.
Then click **Start Calibration** on the admin page. A test pattern appears
on the panel and the controls become active. Watch the physical marquee
while you click; it updates live.

- **Arrow pad**: moves the image up, down, left, and right.
- **Center button**: cycles the nudge step (5px, 1px, 20px). Start coarse,
  finish on 1px.
- **Size + / -**: grows and shrinks the image. Proportions are locked to
  the real 4.44" x 5.44" mini-marquee card, so the image can never be
  stretched or distorted.
- **Tilt buttons**: rotate the image in 0.1° and 0.5° steps. Use this if
  the panel ended up slightly crooked when you taped it; there is no need
  to re-tape.
- **Flip 180°**: use this if the image is upside down. This is saved with
  the rest of your calibration, so it survives reboots.
- **Preview**: switches between the alignment test pattern and real
  marquee art, for a final check of how it actually looks.
- **Save** stores everything; **Cancel** discards it and returns the
  marquee to normal.

Aim to have the pattern slightly overfill the window opening on all four
sides, so no black edge is visible through the plexi. When it looks right,
commit the panel with the double-sided tape and re-run calibration for a
final touch-up if the panel shifted.

## Manual installation

You do not need any of this if you used the quick install above. These
are the same steps the installer performs, written out for anyone who
wants to do it by hand or adapt it to a different setup.

### 1. Operating system

Flash **Raspberry Pi OS (64-bit)** with Raspberry Pi Imager. In the
imager's settings, set a hostname (e.g. `marquee`), enable SSH, and add
your Wi-Fi credentials so the Pi is reachable headless from first boot.

Boot the Pi and SSH in. Optionally bring the OS up to date first (see
[Keeping the Pi updated](#keeping-the-pi-updated)); it isn't required for
MarqueeMark.

Set the Pi to boot to the console (no desktop, MarqueeMark draws to the
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

Copy `marqueemark.py` into `/opt/marqueemark/`. Art is added later from
the admin page, no file-transfer tools needed.

Give your user serial and display access (log out and back in after):

```bash
sudo usermod -aG dialout,video,render,input $USER
```

Allow the display-sleep feature to control panel power (one specific
command only, this is not general sudo access):

```bash
echo "$USER ALL=(root) NOPASSWD: /usr/bin/tee /sys/class/graphics/fb0/blank" \
  | sudo tee /etc/sudoers.d/marqueemark
sudo chmod 440 /etc/sudoers.d/marqueemark
```

### 3. Connect the hardware

- USB cable: Pi to the NeoSD Pro's USB port. The cart only powers up with
  the cabinet, so don't worry if nothing appears until the cab is on.
- HDMI: Pi to the panel driver board. Panel's power: driver board's
  USB-to-barrel cable to one of the Pi's USB-A ports.
- With the cab on, verify the cart enumerates:

```bash
ls /dev/ttyACM0
```

### 4. Run as a service

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

(Replace `YOUR_USERNAME` with yours.) Then:

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

Note on `--rotate`: this only sets the starting orientation. Once you use
the **Flip 180°** button on the admin page, that choice is saved in
`calibration.json` and takes over, so you never need to edit this file to
correct an upside-down image.

## OBS stream overlay

MarqueeMark serves a transparent overlay page showing the current game's
mini-marquee art in the bottom-right corner, updating live.

In OBS: **Add > Browser Source**, URL
`http://YOUR_PI_HOSTNAME.local:8080/overlay`, width 1920, height 1080.
That's it. When no game is identified, the overlay shows `generic.png`.

Also available: `http://...:8080/current` returns the current game as
JSON, if you want to build your own integrations.

## Command-line options

| Option | Default | Purpose |
|---|---|---|
| `--port` | `/dev/ttyACM0` | NeoSD serial device |
| `--art` | `./art` | Art folder |
| `--rotate` | `0` | Starting output rotation (90 or 270 for a portrait panel). Overridden by the admin page's Flip button once used. |
| `--idle` | `blank` | With no cart link: `blank` (dark, dies with the cab) or `generic` (stays lit; for a slot that usually holds a real cartridge) |
| `--keep-awake` | off | Never sleep the panel on link loss |
| `--http-port` | `8080` | Admin and overlay server port |
| `--calibrate` | (none) | Advanced: offline terminal calibration for a bench with no network. The admin page is the normal way to calibrate. Keys: arrows move, `+`/`-` resize, `,` `.` `<` `>` tilt, `t` step size, `p` pattern/art preview, `r` reset, `s` save, `q` quit. |

## Troubleshooting

- **"Connection refused" on the admin page**: you skipped the reboot after
  installing. Run `sudo reboot`.
- **The web page shows a single marquee image with no controls**: that's
  the OBS overlay at the bare address. Add `/admin` to the URL.
- **Image is upside down**: click **Flip 180°** in the admin page's
  calibration controls, then Save.
- **No `/dev/ttyACM0`**: the cart is slot-powered, the cab must be on.
  Check `dmesg | tail` for the "NeoSD Virtual Com Port" enumeration.
- **Permission denied on the serial port**: your user isn't in `dialout`
  (re-login after `usermod`).
- **Service runs but no journal output**: `PYTHONUNBUFFERED=1` is missing
  from the unit.
- **A game shows the generic or placeholder art**: the PNG's name doesn't
  match that game's MAME short name. Load the game and read the name
  MarqueeMark wants from the journal (`journalctl -u marqueemark -n 5`),
  then rename your file to match and re-upload it.
- **Art doesn't restore after a power cycle**: the slot map has to see
  each flash slot announced once. Cycle through your virtual slots one
  time to seed it. Also confirm `/opt/marqueemark` is owned by the service
  user (root-owned files block `lastgame.json`).
- **Panel never sleeps**: verify the sudoers rule, and test your driver
  board manually: `echo 4 | sudo tee /sys/class/graphics/fb0/blank`
  should put it into standby (`echo 0` wakes it). Boards that show a
  permanent "NO SIGNAL" box instead can't use this feature, run with
  `--keep-awake`.

## Limitations & roadmap

- Detection requires a NeoSD Pro. With a real MVS cartridge in the slot
  the marquee shows the generic art (`--idle generic`). v2 will let you
  manually pick a marquee for that slot from the admin panel, plus manual
  controls to turn the display on and put it to sleep on demand.
- One panel/window per Pi HDMI output today; the Pi 4's dual HDMI makes a
  two-window build possible and it's on the roadmap.
- The NeoSD protocol here is unofficial and could change in future
  TerraOnion firmware. Firmware 1.07 behavior is what's documented above.

## Credits

Built by Britt at [Gamesboro](https://gamesboro.net). The NeoSD Pro USB
announcement protocol was reverse-engineered on real hardware for this
project. Not affiliated with or endorsed by TerraOnion or SNK.
