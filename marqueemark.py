#!/usr/bin/env python3
"""MarqueeMark — digital marquee for a Neo Geo MVS with a NeoSD Pro.

Listens for the NeoSD Pro's game-load announcements on USB serial and
shows the matching marquee art fullscreen on the physical panel. Also
publishes the current game to an OBS stream overlay over HTTP/SSE.
Blanks the physical panel when the cab is off; the overlay falls back
to the generic Neo Geo marquee.

Frame format (61 bytes, reverse-engineered July 2026):
  0..2   magic 99 88 3A
  3..4   u16 LE  zero-based menu slot index:
                 flash slots 1-4 announce as 0-3, RAM slot as 4.
                 RAM contents are destroyed at power-off; the cart
                 always auto-boots Flash Slot 1 (index 0) on power-up.
  5..6   u16 LE  library index (position in SD game list)
  7..8   u16 LE  NGH number, BCD (0x0269 -> "269")
  9..10  reserved
  11..43 short name, 33-byte field, null-terminated (stale bytes after)
  44..60 title, 17-byte field, possibly truncated with no terminator
  RAM loads may announce twice; consecutive duplicates are ignored.

Usage:   python3 marqueemark.py [--port /dev/ttyACM0] [--art ./art]
                                 [--http-port 8080] [--rotate 0|90|180|270]
                                 [--idle blank|generic]
         --idle blank (default): no NeoSD USB link -> dark panel, so the
             marquee dies with the cab like the original lamp.
         Display sleep: ~10s after the link is lost, video output is cut
             (DPMS off) so the panel's driver board drops to standby and
             the BACKLIGHT turns off. Restored automatically when the
             link returns. Requires a sudoers rule (see README/comments
             at _fb_blank). Disable with --keep-awake.

         --idle generic: show art/generic.png instead. NOTE: the Pi
             cannot tell "cab off" from "cab on with a real MVS cart" —
             both are just a missing USB link — so this keeps the
             marquee lit even when the cabinet is powered off. Choose
             it if this slot usually holds a real cartridge.
         python3 marqueemark.py --calibrate [--rotate ...]
             Interactive calibration: draws a test pattern, controlled from
             THIS terminal (works over SSH). Keys:
               arrows      move up / down / left / right
               + / -       grow / shrink (uniform - proportions are
                           always locked to the real mini-marquee card,
                           4.44 x 5.44; stretching is not possible)
               , / .       tilt -0.1 deg / +0.1 deg (fine rotation, for
                           squaring up a slightly crooked panel mount)
               < / >       tilt -0.5 deg / +0.5 deg (coarse)
               t           cycle step size (1 / 5 / 20 px)
               p           toggle test pattern <-> sample art
               r           reset to default rectangle
               s           save to calibration.json
               q or Esc    quit
             Flow: arrows to position, +/- to size, ,/./</> to square up
             a crooked mount, s to save. Proportions can never change.
             Saved calibration is applied automatically on normal runs:
             art fills the calibrated rectangle exactly (the window).
Deps:    sudo apt install python3-serial python3-pygame

OBS:     add a Browser Source pointing at
         http://<pi-hostname>.local:8080/overlay

Art management: open http://<pi-hostname>.local:8080/admin in any browser
         on your network to upload marquee PNGs (drag and drop), see what
         is installed, and delete files. Files must be named by MAME short
         name (mslug.png, kof95.png, ...) plus generic.png as fallback.
v1.0
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pygame
import serial

VERSION = "1.0"

MAGIC = b"\x99\x88\x3a"
FRAME_LEN = 61
FADE_MS = 400
BG = (0, 0, 0)

RAM_SLOT = 4  # zero-based: flash slots 1-4 announce as 0-3, RAM as 4
ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "art")
LASTGAME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lastgame.json")
GENERIC = "generic"  # art/generic.png — fallback marquee for the overlay
CAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")

# MVS mini-marquee cards are a standard 4.44 x 5.44 inches on every cab,
# so the correct window aspect is a constant: height = width * this.
MARQUEE_ASPECT = 5.44 / 4.44


def load_calibration():
    """Return ([x, y, w, h], tilt_degrees) in logical-canvas pixels, or None."""
    try:
        with open(CAL_PATH) as f:
            c = json.load(f)
        rect = [int(c["x"]), int(c["y"]), int(c["w"]), int(c["h"])]
        return rect, float(c.get("tilt", 0.0))
    except (OSError, ValueError, KeyError):
        return None


def save_calibration(rect, tilt=0.0):
    with open(CAL_PATH, "w") as f:
        json.dump({"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3],
                   "tilt": round(tilt, 2)}, f)


FB_BLANK = "/sys/class/graphics/fb0/blank"


def _fb_blank(level):
    """Set display power via fbdev blanking: 0 = on, 4 = DPMS powerdown.

    Needs root for the sysfs write. One-time setup so the service user
    can do it without a password prompt:
      echo 'markymark ALL=(root) NOPASSWD: /usr/bin/tee /sys/class/graphics/fb0/blank' \
        | sudo tee /etc/sudoers.d/marqueemark
    Failures are ignored — worst case the panel just stays awake.
    """
    try:
        subprocess.run(["sudo", "-n", "tee", FB_BLANK],
                       input=str(level).encode(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, check=False)
    except Exception:
        pass


def cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def parse_frame(frame: bytes) -> dict:
    ngh_bcd = int.from_bytes(frame[7:9], "little")
    return {
        "slot": int.from_bytes(frame[3:5], "little"),
        "ngh": f"{ngh_bcd:04x}".lstrip("0").zfill(3),
        "short": cstr(frame[11:44]).lower(),
        "title": cstr(frame[44:61]),
    }


# ---------------------------------------------------------------- state

def save_state(game: dict):
    """Remember the active game, and which game lives in each flash slot."""
    try:
        state = load_state() or {"slots": {}}
        state["active"] = game
        if game.get("slot", RAM_SLOT) < RAM_SLOT:  # flash slots persist power-off
            state["slots"][str(game["slot"])] = game
        with open(LASTGAME_PATH, "w") as f:
            json.dump(state, f)
    except OSError:
        pass  # persistence is best-effort


def load_state():
    try:
        with open(LASTGAME_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def boot_game(state):
    """The NeoSD always auto-boots Flash Slot 1 (index 0) after a power cycle."""
    if not state:
        return None
    return state.get("slots", {}).get("0")


# --------------------------------------------------------- overlay server
#
# Runs in a background thread. Completely independent of the serial and
# pygame code — it only receives game dicts via publish(). If anything
# here fails, the physical marquee is unaffected.

OVERLAY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MarqueeMark overlay</title>
<style>
  html, body {
    margin: 0; padding: 0;
    background: transparent;
    overflow: hidden;
  }
  /* Bottom-right mini-marquee card. Portrait art, sized by height. */
  #card {
    position: fixed;
    right: 32px;
    bottom: 32px;
    height: 34vh;                 /* mini-marquee card height on a 1080p canvas */
    aspect-ratio: 44 / 54;        /* MVS mini-marquee proportions */
    filter: drop-shadow(0 6px 18px rgba(0,0,0,0.55));
    transition: opacity 400ms ease;
    opacity: 1;
  }
  #card img {
    width: 100%; height: 100%;
    object-fit: contain;
    display: block;
  }
</style>
</head>
<body>
  <div id="card"><img id="art" alt=""></div>
  <script>
    const img = document.getElementById('art');
    const GENERIC = '/art/__generic__.png';

    function show(shortName) {
      // Server serves generic.png for any unknown name, but guard the
      // client side too so a network blip never leaves a broken image.
      const src = shortName ? '/art/' + shortName + '.png' : GENERIC;
      img.onerror = () => { img.onerror = null; img.src = GENERIC; };
      img.src = src;
    }

    // Live updates over Server-Sent Events. EventSource auto-reconnects.
    const es = new EventSource('/events');
    es.onmessage = (e) => {
      try { show(JSON.parse(e.data).short); }
      catch (_) { show(null); }
    };
    es.onerror = () => { /* EventSource retries on its own */ };

    show(null);  // generic marquee until the first event arrives
  </script>
</body>
</html>
"""


ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MarqueeMark — Art Manager</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #101018;
         color: #e8e8f0; }
  header { padding: 16px 24px; background: #1a1a28; border-bottom: 2px solid #c8102e; }
  h1 { margin: 0; font-size: 1.2rem; letter-spacing: 0.04em; }
  h1 span { color: #c8102e; }
  main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  #drop { border: 2px dashed #555; border-radius: 10px; padding: 34px;
          text-align: center; color: #aaa; cursor: pointer; transition: all .15s; }
  #drop.hot { border-color: #c8102e; color: #fff; background: #1c1420; }
  #status { min-height: 1.4em; margin: 10px 2px; font-size: 0.9rem; color: #9ad; }
  #warn { margin: 10px 2px; color: #f5c542; font-size: 0.9rem; }
  #grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
          gap: 14px; margin-top: 18px; }
  .card { background: #1a1a28; border-radius: 8px; padding: 8px; text-align: center; }
  .card img { width: 100%; aspect-ratio: 44/54; object-fit: contain;
              background: #000; border-radius: 4px; }
  .card .n { font-size: 0.78rem; margin: 6px 0 4px; word-break: break-all; }
  .card button { background: #2a2a3a; color: #e88; border: 0; border-radius: 5px;
                 padding: 3px 10px; font-size: 0.75rem; cursor: pointer; }
  .card button:hover { background: #c8102e; color: #fff; }
</style>
</head>
<body>
<header><h1>Marquee<span>Mark</span> — Art Manager
  <small style="color:#888;font-weight:normal;font-size:0.7em">v{{VERSION}}</small></h1></header>
<main>
  <div id="drop">Drop marquee PNGs here (or click to choose files)<br>
    <small>Name files by MAME short name: mslug.png, kof95.png ... plus generic.png</small>
  </div>
  <input type="file" id="pick" accept=".png" multiple style="display:none">
  <div id="status"></div>
  <div id="warn"></div>
  <div id="grid"></div>
<script>
const drop = document.getElementById('drop');
const pick = document.getElementById('pick');
const grid = document.getElementById('grid');
const status_ = document.getElementById('status');
const warn = document.getElementById('warn');

async function refresh() {
  const files = await (await fetch('/list')).json();
  grid.innerHTML = '';
  warn.textContent = files.includes('generic.png') ? '' :
    'Heads up: no generic.png installed — it is the fallback marquee.';
  for (const f of files) {
    const d = document.createElement('div'); d.className = 'card';
    d.innerHTML = '<img src="/art/' + f + '?t=' + Date.now() + '">' +
                  '<div class="n">' + f + '</div>';
    const b = document.createElement('button'); b.textContent = 'delete';
    b.onclick = async () => {
      if (!confirm('Delete ' + f + '?')) return;
      await fetch('/delete?name=' + encodeURIComponent(f), {method: 'POST'});
      refresh();
    };
    d.appendChild(b); grid.appendChild(d);
  }
}

async function upload(fileList) {
  let ok = 0, bad = 0;
  for (const file of fileList) {
    if (!file.name.toLowerCase().endsWith('.png')) { bad++; continue; }
    status_.textContent = 'Uploading ' + file.name + '...';
    const r = await fetch('/upload?name=' + encodeURIComponent(file.name),
                          {method: 'POST', body: file});
    r.ok ? ok++ : bad++;
  }
  status_.textContent = 'Uploaded ' + ok + ' file(s)' +
                        (bad ? ', ' + bad + ' rejected (PNG only)' : '');
  refresh();
}

drop.onclick = () => pick.click();
pick.onchange = () => upload(pick.files);
drop.ondragover = e => { e.preventDefault(); drop.classList.add('hot'); };
drop.ondragleave = () => drop.classList.remove('hot');
drop.ondrop = e => { e.preventDefault(); drop.classList.remove('hot');
                     upload(e.dataTransfer.files); };
refresh();
</script>
</main>
</body>
</html>
"""

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_UPLOAD = 20 * 1024 * 1024  # 20 MB per file is generous for marquee art


def _safe_art_name(name):
    """Sanitize an uploaded filename to a flat, lowercase .png name."""
    name = os.path.basename(name).lower()
    if not name.endswith(".png"):
        return None
    stem = name[:-4]
    if not stem or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in stem):
        return None
    return stem + ".png"


class OverlayServer:
    def __init__(self, art_dir, port):
        self.art_dir = art_dir
        self.port = port
        self._clients = set()          # set[queue.Queue]
        self._lock = threading.Lock()
        self._current = None           # last published game dict (or None)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence per-request logging
                pass

            def _send(self, code, ctype, body, extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                if extra:
                    for k, v in extra.items():
                        self.send_header(k, v)
                self.end_headers()
                if body is not None:
                    self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path in ("/", "/overlay"):
                    self._send(200, "text/html; charset=utf-8",
                               OVERLAY_HTML.encode("utf-8"))
                elif path == "/admin":
                    page = ADMIN_HTML.replace("{{VERSION}}", VERSION)
                    self._send(200, "text/html; charset=utf-8",
                               page.encode("utf-8"))
                elif path == "/list":
                    try:
                        names = sorted(n for n in os.listdir(server.art_dir)
                                       if n.endswith(".png"))
                    except OSError:
                        names = []
                    self._send(200, "application/json",
                               json.dumps(names).encode())
                elif path == "/current":
                    with server._lock:
                        cur = server._current
                    body = json.dumps(cur or {"short": None}).encode()
                    self._send(200, "application/json", body)
                elif path == "/events":
                    self._serve_events()
                elif path.startswith("/art/"):
                    self._serve_art(path[len("/art/"):])
                else:
                    self._send(404, "text/plain", b"not found")

            def do_POST(self):
                path, _, query = self.path.partition("?")
                params = {}
                for pair in query.split("&"):
                    if "=" in pair:
                        k, _, v = pair.partition("=")
                        from urllib.parse import unquote
                        params[k] = unquote(v)
                if path == "/upload":
                    self._handle_upload(params.get("name", ""))
                elif path == "/delete":
                    self._handle_delete(params.get("name", ""))
                else:
                    self._send(404, "text/plain", b"not found")

            def _handle_upload(self, raw_name):
                name = _safe_art_name(raw_name)
                length = int(self.headers.get("Content-Length") or 0)
                if not name:
                    self._send(400, "text/plain", b"bad name: use MAME short names, a-z 0-9 _ - only, .png")
                    return
                if not 0 < length <= MAX_UPLOAD:
                    self._send(413, "text/plain", b"file too large")
                    return
                data = self.rfile.read(length)
                if not data.startswith(PNG_MAGIC):
                    self._send(400, "text/plain", b"not a PNG file")
                    return
                try:
                    with open(os.path.join(server.art_dir, name), "wb") as f:
                        f.write(data)
                    self._send(200, "text/plain", name.encode())
                except OSError as e:
                    self._send(500, "text/plain", str(e).encode())

            def _handle_delete(self, raw_name):
                name = _safe_art_name(raw_name)
                if not name:
                    self._send(400, "text/plain", b"bad name")
                    return
                try:
                    os.remove(os.path.join(server.art_dir, name))
                    self._send(200, "text/plain", b"deleted")
                except OSError:
                    self._send(404, "text/plain", b"no such file")

            def _serve_art(self, name):
                # Sanitize: filename only, must end in .png.
                name = os.path.basename(name)
                if name == "__generic__.png":
                    name = GENERIC + ".png"
                candidate = os.path.join(server.art_dir, name)
                if not (name.endswith(".png") and os.path.isfile(candidate)):
                    candidate = os.path.join(server.art_dir, GENERIC + ".png")
                try:
                    with open(candidate, "rb") as f:
                        data = f.read()
                    self._send(200, "image/png", data,
                               {"Cache-Control": "no-cache"})
                except OSError:
                    self._send(404, "text/plain", b"no art")

            def _serve_events(self):
                q = queue.Queue()
                with server._lock:
                    server._clients.add(q)
                    current = server._current
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    # Immediately send current state to the new client.
                    self._emit(current)
                    while True:
                        try:
                            game = q.get(timeout=15)
                            self._emit(game)
                        except queue.Empty:
                            # Comment line as keep-alive ping.
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with server._lock:
                        server._clients.discard(q)

            def _emit(self, game):
                payload = json.dumps({"short": game["short"]} if game
                                     else {"short": None})
                self.wfile.write(("data: " + payload + "\n\n").encode())
                self.wfile.flush()

        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)

    def start(self):
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        print("[MarqueeMark v%s] overlay on http://0.0.0.0:%d/overlay"
              % (VERSION, self.port))

    def publish(self, game):
        """Push a game (or None for the generic marquee) to all overlays."""
        with self._lock:
            self._current = game
            clients = list(self._clients)
        for q in clients:
            q.put(game)


# -------------------------------------------------------------- display

class Display:
    def __init__(self, art_dir, rotate=0):
        pygame.init()
        pygame.mouse.set_visible(False)
        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.rotate = rotate % 360
        phys = self.screen.get_size()
        # Logical canvas: what we compose art onto. For 90/270 the canvas
        # is the physical screen turned on its side.
        if self.rotate in (90, 270):
            self.size = (phys[1], phys[0])
        else:
            self.size = phys
        self.art_dir = art_dir
        self.current = None
        # Calibrated window rectangle on the logical canvas (or full canvas).
        cal = load_calibration()
        if cal:
            rect_l, tilt = cal
            self.rect = pygame.Rect(*rect_l)
            self.tilt = tilt
            print("[MarqueeMark] calibration: %s tilt=%.2f" % (rect_l, tilt))
        else:
            self.rect = pygame.Rect(0, 0, self.size[0], self.size[1])
            self.tilt = 0.0
        self.blank()

    def _place(self, canvas, card):
        """Put a window-sized card onto the canvas, tilt-corrected."""
        if self.tilt:
            card = pygame.transform.rotozoom(card, self.tilt, 1.0)
        canvas.blit(card, card.get_rect(center=self.rect.center))

    def _present(self, surf):
        """Rotate the composed logical canvas onto the physical screen."""
        if self.rotate:
            surf = pygame.transform.rotate(surf, self.rotate)
        self.screen.blit(surf, (0, 0))
        pygame.display.flip()

    def _fit(self, img):
        """Fill the calibrated rectangle exactly — the rect IS the window."""
        surf = pygame.Surface(self.size)
        surf.fill(BG)
        img = pygame.transform.smoothscale(img, (self.rect.w, self.rect.h))
        self._place(surf, img)
        return surf

    def _text_card(self, game):
        surf = pygame.Surface(self.size)
        surf.fill(BG)
        card = pygame.Surface((self.rect.w, self.rect.h))
        card.fill((10, 10, 40))
        big = pygame.font.SysFont(None, max(24, self.rect.h // 8), bold=True)
        small = pygame.font.SysFont(None, max(16, self.rect.h // 16))
        title = big.render(game["title"] or game["short"].upper(), True, (255, 220, 60))
        sub = small.render("NGH-%s  (%s)" % (game["ngh"], game["short"]), True, (200, 200, 200))
        cx, cy = self.rect.w // 2, self.rect.h // 2
        card.blit(title, title.get_rect(center=(cx, cy - self.rect.h // 12)))
        card.blit(sub, sub.get_rect(center=(cx, cy + self.rect.h // 10)))
        self._place(surf, card)
        return surf

    def _fade_to(self, surf):
        old = self.current or pygame.Surface(self.size)
        steps = max(1, FADE_MS // 20)
        frame = pygame.Surface(self.size)
        for i in range(steps + 1):
            alpha = int(255 * i / steps)
            frame.blit(old, (0, 0))
            layer = surf.copy()
            layer.set_alpha(alpha)
            frame.blit(layer, (0, 0))
            self._present(frame)
            pygame.time.wait(20)
        self.current = surf

    def show_game(self, game):
        path = os.path.join(self.art_dir, "%s.png" % game["short"])
        if os.path.exists(path):
            surf = self._fit(pygame.image.load(path).convert())
        else:
            fallback = os.path.join(self.art_dir, "default.png")
            if os.path.exists(fallback):
                surf = self._fit(pygame.image.load(fallback).convert())
            else:
                surf = self._text_card(game)
        self._fade_to(surf)

    def blank(self):
        surf = pygame.Surface(self.size)
        surf.fill(BG)
        self._present(surf)
        self.current = surf

    def show_idle(self):
        """Generic marquee for when no game can be identified."""
        path = os.path.join(self.art_dir, GENERIC + ".png")
        if os.path.exists(path):
            self._fade_to(self._fit(pygame.image.load(path).convert()))
        else:
            self.blank()

    def sleep(self):
        """Release the screen and cut video output so the panel's driver
        board loses signal and drops to standby (backlight off)."""
        if self.screen is None:
            return
        pygame.display.quit()
        self.screen = None
        _fb_blank(4)
        print("[MarqueeMark] display sleeping")

    def wake(self):
        """Restore video output and re-acquire the screen."""
        if self.screen is not None:
            return
        _fb_blank(0)
        pygame.display.init()
        pygame.mouse.set_visible(False)
        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.current = None
        print("[MarqueeMark] display awake")

    def pump(self):
        if self.screen is None:  # asleep — nothing to pump
            return True
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False
        return True


# ---------------------------------------------------------- calibration

def calibrate(display):
    """Interactive calibration driven from the controlling terminal (SSH-safe)."""
    import select
    import termios
    import tty

    steps = [5, 1, 20]
    step_i = 0
    show_art = False
    sample = None
    spath = os.path.join(display.art_dir, GENERIC + ".png")
    if os.path.isfile(spath):
        # .convert() normalizes palettized/8-bit PNGs to a smoothscale-able
        # format (same as the main display path does).
        sample = pygame.image.load(spath).convert()

    def default_rect():
        h = int(display.size[1] * 0.9)
        w = int(h / MARQUEE_ASPECT)
        if w > display.size[0]:
            w = int(display.size[0] * 0.9)
            h = int(w * MARQUEE_ASPECT)
        return pygame.Rect((display.size[0] - w) // 2,
                           (display.size[1] - h) // 2, w, h)

    cal = load_calibration()
    if cal:
        r = pygame.Rect(*cal[0])
        tilt = cal[1]
    else:
        r = default_rect()
        tilt = 0.0

    def clamp():
        r.w = max(40, min(r.w, display.size[0]))
        r.h = max(40, min(r.h, display.size[1]))
        r.x = max(-r.w + 20, min(r.x, display.size[0] - 20))
        r.y = max(-r.h + 20, min(r.y, display.size[1] - 20))

    def draw():
        surf = pygame.Surface(display.size)
        surf.fill(BG)
        if show_art and sample:
            card = pygame.transform.smoothscale(sample, (r.w, r.h))
            if tilt:
                card = pygame.transform.rotozoom(card, tilt, 1.0)
            surf.blit(card, card.get_rect(center=r.center))
        else:
            box = pygame.Surface((r.w, r.h))
            box.fill((0, 60, 0))
            pygame.draw.rect(box, (255, 255, 255), box.get_rect(), 4)      # border
            pygame.draw.line(box, (255, 0, 0), (r.w // 2, 0), (r.w // 2, r.h), 2)
            pygame.draw.line(box, (255, 0, 0), (0, r.h // 2), (r.w, r.h // 2), 2)
            for gx in range(0, r.w, max(20, r.w // 10)):                   # grid
                pygame.draw.line(box, (0, 120, 0), (gx, 0), (gx, r.h), 1)
            for gy in range(0, r.h, max(20, r.h // 10)):
                pygame.draw.line(box, (0, 120, 0), (0, gy), (r.w, gy), 1)
            corner = min(r.w, r.h) // 8
            for cx, cy in [(0, 0), (r.w - corner, 0), (0, r.h - corner),
                           (r.w - corner, r.h - corner)]:
                pygame.draw.rect(box, (255, 220, 0), (cx, cy, corner, corner), 3)
            if tilt:
                box = pygame.transform.rotozoom(box, tilt, 1.0)
            surf.blit(box, box.get_rect(center=r.center))
        display._present(surf)
        sys.stdout.write("\r  rect x=%-5d y=%-5d w=%-5d h=%-5d tilt=%-6.1f step=%-3d   "
                         % (r.x, r.y, r.w, r.h, tilt, steps[step_i]))
        sys.stdout.flush()

    print("Calibration: arrows = move | +/- = size (proportions always locked)")
    print("  tilt: , . = 0.1 deg | < > = 0.5 deg  (counters a crooked mount)")
    print("  t = step size | p = pattern/art | r = reset | s = save | q = quit")
    fd = sys.stdin.fileno()
    old_tty = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        draw()
        while True:
            display.pump()
            if not select.select([fd], [], [], 0.05)[0]:
                continue
            # Read raw bytes from the fd (unbuffered) so a 3-byte arrow-key
            # escape sequence arrives whole instead of being split by
            # Python's stdin buffering.
            raw = os.read(fd, 16)
            ch = raw.decode("ascii", errors="ignore")
            if ch == "\x1b":  # a bare Esc with no sequence following
                print("\nquit (not saved)")
                return
            if ch.startswith("\x1b") and len(ch) >= 3:
                ch = ch[:3]  # normalize to the arrow sequence
            s = steps[step_i]
            if ch == "\x1b[A":
                r.y -= s
            elif ch == "\x1b[B":
                r.y += s
            elif ch == "\x1b[D":
                r.x -= s
            elif ch == "\x1b[C":
                r.x += s
            elif ch in ("+", "="):
                r.w += s; r.h = round(r.w * MARQUEE_ASPECT)
            elif ch == "-":
                r.w -= s; r.h = round(r.w * MARQUEE_ASPECT)
            elif ch == ",":
                tilt -= 0.1
            elif ch == ".":
                tilt += 0.1
            elif ch == "<":
                tilt -= 0.5
            elif ch == ">":
                tilt += 0.5
            elif ch == "t":
                step_i = (step_i + 1) % len(steps)
            elif ch == "p":
                show_art = not show_art
            elif ch == "r":
                r.update(default_rect())
            elif ch == "s":
                clamp()
                tilt = round(tilt, 2)
                save_calibration([r.x, r.y, r.w, r.h], tilt)
                print("\nsaved %s tilt=%.2f" % ([r.x, r.y, r.w, r.h], tilt))
            elif ch in ("q", "\x03"):
                print("\ndone")
                return
            clamp()
            draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_tty)


# --------------------------------------------------------------- serial

def frames(port):
    buf = bytearray()
    while True:
        chunk = port.read(64)
        yield None  # heartbeat so the caller can pump UI events
        if chunk:
            buf += chunk
        start = buf.find(MAGIC)
        if start > 0:
            del buf[:start]
        elif start < 0 and len(buf) > len(MAGIC):
            del buf[: -len(MAGIC)]
        while len(buf) >= FRAME_LEN and buf[:3] == MAGIC:
            yield bytes(buf[:FRAME_LEN])
            del buf[:FRAME_LEN]


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--art", default=ART_DIR)
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                    help="rotate output for panel mounting orientation")
    ap.add_argument("--calibrate", action="store_true",
                    help="interactive window calibration, then exit")
    ap.add_argument("--keep-awake", action="store_true",
                    help="never sleep the display on link loss")
    ap.add_argument("--idle", choices=["blank", "generic"], default="blank",
                    help="with no NeoSD link: blank (default; marquee goes dark "
                         "with the cab) or generic (stays lit — for users "
                         "running real MVS carts in this slot)")
    args = ap.parse_args()

    display = Display(args.art, rotate=args.rotate)

    if args.calibrate:
        calibrate(display)
        pygame.quit()
        return

    overlay = None
    try:
        overlay = OverlayServer(args.art, args.http_port)
        overlay.start()
    except OSError as e:
        # Overlay is a bonus; never let it stop the physical marquee.
        print("[MarqueeMark] overlay disabled (%s)" % e)

    def publish(game):
        if overlay:
            overlay.publish(game)

    last = None
    idle_shown = False
    lost_cycles = 0
    while True:
        try:
            with serial.Serial(args.port, 115200, timeout=0.2) as port:
                display.wake()
                print("[MarqueeMark v%s] listening on %s" % (VERSION, args.port))
                idle_shown = False
                lost_cycles = 0

                # Cab just powered on (or USB reconnected): the NeoSD
                # auto-boots Flash Slot 1 silently, so restore it.
                restored = boot_game(load_state())
                if restored:
                    print("[MarqueeMark] restoring NGH-%s %s (slot %s)"
                          % (restored["ngh"], restored["short"], restored["slot"]))
                    display.show_game(restored)
                    publish(restored)
                    last = (restored["ngh"], restored["short"])

                for frame in frames(port):
                    if not display.pump():
                        pygame.quit()
                        sys.exit(0)
                    if frame is None:
                        continue
                    game = parse_frame(frame)
                    key = (game["ngh"], game["short"])
                    if key == last:
                        continue  # duplicate announcement (common on RAM loads)
                    last = key
                    print("[MarqueeMark] NGH-%s %s \"%s\" (slot %s)"
                          % (game["ngh"], game["short"], game["title"], game["slot"]))
                    save_state(game)
                    display.show_game(game)
                    publish(game)
        except (serial.SerialException, OSError):
            # No NeoSD link: cab off, cable unplugged, or a real MVS
            # cart in the slot. Show the idle marquee (generic art by
            # default) — once per disconnection, not every retry.
            if not idle_shown:
                print("[MarqueeMark] no cart link — idle (%s)" % args.idle)
                if args.idle == "generic":
                    display.show_idle()
                else:
                    display.blank()
                publish(None)
                idle_shown = True
            last = None
            lost_cycles += 1
            if lost_cycles == 10 and not args.keep_awake:
                display.sleep()
            for _ in range(10):
                if not display.pump():
                    pygame.quit()
                    sys.exit(0)
                time.sleep(0.1)


if __name__ == "__main__":
    main()
