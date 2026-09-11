# MarqueeMark

[![Watch the build video](https://img.youtube.com/vi/57Q8elVT100/maxresdefault.jpg)](https://youtu.be/57Q8elVT100)

**A digital mini-marquee for the Neo Geo MVS.** MarqueeMark replaces a mini
marquee card with a small LCD panel. With a TerraOnion NeoSD Pro flash
cart, it *always shows the correct game*, switching the marquee art the
instant you load or change one, no NeoSD Pro required if you'd rather
just pick the game by hand instead (see
[Using MarqueeMark without a NeoSD Pro](#using-marqueemark-without-a-neosd-pro)).
It also serves a live "now playing" overlay for OBS so your stream always
shows what's running.

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
- **Safe Pi shutdown after cabinet power-off**: optionally detect the NeoSD
  Pro disconnect when a shared cabinet/Pi power switch is turned off, play a
  native reverse Neo Geo boot sequence, then halt the Pi cleanly after a
  configurable 5–600 second delay. It is disabled by default and normal game
  inactivity never triggers it. Browser and on-marquee previews are safe and
  never power off the Pi. MarqueeMark blanks the framebuffer before requesting
  power-off, while a local tty1 recovery login remains available after reboot. The
  Neo Geo visual sequence is used only with the Ultrawide Marquee; mini
  marquees cleanly blank before power-off.
- **Calibration from your browser**: position, resize, tilt, and flip the
  image from the admin page on any phone or PC while watching the panel
  update live. No SSH, no keyboard, no Linux required. Proportions are
  locked to the real mini-marquee card, so the image can never be
  stretched.
- **Optional Neo Geo boot splash**: set `MARQUEEMARK_NEO_GEO_SPLASH=1` when
  running the installer to select the guarded custom Plymouth theme. The
  choice persists across updates and enables a quiet black shutdown handoff.
  Normal installs retain Raspberry Pi OS's stock boot theme. Both choices
  retain a usable local recovery login. With the optional presentation,
  press **Ctrl+Alt+F2** to reach that login on tty2; tty1 is reserved for the
  clean marquee handoff. In addition,
  cloud-init diagnostics remain in the system journal instead of also being
  copied into tty1's visible framebuffer.
- **Second marquee support**: a slot with a real cartridge instead of a
  NeoSD Pro can't announce itself, so a second panel (on its own Pi) can
  be set to manual mode instead. Pick its art from the same kind of admin
  page, and it can pull its whole art library from the primary Pi and
  mirror its sleep state automatically, or be put to sleep by hand.
- **Electrocoin four-slot mode**: an alternate wide layout for Electrocoin
  cabinet conversions, with a saved layout library and optional
  AI-generated backgrounds, contributed by Darren (darren-p-thomas)
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
| [CanaKit Raspberry Pi 4 Starter Kit (2 GB)](https://amzn.to/3SnVsro) | One box covers the Pi 4 Model B, the correct micro-HDMI display cable, a proper 3.5A USB-C power supply, a 32 GB microSD card, case, heatsinks, and an inline power switch. 2 GB of RAM is plenty; the Pi only renders images. Reflash the included SD card per step 1 (skip the pre-loaded image), and skip installing the fan: heatsinks alone are enough for this workload, and a fan just pulls cabinet dust through the case. One Pi per panel; see [Adding a second marquee](#adding-a-second-marquee) if your cabinet has more than one slot. |
| [Double-sided mounting tape](https://amzn.to/45NlqaV) | Final panel mounting to the back of the marquee plexi. |
| [Painter's tape](https://amzn.to/4wI9rHr) | Temporary mounting while you align and calibrate; commit to the strong tape only after calibration looks right. |
| [USB-A to Micro-USB cable, 5 ft](https://amzn.to/4c5oxi0) | Pi to the NeoSD Pro's USB port (the cart uses Micro-USB). Only needed for a panel following a NeoSD Pro; a manual-mode panel doesn't need one. |
| TerraOnion NeoSD Pro (optional) | Enables automatic game detection. MarqueeMark reads its game announcements; it does not modify the cart in any way. Not required at all if you use `--manual` mode; see [Using MarqueeMark without a NeoSD Pro](#using-marqueemark-without-a-neosd-pro). |

*The hardware links above are Amazon affiliate links, buying through them
supports this project at no cost to you.*

**Marquee art is not included.** Mini-marquee art packs
using MAME short-name file naming (`mslug.png`, `kof95.png`, ...) are
available to registered users at EmuMovies.

https://emumovies.com/files/file/1628-neo-geo-mvs-marquee-pack-mini/

Add the PNGs from the built-in art manager page.
I include a "generic.png" image of the Gamesboro logo
so that you can align your image before downloading the pack.

### Ultrawide Marquee layouts

MarqueeMark has two display experiences, both available from the Admin page:

- **Mini Marquee** retains the original portrait artwork, calibration, and
  art-library workflow.
- **Ultrawide Marquee** drives a 1366 × 380 wide canvas made from one
  background and one or more mini-marquee cards.

Use the prominent **What display do you have?** selector to choose which
renderer drives the physical display. The choice is saved on the Pi, while
both Admin tabs stay available for setup and exploration. New installs choose
an initial display type; `--layout mini` and `--layout ultrawide` provide an
initial command-line default. `--electrocoin` remains a compatibility alias
for existing wide-panel installations. This mode was contributed by Darren
(`darren-p-thomas`), who built it for his Electrocoin cabinet conversion.

The first included Ultrawide template is the wide four-card **Electrocoin 4
Slot** conversion. Its Admin page lets
you choose what each card shows: blank, the highlighted live **NeoSD Pro**
card, or a fixed artwork card. Artwork choices use friendly game titles;
an unknown hack or homebrew shows its PNG filename instead, for example
`myhack.png`. Only one card can be the live NeoSD Pro card.

**Custom layouts** are a small layout library, separate from game-art
assignments. Give a new layout a name, choose either a PNG/JPEG background
(crop to fill or fit with black bars) or a solid colour, then select a real
Neo Geo slot count: **1, 2, 4, or 6**. The editor adds that many labelled
mini-marquee objects; drag them and resize them with their aspect ratio
locked. Saving makes the layout a reusable pill, which can later be selected
or deleted without touching game artwork. Selecting or editing a layout does
not change the physical marquee: use **Send to display** when it is ready.
Once a layout is selected, its separate card-assignment controls appear
underneath. Deleting a layout that is currently on display safely returns the
cabinet to the built-in Electrocoin layout after confirmation.

The built-in template library includes **Electrocoin 4 Slot** (with the
Electrocoin-style speaker grilles and four framed card windows), plus **Neo
Geo one-, two-, four-, and six-slot** templates. The one-slot design uses its
original black “Now Featuring” window for the generated mini marquee.

The template library includes the locked, aspect-preserved red Neo Geo
two-, four-, and six-slot panels alongside the one-slot design. Built-in
marquees remain in a consistent 1-, 2-, 4-, 6-slot, then Electrocoin order;
the active marquee is highlighted in place.

For particularly text-heavy mini marquees, MarqueeMark can keep exact-size
high-quality variants in its private `cache/mini-marquees` directory. The
shared `art/` directory remains original game artwork only, so it is safe to
use alongside other frontends.

MarqueeMark's Admin page also works with HDMI disconnected, so layouts can be
created and managed while the cabinet is powered down. Reconnect the display
and restart MarqueeMark to resume physical output.

### Optional AI-generated backgrounds

Also contributed by Darren, the custom-layout editor can generate a
background from a text prompt with a user-supplied OpenAI or Google
Gemini API key. The key is sent directly from the user's browser to that
provider; MarqueeMark and the Pi receive only the generated image. Keys
are kept only for the current browser session unless the user explicitly
checks **Remember this key on this device**, which stores it in that
browser's local storage. Anyone with access to that browser profile can
use a remembered key, so use the option only on a trusted device.

The generation workflow is independently implemented and was inspired by
[IFWG by raz0red](https://github.com/raz0red/ifwithgraphics).

Use **Validate key** before generating to load the image models currently
available to that provider key, then choose the desired model from the list.

When placing slots, **Keep all slots the same size** lets the user choose one
reference slot. Its resize handle updates every slot's dimensions together,
while their positions remain independently adjustable.

### Enabling Electrocoin mode

Electrocoin mode uses the same install and update process as the rest of
this README. Run the install command below (or the update command if
MarqueeMark is already installed) so the Pi is on a version that includes
it, then add the flag to the service:

1. Edit the service:

```bash
sudo systemctl edit --full marqueemark
```

2. Add `--electrocoin` to the end of the `ExecStart` line, then save and
   exit.

3. Restart the service:

```bash
sudo systemctl restart marqueemark
```

Open the admin page as usual. A **Digital Marquee** section appears at
the top once Electrocoin mode is active, with the layout library and card
assignment controls described above.

Electrocoin mode targets a wide 1366 x 380 pixel display instead of the
portrait panel listed in the Hardware section above, so it needs a
different screen than a standard single-slot install.

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

MarqueeMark itself is one Python file: a serial listener (or, in manual
mode, an admin-page selection instead), a pygame renderer that draws
directly to the display (no desktop needed), a small state store, and an
HTTP server for the admin page and OBS overlay.

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
options you added to the service (such as `--idle generic`, `--keep-awake`,
or `--electrocoin`), and restarts the service instead of rebooting. Your
art, calibration, and slot history are left untouched.

If you calibrated before this update, the arrow keys may need a quick
recheck the first time you open calibration afterward. See
[D-pad orientation](#calibrating-the-image) below; it's a one-time fix,
not something that needs redoing on every update.

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
your network can upload art, delete files, change the calibration, and
sleep or wake the display. That's a deliberate trade for ease of setup,
and it's fine on a normal home network.

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

### Display power

A **Sleep** and **Wake** button live on the admin page for every panel.
Use them to blank the panel and let its backlight switch off, or bring it
back, whenever you like. Sleeping by hand stays in effect even if the
cabinet powers on again; only Wake, or the automatic wake for a panel
following a NeoSD Pro, clears it.

This is the only way to control sleep on a manual-mode panel with no
`--sleep-source` configured, since it has no cabinet-power signal to
follow on its own.

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
- **D-pad orientation**: which way the arrow pad needs correcting depends
  on which edge your panel's ribbon cable exits, which varies between
  panels and can't be figured out automatically. If pressing an arrow
  moves the image a direction other than the one you pressed, click this
  button and try again. It cycles through four settings; one of them will
  be correct. This is a one-time setting per panel, saved with the rest
  of your calibration.
- **Preview**: switches between the alignment test pattern and real
  marquee art, for a final check of how it actually looks.
- **Save** stores everything; **Cancel** discards it and returns the
  marquee to normal.

Aim to have the pattern slightly overfill the window opening on all four
sides, so no black edge is visible through the plexi. When it looks right,
commit the panel with the double-sided tape and re-run calibration for a
final touch-up if the panel shifted.

## Using MarqueeMark without a NeoSD Pro

You don't need a NeoSD Pro, or any flash cart, to use MarqueeMark at all.
`--manual` mode works perfectly well as your only panel, on any MVS
cabinet, real cartridges included. The tradeoff is simple: instead of the
marquee updating itself automatically when you swap a cart, you pick the
game from the admin page, and it stays showing that until you change it.

This is a good fit if you swap cartridges rarely, if you'd rather not buy
a flash cart just for this project, or if you just want a nicer marquee
than the paper card without needing automatic detection at all.

Setup is the same install as anywhere else in this README, with one
difference: skip the NeoSD Pro's USB cable entirely, and add `--manual`
to the service's `ExecStart` line:

```
--manual
```

That's it. No `--art-source` or `--sleep-source` needed unless you're
running a second panel too (see below). Upload art directly to this
panel's own admin page, pick a game from the **Now showing** dropdown,
and use the **Sleep** and **Wake** buttons there whenever you turn the
cabinet on or off, since without a NeoSD Pro there's no automatic signal
for MarqueeMark to follow.

## Adding a second marquee

Some cabinets have more than one cartridge slot, and only one can hold a
NeoSD Pro. A second slot with a real cartridge in it has no way to
announce itself, so MarqueeMark can't auto-detect what's loaded there the
way it does for a NeoSD Pro. A second panel handles this in manual mode
instead: you pick what it shows from its own admin page, and it stays on
that selection until you change it.

This runs on its own Raspberry Pi, one Pi per panel. A single Pi can only
own one display at a time under the display driver MarqueeMark uses, so a
second panel needs a second Pi rather than a second cable into the first
one.

Install MarqueeMark on the second Pi the same way as the first (Step 1
and Step 2 above), then edit its service to add:

```
--manual --art-source http://marquee.local:8080 --sleep-source http://marquee.local:8080/current
```

replacing `marquee.local` with your primary Pi's actual hostname or IP.

- `--manual` turns off the serial listener and switches this panel to
  admin-page selection instead. Its own admin page gains a **Now
  showing** picker at the top.
- `--art-source` points this panel at the primary Pi's art library, so
  you only maintain one set of PNGs. This panel downloads and caches
  whatever it needs and falls back to its local cache if the primary is
  unreachable.
- `--sleep-source` points at the primary's `/current` endpoint, which is
  read-only, so the primary needs no changes at all. When it reports no
  game running, the cabinet is off, and this panel sleeps too; when it
  reports a game again, this panel wakes automatically.

Both flags are optional. Without `--art-source`, upload art directly to
this panel's own admin page instead. Without `--sleep-source`, use the
**Sleep** and **Wake** buttons on this panel's admin page by hand.

## OBS stream overlay

MarqueeMark serves a transparent overlay page showing the current game's
mini-marquee art in the bottom-right corner, updating live.

In OBS: **Add > Browser Source**, URL
`http://YOUR_PI_HOSTNAME.local:8080/overlay`, width 1920, height 1080.
That's it. When no game is identified, the overlay shows `generic.png`.

Also available: `http://...:8080/current` returns the current game as
JSON, if you want to build your own integrations. This is also what a
second panel polls for `--sleep-source`.

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
sudo apt install -y python3-serial python3-pygame python3-pil
sudo mkdir -p /opt/marqueemark/art
sudo chown -R $USER:$USER /opt/marqueemark
```

`python3-pil` is only used by Electrocoin mode's AI background generator;
it's small, so it's worth installing either way.

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
  Skip this if the panel is running in `--manual` mode.
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

(Replace `YOUR_USERNAME` with yours. For a second, manual-mode panel, add
the flags described in [Adding a second marquee](#adding-a-second-marquee)
to the `ExecStart` line. For Electrocoin mode, add `--electrocoin`
instead; see [Enabling Electrocoin mode](#enabling-electrocoin-mode).)
Then:

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

## Command-line options

| Option | Default | Purpose |
|---|---|---|
| `--port` | `/dev/ttyACM0` | NeoSD serial device |
| `--art` | `./art` | Art folder |
| `--rotate` | `90` | Starting output rotation (90 or 270 for a portrait panel). Overridden by the admin page's Flip button once used. Only matters for a fresh panel with no saved calibration. |
| `--idle` | `blank` | With no cart link: `blank` (dark, dies with the cab) or `generic` (stays lit; for a slot that usually holds a real cartridge) |
| `--keep-awake` | off | Never sleep the panel automatically. The admin page's Sleep/Wake buttons still work. |
| `--http-port` | `8080` | Admin and overlay server port |
| `--manual` | off | No NeoSD Pro on this panel; pick the marquee by hand from the admin page instead. Use for a second panel behind a real cartridge, or any cabinet without a NeoSD Pro at all. See [Adding a second marquee](#adding-a-second-marquee). |
| `--layout` | saved choice / `mini` | Initial display type: `mini` for the original portrait renderer or `ultrawide` for the wide layout renderer. The Admin display-type selector persists later changes. |
| `--electrocoin` | off | Deprecated compatibility alias for an Ultrawide installation. |
| `--art-source` | none | `--manual` only. Base URL of another MarqueeMark (e.g. `http://marquee.local:8080`) to pull art from, so the library lives in one place instead of being copied to every panel. |
| `--sleep-source` | none | `--manual` only. URL of another MarqueeMark's `/current` endpoint. This panel sleeps and wakes to match that cabinet's power state. Omit to control sleep by hand instead. |
| `--calibrate` | (none) | Advanced: offline terminal calibration for a bench with no network. The admin page is the normal way to calibrate. Keys: arrows move, `+`/`-` resize, `,` `.` `<` `>` tilt, `d` cycle D-pad orientation, `t` step size, `p` pattern/art preview, `r` reset, `s` save, `q` quit. |

## Ultrawide Marquee notes

Ultrawide layouts are configurable through their Admin tab. The Electrocoin
4 Slot base is included as one template, but custom layouts can use any
background and one, two, four, or six mini-marquee windows. The reference
canvas is 1366 × 380. The saved Ultrawide layout choices currently live in
`/opt/marqueemark/electrocoin.json` for backwards compatibility; this filename
will be migrated in a later compatibility release.

This mode does not yet identify the MVS motherboard's active physical slot;
GPIO-based active-slot highlighting is a future enhancement.

## Troubleshooting

- **"Connection refused" on the admin page**: you skipped the reboot after
  installing. Run `sudo reboot`.
- **The web page shows a single marquee image with no controls**: that's
  the OBS overlay at the bare address. Add `/admin` to the URL.
- **Image is upside down**: click **Flip 180°** in the admin page's
  calibration controls, then Save.
- **Arrows in calibration move the wrong direction**: click **D-pad
  orientation** and try again. Which setting is correct depends on this
  specific panel, not on rotation, so it can differ between two panels
  even if both are mounted the same way.
- **No `/dev/ttyACM0`**: the cart is slot-powered, the cab must be on.
  Check `dmesg | tail` for the "NeoSD Virtual Com Port" enumeration. If
  this panel is in `--manual` mode, it doesn't use this device at all.
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
  `--keep-awake`, and use the admin page's Sleep/Wake buttons by hand
  instead.
- **A second panel isn't following the cabinet to sleep**: confirm its
  service line actually includes `--sleep-source` pointed at the primary
  Pi's `/current` URL. Run `sudo systemctl cat marqueemark` on the second
  Pi to see the exact command it's running.
- **A second panel's art list is empty or stale**: confirm `--art-source`
  points at the primary Pi and that Pi is reachable on the network. This
  panel falls back to its local cache when the primary can't be reached,
  so a brief outage shows old art rather than nothing.
- **Electrocoin mode's Digital Marquee section doesn't appear**: confirm
  the service actually includes `--electrocoin`. Run
  `sudo systemctl cat marqueemark` to see the exact command it's running,
  and restart the service after any change.

## Limitations & roadmap

- The NeoSD protocol here is unofficial and could change in future
  TerraOnion firmware. Firmware 1.07 behavior is what's documented above.
- A second, manual-mode panel needs its own Raspberry Pi. A single Pi
  driving two panels at once is not possible under the display driver
  MarqueeMark currently uses.
- Electrocoin mode does not yet identify the MVS motherboard's active
  physical slot; GPIO-based active-slot highlighting is a possible future
  enhancement.

## Credits

Built by Britt at [Gamesboro](https://gamesboro.net). The NeoSD Pro USB
announcement protocol was reverse-engineered on real hardware for this
project. Electrocoin four-slot mode, its layout library, and the AI
background generator were contributed by Darren (darren-p-thomas). Not
affiliated with or endorsed by TerraOnion or SNK.
