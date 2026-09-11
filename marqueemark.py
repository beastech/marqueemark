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
Calibration: the primary way to align the image is now the web admin
         page (http://<pi-hostname>.local:8080/admin) - a live session
         with on-screen position/size/tilt/flip buttons; the physical
         panel updates as you click, no SSH or keyboard needed. Only two
         rotations are valid for this portrait-mounted panel, 90 and
         270; the "Flip" button toggles between them and is saved to
         calibration.json, so it survives reboots without editing the
         systemd unit.

         An offline/advanced fallback still exists for a bench without
         network access - python3 marqueemark.py --calibrate [--rotate N]
         drives the same kind of session from THIS terminal instead
         (works over SSH). Keys: arrows move, +/- resize (proportions
         always locked to the real 4.44 x 5.44 card), ,/./</> tilt,
         t cycles step size, p toggles pattern/art preview, r resets,
         s saves, q quits.
Deps:    sudo apt install python3-serial python3-pygame

OBS:     add a Browser Source pointing at
         http://<pi-hostname>.local:8080/overlay

Art management: open http://<pi-hostname>.local:8080/admin in any browser
         on your network to upload marquee PNGs (drag and drop), see what
         is installed, and delete files. Files must be named by MAME short
         name (mslug.png, kof95.png, ...) plus generic.png as fallback.
"""

import argparse
import io
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pygame
import serial

VERSION = "1.3.6-cabinet-shutdown.9"

MAGIC = b"\x99\x88\x3a"
FRAME_LEN = 61
FADE_MS = 400
BG = (0, 0, 0)

RAM_SLOT = 4  # zero-based: flash slots 1-4 announce as 0-3, RAM as 4
ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "art")
CARD_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "mini-marquees")
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bases")
LASTGAME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lastgame.json")
GENERIC = "generic"  # art/generic.png — fallback marquee for the overlay
ELECTROCOIN_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "electrocoin.json")
ELECTROCOIN_LAYOUTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digital_layouts.json")
DISPLAY_MODE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_mode.json")
CABINET_SHUTDOWN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cabinet_shutdown.json")
GAME_TITLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_titles.json")
ELECTROCOIN_CANVAS_WIDTH = 1366
ELECTROCOIN_CANVAS_HEIGHT = 380
ELECTROCOIN_BASE_SIZE = (ELECTROCOIN_CANVAS_WIDTH, ELECTROCOIN_CANVAS_HEIGHT)
ELECTROCOIN_VIEWPORT_HEIGHT = ELECTROCOIN_CANVAS_HEIGHT
ELECTROCOIN_SLOT_COUNTS = (1, 2, 4, 6)
ELECTROCOIN_SLOT_RATIO = 176 / 230
ELECTROCOIN_DEFAULT = {"base": "electrocoin-alt-9-bottom-fixed.png",
    "base_source": "builtin",
    "layout_id": "electrocoin",
    "cards": [{"source": "fixed", "art": ""}, {"source": "fixed", "art": ""},
              {"source": "fixed", "art": ""}, {"source": "neosd", "art": ""}],
    "windows": [[356, 30, 116, 188], [530, 30, 120, 188],
                [714, 30, 121, 188], [893, 30, 120, 188]]}
BUILTIN_LAYOUTS = {
    "electrocoin": {"id": "electrocoin", "name": "Electrocoin 4 Slot", "base": "electrocoin-alt-9-bottom-fixed.png",
                    "base_source": "builtin", "background_type": "image", "background_color": "#000000",
                    "windows": [list(r) for r in ELECTROCOIN_DEFAULT["windows"]]},
    "neogeo-one-slot": {"id": "neogeo-one-slot", "name": "Neo Geo 1 Slot", "base": "neogeo-one-slot.png",
                        "base_source": "builtin", "background_type": "image", "background_color": "#000000",
                        "windows": [[1053, 36, 231, 306]]},
    "neogeo-two-slot-red": {"id": "neogeo-two-slot-red", "name": "Neo Geo 2 Slot", "base": "neogeo-two-slot-red-left-one-slot-scale-v12.png",
                              "base_source": "builtin", "background_type": "image", "background_color": "#000000",
                              "windows": [[788, 36, 231, 306], [1073, 36, 231, 306]]},
    "neogeo-four-slot-red": {"id": "neogeo-four-slot-red", "name": "Neo Geo 4 Slot", "base": "neogeo-four-slot-red-low-v5.png",
                               "base_source": "builtin", "background_type": "image", "background_color": "#000000",
                               "windows": [[191, 105, 149, 227], [463, 105, 149, 227],
                                           [735, 105, 149, 227], [1007, 105, 149, 227]]},
    "neogeo-six-slot-red-clean": {"id": "neogeo-six-slot-red-clean", "name": "Neo Geo 6 Slot", "base": "neogeo-six-slot-red-clean-final-v8.png",
                                    "base_source": "builtin", "background_type": "image", "background_color": "#000000",
                                    "windows": [[59, 105, 149, 232], [280, 105, 149, 232],
                                                [501, 105, 149, 232], [722, 105, 149, 232],
                                                [943, 105, 149, 232], [1164, 105, 149, 232]]},
    # A diagnostic, not a normal cabinet template. Its image is deliberately
    # 420px tall so panels with a different visible height can be measured.
    "viewport-test": {"id": "viewport-test", "name": "Advanced: Viewport Height Test", "base": "ultrawide-viewport-test.png",
                      "base_source": "builtin", "background_type": "image", "background_color": "#000000",
                      "viewport_height": 420, "diagnostic": True, "windows": []},
}
# The cabinet templates are intentionally shown in this fixed order.  The
# active layout keeps its own highlight and live dot rather than being moved.
BUILTIN_LAYOUT_ORDER = (
    "neogeo-one-slot",
    "neogeo-two-slot-red",
    "neogeo-four-slot-red",
    "neogeo-six-slot-red-clean",
    "electrocoin",
    "viewport-test",
)

DISPLAY_MODES = ("mini", "ultrawide")
CABINET_SHUTDOWN_DEFAULT = {"enabled": False, "delay_seconds": 10}
CABINET_SHUTDOWN_MIN_SECONDS = 5
CABINET_SHUTDOWN_MAX_SECONDS = 600
CABINET_SHUTDOWN_LOCK = threading.Lock()
CABINET_SHUTDOWN_RUNTIME = {"state": "disabled", "remaining_seconds": None}

def _cabinet_shutdown_config(value=None):
    value = value if isinstance(value, dict) else {}
    try:
        delay = int(value.get("delay_seconds", CABINET_SHUTDOWN_DEFAULT["delay_seconds"]))
    except (TypeError, ValueError):
        delay = CABINET_SHUTDOWN_DEFAULT["delay_seconds"]
    return {"enabled": bool(value.get("enabled", False)),
            "delay_seconds": max(CABINET_SHUTDOWN_MIN_SECONDS,
                                 min(CABINET_SHUTDOWN_MAX_SECONDS, delay))}

def load_cabinet_shutdown_config():
    try:
        with open(CABINET_SHUTDOWN_PATH) as f:
            return _cabinet_shutdown_config(json.load(f))
    except (OSError, ValueError):
        return dict(CABINET_SHUTDOWN_DEFAULT)

def save_cabinet_shutdown_config(value):
    config = _cabinet_shutdown_config(value)
    with open(CABINET_SHUTDOWN_PATH, "w") as f:
        json.dump(config, f, indent=2)
    return config

def set_cabinet_shutdown_runtime(state, remaining_seconds=None):
    with CABINET_SHUTDOWN_LOCK:
        CABINET_SHUTDOWN_RUNTIME["state"] = state
        CABINET_SHUTDOWN_RUNTIME["remaining_seconds"] = remaining_seconds

def cabinet_shutdown_payload():
    config = load_cabinet_shutdown_config()
    with CABINET_SHUTDOWN_LOCK:
        runtime = dict(CABINET_SHUTDOWN_RUNTIME)
    return dict(config, runtime=runtime)

def saved_display_mode():
    """Return the explicit Pi-side display choice, or None before setup."""
    try:
        with open(DISPLAY_MODE_PATH) as f:
            mode = json.load(f).get("mode")
        return mode if mode in DISPLAY_MODES else None
    except (OSError, ValueError, AttributeError):
        return None

def load_display_mode(default="mini"):
    """Read the user's persisted active display type, without credentials."""
    return saved_display_mode() or default

def save_display_mode(mode):
    if mode not in DISPLAY_MODES:
        return False
    with open(DISPLAY_MODE_PATH, "w") as f:
        json.dump({"mode": mode}, f)
    return True

def _art_stem(value):
    value = os.path.basename(value.lower()) if isinstance(value, str) else ""
    if value.endswith(".png"): value = value[:-4]
    return value if value and all(c in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in value) else ""

def _safe_base_name(value):
    """A flat, generated custom-base filename (PNG only)."""
    name = os.path.basename(value.lower()) if isinstance(value, str) else ""
    return name if name.startswith("custom-") and _art_stem(name) else ""

def _slot_rect(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try: x, y, w, h = (round(float(v)) for v in value)
    except (TypeError, ValueError): return None
    w, h = max(40, min(w, ELECTROCOIN_CANVAS_WIDTH)), max(40, min(h, ELECTROCOIN_CANVAS_HEIGHT))
    x, y = max(0, min(x, ELECTROCOIN_CANVAS_WIDTH - w)), max(0, min(y, ELECTROCOIN_CANVAS_HEIGHT - h))
    return [x, y, w, h]

def _safe_layout_id(value):
    value = str(value).lower() if isinstance(value, str) else ""
    return value if value.startswith("custom-") and all(c in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in value) else ""

def _layout_name(value):
    value = " ".join(str(value).split()) if isinstance(value, str) else ""
    return value[:48] if value else ""

def _hex_colour(value):
    value = str(value).strip().lower() if isinstance(value, str) else ""
    return value if len(value) == 7 and value.startswith("#") and all(c in "0123456789abcdef" for c in value[1:]) else ""

def _colour_rgb(value):
    value = _hex_colour(value) or "#000000"
    return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))

def _cards(value, count):
    result, neosd_seen = [], False
    for i in range(count):
        card = value[i] if isinstance(value, list) and i < len(value) and isinstance(value[i], dict) else {}
        source = card.get("source") if card.get("source") in ("fixed", "neosd", "blank") else "blank"
        if source == "neosd": source = "blank" if neosd_seen else "neosd"; neosd_seen = True
        result.append({"source": source, "art": _art_stem(card.get("art", "")) if source == "fixed" else ""})
    return result

def load_custom_layouts():
    try:
        with open(ELECTROCOIN_LAYOUTS_PATH) as f: raw = json.load(f)
    except (OSError, ValueError): raw = []
    result, seen = [], set()
    for layout in raw if isinstance(raw, list) else []:
        if not isinstance(layout, dict): continue
        ident, name, base = _safe_layout_id(layout.get("id")), _layout_name(layout.get("name")), _safe_base_name(layout.get("base"))
        background_type = layout.get("background_type") if layout.get("background_type") in ("image", "color") else "image"
        background_color = _hex_colour(layout.get("background_color")) or "#000000"
        windows = layout.get("windows")
        source_height = layout.get("canvas_height", 360)
        source_height = source_height if source_height in (360, ELECTROCOIN_CANVAS_HEIGHT) else 360
        parsed = [_slot_rect(r) for r in windows] if isinstance(windows, list) and len(windows) in ELECTROCOIN_SLOT_COUNTS else []
        if source_height != ELECTROCOIN_CANVAS_HEIGHT:
            parsed = [_slot_rect([r[0], round(r[1] * ELECTROCOIN_CANVAS_HEIGHT / source_height),
                                 r[2], round(r[3] * ELECTROCOIN_CANVAS_HEIGHT / source_height)]) for r in parsed]
        if ident and ident not in seen and name and (base if background_type == "image" else True) and parsed and all(parsed):
            result.append({"id": ident, "name": name, "base": base, "background_type": background_type,
                           "background_color": background_color, "windows": parsed,
                           "canvas_height": ELECTROCOIN_CANVAS_HEIGHT}); seen.add(ident)
    return result

def save_custom_layouts(layouts):
    with open(ELECTROCOIN_LAYOUTS_PATH, "w") as f: json.dump(layouts, f, indent=2)

def builtin_layout(ident="electrocoin"):
    layout = BUILTIN_LAYOUTS.get(ident, BUILTIN_LAYOUTS["electrocoin"])
    return dict(layout, windows=[list(r) for r in layout["windows"]])

def is_builtin_layout(ident):
    return ident in BUILTIN_LAYOUTS

def is_diagnostic_layout(ident):
    """Diagnostics are display tools, never card-marquee layouts."""
    return bool(BUILTIN_LAYOUTS.get(ident, {}).get("diagnostic"))

def find_layout(ident, layouts=None):
    if is_builtin_layout(ident): return builtin_layout(ident)
    for layout in layouts if layouts is not None else load_custom_layouts():
        if layout["id"] == ident: return dict(layout, base_source="custom")
    return None

def load_game_titles():
    """Human-readable Neo Geo titles for MAME-style artwork filenames."""
    try:
        with open(GAME_TITLES_PATH) as f:
            titles = json.load(f)
        return {str(k): str(v) for k, v in titles.items()}
    except (OSError, ValueError, AttributeError):
        return {}

GAME_TITLES = load_game_titles()

# Panel templates and calibration/reference backgrounds share the art folder
# with game marquees so the renderer can load them directly.  They are not
# games, however: never offer them in either of the game-art selectors.
REFERENCE_ART_PREFIXES = ("electrocoin-", "neogeo-", "ultrawide-")
CARD_CACHE_FILENAME = re.compile(r"^.+--\d+x\d+\.png$")

def is_game_art_filename(name):
    return (isinstance(name, str) and name.endswith(".png") and
            not name.startswith(REFERENCE_ART_PREFIXES) and
            not CARD_CACHE_FILENAME.fullmatch(name))

def electro_config(raw=None):
    cfg = {"base": ELECTROCOIN_DEFAULT["base"], "cards": [dict(c) for c in ELECTROCOIN_DEFAULT["cards"]],
           "windows": [list(r) for r in ELECTROCOIN_DEFAULT["windows"]], "base_source": "builtin", "background_type": "image", "background_color": "#000000",
           "layout_id": "electrocoin", "selected_layout_id": "electrocoin",
           # Existing configurations predate this flag and therefore retain
           # their established displayed layout. A brand-new Pi is marked
           # false by load_electrocoin_config() until Send to display.
           "layout_sent": True,
           "assignments": {"electrocoin": [dict(c) for c in ELECTROCOIN_DEFAULT["cards"]]}}
    if isinstance(raw, dict):
        cfg["layout_sent"] = bool(raw.get("layout_sent", True))
        source = raw.get("base_source")
        cfg["background_type"] = raw.get("background_type") if raw.get("background_type") in ("image", "color") else "image"
        cfg["background_color"] = _hex_colour(raw.get("background_color")) or "#000000"
        if source == "custom":
            if cfg["background_type"] == "color":
                cfg["base"], cfg["base_source"] = "", "custom"
            else:
                name = _safe_base_name(raw.get("base"))
                if name: cfg["base"], cfg["base_source"] = name, "custom"
        else:
            stem = _art_stem(raw.get("base"))
            if stem: cfg["base"] = stem + ".png"
        ident = raw.get("layout_id")
        if is_builtin_layout(ident) or _safe_layout_id(ident): cfg["layout_id"] = ident
        ident = raw.get("selected_layout_id")
        if is_builtin_layout(ident) or _safe_layout_id(ident): cfg["selected_layout_id"] = ident
        cards = raw.get("cards")
        valid_active_count = (len(cards) in ELECTROCOIN_SLOT_COUNTS or
                              (len(cards) == 0 and is_diagnostic_layout(cfg["layout_id"]))) if isinstance(cards, list) else False
        if valid_active_count:
            cfg["cards"] = _cards(cards, len(cards))
            windows = raw.get("windows")
            if isinstance(windows, list) and len(windows) == len(cfg["cards"]):
                parsed = [_slot_rect(r) for r in windows]
                if all(parsed): cfg["windows"] = parsed
        elif isinstance(raw.get("fixed"), dict):  # migrate the original POC config
            for i, key in enumerate(("1", "2", "3")): cfg["cards"][i]["art"] = _art_stem(raw["fixed"].get(key, ""))
        assignments = raw.get("assignments")
        if isinstance(assignments, dict):
            cfg["assignments"] = {}
            for ident, cards in assignments.items():
                ident = ident if is_builtin_layout(ident) else _safe_layout_id(ident)
                valid_assignment_count = (len(cards) in ELECTROCOIN_SLOT_COUNTS or
                                          (len(cards) == 0 and is_diagnostic_layout(ident))) if isinstance(cards, list) else False
                if valid_assignment_count and ident:
                    cfg["assignments"][ident] = _cards(cards, len(cards))
        # Never inherit a previous cabinet's assignments when opening a
        # diagnostic. Older saved configs did not understand zero-slot
        # layouts, so explicitly canonicalise them here as well.
        if is_diagnostic_layout(cfg["layout_id"]):
            cfg["cards"], cfg["windows"] = [], []
        cfg["assignments"][cfg["layout_id"]] = [dict(c) for c in cfg["cards"]]
    return cfg

def load_electrocoin_config():
    try:
        with open(ELECTROCOIN_CONFIG_PATH) as f: raw = json.load(f)
    except (OSError, ValueError): return electro_config({"layout_sent": False})
    cfg = electro_config(raw)
    # Built-in templates and legacy custom layouts were authored on the old
    # 360px canvas. Always use their current stored geometry on load so a
    # layout and its card assignments cannot drift apart after an upgrade.
    active = find_layout(cfg["layout_id"], load_custom_layouts())
    if active:
        cfg["base"], cfg["base_source"] = active["base"], active.get("base_source", "custom")
        cfg["background_type"] = active.get("background_type", "image")
        cfg["background_color"] = active.get("background_color", "#000000")
        cfg["windows"] = [list(r) for r in active["windows"]]
        cfg["cards"] = _cards(cfg.get("assignments", {}).get(active["id"], cfg["cards"]), len(cfg["windows"]))
    # Preserve an already-built pre-library Custom layout on upgrade rather
    # than leaving it as an anonymous one-off configuration.
    if raw.get("base_source") == "custom" and not _safe_layout_id(raw.get("layout_id")):
        layouts = load_custom_layouts()
        ident = "custom-imported"
        used = {layout["id"] for layout in layouts}
        suffix = 2
        while ident in used: ident, suffix = "custom-imported-%d" % suffix, suffix + 1
        layouts.append({"id": ident, "name": "Imported custom layout", "base": cfg["base"], "windows": cfg["windows"]})
        save_custom_layouts(layouts)
        cfg["layout_id"] = cfg["selected_layout_id"] = ident
        cfg["assignments"][ident] = [dict(card) for card in cfg["cards"]]
        save_electrocoin_config(cfg)
    return cfg

def save_electrocoin_config(cfg):
    cfg = electro_config(cfg)
    with open(ELECTROCOIN_CONFIG_PATH, "w") as f: json.dump(cfg, f, indent=2)
    return cfg

def electro_payload(cfg):
    payload = dict(cfg)
    payload["layouts"] = [builtin_layout(ident) for ident in BUILTIN_LAYOUT_ORDER] + load_custom_layouts()
    return payload

def activate_layout(cfg, layout, layouts=None):
    """Make a saved layout active while retaining its own card selections."""
    cards = cfg.get("assignments", {}).get(layout["id"])
    if not isinstance(cards, list) or len(cards) != len(layout["windows"]):
        cards = ELECTROCOIN_DEFAULT["cards"] if layout["id"] == "electrocoin" else []
    cfg["layout_id"] = layout["id"]
    cfg["layout_sent"] = True
    cfg["base"], cfg["base_source"] = layout["base"], layout.get("base_source", "custom")
    cfg["background_type"] = layout.get("background_type", "image")
    cfg["background_color"] = _hex_colour(layout.get("background_color")) or "#000000"
    cfg["windows"] = [list(r) for r in layout["windows"]]
    cfg["cards"] = _cards(cards, len(cfg["windows"]))
    cfg.setdefault("assignments", {})[cfg["layout_id"]] = [dict(card) for card in cfg["cards"]]
    return cfg

# --manual mode: this panel has no NeoSD Pro behind it (a real cartridge
# can't announce itself), so what it shows is picked by hand from the
# admin page and remembered here.
SELECTION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "selection.json")


def read_selection():
    try:
        with open(SELECTION_PATH) as f:
            return json.load(f).get("short")
    except (OSError, ValueError, AttributeError):
        return None


def write_selection(short_name):
    try:
        with open(SELECTION_PATH, "w") as f:
            json.dump({"short": short_name}, f)
    except OSError:
        pass


# --art-source: with more than one panel, the primary Pi holds the art
# library and every other panel fetches from it, so there is one folder to
# maintain instead of one per display. Fetched files are cached locally so
# a brief network outage doesn't blank the marquee.

def remote_art_list(base_url):
    """Art filenames available on the primary, or None if unreachable."""
    try:
        import urllib.request
        with urllib.request.urlopen(base_url.rstrip("/") + "/list",
                                    timeout=3) as r:
            names = json.loads(r.read().decode())
        return [n for n in names if is_game_art_filename(n)]
    except Exception:
        return None


def fetch_remote_art(base_url, short_name, art_dir):
    """Pull one PNG from the primary into the local cache.

    Returns True if a usable local copy exists afterwards — including the
    case where the download failed but a previous copy is still cached."""
    safe = _safe_art_name(short_name + ".png")
    if not safe:
        return False
    dest = os.path.join(art_dir, safe)
    try:
        import urllib.request
        url = base_url.rstrip("/") + "/art/" + safe
        with urllib.request.urlopen(url, timeout=5) as r:
            data = r.read()
        if data[:8] == PNG_MAGIC:
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)  # atomic: never leave a half-written PNG
            return True
    except Exception:
        pass
    return os.path.exists(dest)
CAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")

# MVS mini-marquee cards are a standard 4.44 x 5.44 inches on every cab,
# so the correct window aspect is a constant: height = width * this.
MARQUEE_ASPECT = 5.44 / 4.44

# Which physical edge a panel's ribbon cable exits decides which way the
# D-pad needs to be corrected, and that's an installation detail, not
# something derivable from the rotate angle alone (two different panels
# tonight needed two different corrections at the same math). So this is
# an empirical, per-panel setting instead of a formula: DPAD_CYCLE is the
# compass in on-screen order, and dpad_offset (0/90/180/270, stored in
# calibration.json) says how many steps to rotate it before applying the
# base identity vectors. One click of "D-pad" in the admin page advances
# it; Save remembers it. Works regardless of what pygame's rotation
# convention actually does internally.
DPAD_IDENTITY = {"up": (0, -1), "right": (1, 0), "down": (0, 1), "left": (-1, 0)}
DPAD_CYCLE = ["up", "right", "down", "left"]


def physical_delta(dpad_offset, direction, step):
    if direction not in DPAD_CYCLE:
        return 0, 0
    shift = (int(dpad_offset) // 90) % 4
    i = DPAD_CYCLE.index(direction)
    eff_dir = DPAD_CYCLE[(i + shift) % 4]
    ux, uy = DPAD_IDENTITY[eff_dir]
    return ux * step, uy * step

# Commands from the web admin page's live calibration controls, drained
# by the main thread only (SDL/KMS rendering is not thread-safe).
CAL_QUEUE = queue.Queue()

# Sleep/Wake requests from the admin page. Same reason as CAL_QUEUE: SDL
# and DRM calls must happen on the main thread, not in an HTTP handler.
DISPLAY_QUEUE = queue.Queue()
CABINET_POWEROFF_MARKER = Path("/run/marqueemark/cabinet-poweroff")
SYSTEM_BOOT_MARKER = Path("/run/marqueemark/system-boot")


def queue_shutdown_sequence(_signum=None, _frame=None):
    """Hand a machine-shutdown signal to the renderer's main thread."""
    DISPLAY_QUEUE.put(("shutdown_sequence",))


def load_calibration():
    """Return ([x, y, w, h], tilt_degrees, rotate_or_None, dpad_offset), or None.

    rotate is only present once something has saved it (the web "Flip"
    button, or the CLI calibrator preserving it on save). When absent,
    the caller should fall back to the --rotate launch argument.
    dpad_offset defaults to 0 (no correction) until "D-pad" is used."""
    try:
        with open(CAL_PATH) as f:
            c = json.load(f)
        rect = [int(c["x"]), int(c["y"]), int(c["w"]), int(c["h"])]
        rot = c.get("rotate")
        return (rect, float(c.get("tilt", 0.0)),
               (int(rot) if rot is not None else None),
               int(c.get("dpad_offset", 0)))
    except (OSError, ValueError, KeyError):
        return None


def save_calibration(rect, tilt=0.0, rotate=None, dpad_offset=0):
    data = {"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3],
            "tilt": round(tilt, 2), "dpad_offset": int(dpad_offset) % 360}
    if rotate is not None:
        data["rotate"] = int(rotate)
    with open(CAL_PATH, "w") as f:
        json.dump(data, f)


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

SHUTDOWN_PREVIEW_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MarqueeMark — Shutdown preview</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#090a10;color:#e8e8f0;font-family:system-ui,sans-serif}
main{width:min(1366px,96vw)}h1{font-size:1rem;margin:0 0 12px;color:#cdd3e6}.frame{position:relative;aspect-ratio:1366/380;display:grid;place-items:center;overflow:hidden;background:#000;border:1px solid #5e6380;box-shadow:0 18px 60px #000}.logo{font:700 clamp(2rem,7vw,6rem) Georgia,serif;letter-spacing:-.07em}.sub{font-weight:800;letter-spacing:.08em;font-size:clamp(.7rem,1.7vw,1.5rem)}.snk{font-weight:900;font-style:italic;font-size:clamp(1rem,2.2vw,2rem)}.boot,.over{text-align:center}.over{font-size:clamp(2rem,6vw,5rem);font-weight:900}.mirror{background:#f8f8f8;color:#080808;width:100%;height:100%;display:grid;place-items:center}.replay{color:#bdd3ff;text-decoration:underline;cursor:pointer}p{color:#aab1c1;font-size:.85rem}
</style></head><body><main><h1>Shutdown sequence preview</h1><div class="frame" id="frame"></div><p>Native marquee sequence: boot screen, reverse logo, GAME OVER, then black. <span class="replay" id="replay">Replay</span></p></main><script>const f=document.getElementById('frame');let start=performance.now();function draw(now){const t=((now-start)/1000)%10;if(t<.9)f.innerHTML='<div class="boot"><div class="logo">NEO·GEO</div><div class="sub">MAX 330 MEGA<br>PRO-GEAR SPEC</div><div class="snk">SNK</div></div>';else if(t<2.1){const p=(t-.9)/1.2;if(p<.5)f.innerHTML='<div class="logo" style="transform:scaleX('+(1-p*2)+')">NEO·GEO</div>';else f.innerHTML='<div class="mirror"><div class="logo" style="transform:scaleX(-'+((p-.5)*2)+')">NEO·GEO</div></div>';}else if(t<7.6)f.innerHTML='<div class="mirror"><div class="logo" style="transform:scaleX(-1)">NEO·GEO</div></div>';else if(t<9.45)f.innerHTML='<div class="over">GAME OVER<div class="sub" style="margin-top:.8rem;color:#aaa">CABINET POWER OFF</div></div>';else f.innerHTML='';requestAnimationFrame(draw)}requestAnimationFrame(draw);document.getElementById('replay').onclick=()=>start=performance.now();</script></body></html>"""


ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MarqueeMark — Admin</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #101018;
         color: #e8e8f0; }
  header { padding: 16px 24px; background: #1a1a28; border-bottom: 2px solid #c8102e; }
  .header-row { max-width: 1100px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 14px; }
  .header-actions { display: flex; align-items: center; gap: 8px; }
  h1 { margin: 0; font-size: 1.2rem; letter-spacing: 0.04em; }
  h1 span { color: #c8102e; }
  main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  section { margin-bottom: 36px; }
  h2 { font-size: 1rem; letter-spacing: 0.03em; color: #ccc; margin: 0 0 4px; }
  .hint { color: #888; font-size: 0.85rem; margin: 0 0 14px; }
  .btn { background: #2a2a3a; color: #e8e8f0; border: 1px solid #3a3a4a;
         border-radius: 6px; padding: 9px 14px; font-size: 0.85rem;
         cursor: pointer; }
  .btn:hover { background: #33334a; }
  .btn.primary { background: #1e5c2e; border-color: #2a7a3e; color: #fff; }
  .btn.primary:hover { background: #257038; }
  .btn.primary.saved { background: #23733a; border-color: #65d58b; color: #fff; }
  .btn:disabled, .btn.primary:disabled { background: #2a2a3a; border-color: #3a3a4a; color: #777; cursor: not-allowed; }
  .btn.danger { background: #4a1e1e; border-color: #6a2a2a; color: #fca; }
  .btn.danger:hover { background: #5a2424; }
  .hidden { display: none !important; }
  .display-mode-button { white-space: nowrap; flex: 0 0 auto; min-width: 215px; padding: 8px 12px;
                         text-align: left; }
  .display-mode-label { display: block; margin: 0 0 2px 14px; color: #aeb8d7; font-size: .61rem;
                        font-weight: 750; letter-spacing: .09em; }
  .display-mode-value { display: block; font-size: .84rem; font-weight: 650; }
  .display-mode-button .mode-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #63d986; margin-right: 6px; }
  .display-mode-button.needs-setup .mode-dot { background: #e6b94f; }
  .settings-button { width: 42px; min-height: 42px; padding: 0; font-size: 1.25rem; }
  .mode-options, .admin-tabs { display: flex; flex-wrap: wrap; gap: 8px; }
  .mode-option { min-width: 160px; text-align: left; padding: 10px 13px; border: 1px solid #30354b;
                 border-radius: 8px; background: #171a26; color: #9399aa; cursor: pointer; }
  .mode-option strong, .mode-option small { display: block; }
  .mode-option small { margin-top: 2px; font-size: .76rem; color: inherit; }
  .mode-option.selected { background: #334a80; border-color: #86a8ff; color: #fff; }
  .admin-tabs { border-bottom: 1px solid #30354b; gap: 12px; margin-bottom: 24px; }
  .admin-tab-button { border: 0; border-bottom: 3px solid transparent; background: transparent; color: #9ba1b2;
                      border-radius: 8px 8px 0 0; padding: 13px 20px 12px; font: 700 1.04rem inherit; cursor: pointer; }
  .admin-tab-button:hover { color: #fff; background: #181b29; }
  .admin-tab-button.selected { color: #fff; border-color: #86a8ff; background: #1b2032; }
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

  #cal-panel { background: #161622; border-radius: 10px; padding: 20px;
               margin-top: 14px; }
  .cal-readout { font-family: ui-monospace, monospace; font-size: 0.8rem;
                 color: #7ad; background: #0c0c14; border-radius: 6px;
                 padding: 8px 12px; margin-bottom: 16px; display: inline-block; }
  .cal-layout { display: flex; gap: 32px; flex-wrap: wrap; }
  .dpad { display: grid; grid-template-columns: 52px 52px 52px;
          grid-template-rows: 52px 52px 52px; gap: 4px; }
  .dpad button { font-size: 1.1rem; padding: 0; }
  .dpad .u { grid-column: 2; grid-row: 1; }
  .dpad .l { grid-column: 1; grid-row: 2; }
  .dpad .mid { grid-column: 2; grid-row: 2; font-size: 0.68rem; }
  .dpad .r { grid-column: 3; grid-row: 2; }
  .dpad .d { grid-column: 2; grid-row: 3; }
  .cal-col { display: flex; flex-direction: column; gap: 14px; min-width: 220px; }
  .cal-row { display: flex; align-items: center; gap: 8px; }
  .cal-row .label { color: #999; font-size: 0.82rem; width: 44px; }
  .eco-card { appearance: none; -webkit-appearance: none; min-width: 240px; box-sizing: border-box;
              padding: 8px 34px 8px 11px; border: 1px solid #465579; border-radius: 8px;
              color: #f1f4ff; background-color: #1b2132;
              background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='8' viewBox='0 0 14 8'%3E%3Cpath d='M1 1l6 6 6-6' fill='none' stroke='%23cbd6ff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
              background-repeat: no-repeat; background-position: right 12px center; background-size: 12px 7px;
              font: 600 .85rem inherit; line-height: 1.2; box-shadow: inset 0 1px 0 rgba(255,255,255,.045); cursor: pointer; }
  .eco-card:hover { border-color: #728bc8; background-color: #20283d; }
  .eco-card:focus { outline: 2px solid #86a8ff; outline-offset: 2px; border-color: #86a8ff; }
  .eco-card option, .eco-card optgroup { background: #1b2132; color: #f1f4ff; }
  .cal-actions { margin-top: 20px; display: flex; gap: 10px; }
  .subheading { margin: 22px 0 6px; font-size: 1rem; color: var(--text); }
  .template-pills { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 4px; }
  .template-pill { border: 1px solid #292f42; border-radius: 999px; background: #171a26;
                   color: #c0c4d0; padding: 5px 10px; font: 0.9rem/1.25 inherit; cursor: pointer; }
  .template-pill:hover:not(:disabled) { border-color: #667aaf; background: #20283e; color: #fff; }
  .template-pill.selected { background: #405489; border-color: #86a8ff; color: #fff;
                            font-weight: 600; }
  .template-pill:disabled { cursor: not-allowed; opacity: .4; }
  .layout-choice { display: inline-flex; align-items: center; gap: 3px; }
  .layout-icon { width: 27px; height: 27px; padding: 0; border: 1px solid #292f42; border-radius: 50%;
                 background: #171a26; color: #bcc4d8; cursor: pointer; font-size: .9rem; }
  .layout-icon:hover { color: #fff; border-color: #667aaf; background: #20283e; }
  .layout-library-group { margin-top: 14px; }
  .layout-library-group:first-child { margin-top: 8px; }
  .layout-library-title { margin: 0 0 4px; color: #9ea6bc; font-size: .78rem; font-weight: 600;
                          letter-spacing: .03em; text-transform: uppercase; }
  .create-layout-cta { display: flex; align-items: center; justify-content: space-between; gap: 16px;
                        margin: 16px 0; padding: 14px; border: 1px solid #405489; border-radius: 10px;
                        background: linear-gradient(90deg, #18213b, #141520); }
  .create-layout-cta strong, .create-layout-cta span { display: block; }
  .create-layout-cta span { margin-top: 3px; color: #aab1c1; font-size: .84rem; }
  @media (max-width: 560px) { .create-layout-cta { align-items: flex-start; flex-direction: column; } }
  .advanced-diagnostics { margin-top: 14px; color: #9ea6bc; font-size: .84rem; }
  .advanced-diagnostics summary { cursor: pointer; width: fit-content; }
  .advanced-diagnostics .template-pills { margin: 8px 0 0; }
  .diagnostic-run { padding: 5px 10px; font-size: .82rem; }
  .layout-live-dot { width: 8px; height: 8px; flex: 0 0 8px; border-radius: 50%; background: #63d986;
                     box-shadow: 0 0 0 2px rgba(99,217,134,.16); }
  .modal-backdrop { position: fixed; inset: 0; z-index: 20; display: grid; place-items: center;
                    background: rgba(0,0,0,.72); padding: 20px; }
  .modal { width: min(900px, 100%); max-height: 90vh; overflow: auto; position: relative;
           padding: 20px; border: 1px solid #4a5270; border-radius: 12px; background: #151724;
           box-shadow: 0 18px 60px rgba(0,0,0,.65); }
  .modal-close { position: absolute; right: 12px; top: 10px; border: 0; background: transparent;
                 color: #cbd1e2; font-size: 1.6rem; cursor: pointer; }
  #layout-preview-canvas { position: relative; width: 100%; aspect-ratio: 1366 / 380; margin-top: 12px;
                           background: #050508 center / cover no-repeat; border: 1px solid #5e6380; }
  #live-layout-panel { margin: 14px 0 24px; padding: 14px; border: 1px solid #30354b; border-radius: 10px; background: #141520; }
  #live-layout-canvas { position: relative; width: min(720px,100%); aspect-ratio: 1366 / 380; margin: 10px auto 0;
                         overflow: hidden; background: #050508 center / cover no-repeat; border: 1px solid #5e6380; }
  #live-layout-canvas.empty { display: grid; place-items: center; background: #090a11 !important; }
  .live-layout-empty { max-width: 420px; padding: 16px; text-align: center; }
  .live-layout-empty strong { display: block; font-size: 1rem; }
  .live-layout-empty p { margin: 6px 0 12px; color: #aab1c1; font-size: .84rem; }
  .live-layout-empty .cal-row { justify-content: center; flex-wrap: wrap; }
  .custom-layout-empty { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 10px 12px;
                         border: 1px dashed #3d4563; border-radius: 8px; color: #aab1c1; font-size: .88rem; }
  .advanced-enable { padding: 12px; border: 1px solid #3d4563; border-radius: 8px; background: #11131d; }
  .advanced-enable label { font-weight: 700; }
  .advanced-enable p { margin: 6px 0 0; }
  fieldset.advanced-settings-content { min-width: 0; margin: 20px 0 0; padding: 0; border: 0; }
  fieldset.advanced-settings-content:disabled { opacity: .42; }
  .marquee-card-image { position: absolute; object-fit: fill; }
  .neosd-placeholder { position: absolute; box-sizing: border-box; display: flex; flex-direction: column;
                       align-items: center; justify-content: center; gap: 3px; overflow: hidden;
                       border: 2px solid #e23c58; background: linear-gradient(145deg,#30101b,#09080e);
                       color: #fff; text-align: center; text-shadow: 0 1px 2px #000; font-weight: 700; }
  .neosd-placeholder::before { content: '★'; color: #ef3f5a; font-size: clamp(11px,2.1vw,22px); line-height: 1; }
  .neosd-placeholder span { font-size: clamp(8px,1.35vw,16px); white-space: nowrap; }
  .neosd-placeholder small { color: #f0a7b3; font-size: clamp(6px,.85vw,10px); text-transform: uppercase;
                             letter-spacing: .08em; white-space: nowrap; }
  #layout-preview-canvas .layout-slot { pointer-events: none; }
  #custom-editor { margin-top: 14px; padding: 14px; border: 1px solid #30354b; border-radius: 10px; background: #11121b; }
  #layout-canvas { position: relative; width: min(100%, 1000px); aspect-ratio: 1366 / 380;
                   margin-top: 12px; overflow: hidden; background: #050508 center / cover no-repeat;
                   border: 1px solid #5e6380; user-select: none; touch-action: none; }
  .layout-slot { position: absolute; box-sizing: border-box; border: 2px solid #31d7e9;
                 background: rgba(49, 215, 233, .18); color: #fff; cursor: grab; display: grid; place-items: center;
                 font-size: clamp(9px, 1.5vw, 15px); font-weight: 700; text-shadow: 0 1px 2px #000; }
  .layout-slot:active { cursor: grabbing; }
  .layout-slot button { position: absolute; right: 2px; top: 2px; width: 20px; height: 20px; padding: 0;
                        border: 0; border-radius: 50%; background: #b4283b; color: #fff; cursor: pointer; }
  .slot-resize { position: absolute; right: -6px; bottom: -6px; width: 14px; height: 14px;
                 background: #fff; border: 2px solid #168e9c; border-radius: 2px; cursor: nwse-resize; }
  .ai-generator { margin-top: 12px; padding: 12px; border: 1px solid #30354b; border-radius: 8px; background: #151621; }
  .ai-generator summary { cursor: pointer; color: #cdd7fa; font-weight: 600; }
  .ai-generator textarea { width: min(700px,100%); min-height: 76px; box-sizing: border-box; resize: vertical; }
  #ai-status { min-height: 1.2em; margin: 8px 0 0; color: #9ad; font-size: .82rem; }
</style>
</head>
<body>
<header><div class="header-row"><h1>Marquee<span>Mark</span> — Admin
  <small style="color:#888;font-weight:normal;font-size:0.7em">v{{VERSION}}</small></h1>
  <div class="header-actions">
    <button id="display-mode-change" type="button" class="btn display-mode-button" aria-label="Change monitor output type">
      <span class="display-mode-label">CURRENTLY SELECTED MONITOR OUTPUT</span><span class="display-mode-value"><span class="mode-dot"></span><span id="display-mode-summary">Display setup needed</span> <span id="display-mode-action">· Set up</span></span>
    </button>
    <button id="advanced-settings-open" type="button" class="btn settings-button" title="Advanced settings" aria-label="Advanced settings">⚙</button>
  </div>
</div></header>
<main>

<nav class="admin-tabs" aria-label="Marquee administration">
  <button type="button" class="admin-tab-button" data-admin-tab="mini">Mini Marquee</button>
  <button type="button" class="admin-tab-button" data-admin-tab="ultrawide">Ultrawide Marquee</button>
</nav>

<section id="tab-ultrawide" class="admin-tab">
<div id="electrocoin-section" class="hidden">
  <h2>Ultrawide Marquee <span style="color:#888;font-weight:normal;font-size:0.7em">(1366 × 380)</span></h2>
  <section id="live-layout-panel"><h3 class="subheading" style="margin-top:0">On display</h3>
    <p id="live-layout-name" class="hint">Loading current layout…</p><div id="live-layout-canvas" aria-label="Current digital marquee preview"></div>
  </section>
  <h3 class="subheading">Marquee layout</h3>
  <p class="hint">Choose a saved layout, or create a new one. Layouts contain only the background and mini-marquee positions.</p>
  <div class="layout-library-group"><p class="layout-library-title">Built in Marquees</p><div id="eco-builtins" class="template-pills" role="radiogroup" aria-label="Built in marquees"></div></div>
  <div class="layout-library-group"><p class="layout-library-title">Your Custom Marquees</p><div id="eco-customs" class="template-pills" role="radiogroup" aria-label="Your custom marquees"></div></div>
  <div class="create-layout-cta"><div><strong>Create your own marquee</strong><span>Upload or generate a background, then position 1, 2, 4, or 6 mini-marquee windows.</span></div><button id="create-custom-layout" type="button" class="btn primary">+ Create custom layout</button></div>
  <details class="advanced-diagnostics"><summary>Advanced diagnostics</summary><p class="hint" style="margin:8px 0 0">Tools for measuring or troubleshooting an unusual display.</p><div id="eco-diagnostics" class="template-pills" role="radiogroup" aria-label="Advanced display diagnostics"></div></details>
  <div id="custom-editor" class="hidden">
    <p class="hint">Choose either an image or a solid colour background, then position the mini-marquee objects over it. Slot labels are editing guides only.</p>
    <div class="cal-row"><input id="custom-file" type="file" accept="image/png,image/jpeg"><select id="custom-fit"><option value="cover">Fill canvas (crop edges)</option><option value="contain">Fit canvas (black bars if needed)</option></select><button id="custom-upload" class="btn">Upload image</button></div>
    <div class="cal-row" style="margin-top:10px"><label><input id="custom-solid" type="checkbox"> Use a solid colour instead</label><input id="custom-colour" type="color" value="#000000" aria-label="Background colour"></div>
    <details class="ai-generator"><summary>Generate a background with AI</summary>
      <p class="hint" style="margin-top:10px">Your key is used directly by your browser and is never sent to this Pi. Image-generation workflow inspired by <a href="https://github.com/raz0red/ifwithgraphics" target="_blank" rel="noopener">IFWG by raz0red</a>.</p>
      <div class="cal-row"><label class="label" for="ai-provider">Provider</label><select id="ai-provider"><option value="openai">OpenAI</option><option value="gemini">Google Gemini</option></select><input id="ai-key" type="password" autocomplete="off" placeholder="API key" aria-label="API key"><button id="ai-validate" class="btn">Validate key</button></div>
      <div class="cal-row" style="margin-top:8px"><label class="label" for="ai-model">Model</label><select id="ai-model" disabled><option>Validate key first</option></select></div>
      <div class="cal-row" style="margin-top:8px"><label><input id="ai-remember" type="checkbox"> Remember this key on this device</label></div>
      <div style="margin-top:8px"><label for="ai-prompt" class="hint" style="display:block">Describe the marquee background</label><textarea id="ai-prompt" placeholder="e.g. brushed steel Neo Geo-inspired arcade art deco background, teal and magenta circuitry, large clear space for four game cards"></textarea></div>
      <div class="cal-actions" style="margin-top:10px"><button id="ai-generate" class="btn">Generate background</button></div><p id="ai-status"></p>
    </details>
    <div id="layout-canvas" aria-label="Custom marquee layout editor"></div>
    <div class="cal-actions"><span class="hint" style="margin:auto 0">Mini-marquees</span><div id="slot-count-pills" class="template-pills" role="radiogroup" aria-label="Number of mini-marquee slots"></div></div>
    <div class="cal-row" style="margin-top:10px"><label><input id="uniform-slots" type="checkbox"> Keep all slots the same size</label><label id="uniform-leader-wrap" class="hidden">Follow size of <select id="uniform-leader"></select></label></div>
    <p id="uniform-slots-hint" class="hint" style="margin:8px 0 0"></p>
    <div class="cal-actions"><button id="layout-rename" class="btn hidden">Rename</button><button id="layout-save" class="btn primary">Save layout</button><button id="layout-cancel" class="btn">Cancel</button></div>
  </div>
  <section id="eco-assignment-section">
    <h3 class="subheading">Card marquee assignment</h3>
    <p id="eco-assignment-hint" class="hint">Choose what each card window shows. NeoSD Pro is the special live card; artwork choices are labelled with game titles.</p>
    <div id="eco-cards"></div>
    <div class="cal-actions"><button id="eco-save" class="btn primary">Save card assignments</button><button id="layout-send" class="btn primary">Send to display</button><button id="layout-delete" class="btn hidden">Delete this custom layout</button></div>
  </section>
</div>
</section>

<div id="layout-name-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="layout-name-title">
  <div class="modal" id="layout-name-dialog"><button class="modal-close" id="layout-name-close" aria-label="Close">×</button>
    <h2 id="layout-name-title">Name this layout</h2><p class="hint">This is how it will appear in your saved layout library.</p>
    <input id="layout-name-input" maxlength="48" placeholder="e.g. My four-slot marquee" style="width:min(420px,100%)">
    <div class="cal-actions"><button id="layout-name-confirm" class="btn primary">Save layout</button><button id="layout-name-cancel" class="btn">Cancel</button></div>
  </div>
</div>
<div id="layout-preview-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="layout-preview-title">
  <div class="modal" id="layout-preview-dialog"><button class="modal-close" id="layout-preview-close" aria-label="Close">×</button>
    <h2 id="layout-preview-title">Layout preview</h2><p id="layout-preview-hint" class="hint">Saved card assignments are shown here. NeoSD Pro shows its current game when available, otherwise a live-marquee placeholder.</p><div id="layout-preview-canvas"></div>
  </div>
</div>
<div id="display-mode-warning-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="display-mode-warning-title">
  <div class="modal"><button class="modal-close" id="display-mode-warning-close" aria-label="Close">×</button>
    <h2 id="display-mode-warning-title">Switch display type?</h2>
    <p id="display-mode-warning-copy" class="hint"></p>
    <div class="cal-actions"><button id="display-mode-warning-continue" class="btn primary">Switch display</button><button id="display-mode-warning-cancel" class="btn">Cancel</button></div>
  </div>
</div>
<div id="display-mode-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="display-mode-title">
  <div class="modal"><button class="modal-close" id="display-mode-close" aria-label="Close">×</button>
    <h2 id="display-mode-title">Display hardware setup</h2><p id="display-mode-intro" class="hint"></p>
    <div class="mode-options" role="radiogroup" aria-label="Active display type">
      <button type="button" class="mode-option" data-display-mode="mini" role="radio"><strong>Mini Marquee</strong><small>Original portrait marquee artwork and calibration</small></button>
      <button type="button" class="mode-option" data-display-mode="ultrawide" role="radio"><strong>Ultrawide Marquee</strong><small>Wide layouts with one or more mini-marquee cards</small></button>
    </div>
    <p id="display-mode-current" class="hint" style="margin:12px 0 0"></p>
    <div class="cal-actions"><button id="display-mode-apply" class="btn primary">Save and activate</button><button id="display-mode-cancel" class="btn">Cancel</button></div>
  </div>
</div>
<div id="display-mode-keep-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="display-mode-keep-title">
  <div class="modal"><button class="modal-close" id="display-mode-keep-close" aria-label="Revert and close">×</button>
    <h2 id="display-mode-keep-title">Testing display type</h2>
    <p id="display-mode-keep-copy" class="hint"></p>
    <p id="display-mode-countdown" style="font-weight:700"></p>
    <div class="cal-actions"><button id="display-mode-keep-confirm" class="btn primary">Keep this display type</button><button id="display-mode-keep-revert" class="btn">Revert now</button></div>
  </div>
</div>
<div id="advanced-settings-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="advanced-settings-title">
  <div class="modal"><button class="modal-close" id="advanced-settings-close" aria-label="Close">×</button>
    <h2 id="advanced-settings-title">Advanced settings</h2>
    <div class="advanced-enable"><label><input id="advanced-settings-enabled" type="checkbox"> Enable advanced settings</label>
      <p class="hint">Advanced settings can change how MarqueeMark and the cabinet behave. Enable them only if you understand the options below.</p>
    </div>
    <fieldset id="advanced-settings-content" class="advanced-settings-content" disabled>
      <h3 class="subheading">Auto Shutdown of Pi Safely Upon Cabinet Power-off</h3>
      <p class="hint">When cabinet power is switched off, the NeoSD Pro disconnects. After the delay below, MarqueeMark shows the shutdown screen and safely shuts down the Pi. Normal game inactivity will never trigger a shutdown.</p>
      <div class="cal-row">
        <label><input id="shutdown-enabled" type="checkbox"> Turn on automatic Pi shutdown</label>
        <label>After cabinet power-off is detected, wait <input id="shutdown-delay" type="number" min="5" max="600" step="1" value="10" style="width:5.5em"> seconds before safely shutting down the Pi.</label>
        <button class="btn primary" id="shutdown-save">Save auto-shutdown setting</button>
      </div>
      <div class="cal-actions"><button class="btn" id="shutdown-preview-display">Preview on marquee</button><button class="btn" id="shutdown-preview-browser">Open browser preview</button></div>
      <p id="shutdown-now" class="hint" style="margin-bottom:0"></p>
    </fieldset>
  </div>
</div>

<section id="tab-mini" class="admin-tab">
<section id="sec-showing" class="hidden">
  <h2>Now showing</h2>
  <p class="hint">This panel has no NeoSD Pro behind it, so pick its
    marquee here. It stays until you change it.</p>
  <div class="cal-row">
    <select id="sel"><option value="">(generic marquee)</option></select>
    <button class="btn primary" id="sel-set">Show it</button>
    <span id="sel-now" style="color:var(--text-secondary);font-size:0.85rem"></span>
  </div>
</section>

<section>
  <h2>Display power</h2>
  <p class="hint">Blank the panel and let its backlight switch off, or
    bring it back. Sleeping by hand stays in effect until you wake it
    here, even if the cabinet powers on.</p>
  <div class="cal-row">
    <button class="btn" id="pwr-sleep">Sleep</button>
    <button class="btn" id="pwr-wake">Wake</button>
    <span id="pwr-now" style="color:var(--text-secondary);font-size:0.85rem"></span>
  </div>
</section>

<section>
  <h2>Calibrate Marquee</h2>
  <p class="hint">Positions and sizes the image to match your physical
    marquee window. Watch the panel while you click — it updates live.
    Proportions are always locked; you cannot stretch the image.</p>
  <button id="cal-enter-btn" class="btn primary">Start Calibration</button>

  <div id="cal-panel" class="hidden">
    <div class="cal-readout" id="cal-readout">-</div>
    <div class="cal-layout">
      <div class="dpad">
        <button class="u" data-dir="up">&#9650;</button>
        <button class="l" data-dir="left">&#9664;</button>
        <button class="mid" id="cal-step">5px</button>
        <button class="r" data-dir="right">&#9654;</button>
        <button class="d" data-dir="down">&#9660;</button>
      </div>
      <div class="cal-col">
        <div class="cal-row">
          <span class="label">Size</span>
          <button class="btn" data-size="-1">&minus;</button>
          <button class="btn" data-size="1">+</button>
        </div>
        <div class="cal-row">
          <span class="label">Tilt</span>
          <button class="btn" data-tilt="-0.5">&laquo;</button>
          <button class="btn" data-tilt="-0.1">&lsaquo;</button>
          <button class="btn" data-tilt="0.1">&rsaquo;</button>
          <button class="btn" data-tilt="0.5">&raquo;</button>
        </div>
        <div class="cal-row">
          <button id="cal-flip" class="btn">Flip 180&deg; (if upside down)</button>
        </div>
        <div class="cal-row">
          <button id="cal-dpad" class="btn">D-pad: 0&deg;</button>
          <span class="hint" style="margin:0">click if arrows go the wrong way</span>
        </div>
        <div class="cal-row">
          <button id="cal-preview" class="btn">Preview: Test Pattern</button>
        </div>
      </div>
    </div>
    <div class="cal-actions">
      <button id="cal-save" class="btn primary">Save</button>
      <button id="cal-cancel" class="btn">Cancel</button>
    </div>
  </div>
</section>

<section>
  <h2>Marquee Art</h2>
  <div id="drop">Drop marquee PNGs here (or click to choose files)<br>
    <small>Name files by MAME short name: mslug.png, kof95.png ... plus generic.png</small>
  </div>
  <input type="file" id="pick" accept=".png" multiple style="display:none">
  <div id="status"></div>
  <div id="warn"></div>
  <div id="grid"></div>
</section>
</section>

<script>
// -------------------------------------------------------- art manager
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

const ECO_WIDTH=1366, ECO_HEIGHT=380;
const ECO_DEFAULT_WINDOWS=[[65,51,176,243],[442,51,178,243],[752,51,174,243],[1125,51,176,243]];
const ECO_SLOT_RATIO=176/230, ECO_SLOT_COUNTS=[1,2,4,6];
let ecoConfig=null, ecoFiles=[], ecoTitles={}, editingLayout=false, editingLayoutId=null, layoutDraft={base:'',background_type:'image',background_color:'#000000',windows:[]}, layoutDraftDirty=false, nameModalMode='create', uniformSlots=false, uniformLeader=0;
let ecoLiveShort=null, ecoLiveEvents=null, layoutSelectionRequest=0, livePreviewRequest=0;
const blankCard=()=>({source:'blank',art:''});

function selectedLayout() {
  return (ecoConfig.layouts||[]).find(layout=>layout.id===ecoConfig.selected_layout_id) || null;
}
function cardsForLayout(layout) {
  if (!layout) return [];
  const saved=ecoConfig.assignments && ecoConfig.assignments[layout.id];
  return saved ? saved.map(card=>({...card})) : Array.from({length:layout.windows.length},blankCard);
}
function selectedCards() { return cardsForLayout(selectedLayout()); }
function layoutBackgroundPath(layout) {
  // Built-in templates live with application art; uploaded custom bases live
  // in the separate base directory.  Do not infer this from a layout ID:
  // future built-in templates use the same art location as Electrocoin.
  return layout.base_source === 'builtin' ? '/art/' : '/base/';
}
function positionMiniMarquee(element, slot) {
  element.style.left=(slot[0]/ECO_WIDTH*100)+'%'; element.style.top=(slot[1]/ECO_HEIGHT*100)+'%';
  element.style.width=(slot[2]/ECO_WIDTH*100)+'%'; element.style.height=(slot[3]/ECO_HEIGHT*100)+'%';
}
function appendMiniMarquee(canvas, card, slot, liveShort) {
  if (!card || !slot || card.source==='blank') return false;
  if (card.source==='neosd' && !liveShort) {
    const placeholder=document.createElement('div'); placeholder.className='neosd-placeholder';
    placeholder.innerHTML='<span>NeoSD Pro</span><small>Live marquee</small>'; positionMiniMarquee(placeholder,slot);
    canvas.appendChild(placeholder); return true;
  }
  const stem=card.source==='neosd' ? liveShort : card.source==='fixed' ? card.art : '';
  if (!stem) return false;
  const art=document.createElement('img'); art.className='marquee-card-image'; art.src='/art/'+encodeURIComponent(stem)+'.png'; art.alt='';
  art.onerror=()=>art.remove(); positionMiniMarquee(art,slot); canvas.appendChild(art); return true;
}
function liveCardLabel(card, index) {
  const slot='Card '+(index+1)+': ';
  if (!card || card.source==='blank') return slot+'Blank';
  if (card.source==='neosd') return slot+'NeoSD Pro'+(ecoLiveShort ? ': '+(ecoTitles[ecoLiveShort]||ecoLiveShort) : ' (live marquee)');
  if (card.source==='fixed' && card.art) return slot+(ecoTitles[card.art]||card.art);
  return slot+'Blank';
}
function renderLivePreview() {
  if (!ecoConfig) return;
  const request=++livePreviewRequest;
  const layout=(ecoConfig.layouts||[]).find(item=>item.id===ecoConfig.layout_id) || null;
  const canvas=document.getElementById('live-layout-canvas'), name=document.getElementById('live-layout-name');
  if (!ecoConfig.layout_sent) {
    canvas.innerHTML='<div class="live-layout-empty"><strong>No Ultrawide layout on display yet</strong><p>Choose a built-in template or create your own, then send it to the display.</p><div class="cal-row"><button id="live-empty-create" type="button" class="btn primary">Create custom layout</button><button id="live-empty-browse" type="button" class="btn">Browse templates</button></div></div>';
    canvas.classList.add('empty'); canvas.style.backgroundImage='none'; canvas.style.backgroundColor='#090a11';
    name.textContent='Nothing has been sent to the Ultrawide display yet.';
    document.getElementById('live-empty-create').onclick=createCustomLayout;
    document.getElementById('live-empty-browse').onclick=()=>document.getElementById('eco-builtins').scrollIntoView({behavior:'smooth',block:'center'});
    return;
  }
  canvas.classList.remove('empty');
  if (!layout) { canvas.innerHTML=''; name.textContent='No live layout is available.'; return; }
  const image=layout.background_type!=='color', path=layoutBackgroundPath(layout);
  const diagnostic=!!layout.diagnostic;
  canvas.innerHTML=''; canvas.style.backgroundImage=image?'url('+path+encodeURIComponent(layout.base)+'?layout='+encodeURIComponent(layout.id)+')':'none'; canvas.style.backgroundColor=image?'#050508':(layout.background_color||'#000000');
  if (!diagnostic && request===livePreviewRequest) (ecoConfig.cards||[]).forEach((card,index)=>appendMiniMarquee(canvas,card,layout.windows[index],ecoLiveShort));
  const cards=!diagnostic ? (ecoConfig.cards||[]).map(liveCardLabel).join(' · ') : '';
  name.textContent='Currently showing: '+layout.name+(cards ? ' · '+cards : '');
}
function startEcoLiveUpdates() {
  if (ecoLiveEvents) return;
  ecoLiveEvents=new EventSource('/events');
  ecoLiveEvents.onmessage=e=>{ try {
    const next=(JSON.parse(e.data)||{}).short||null;
    // The event stream sends heartbeats. Rebuilding during every identical
    // heartbeat repeatedly cancels an in-flight preview image load, leaving
    // the background visible but its cards absent.
    if (next===ecoLiveShort) return;
    ecoLiveShort=next; renderLivePreview();
  } catch (_) {} };
}
function pullCards() {
  const layout=selectedLayout(); if (!ecoConfig || !layout) return [];
  const cards=[...document.querySelectorAll('#eco-cards .eco-card')].map(pick => {
    if (pick.value === '__neosd__') return {source:'neosd',art:''};
    return pick.value ? {source:'fixed',art:pick.value} : blankCard();
  });
  ecoConfig.assignments=ecoConfig.assignments||{}; ecoConfig.assignments[layout.id]=cards;
  if (layout.id===ecoConfig.layout_id) ecoConfig.cards=cards.map(card=>({...card}));
  return cards;
}
function renderCards() {
  const host=document.getElementById('eco-cards'); host.innerHTML='';
  const cards=selectedCards();
  if (!cards.length) { host.innerHTML='<p class="hint">Choose 1, 2, 4, or 6 mini-marquees above to create card assignments.</p>'; return; }
  cards.forEach((card, i) => {
    const row=document.createElement('div'); row.className='cal-row'; row.innerHTML='<span class="label">Card '+(i+1)+'</span>';
    const pick=document.createElement('select'); pick.className='eco-card';
    const special=document.createElement('optgroup'); special.label='Special';
    special.append(new Option('— Blank —', '', false, card.source === 'blank'));
    special.append(new Option('★ NeoSD Pro (live marquee)', '__neosd__', false, card.source === 'neosd'));
    pick.appendChild(special);
    const art=document.createElement('optgroup'); art.label='Artwork';
    const layoutBases=new Set((ecoConfig.layouts||[]).map(layout=>layout.base));
    for (const f of ecoFiles) {
      const stem=f.replace(/\.png$/, '');
      if (f === 'generic.png' || layoutBases.has(f)) continue;
      // A verified title keeps the selector clean. A literal filename is
      // deliberately retained for unknown hacks/homebrew, so it is clear
      // which labels still need mapping rather than silently guessing.
      art.append(new Option(ecoTitles[stem] || f, stem, false,
        card.source === 'fixed' && stem === card.art));
    }
    pick.appendChild(art); row.append(pick); host.appendChild(row);
  });
  const syncCards = changed => {
    const rows=[...document.querySelectorAll('#eco-cards .cal-row')];
    const changedRow = changed ? changed.closest('.cal-row') : null;
    const live = changed && changed.value === '__neosd__' ? rows.indexOf(changedRow) :
      rows.findIndex(row => row.querySelector('.eco-card').value === '__neosd__');
    rows.forEach((row, i) => {
      const pick=row.querySelector('.eco-card');
      if (pick.value === '__neosd__' && i !== live) pick.value='';
      [...pick.options].forEach(option => option.disabled =
        option.value === '__neosd__' && live !== -1 && i !== live);
    });
  };
  [...document.querySelectorAll('.eco-card')].forEach(pick =>
    pick.onchange = () => { syncCards(pick); pullCards(); });
  syncCards();
}

function renderCustomEditor() {
  const panel=document.getElementById('custom-editor'), canvas=document.getElementById('layout-canvas');
  panel.classList.toggle('hidden', !editingLayout);
  if (!editingLayout) return;
  canvas.innerHTML='';
  const solid=layoutDraft.background_type==='color';
  canvas.style.backgroundImage=!solid && layoutDraft.base ? 'url(/base/'+encodeURIComponent(layoutDraft.base)+')' : 'none';
  canvas.style.backgroundColor=solid ? layoutDraft.background_color : '#050508';
  const solidInput=document.getElementById('custom-solid'), colourInput=document.getElementById('custom-colour');
  solidInput.checked=solid; colourInput.value=layoutDraft.background_color || '#000000'; colourInput.disabled=!solid;
  const uniform=document.getElementById('uniform-slots'), leaderWrap=document.getElementById('uniform-leader-wrap'), leader=document.getElementById('uniform-leader'), uniformHint=document.getElementById('uniform-slots-hint');
  uniform.disabled=layoutDraft.windows.length<2; uniform.checked=uniformSlots && layoutDraft.windows.length>1;
  leaderWrap.classList.toggle('hidden',!uniform.checked); leader.innerHTML='';
  if (uniformLeader>=layoutDraft.windows.length) uniformLeader=0;
  layoutDraft.windows.forEach((_,index)=>leader.append(new Option('Slot '+(index+1),String(index),false,index===uniformLeader)));
  uniformHint.textContent=uniform.checked?'Resize '+leader.options[leader.selectedIndex].text+' to resize every slot. Slots can still be moved independently.':'';
  const countPills=document.getElementById('slot-count-pills'); countPills.innerHTML='';
  ECO_SLOT_COUNTS.forEach(count => {
    const pill=document.createElement('button'); pill.type='button'; pill.className='template-pill'; pill.textContent=count+' '+(count===1?'Slot':'Slots');
    const active=layoutDraft.windows.length===count; pill.classList.toggle('selected',active); pill.setAttribute('role','radio'); pill.setAttribute('aria-checked',active);
    pill.onclick=()=>setSlotCount(count); countPills.appendChild(pill);
  });
  layoutDraft.windows.forEach((slot, index) => {
    const el=document.createElement('div'); el.className='layout-slot';
    el.style.left=(slot[0]/ECO_WIDTH*100)+'%'; el.style.top=(slot[1]/ECO_HEIGHT*100)+'%';
    el.style.width=(slot[2]/ECO_WIDTH*100)+'%'; el.style.height=(slot[3]/ECO_HEIGHT*100)+'%';
    el.textContent='Slot '+(index+1);
    if (!uniform.checked || index===uniformLeader) {
      const handle=document.createElement('span'); handle.className='slot-resize'; handle.title='Resize slot';
      handle.onpointerdown=e=>startSlotDrag(e,index,'resize'); el.append(handle);
    }
    el.onpointerdown=e=>{ if (e.target===el) startSlotDrag(e,index,'move'); };
    canvas.appendChild(el);
  });
  updateEditorActions();
}
function draftIsValid() { return (layoutDraft.background_type==='color' || !!layoutDraft.base) && ECO_SLOT_COUNTS.includes(layoutDraft.windows.length); }
function updateEditorActions() {
  const saving=document.getElementById('layout-save'), rename=document.getElementById('layout-rename'), editing=!!editingLayoutId;
  rename.classList.toggle('hidden',!editing); saving.textContent=editing?'Save changes':'Save layout';
  saving.disabled=editing ? !layoutDraftDirty : !draftIsValid();
}
function defaultSlots(count) {
  const h=count>=6?190:count>=4?220:230, w=Math.round(h*ECO_SLOT_RATIO), gap=(ECO_WIDTH-count*w)/(count+1), y=Math.round((ECO_HEIGHT-h)/2);
  return Array.from({length:count},(_,i)=>[Math.round(gap+(w+gap)*i),y,w,h]);
}
function setSlotCount(count) {
  if (layoutDraft.windows.length===count) return;
  const oldWindows=layoutDraft.windows, defaults=defaultSlots(count);
  layoutDraft.windows=defaults.map((slot,i)=>oldWindows[i] || slot);
  if (uniformLeader>=count) uniformLeader=0;
  if (uniformSlots && count>1) applyUniformSize();
  layoutDraftDirty=true; renderCustomEditor();
}
function clampSlotSize(slot,w,h) {
  return [Math.max(0,Math.min(ECO_WIDTH-w,slot[0])),Math.max(0,Math.min(ECO_HEIGHT-h,slot[1])),w,h];
}
function applyUniformSize() {
  const lead=layoutDraft.windows[uniformLeader]; if (!lead) return;
  layoutDraft.windows=layoutDraft.windows.map(slot=>clampSlotSize(slot,lead[2],lead[3]));
}
function placeSlotElement(el, slot) {
  el.style.left=(slot[0]/ECO_WIDTH*100)+'%'; el.style.top=(slot[1]/ECO_HEIGHT*100)+'%';
  el.style.width=(slot[2]/ECO_WIDTH*100)+'%'; el.style.height=(slot[3]/ECO_HEIGHT*100)+'%';
}
function startSlotDrag(event, index, mode) {
  event.preventDefault(); event.stopPropagation();
  const canvas=document.getElementById('layout-canvas'), bounds=canvas.getBoundingClientRect(), start=[...layoutDraft.windows[index]], x0=event.clientX, y0=event.clientY; let moved=false;
  const slotElement=canvas.children[index];
  const move=e=>{
    const dx=(e.clientX-x0)*ECO_WIDTH/bounds.width, dy=(e.clientY-y0)*ECO_HEIGHT/bounds.height;
    let [x,y,w,h]=start;
    if (mode === 'move') { x=Math.max(0,Math.min(ECO_WIDTH-w,Math.round(x+dx))); y=Math.max(0,Math.min(ECO_HEIGHT-h,Math.round(y+dy))); }
    else { w=Math.max(60,Math.min(ECO_WIDTH-x,(ECO_HEIGHT-y)*ECO_SLOT_RATIO,Math.round(w+dx))); h=Math.round(w/ECO_SLOT_RATIO); }
    moved = moved || x!==start[0] || y!==start[1] || w!==start[2] || h!==start[3]; layoutDraft.windows[index]=[x,y,w,h];
    if (mode==='resize' && uniformSlots) applyUniformSize();
    // Move the existing overlay rather than rebuilding the canvas. Rebuilding
    // changes the background-image URL on every pointer event, which causes
    // a visible black flash while a browser repaints the image.
    if (mode==='resize' && uniformSlots) [...canvas.children].forEach((el,i)=>placeSlotElement(el,layoutDraft.windows[i]));
    else placeSlotElement(slotElement,layoutDraft.windows[index]);
  };
  const end=()=>{ if (moved) layoutDraftDirty=true; updateEditorActions(); window.removeEventListener('pointermove',move); window.removeEventListener('pointerup',end); };
  window.addEventListener('pointermove',move); window.addEventListener('pointerup',end);
}
function renderBasePills() {
  const builtins=document.getElementById('eco-builtins'), customs=document.getElementById('eco-customs'), diagnostics=document.getElementById('eco-diagnostics'); builtins.innerHTML=''; customs.innerHTML=''; diagnostics.innerHTML='';
  const add=(base, layout, disabled=false) => {
    const ident=layout.id, label=layout.name;
    const choice=document.createElement('div'); choice.className='layout-choice';
    const pill=document.createElement('button'); pill.type='button'; pill.className='template-pill'; pill.textContent=label.length>50?label.slice(0,50)+'…':label; pill.title=label; pill.disabled=disabled;
    pill.dataset.layoutId=ident; pill.setAttribute('role','radio');
    const selected=!editingLayout && ecoConfig.selected_layout_id===ident; pill.classList.toggle('selected',selected); pill.setAttribute('aria-checked',selected);
    if (!disabled) pill.onclick=()=>selectLayout(ident);
    if (!disabled && ecoConfig.layout_id===ident) {
      const live=document.createElement('span'); live.className='layout-live-dot'; live.title='Currently on display'; live.setAttribute('aria-label','Currently on display'); choice.appendChild(live);
    }
    choice.appendChild(pill);
    if (!disabled) {
      const preview=document.createElement('button'); preview.type='button'; preview.className='layout-icon'; preview.textContent='👁'; preview.title='Preview '+label; preview.setAttribute('aria-label',preview.title);
      preview.onclick=e=>{e.stopPropagation(); previewLayout(layout);}; choice.appendChild(preview);
      if (ident==='viewport-test') {
        const run=document.createElement('button'); run.type='button'; run.className='btn diagnostic-run'; run.textContent='Run on display';
        run.onclick=async e=>{e.stopPropagation(); await sendLayoutToDisplay(ident);}; choice.appendChild(run);
      }
      if (ident.startsWith('custom-')) {
        const edit=document.createElement('button'); edit.type='button'; edit.className='layout-icon'; edit.textContent='✎'; edit.title='Edit '+label; edit.setAttribute('aria-label',edit.title);
        edit.onclick=e=>{e.stopPropagation(); editLayout(layout);}; choice.appendChild(edit);
      }
    }
    base.appendChild(choice);
  };
  const liveFirst=items=>items.slice().sort((a,b)=>(b.id===ecoConfig.layout_id)-(a.id===ecoConfig.layout_id));
  const layouts=ecoConfig.layouts || [];
  const customLayouts=layouts.filter(layout=>layout.base_source!=='builtin');
  layouts.filter(layout=>layout.base_source==='builtin' && layout.id!=='viewport-test').forEach(layout=>add(builtins,layout));
  if (customLayouts.length) liveFirst(customLayouts).forEach(layout=>add(customs,layout));
  else {
    const empty=document.createElement('div'); empty.className='custom-layout-empty';
    empty.append(document.createTextNode('No custom marquees yet.'));
    const button=document.createElement('button'); button.type='button'; button.className='btn primary'; button.textContent='+ Create custom layout';
    button.onclick=createCustomLayout; empty.appendChild(button); customs.appendChild(empty);
  }
  liveFirst(layouts.filter(layout=>layout.id==='viewport-test')).forEach(layout=>add(diagnostics,layout));
}
function createCustomLayout() {
  editingLayout=true; editingLayoutId=null;
  layoutDraft={base:'',background_type:'image',background_color:'#000000',windows:[]};
  uniformSlots=false; uniformLeader=0; layoutDraftDirty=false; renderAll();
}
document.getElementById('create-custom-layout').onclick=createCustomLayout;
async function uploadCustomBase() {
  const file=document.getElementById('custom-file').files[0]; if (!file) { alert('Choose a PNG or JPEG background first.'); return; }
  const button=document.getElementById('custom-upload'); button.disabled=true; button.textContent='Uploading…';
  try {
    const fit=document.getElementById('custom-fit').value;
    const r=await fetch('/base/upload?fit='+fit,{method:'POST',body:file}); if (!r.ok) throw new Error(await r.text());
    const result=await r.json(); layoutDraft.base=result.name; layoutDraft.background_type='image'; layoutDraftDirty=true; renderCustomEditor();
  } catch (e) { alert('Background upload failed: '+e.message); }
  button.disabled=false; button.textContent='Upload image';
}
document.getElementById('custom-upload').onclick=uploadCustomBase;
document.getElementById('custom-solid').onchange=e=>{ layoutDraft.background_type=e.target.checked?'color':'image'; layoutDraftDirty=true; renderCustomEditor(); };
document.getElementById('custom-colour').oninput=e=>{ layoutDraft.background_color=e.target.value; layoutDraftDirty=true; renderCustomEditor(); };
document.getElementById('uniform-slots').onchange=e=>{ uniformSlots=e.target.checked; if (uniformSlots) applyUniformSize(); layoutDraftDirty=true; renderCustomEditor(); };
document.getElementById('uniform-leader').onchange=e=>{ uniformLeader=Number(e.target.value)||0; applyUniformSize(); layoutDraftDirty=true; renderCustomEditor(); };

// Bring-your-own-key image generation. The Pi only receives the resulting
// image through /base/upload; keys remain in this browser (session memory by
// default, localStorage only after the user explicitly opts in).
const AI_SETTINGS_KEY='marqueemark-ai-settings'; let aiSessionKeys={}, aiModelsByProvider={}, aiSelectedModels={};
function readAiSettings() { try { return JSON.parse(localStorage.getItem(AI_SETTINGS_KEY)) || {keys:{},provider:'openai'}; } catch (_) { return {keys:{},provider:'openai'}; } }
function writeAiSettings(settings) { localStorage.setItem(AI_SETTINGS_KEY,JSON.stringify(settings)); }
function aiProvider() { return document.getElementById('ai-provider').value; }
function restoreAiKey() {
  const settings=readAiSettings(), provider=aiProvider(), saved=settings.keys || {};
  document.getElementById('ai-key').value=aiSessionKeys[provider] || saved[provider] || '';
  document.getElementById('ai-remember').checked=!!saved[provider];
  renderAiModels();
}
function rememberAiProvider() {
  const settings=readAiSettings(); settings.provider=aiProvider(); writeAiSettings(settings);
}
function rememberAiKey() {
  const provider=aiProvider(), key=document.getElementById('ai-key').value.trim(), remember=document.getElementById('ai-remember').checked;
  aiSessionKeys[provider]=key;
  const settings=readAiSettings(); settings.keys=settings.keys || {};
  if (remember && key) settings.keys[provider]=key; else delete settings.keys[provider];
  writeAiSettings(settings);
}
function aiError(data, fallback) { return data && data.error && (data.error.message || data.error) || fallback; }
function renderAiModels() {
  const provider=aiProvider(), select=document.getElementById('ai-model'), models=aiModelsByProvider[provider] || [];
  select.innerHTML=''; select.disabled=!models.length;
  if (!models.length) { select.append(new Option('Validate key first','')); return; }
  const wanted=aiSelectedModels[provider] || models[0].value;
  models.forEach(model=>select.append(new Option(model.label,model.value,false,model.value===wanted)));
}
function aiModel() { return document.getElementById('ai-model').value || (aiProvider()==='openai'?'gpt-image-1':'gemini-3.1-flash-image'); }
async function validateOpenAiKey(key) {
  const r=await fetch('https://api.openai.com/v1/models',{headers:{'Authorization':'Bearer '+key}}), data=await r.json();
  if (!r.ok) throw new Error(aiError(data,r.status));
  return (data.data||[]).filter(model=>model.id && model.id.includes('image')).sort((a,b)=>(b.created||0)-(a.created||0)).map(model=>({value:model.id,label:model.id}));
}
async function validateGeminiKey(key) {
  const r=await fetch('https://generativelanguage.googleapis.com/v1beta/models?key='+encodeURIComponent(key)), data=await r.json();
  if (!r.ok) throw new Error(aiError(data,r.status));
  return (data.models||[]).filter(model=>model.name && model.name.includes('image') && !model.name.includes('imagen')).map(model=>{
    const value=model.name.replace(/^models\//,''); return {value,label:model.displayName ? model.displayName+' ('+value+')' : value};
  });
}
async function validateAiKey() {
  const provider=aiProvider(), key=document.getElementById('ai-key').value.trim(), status=document.getElementById('ai-status'), button=document.getElementById('ai-validate');
  if (!key) { status.textContent='Enter your API key first.'; return; }
  rememberAiKey(); button.disabled=true; button.textContent='Validating…'; status.textContent='Checking available image models…';
  try {
    const models=provider==='openai' ? await validateOpenAiKey(key) : await validateGeminiKey(key);
    if (!models.length) throw new Error('This key has no available image-generation models.');
    aiModelsByProvider[provider]=models; aiSelectedModels[provider]=models[0].value; renderAiModels();
    status.textContent='Key validated. Choose a model, then generate your background.';
  } catch (e) { status.textContent='Key validation failed: '+e.message; }
  button.disabled=false; button.textContent='Validate key';
}
function marqueeAiPrompt(request) {
  return 'Create a background-only digital arcade marquee design. The final physical canvas is exactly 1366 by 380 pixels, an ultra-wide 3.595:1 landscape. Compose important artwork safely within this very wide ratio; the app will crop or fit the returned image to 1366 × 380. Leave uncluttered space for mini-marquee game cards which will be placed later. Do not include readable text, game titles, logos, slot frames, UI, controls, or characters that overlap the card areas. High-quality arcade cabinet artwork. Design request: ' + request.trim();
}
async function generateOpenAiImage(key, prompt, model) {
  const r=await fetch('https://api.openai.com/v1/images/generations',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},body:JSON.stringify({model,prompt,size:'1536x1024',quality:'medium',output_format:'png'})});
  const data=await r.json(); if (!r.ok) throw new Error(aiError(data,r.status));
  const image=data.data && data.data[0]; if (!image || !image.b64_json) throw new Error('OpenAI returned no image data.');
  return 'data:image/png;base64,'+image.b64_json;
}
async function fetchWithTimeout(url, options, timeoutMs, timeoutMessage) {
  const controller=new AbortController(), timer=window.setTimeout(()=>controller.abort(),timeoutMs);
  try { return await fetch(url,{...options,signal:controller.signal}); }
  catch (error) {
    if (controller.signal.aborted) throw new Error(timeoutMessage);
    throw error;
  } finally { window.clearTimeout(timer); }
}
async function generateGeminiImage(key, prompt, model) {
  const r=await fetchWithTimeout('https://generativelanguage.googleapis.com/v1beta/interactions',{method:'POST',headers:{'Content-Type':'application/json','x-goog-api-key':key},body:JSON.stringify({model,input:[{type:'text',text:prompt}],response_format:{type:'image',mime_type:'image/jpeg',aspect_ratio:'16:9'}})},180000,'Gemini did not respond within three minutes. Try a faster Gemini image model, then try again.');
  const data=await r.json(); if (!r.ok) throw new Error(aiError(data,r.status));
  const image=data.output_image || (data.steps||[]).flatMap(step=>step.content||[]).find(item=>item.type==='image' && item.data);
  if (!image || !image.data) throw new Error('Gemini returned no image data.');
  return 'data:'+(image.mime_type||'image/png')+';base64,'+image.data;
}
async function generateAiBackground() {
  const provider=aiProvider(), key=document.getElementById('ai-key').value.trim(), request=document.getElementById('ai-prompt').value.trim(), status=document.getElementById('ai-status'), button=document.getElementById('ai-generate'), model=aiModel();
  if (!key) { status.textContent='Enter your '+(provider==='openai'?'OpenAI':'Gemini')+' API key first.'; return; }
  if (!request) { status.textContent='Describe the background you want to create first.'; return; }
  rememberAiKey(); button.disabled=true; button.textContent='Generating…'; status.textContent='Generating in your browser…';
  const slowNotice=window.setTimeout(()=>{ if (button.disabled) status.textContent='Still generating with '+model+'… this can take a couple of minutes.'; },15000);
  try {
    const dataUrl=provider==='openai' ? await generateOpenAiImage(key,marqueeAiPrompt(request),model) : await generateGeminiImage(key,marqueeAiPrompt(request),model);
    status.textContent='Normalising the generated image for the marquee…';
    const blob=await (await fetch(dataUrl)).blob(), fit=document.getElementById('custom-fit').value;
    const r=await fetch('/base/upload?fit='+encodeURIComponent(fit),{method:'POST',body:blob}); if (!r.ok) throw new Error(await r.text());
    const result=await r.json(); layoutDraft.base=result.name; layoutDraft.background_type='image'; layoutDraftDirty=true; renderCustomEditor();
    status.textContent='Generated background is ready in the editor. Position slots, then save the layout.';
  } catch (e) { status.textContent='Generation failed: '+e.message; }
  finally { window.clearTimeout(slowNotice); button.disabled=false; button.textContent='Generate background'; }
}
document.getElementById('ai-provider').onchange=()=>{ rememberAiProvider(); restoreAiKey(); };
document.getElementById('ai-key').oninput=e=>{ aiSessionKeys[aiProvider()]=e.target.value; };
document.getElementById('ai-remember').onchange=rememberAiKey;
document.getElementById('ai-validate').onclick=validateAiKey;
document.getElementById('ai-model').onchange=e=>{ aiSelectedModels[aiProvider()]=e.target.value; };
document.getElementById('ai-generate').onclick=generateAiBackground;
const rememberedAiProvider=readAiSettings().provider;
if (rememberedAiProvider==='openai' || rememberedAiProvider==='gemini') document.getElementById('ai-provider').value=rememberedAiProvider;
restoreAiKey();
async function selectLayout(ident) {
  // A library pill always leaves the editor. Do this before awaiting the
  // server so normal layouts cannot briefly retain the editor's hidden
  // assignment state; the sequence also makes rapid clicks deterministic.
  const request=++layoutSelectionRequest;
  editingLayout=false; editingLayoutId=null; layoutDraftDirty=false;
  if (ecoConfig) ecoConfig.selected_layout_id=ident;
  renderAll();
  const r=await fetch('/electrocoin/layout/select?id='+encodeURIComponent(ident),{method:'POST'});
  if (request!==layoutSelectionRequest) return;
  if (!r.ok) { alert('Could not select that layout.'); return; }
  ecoConfig=await r.json(); editingLayout=false; renderAll();
}
function editLayout(layout) {
  editingLayout=true; editingLayoutId=layout.id;
  layoutDraft={base:layout.base,background_type:layout.background_type||'image',background_color:layout.background_color||'#000000',windows:layout.windows.map(slot=>[...slot])}; uniformSlots=false; uniformLeader=0; layoutDraftDirty=false; renderAll();
}
function closeNameModal() { document.getElementById('layout-name-modal').classList.add('hidden'); }
function openNameModal(mode) {
  if (mode==='create' && !draftIsValid()) { alert('Choose an image or solid colour, and choose 1, 2, 4, or 6 mini-marquees first.'); return; }
  nameModalMode=mode;
  const layout=(ecoConfig.layouts||[]).find(l=>l.id===editingLayoutId);
  document.getElementById('layout-name-title').textContent=mode==='rename'?'Rename layout':'Name this layout';
  const input=document.getElementById('layout-name-input'); input.value=layout?layout.name:'';
  document.getElementById('layout-name-modal').classList.remove('hidden'); setTimeout(()=>input.focus(),0);
}
async function persistLayout(name) {
  const q=new URLSearchParams({name,base:layoutDraft.base,background_type:layoutDraft.background_type,background_color:layoutDraft.background_color,windows:JSON.stringify(layoutDraft.windows)});
  if (editingLayoutId) q.set('id',editingLayoutId);
  const r=await fetch('/electrocoin/layout/save?'+q,{method:'POST'}); if (!r.ok) { alert(await r.text()); return; }
  ecoConfig=await r.json(); editingLayout=false; editingLayoutId=null; layoutDraftDirty=false; renderAll();
}
async function confirmLayoutName() {
  const name=document.getElementById('layout-name-input').value.trim();
  if (!name) { alert('Enter a layout name.'); return; }
  if (nameModalMode==='rename') {
    const r=await fetch('/electrocoin/layout/rename?'+new URLSearchParams({id:editingLayoutId,name}),{method:'POST'});
    if (!r.ok) { alert(await r.text()); return; } ecoConfig=await r.json(); closeNameModal(); renderAll(); return;
  }
  closeNameModal(); await persistLayout(name);
}
function closePreviewModal() { document.getElementById('layout-preview-modal').classList.add('hidden'); }
function previewLayout(layout) {
  document.getElementById('layout-preview-title').textContent=layout.name;
  document.getElementById('layout-preview-hint').textContent=layout.id==='viewport-test'
    ? 'This diagnostic renders coloured bands from pixel rows 340–419. On the physical panel, note the final band visible before the lower bezel to measure the active viewport height.'
    : 'Saved card assignments are shown here. NeoSD Pro shows its current game when available, otherwise a live-marquee placeholder.';
  const canvas=document.getElementById('layout-preview-canvas'); canvas.innerHTML='';
  const image=layout.background_type!=='color', path=layoutBackgroundPath(layout);
  canvas.style.backgroundImage=image?'url('+path+encodeURIComponent(layout.base)+')':'none'; canvas.style.backgroundColor=image?'#050508':(layout.background_color||'#000000');
  const cards=cardsForLayout(layout), liveShort=layout.id===ecoConfig.layout_id ? ecoLiveShort : null;
  if (layout.id==='viewport-test') { document.getElementById('layout-preview-modal').classList.remove('hidden'); return; }
  layout.windows.forEach((slot,index)=>{
    if (appendMiniMarquee(canvas,cards[index],slot,liveShort)) return;
    const guide=document.createElement('div'); guide.className='layout-slot'; guide.textContent='Slot '+(index+1);
    positionMiniMarquee(guide,slot); canvas.appendChild(guide);
  });
  document.getElementById('layout-preview-modal').classList.remove('hidden');
}
async function deleteActiveLayout() {
  const layout=selectedLayout(); if (!layout || layout.id==='electrocoin') return;
  const isLive=layout.id===ecoConfig.layout_id;
  const message=isLive
    ? '“'+layout.name+'” is currently on display. Delete it and switch the cabinet safely back to Electrocoin four-slot?'
    : 'Delete “'+layout.name+'”? This removes its saved background and slot layout, but never your game artwork.';
  if (!confirm(message)) return;
  const q=new URLSearchParams({id:layout.id}); if (isLive) q.set('replace','electrocoin');
  const r=await fetch('/electrocoin/layout/delete?'+q,{method:'POST'}); if (!r.ok) { alert('Could not delete that layout: '+await r.text()); return; }
  ecoConfig=await r.json(); renderAll();
}
document.getElementById('layout-save').onclick=()=>editingLayoutId?persistLayout((ecoConfig.layouts||[]).find(l=>l.id===editingLayoutId).name):openNameModal('create');
document.getElementById('layout-rename').onclick=()=>openNameModal('rename');
document.getElementById('layout-cancel').onclick=()=>{editingLayout=false; editingLayoutId=null; layoutDraftDirty=false; renderAll();};
document.getElementById('layout-delete').onclick=deleteActiveLayout;
document.getElementById('layout-name-confirm').onclick=confirmLayoutName;
document.getElementById('layout-name-cancel').onclick=closeNameModal;
document.getElementById('layout-name-close').onclick=closeNameModal;
document.getElementById('layout-preview-close').onclick=closePreviewModal;
document.getElementById('layout-name-modal').onclick=e=>{if(e.target===e.currentTarget)closeNameModal();};
document.getElementById('layout-preview-modal').onclick=e=>{if(e.target===e.currentTarget)closePreviewModal();};
async function loadEco() {
  const r=await fetch('/electrocoin/config'); if (!r.ok) return;
  ecoConfig=await r.json(); ecoFiles=await (await fetch('/list')).json(); try { ecoTitles=await (await fetch('/game-titles')).json(); } catch (_) {}
  try { ecoLiveShort=((await (await fetch('/current')).json())||{}).short||null; } catch (_) {}
  startEcoLiveUpdates();
  document.getElementById('electrocoin-section').classList.remove('hidden'); renderAll();
}
function renderAll() {
  renderBasePills(); renderCustomEditor(); renderLivePreview();
  const layout=selectedLayout(), isDiagnostic=layout && layout.diagnostic;
  // Only the zero-slot diagnostic and the layout editor suppress this panel.
  // Every selected cabinet/custom layout always has its assignments and send
  // controls available, regardless of what was previously on display.
  const showAssignments=!!layout && !editingLayout && !isDiagnostic;
  const assignments=document.getElementById('eco-assignment-section'); assignments.classList.toggle('hidden',!showAssignments);
  if (!editingLayout) {
    if (isDiagnostic) return;
    document.getElementById('eco-assignment-hint').textContent='Assign marquee art to “'+(layout?layout.name:'this layout')+'”. NeoSD Pro is the special live card.';
    document.getElementById('layout-delete').classList.toggle('hidden',!layout || !layout.id.startsWith('custom-')); renderCards();
    const send=document.getElementById('layout-send'), isLive=layout && layout.id===ecoConfig.layout_id;
    const ultrawideActive=typeof activeDisplayMode==='undefined' || activeDisplayMode==='ultrawide';
    send.disabled=!layout || isLive || !ultrawideActive;
    send.textContent=!ultrawideActive?'Choose Ultrawide mode to send':isLive?'Currently on display':'Send to display';
  }
}
async function saveCardAssignments({feedback=false}={}) {
  const layout=selectedLayout(); if (!layout) return false;
  const button=feedback ? document.getElementById('eco-save') : null;
  const originalLabel=button ? button.textContent : '';
  if (button) { button.disabled=true; button.textContent='Saving…'; button.classList.remove('saved'); }
  const cards=pullCards(), q=new URLSearchParams({layout_id:layout.id});
  cards.forEach((card,i)=>q.set('card'+i,card.source==='neosd'?'__neosd__':card.source==='fixed'?card.art:''));
  try {
    const r=await fetch('/electrocoin/config?'+q,{method:'POST'}); if (!r.ok) throw new Error(await r.text());
    ecoConfig=await r.json();
    // Do not rebuild the card controls here: a native picker may still be
    // open, and saving should acknowledge the action without disrupting it.
    renderLivePreview();
    if (button) {
      button.textContent='✓ Saved'; button.classList.add('saved');
      window.setTimeout(()=>{
        if (!button.isConnected) return;
        button.disabled=false; button.textContent=originalLabel; button.classList.remove('saved');
      },1800);
    }
    return true;
  } catch (error) {
    if (button) { button.disabled=false; button.textContent=originalLabel; }
    alert('Could not save card assignments.'+(error.message ? ' '+error.message : ''));
    return false;
  }
}
document.getElementById('eco-save').onclick=()=>saveCardAssignments({feedback:true});
async function sendLayoutToDisplay(ident) {
  const r=await fetch('/electrocoin/layout/display?id='+encodeURIComponent(ident),{method:'POST'});
  if (!r.ok) { alert('Could not send this layout to the display: '+await r.text()); return; }
  ecoConfig=await r.json(); renderAll();
}
document.getElementById('layout-send').onclick=async()=>{
  const layout=selectedLayout(); if (!layout) return;
  if (!await saveCardAssignments()) return;
  await sendLayoutToDisplay(layout.id);
};
loadEco();

// ------------------------------------------------- mode / power / picker
const sel = document.getElementById('sel');
const selNow = document.getElementById('sel-now');
const pwrNow = document.getElementById('pwr-now');
const shutdownEnabled = document.getElementById('shutdown-enabled');
const shutdownDelay = document.getElementById('shutdown-delay');
const shutdownNow = document.getElementById('shutdown-now');
const advancedSettingsEnabled = document.getElementById('advanced-settings-enabled');
const advancedSettingsContent = document.getElementById('advanced-settings-content');
let isManual = false, activeDisplayMode = 'mini', pendingDisplayMode = 'mini', displayModeConfigured = false, firstSetupPrompted = false, displayModePreview = null, displayModeCountdownTimer = null;
const ADMIN_TAB_KEY='marqueemark-admin-tab';
const savedAdminTab=localStorage.getItem(ADMIN_TAB_KEY);
let hasSavedAdminTab=!!savedAdminTab;

function setAdminTab(tab, remember=true) {
  if (tab !== 'mini' && tab !== 'ultrawide') return;
  document.querySelectorAll('.admin-tab').forEach(panel=>panel.classList.toggle('hidden',panel.id !== 'tab-'+tab));
  document.querySelectorAll('[data-admin-tab]').forEach(button=>{
    const selected=button.dataset.adminTab===tab;
    button.classList.toggle('selected',selected); button.setAttribute('aria-selected',selected);
  });
  if (remember) { localStorage.setItem(ADMIN_TAB_KEY,tab); hasSavedAdminTab=true; }
}
document.querySelectorAll('[data-admin-tab]').forEach(button=>button.onclick=()=>setAdminTab(button.dataset.adminTab));
setAdminTab(savedAdminTab || 'mini', !!savedAdminTab);

function displayModeName(mode) { return mode==='ultrawide'?'Ultrawide Marquee':'Mini Marquee'; }
function displayModeDescription(mode) {
  return mode==='ultrawide'
    ? 'wide layouts with one or more mini-marquee cards'
    : 'original portrait marquee artwork and calibration';
}
function renderDisplayMode() {
  document.querySelectorAll('[data-display-mode]').forEach(button=>{
    const selected=button.dataset.displayMode===pendingDisplayMode;
    button.classList.toggle('selected',selected); button.setAttribute('aria-checked',selected);
  });
  const change=document.getElementById('display-mode-change');
  change.classList.toggle('needs-setup',!displayModeConfigured || !!displayModePreview);
  document.getElementById('display-mode-summary').textContent=displayModePreview ? 'Testing: '+displayModeName(activeDisplayMode) : displayModeConfigured ? displayModeName(activeDisplayMode) : 'Display setup needed';
  document.getElementById('display-mode-action').textContent=displayModePreview ? '· Keep?' : displayModeConfigured ? '· Change' : '· Set up';
  document.getElementById('display-mode-current').textContent=displayModeConfigured
    ? 'Currently driving: '+displayModeName(activeDisplayMode)+'.'
    : 'No display type is configured yet. Saving your choice will immediately activate it.';
  document.getElementById('display-mode-apply').disabled=!pendingDisplayMode || (displayModeConfigured && pendingDisplayMode===activeDisplayMode);
}
function closeDisplayModeWarning() { document.getElementById('display-mode-warning-modal').classList.add('hidden'); }
function closeDisplayModeSetup() { document.getElementById('display-mode-modal').classList.add('hidden'); }
function closeDisplayModeKeep() {
  document.getElementById('display-mode-keep-modal').classList.add('hidden');
  if (displayModeCountdownTimer) { clearInterval(displayModeCountdownTimer); displayModeCountdownTimer=null; }
}
function dismissDisplayModeSetup() {
  closeDisplayModeSetup();
}
function openDisplayModeSetup(firstVisit=false) {
  pendingDisplayMode=activeDisplayMode;
  document.getElementById('display-mode-title').textContent=firstVisit?'Which display do you have?':'Display hardware setup';
  document.getElementById('display-mode-intro').textContent=firstVisit
    ? 'Before MarqueeMark drives the panel, choose the connected display. This setting is saved on this Pi, not in your browser.'
    : 'Choose which connected display MarqueeMark should drive. This setting is saved on the Pi, not in this browser.';
  document.getElementById('display-mode-cancel').textContent=firstVisit?'Set up later':'Cancel';
  document.getElementById('display-mode-modal').classList.remove('hidden'); renderDisplayMode();
}
function targetDisplayMode() { return activeDisplayMode==='ultrawide'?'mini':'ultrawide'; }
function openDisplayModeWarning() {
  if (displayModePreview) { document.getElementById('display-mode-keep-modal').classList.remove('hidden'); return; }
  if (!displayModeConfigured) { openDisplayModeSetup(true); return; }
  const target=targetDisplayMode(), targetName=displayModeName(target);
  document.getElementById('display-mode-warning-title').textContent='Switch to '+targetName+'?';
  document.getElementById('display-mode-warning-copy').textContent='This is rarely needed. Switching takes effect immediately: MarqueeMark will redraw the HDMI output using the '+targetName+' renderer for '+displayModeDescription(target)+'. Only continue after connecting that display. Your saved Mini Marquee and Ultrawide Marquee settings are kept.';
  document.getElementById('display-mode-warning-continue').textContent='Switch to '+targetName;
  document.getElementById('display-mode-warning-modal').classList.remove('hidden');
}
function renderDisplayModeCountdown() {
  if (!displayModePreview) return;
  const seconds=Math.max(0,Math.ceil((displayModePreview.deadline-Date.now())/1000));
  document.getElementById('display-mode-countdown').textContent='Reverting automatically in '+seconds+' second'+(seconds===1?'':'s')+'.';
}
function openDisplayModeKeep(mode, previous, seconds) {
  displayModePreview={mode,previous,deadline:Date.now()+Math.max(1,seconds)*1000};
  document.getElementById('display-mode-keep-title').textContent='Keep '+displayModeName(mode)+'?';
  document.getElementById('display-mode-keep-copy').textContent=mode===previous
    ? 'The HDMI output is already using '+displayModeName(mode)+' as its temporary startup fallback. Keep it to record this as the display connected to this Pi.'
    : 'The HDMI output has changed now and is using '+displayModeName(mode)+' for '+displayModeDescription(mode)+'. If this is not the connected display, it will automatically return to '+displayModeName(previous)+' unless you keep it.';
  document.getElementById('display-mode-keep-modal').classList.remove('hidden');
  if (displayModeCountdownTimer) clearInterval(displayModeCountdownTimer);
  renderDisplayModeCountdown(); displayModeCountdownTimer=setInterval(renderDisplayModeCountdown,250);
  renderDisplayMode();
}
async function tryDisplayMode(mode, button) {
  if (button) button.disabled=true;
  try {
    const response=await fetch('/display/mode/preview?layout='+encodeURIComponent(mode),{method:'POST'});
    if (!response.ok) throw new Error(await response.text());
    const result=await response.json();
    activeDisplayMode=mode; pendingDisplayMode=mode; closeDisplayModeSetup(); closeDisplayModeWarning(); openDisplayModeKeep(mode,result.previous,result.revert_in); if (ecoConfig) renderAll();
  } catch (error) { alert('Could not try display mode: '+error.message); }
  finally { if (button) button.disabled=false; }
}
async function saveInitialDisplayMode(mode, button) {
  if (button) button.disabled=true;
  try {
    const response=await fetch('/display/mode?layout='+encodeURIComponent(mode),{method:'POST'});
    if (!response.ok) throw new Error(await response.text());
    activeDisplayMode=mode; pendingDisplayMode=mode; displayModeConfigured=true; closeDisplayModeSetup(); renderDisplayMode(); if (ecoConfig) renderAll();
  } catch (error) { alert('Could not save display mode: '+error.message); }
  finally { if (button) button.disabled=false; }
}
async function confirmDisplayModePreview() {
  const button=document.getElementById('display-mode-keep-confirm'); button.disabled=true;
  try {
    const response=await fetch('/display/mode/confirm',{method:'POST'}); if (!response.ok) throw new Error(await response.text());
    displayModeConfigured=true; displayModePreview=null; closeDisplayModeKeep(); renderDisplayMode();
  } catch (error) { alert('Could not keep this display type: '+error.message); }
  finally { button.disabled=false; }
}
async function revertDisplayModePreview() {
  const button=document.getElementById('display-mode-keep-revert'); button.disabled=true;
  try {
    const response=await fetch('/display/mode/cancel',{method:'POST'}); if (!response.ok) throw new Error(await response.text());
    const result=await response.json(); activeDisplayMode=result.layout; pendingDisplayMode=result.layout; displayModePreview=null; closeDisplayModeKeep(); renderDisplayMode(); if (ecoConfig) renderAll();
  } catch (error) { alert('Could not revert display type: '+error.message); }
  finally { button.disabled=false; }
}
document.querySelectorAll('[data-display-mode]').forEach(button=>button.onclick=()=>{
  pendingDisplayMode=button.dataset.displayMode; renderDisplayMode();
});
document.getElementById('display-mode-change').onclick=openDisplayModeWarning;
document.getElementById('display-mode-warning-cancel').onclick=closeDisplayModeWarning;
document.getElementById('display-mode-warning-close').onclick=closeDisplayModeWarning;
document.getElementById('display-mode-warning-continue').onclick=()=>tryDisplayMode(targetDisplayMode(),document.getElementById('display-mode-warning-continue'));
document.getElementById('display-mode-close').onclick=dismissDisplayModeSetup;
document.getElementById('display-mode-cancel').onclick=dismissDisplayModeSetup;
document.getElementById('display-mode-warning-modal').onclick=e=>{if(e.target===e.currentTarget)closeDisplayModeWarning();};
document.getElementById('display-mode-modal').onclick=e=>{if(e.target===e.currentTarget)dismissDisplayModeSetup();};
document.getElementById('display-mode-apply').onclick=()=>saveInitialDisplayMode(pendingDisplayMode,document.getElementById('display-mode-apply'));
document.getElementById('display-mode-keep-confirm').onclick=confirmDisplayModePreview;
document.getElementById('display-mode-keep-revert').onclick=revertDisplayModePreview;
document.getElementById('display-mode-keep-close').onclick=revertDisplayModePreview;
document.getElementById('display-mode-keep-modal').onclick=e=>{if(e.target===e.currentTarget)revertDisplayModePreview();};

let selListCache = [];  // last-known art list, so we only rebuild the
                        // <select> when it actually changes

async function refreshMode() {
  let m;
  try { m = await (await fetch('/mode')).json(); } catch (_) { return; }
  const previousDisplayMode=activeDisplayMode;
  isManual = !!m.manual;
  activeDisplayMode=m.layout==='ultrawide'?'ultrawide':'mini';
  displayModeConfigured=!!m.layout_configured;
  if (m.layout_pending) {
    const isNewPreview=!displayModePreview || displayModePreview.mode!==m.layout_pending;
    if (isNewPreview) openDisplayModeKeep(m.layout_pending,activeDisplayMode===m.layout_pending?'mini':'ultrawide',m.layout_revert_in||30);
  } else if (displayModePreview) {
    displayModePreview=null; closeDisplayModeKeep();
  }
  if (!hasSavedAdminTab) setAdminTab(activeDisplayMode, false);
  renderDisplayMode();
  // The five-second status poll must not rebuild the Ultrawide controls: doing
  // so closes a native card-art picker while the person is scrolling it. A
  // redraw is only needed if the renderer itself genuinely changed.
  if (ecoConfig && previousDisplayMode!==activeDisplayMode) renderAll();
  if (!displayModeConfigured && !firstSetupPrompted) {
    firstSetupPrompted=true; openDisplayModeSetup(true);
  }
  document.getElementById('sec-showing').classList.toggle('hidden', !isManual);
  pwrNow.textContent = m.asleep
    ? (m.manual_sleep ? 'asleep (by hand)' : 'asleep')
    : 'awake';
  if (isManual) {
    if (selListCache.length === 0) await refreshSelList();  // once, on load
    refreshSelCurrent();  // cheap: just the "currently showing" text
  }
}

async function refreshSelList() {
  let files;
  try { files = await (await fetch('/list')).json(); } catch (_) { return; }
  const changed = files.length !== selListCache.length ||
    files.some((f, i) => f !== selListCache[i]);
  if (!changed) return;
  selListCache = files;
  // Never rebuild while the person has the dropdown open — that's the
  // flashing/yanked-list bug: a periodic poll shouldn't touch a control
  // someone is actively using.
  if (document.activeElement === sel) return;
  const keep = sel.value;
  sel.innerHTML = '<option value="">(generic marquee)</option>';
  for (const f of files) {
    if (f === 'generic.png') continue;
    const short = f.replace(/\.png$/, '');
    const o = document.createElement('option');
    o.value = short; o.textContent = short;
    sel.appendChild(o);
  }
  sel.value = keep;
}

async function refreshSelCurrent() {
  let cur = null;
  try { cur = (await (await fetch('/selection')).json()).short; } catch (_) { return; }
  selNow.textContent = cur ? ('currently: ' + cur) : 'currently: generic';
  if (document.activeElement !== sel) sel.value = cur || '';
}

document.getElementById('sel-set').onclick = async () => {
  await fetch('/select?name=' + encodeURIComponent(sel.value), {method: 'POST'});
  refreshSelCurrent();
};
document.getElementById('pwr-sleep').onclick = async () => {
  await fetch('/display/sleep', {method: 'POST'});
  setTimeout(refreshMode, 400);
};
document.getElementById('pwr-wake').onclick = async () => {
  await fetch('/display/wake', {method: 'POST'});
  setTimeout(refreshMode, 400);
};

function renderShutdownSetting(data) {
  shutdownEnabled.checked=!!data.enabled;
  if (document.activeElement !== shutdownDelay) shutdownDelay.value=data.delay_seconds;
  shutdownDelay.disabled=!shutdownEnabled.checked;
  const runtime=data.runtime || {};
  if (!data.enabled) shutdownNow.textContent='Off — normal display-sleep behaviour remains unchanged.';
  else if (runtime.state==='waiting-for-link') shutdownNow.textContent='Enabled, waiting to see the NeoSD USB link once before arming.';
  else if (runtime.state==='countdown') shutdownNow.textContent='Cabinet link lost — powering down in '+runtime.remaining_seconds+' second'+(runtime.remaining_seconds===1?'':'s')+'.';
  else if (runtime.state==='powering-off') shutdownNow.textContent='Powering down cleanly now. It is safe to switch off once the Pi has halted.';
  else shutdownNow.textContent='Enabled — the Pi will safely shut down '+data.delay_seconds+' seconds after cabinet power-off is detected.';
}
async function refreshShutdownSetting() {
  try { const r=await fetch('/cabinet-shutdown'); if (r.ok) renderShutdownSetting(await r.json()); } catch (_) {}
}
shutdownEnabled.onchange=()=>{ shutdownDelay.disabled=!shutdownEnabled.checked; };
document.getElementById('shutdown-save').onclick=async()=>{
  const button=document.getElementById('shutdown-save');
  const delay=Math.max(5,Math.min(600,Number.parseInt(shutdownDelay.value,10)||10));
  button.disabled=true;
  try {
    const r=await fetch('/cabinet-shutdown?enabled='+(shutdownEnabled.checked?'1':'0')+'&delay_seconds='+delay,{method:'POST'});
    if (!r.ok) throw new Error(await r.text());
    renderShutdownSetting(await r.json());
  } catch (error) { alert('Could not save shutdown setting: '+error.message); }
  finally { button.disabled=false; }
};
function closeAdvancedSettings() { document.getElementById('advanced-settings-modal').classList.add('hidden'); }
document.getElementById('advanced-settings-open').onclick=async()=>{
  advancedSettingsEnabled.checked=false;
  advancedSettingsContent.disabled=true;
  await refreshShutdownSetting();
  document.getElementById('advanced-settings-modal').classList.remove('hidden');
};
advancedSettingsEnabled.onchange=()=>{ advancedSettingsContent.disabled=!advancedSettingsEnabled.checked; };
document.getElementById('advanced-settings-close').onclick=closeAdvancedSettings;
document.getElementById('advanced-settings-modal').onclick=e=>{if(e.target===e.currentTarget)closeAdvancedSettings();};
document.getElementById('shutdown-preview-display').onclick=async()=>{
  const button=document.getElementById('shutdown-preview-display'); button.disabled=true;
  try {
    const r=await fetch('/shutdown/preview',{method:'POST'});
    if (!r.ok) throw new Error(await r.text());
    shutdownNow.textContent='Preview playing on the marquee. It will restore the current art when the clip ends.';
  } catch (error) { alert('Could not start marquee preview: '+error.message); }
  finally { window.setTimeout(()=>button.disabled=false,700); }
};
document.getElementById('shutdown-preview-browser').onclick=()=>window.open('/shutdown-preview','_blank','noopener');

refreshMode();
setInterval(refreshMode, 5000);

// ----------------------------------------------------- live calibration
const STEPS = [5, 1, 20];
let stepI = 0;
let previewMode = 'pattern';
const calEnterBtn = document.getElementById('cal-enter-btn');
const calPanel = document.getElementById('cal-panel');
const calReadout = document.getElementById('cal-readout');
const calStepBtn = document.getElementById('cal-step');
const calFlipBtn = document.getElementById('cal-flip');
const calDpadBtn = document.getElementById('cal-dpad');
const calPreviewBtn = document.getElementById('cal-preview');

async function calPost(path, params) {
  const qs = params ? ('?' + new URLSearchParams(params).toString()) : '';
  try { await fetch(path + qs, {method: 'POST'}); } catch (_) {}
  refreshCalState();
}

async function refreshCalState() {
  let s;
  try { s = await (await fetch('/calibrate/state')).json(); }
  catch (_) { return; }
  calPanel.classList.toggle('hidden', !s.active);
  calEnterBtn.classList.toggle('hidden', s.active);
  if (s.active) {
    calReadout.textContent = 'x=' + s.x + ' y=' + s.y + ' w=' + s.w +
      ' h=' + s.h + ' tilt=' + s.tilt.toFixed(1) + ' rotate=' + s.rotate +
      ' dpad=' + (s.dpad_offset || 0);
    previewMode = s.preview || 'pattern';
    calPreviewBtn.textContent = 'Preview: ' +
      (previewMode === 'art' ? 'Marquee Art' : 'Test Pattern');
    calDpadBtn.textContent = 'D-pad: ' + (s.dpad_offset || 0) + '\u00b0';
  }
}

calEnterBtn.onclick = () => calPost('/calibrate/enter');
calStepBtn.onclick = () => {
  stepI = (stepI + 1) % STEPS.length;
  calStepBtn.textContent = STEPS[stepI] + 'px';
};
document.querySelectorAll('[data-dir]').forEach(b => {
  b.onclick = () => calPost('/calibrate/move',
    {dir: b.dataset.dir, step: STEPS[stepI]});
});
document.querySelectorAll('[data-size]').forEach(b => {
  b.onclick = () => calPost('/calibrate/size',
    {delta: parseInt(b.dataset.size) * STEPS[stepI]});
});
document.querySelectorAll('[data-tilt]').forEach(b => {
  b.onclick = () => calPost('/calibrate/tilt', {delta: b.dataset.tilt});
});
calFlipBtn.onclick = () => calPost('/calibrate/flip');
calDpadBtn.onclick = () => calPost('/calibrate/dpad');
calPreviewBtn.onclick = () => {
  previewMode = previewMode === 'art' ? 'pattern' : 'art';
  calPost('/calibrate/preview', {mode: previewMode});
};
document.getElementById('cal-save').onclick = () => calPost('/calibrate/save');
document.getElementById('cal-cancel').onclick = () => calPost('/calibrate/exit');

refreshCalState();
setInterval(refreshCalState, 600);
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
    def __init__(self, art_dir, port, display):
        self.art_dir = art_dir
        self.port = port
        self.display = display         # read-only status for /calibrate/state
        self._clients = set()          # set[queue.Queue]
        self._lock = threading.Lock()
        self._display_mode_lock = threading.Lock()
        self._display_mode_pending = None
        self._display_mode_token = 0
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
                elif path == "/shutdown-preview":
                    self._send(200, "text/html; charset=utf-8",
                               SHUTDOWN_PREVIEW_HTML.encode("utf-8"))
                elif path == "/admin":
                    page = ADMIN_HTML.replace("{{VERSION}}", VERSION)
                    self._send(200, "text/html; charset=utf-8",
                               page.encode("utf-8"), {"Cache-Control": "no-store"})
                elif path == "/list":
                    names = None
                    src_url = getattr(server, "art_source", None)
                    if src_url:
                        names = remote_art_list(src_url)
                    if names is None:  # no source, or it's unreachable
                        try:
                            names = sorted(n for n in os.listdir(server.art_dir)
                                           if is_game_art_filename(n))
                        except OSError:
                            names = []
                    self._send(200, "application/json",
                               json.dumps(sorted(names)).encode(), {"Cache-Control": "no-store"})
                elif path == "/game-titles":
                    self._send(200, "application/json",
                               json.dumps(GAME_TITLES).encode())
                elif path == "/current":
                    with server._lock:
                        cur = server._current
                    body = json.dumps(cur or {"short": None}).encode()
                    self._send(200, "application/json", body)
                elif path == "/mode":
                    self._send(200, "application/json", json.dumps({
                        "manual": bool(getattr(server, "manual", False)),
                        "layout": server.display.layout_mode,
                        "layout_configured": saved_display_mode() is not None,
                        **server.display_mode_preview_status(),
                        "asleep": server.display.screen is None,
                        "manual_sleep": server.display.manual_sleep,
                    }).encode())
                elif path == "/cabinet-shutdown":
                    self._send(200, "application/json",
                               json.dumps(cabinet_shutdown_payload()).encode())
                elif path == "/selection":
                    self._send(200, "application/json",
                               json.dumps({"short": read_selection()}).encode())
                elif path == "/electrocoin/config":
                    self._send(200, "application/json", json.dumps(electro_payload(server.display.electro_config)).encode())
                elif path == "/calibrate/state":
                    self._send(200, "application/json",
                               json.dumps(self._cal_state()).encode())
                elif path == "/events":
                    self._serve_events()
                elif path.startswith("/art/"):
                    self._serve_art(path[len("/art/"):])
                elif path.startswith("/base/"):
                    self._serve_base(path[len("/base/"):])
                else:
                    self._send(404, "text/plain", b"not found")

            def _cal_state(self):
                """Current calibration status, live session or last saved."""
                d = server.display
                if d.calibrating and d.cal_work:
                    w = d.cal_work
                    return {"active": True, "x": w["x"], "y": w["y"],
                           "w": w["w"], "h": w["h"], "tilt": w["tilt"],
                           "rotate": w["rotate"], "preview": w["preview"],
                           "dpad_offset": w.get("dpad_offset", 0)}
                return {"active": False, "x": d.rect.x, "y": d.rect.y,
                       "w": d.rect.w, "h": d.rect.h, "tilt": d.tilt,
                       "rotate": d.rotate, "preview": "pattern",
                       "dpad_offset": getattr(d, "dpad_offset", 0)}

            def do_POST(self):
                path, _, query = self.path.partition("?")
                params = {}
                for pair in query.split("&"):
                    if "=" in pair:
                        k, _, v = pair.partition("=")
                        from urllib.parse import unquote_plus
                        params[k] = unquote_plus(v)
                if path == "/upload":
                    self._handle_upload(params.get("name", ""))
                elif path == "/delete":
                    self._handle_delete(params.get("name", ""))
                elif path == "/select":
                    self._handle_select(params.get("name", ""))
                elif path == "/base/upload":
                    self._handle_base_upload(params.get("fit", "cover"))
                elif path == "/electrocoin/layout/save":
                    self._save_electro_layout(params)
                elif path == "/electrocoin/layout/delete":
                    self._delete_electro_layout(params.get("id", ""), params.get("replace", ""))
                elif path == "/electrocoin/layout/rename":
                    self._rename_electro_layout(params.get("id", ""), params.get("name", ""))
                elif path == "/electrocoin/layout/select":
                    self._select_electro_layout(params.get("id", ""))
                elif path == "/electrocoin/layout/display":
                    self._display_electro_layout(params.get("id", ""))
                elif path == "/electrocoin/config":
                    if not isinstance(server.display.electro_config, dict):
                        self._send(404, "text/plain", b"Electrocoin mode disabled")
                    else:
                        cfg = electro_config(server.display.electro_config)
                        target = params.get("layout_id", cfg["selected_layout_id"])
                        layouts = load_custom_layouts(); layout = find_layout(target, layouts)
                        if not layout:
                            self._send(404, "text/plain", b"layout not found")
                            return
                        cards = cfg.get("assignments", {}).get(target, [])
                        cfg["assignments"][target] = _cards(cards, len(layout["windows"]))
                        target_cards = cfg["assignments"][target]
                        neosd_seen = False
                        for i, card in enumerate(target_cards):
                            value = params.get("card" + str(i))
                            if value is not None:
                                source = "neosd" if value == "__neosd__" else "fixed" if _art_stem(value) else "blank"
                                card["art"] = _art_stem(value) if source == "fixed" else ""
                            else:  # accepts the older two-control admin page too
                                source = params.get("source" + str(i), card["source"])
                                source = source if source in ("fixed", "neosd", "blank") else "blank"
                                if "art" + str(i) in params: card["art"] = _art_stem(params["art" + str(i)])
                            if source == "neosd": source = "blank" if neosd_seen else "neosd"; neosd_seen = True
                            card["source"] = source
                        cfg["assignments"][target] = [dict(card) for card in target_cards]
                        if target == cfg["layout_id"]:
                            cfg["cards"] = [dict(card) for card in target_cards]
                        cfg = save_electrocoin_config(cfg)
                        # Keep the server's configuration in sync even when
                        # editing an inactive layout. Only the queue changes
                        # the physical display, but subsequent Admin reads
                        # must see the newly saved assignment immediately.
                        server.display.electro_config = cfg
                        if target == cfg["layout_id"]: CAL_QUEUE.put(("electro_config", cfg))
                        self._send(200, "application/json", json.dumps(electro_payload(cfg)).encode())
                elif path == "/display/sleep":
                    DISPLAY_QUEUE.put(("sleep",))
                    self._send(200, "text/plain", b"ok")
                elif path == "/display/wake":
                    DISPLAY_QUEUE.put(("wake",))
                    self._send(200, "text/plain", b"ok")
                elif path == "/shutdown/preview":
                    DISPLAY_QUEUE.put(("shutdown_preview",))
                    self._send(200, "text/plain", b"ok")
                elif path == "/cabinet-shutdown":
                    try:
                        enabled = params.get("enabled", "") in ("1", "true", "yes", "on")
                        delay = int(params.get("delay_seconds", ""))
                    except ValueError:
                        self._send(400, "text/plain", b"delay_seconds must be a whole number")
                        return
                    if not CABINET_SHUTDOWN_MIN_SECONDS <= delay <= CABINET_SHUTDOWN_MAX_SECONDS:
                        self._send(400, "text/plain", ("delay_seconds must be between %d and %d" %
                                   (CABINET_SHUTDOWN_MIN_SECONDS, CABINET_SHUTDOWN_MAX_SECONDS)).encode())
                        return
                    try:
                        config = save_cabinet_shutdown_config({"enabled": enabled,
                                                               "delay_seconds": delay})
                    except OSError:
                        self._send(500, "text/plain", b"could not save shutdown setting")
                        return
                    payload = cabinet_shutdown_payload()
                    payload.update(config)
                    self._send(200, "application/json", json.dumps(payload).encode())
                elif path == "/display/mode":
                    mode = params.get("layout", "")
                    if mode not in DISPLAY_MODES:
                        self._send(400, "text/plain", b"layout must be mini or ultrawide")
                        return
                    try:
                        save_display_mode(mode)
                    except OSError:
                        self._send(500, "text/plain", b"could not save display mode")
                        return
                    CAL_QUEUE.put(("display_mode", mode))
                    self._send(200, "application/json", json.dumps({"layout": mode}).encode())
                elif path == "/display/mode/preview":
                    mode = params.get("layout", "")
                    if mode not in DISPLAY_MODES:
                        self._send(400, "text/plain", b"layout must be mini or ultrawide")
                        return
                    preview = server.preview_display_mode(mode)
                    if preview is None:
                        self._send(409, "text/plain", b"that display type is already active")
                        return
                    self._send(200, "application/json", json.dumps(preview).encode())
                elif path == "/display/mode/confirm":
                    confirmed = server.confirm_display_mode_preview()
                    if confirmed is None:
                        self._send(409, "text/plain", b"no display change is awaiting confirmation")
                        return
                    self._send(200, "application/json", json.dumps(confirmed).encode())
                elif path == "/display/mode/cancel":
                    reverted = server.cancel_display_mode_preview()
                    if reverted is None:
                        self._send(409, "text/plain", b"no display change is awaiting confirmation")
                        return
                    self._send(200, "application/json", json.dumps(reverted).encode())
                elif path.startswith("/calibrate/"):
                    self._handle_calibrate(path[len("/calibrate/"):], params)
                else:
                    self._send(404, "text/plain", b"not found")

            def _handle_select(self, raw):
                # Empty is valid: show the generic marquee.
                if raw == "":
                    write_selection(None)
                    self._send(200, "text/plain", b"cleared")
                    return
                safe = _safe_art_name(raw + ".png")
                if not safe:
                    self._send(400, "text/plain", b"bad name")
                    return
                write_selection(safe[:-4])
                self._send(200, "text/plain", safe.encode())

            def _save_electro_layout(self, params):
                if not isinstance(server.display.electro_config, dict):
                    self._send(404, "text/plain", b"Ultrawide configuration unavailable")
                    return
                name, base = _layout_name(params.get("name")), _safe_base_name(params.get("base"))
                background_type = params.get("background_type") if params.get("background_type") in ("image", "color") else "image"
                background_color = _hex_colour(params.get("background_color")) or "#000000"
                if background_type == "color": base = ""
                try: windows = json.loads(params.get("windows", ""))
                except ValueError: windows = None
                parsed = [_slot_rect(slot) for slot in windows] if isinstance(windows, list) and len(windows) in ELECTROCOIN_SLOT_COUNTS else []
                if (not name or not parsed or not all(parsed) or
                    (background_type == "image" and (not base or not os.path.isfile(os.path.join(BASE_DIR, base))))):
                    self._send(400, "text/plain", b"A name, image or colour background, and 1, 2, 4, or 6 slots are required")
                    return
                layouts = load_custom_layouts(); ident = _safe_layout_id(params.get("id", ""))
                existing = next((i for i, layout in enumerate(layouts) if layout["id"] == ident), None)
                old_base = layouts[existing]["base"] if existing is not None else None
                if existing is None:
                    ident = "custom-%d" % int(time.time() * 1000)
                    layouts.append({"id": ident, "name": name, "base": base, "background_type": background_type,
                                    "background_color": background_color, "windows": parsed,
                                    "canvas_height": ELECTROCOIN_CANVAS_HEIGHT})
                else:
                    layouts[existing] = {"id": ident, "name": name, "base": base, "background_type": background_type,
                                         "background_color": background_color, "windows": parsed,
                                         "canvas_height": ELECTROCOIN_CANVAS_HEIGHT}
                save_custom_layouts(layouts)
                cfg = electro_config(server.display.electro_config)
                if existing is None:
                    cfg["assignments"][ident] = _cards([], len(parsed))
                else:
                    cfg["assignments"][ident] = _cards(cfg.get("assignments", {}).get(ident, []), len(parsed))
                cfg["selected_layout_id"] = ident
                cfg = save_electrocoin_config(cfg)
                server.display.electro_config = cfg
                if old_base and old_base != base and not any(layout["base"] == old_base for layout in layouts):
                    try: os.remove(os.path.join(BASE_DIR, old_base))
                    except OSError: pass
                self._send(200, "application/json", json.dumps(electro_payload(cfg)).encode())

            def _select_electro_layout(self, ident):
                if not isinstance(server.display.electro_config, dict):
                    self._send(404, "text/plain", b"Ultrawide configuration unavailable")
                    return
                layouts = load_custom_layouts(); layout = find_layout(ident, layouts)
                if not layout:
                    self._send(404, "text/plain", b"layout not found")
                    return
                cfg = electro_config(server.display.electro_config); cfg["selected_layout_id"] = layout["id"]
                cfg = save_electrocoin_config(cfg); server.display.electro_config = cfg
                self._send(200, "application/json", json.dumps(electro_payload(cfg)).encode())

            def _display_electro_layout(self, ident):
                if not isinstance(server.display.electro_config, dict):
                    self._send(404, "text/plain", b"Ultrawide configuration unavailable")
                    return
                layouts = load_custom_layouts(); layout = find_layout(ident, layouts)
                if not layout:
                    self._send(404, "text/plain", b"layout not found")
                    return
                cfg = activate_layout(electro_config(server.display.electro_config), layout)
                cfg["selected_layout_id"] = layout["id"]
                cfg = save_electrocoin_config(cfg); server.display.electro_config = cfg; CAL_QUEUE.put(("electro_config", cfg))
                self._send(200, "application/json", json.dumps(electro_payload(cfg)).encode())

            def _delete_electro_layout(self, ident, replace):
                ident = _safe_layout_id(ident)
                if not isinstance(server.display.electro_config, dict) or not ident:
                    self._send(400, "text/plain", b"bad layout")
                    return
                layouts = load_custom_layouts(); doomed = next((layout for layout in layouts if layout["id"] == ident), None)
                if not doomed:
                    self._send(404, "text/plain", b"layout not found")
                    return
                if server.display.electro_config["layout_id"] == ident and replace != "electrocoin":
                    self._send(409, "text/plain", b"layout is currently on display")
                    return
                layouts = [layout for layout in layouts if layout["id"] != ident]; save_custom_layouts(layouts)
                cfg = electro_config(server.display.electro_config); cfg.get("assignments", {}).pop(ident, None)
                was_displayed = cfg["layout_id"] == ident
                if was_displayed: cfg = activate_layout(cfg, builtin_layout())
                if cfg["selected_layout_id"] == ident: cfg["selected_layout_id"] = "electrocoin"
                cfg = save_electrocoin_config(cfg)
                server.display.electro_config = cfg
                if was_displayed: CAL_QUEUE.put(("electro_config", cfg))
                if not any(layout["base"] == doomed["base"] for layout in layouts):
                    try: os.remove(os.path.join(BASE_DIR, doomed["base"]))
                    except OSError: pass
                self._send(200, "application/json", json.dumps(electro_payload(cfg)).encode())

            def _rename_electro_layout(self, ident, name):
                ident, name = _safe_layout_id(ident), _layout_name(name)
                if not isinstance(server.display.electro_config, dict) or not ident or not name:
                    self._send(400, "text/plain", b"bad layout name")
                    return
                layouts = load_custom_layouts(); layout = next((item for item in layouts if item["id"] == ident), None)
                if not layout:
                    self._send(404, "text/plain", b"layout not found")
                    return
                layout["name"] = name; save_custom_layouts(layouts)
                self._send(200, "application/json", json.dumps(electro_payload(server.display.electro_config)).encode())

            def _handle_calibrate(self, action, params):
                # All of these just enqueue a command for the main thread
                # to apply (SDL/KMS rendering must happen off this thread).
                try:
                    if action == "enter":
                        CAL_QUEUE.put(("enter",))
                    elif action == "exit":
                        CAL_QUEUE.put(("exit",))
                    elif action == "save":
                        CAL_QUEUE.put(("save",))
                    elif action == "move":
                        CAL_QUEUE.put(("move", params.get("dir", ""),
                                       int(params.get("step", "5"))))
                    elif action == "size":
                        CAL_QUEUE.put(("size", int(params.get("delta", "0"))))
                    elif action == "tilt":
                        CAL_QUEUE.put(("tilt", float(params.get("delta", "0"))))
                    elif action == "flip":
                        CAL_QUEUE.put(("flip",))
                    elif action == "dpad":
                        CAL_QUEUE.put(("dpad",))
                    elif action == "preview":
                        CAL_QUEUE.put(("preview", params.get("mode", "pattern")))
                    else:
                        self._send(404, "text/plain", b"not found")
                        return
                    self._send(200, "text/plain", b"ok")
                except (ValueError, TypeError):
                    self._send(400, "text/plain", b"bad parameters")

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

            def _handle_base_upload(self, fit):
                """Normalize a user background to the physical marquee canvas."""
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_UPLOAD:
                    self._send(413, "text/plain", b"file too large")
                    return
                try:
                    from PIL import Image, ImageOps, UnidentifiedImageError
                    data = self.rfile.read(length)
                    with Image.open(io.BytesIO(data)) as raw:
                        raw.load()
                        image = raw.convert("RGB")
                    size = ELECTROCOIN_BASE_SIZE
                    if fit == "contain":
                        image.thumbnail(size, Image.Resampling.LANCZOS)
                        canvas = Image.new("RGB", size, (0, 0, 0))
                        canvas.paste(image, ((size[0]-image.width)//2, (size[1]-image.height)//2))
                    else:
                        canvas = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
                    os.makedirs(BASE_DIR, exist_ok=True)
                    name = "custom-%d.png" % int(time.time() * 1000)
                    canvas.save(os.path.join(BASE_DIR, name), "PNG", optimize=True)
                    self._send(200, "application/json", json.dumps({"name": name}).encode())
                except (OSError, ValueError, UnidentifiedImageError):
                    self._send(400, "text/plain", b"could not read that image")

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

            def _serve_base(self, name):
                name = _safe_base_name(name)
                candidate = os.path.join(BASE_DIR, name) if name else ""
                if not candidate or not os.path.isfile(candidate):
                    self._send(404, "text/plain", b"no base")
                    return
                try:
                    with open(candidate, "rb") as f: data = f.read()
                    self._send(200, "image/png", data, {"Cache-Control": "no-cache"})
                except OSError:
                    self._send(404, "text/plain", b"no base")

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

    def display_mode_preview_status(self):
        """Status safe to expose to Admin while a temporary switch is live."""
        with self._display_mode_lock:
            pending = self._display_mode_pending
            if not pending:
                return {"layout_pending": None, "layout_revert_in": 0}
            remaining = max(0, int(pending["deadline"] - time.monotonic() + .999))
            return {"layout_pending": pending["mode"],
                    "layout_revert_in": remaining}

    def preview_display_mode(self, mode):
        """Try a renderer for 30 seconds; the Pi reverts it unless confirmed."""
        with self._display_mode_lock:
            previous = self.display.layout_mode
            # First-run setup may confirm the renderer chosen as the startup
            # fallback. It still needs a timed confirmation so the Pi records
            # that deliberate choice, even though no redraw is necessary.
            if mode == previous and saved_display_mode() is not None:
                return None
            old = self._display_mode_pending
            if old:
                old["timer"].cancel()
            self._display_mode_token += 1
            token = self._display_mode_token
            deadline = time.monotonic() + 30
            timer = threading.Timer(30, self._auto_revert_display_mode, args=(token,))
            timer.daemon = True
            self._display_mode_pending = {"mode": mode, "previous": previous,
                                          "deadline": deadline, "timer": timer,
                                          "token": token}
            timer.start()
        CAL_QUEUE.put(("display_mode", mode))
        return {"layout": mode, "previous": previous, "revert_in": 30}

    def _auto_revert_display_mode(self, token):
        with self._display_mode_lock:
            pending = self._display_mode_pending
            if not pending or pending["token"] != token:
                return
            self._display_mode_pending = None
        CAL_QUEUE.put(("display_mode", pending["previous"]))
        print("[MarqueeMark] display type change timed out; restored %s" % pending["previous"])

    def confirm_display_mode_preview(self):
        with self._display_mode_lock:
            pending = self._display_mode_pending
            if not pending:
                return None
            try:
                save_display_mode(pending["mode"])
            except OSError:
                return None
            pending["timer"].cancel()
            self._display_mode_pending = None
        return {"layout": pending["mode"], "confirmed": True}

    def cancel_display_mode_preview(self):
        with self._display_mode_lock:
            pending = self._display_mode_pending
            if not pending:
                return None
            pending["timer"].cancel()
            self._display_mode_pending = None
        CAL_QUEUE.put(("display_mode", pending["previous"]))
        return {"layout": pending["previous"], "reverted": True}

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
    def __init__(self, art_dir, rotate=0, electrocoin=False, layout_mode=None):
        pygame.init()
        # ``electrocoin`` is retained internally for compatibility with the
        # first wide-panel implementation. The public name is Ultrawide.
        self.layout_mode = layout_mode if layout_mode in DISPLAY_MODES else ("ultrawide" if electrocoin else "mini")
        self.electrocoin = self.layout_mode == "ultrawide"
        self.headless = False
        # A panel can take several seconds after power-on before KMS will
        # accept a fullscreen surface.  Keep the web server alive in that
        # case, then periodically try again from the main pygame thread.
        self._headless_retry_at = 0.0
        try:
            pygame.mouse.set_visible(False)
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        except pygame.error as e:
            # The Admin service is useful with the cabinet powered down or
            # HDMI unplugged. Keep it alive with an off-screen surface; a
            # later service restart after reconnecting HDMI restores output.
            self.headless = True
            self.screen = pygame.Surface(ELECTROCOIN_BASE_SIZE if self.electrocoin else (1024, 768))
            self._headless_retry_at = time.monotonic() + 2.0
            print("[MarqueeMark] no HDMI display (%s); running headless" % e)
        phys = self.screen.get_size()

        self.phys = phys
        cal = None if self.electrocoin else load_calibration()
        rect_l, tilt, saved_rotate, saved_dpad = cal if cal else (None, 0.0, None, 0)
        # A rotate saved via the web "Flip" button overrides the --rotate
        # launch flag, so flipping never requires editing the systemd
        # unit again once it's been set once.
        self.rotate = 0 if self.electrocoin else (saved_rotate if saved_rotate is not None else rotate) % 360
        # Which physical edge this panel's ribbon exits decides which way
        # the D-pad needs correcting — an installation detail, not
        # something derivable from the rotate angle. 0 = no correction
        # until the admin page's "D-pad" button has been used once.
        self.dpad_offset = saved_dpad

        # Logical canvas: what we compose art onto. For 90/270 the canvas
        # is the physical screen turned on its side. (90 and 270 share
        # the same logical size - they differ only in final rotation.)
        if self.rotate in (90, 270) and not self.electrocoin:
            self.size = (phys[1], phys[0])
        else:
            self.size = phys

        self.art_dir = art_dir
        # Generated quality variants deliberately live outside art/: that
        # folder can be shared with other frontends (such as Gamesboro) and
        # must contain only their original game artwork.
        self.card_cache_dir = CARD_CACHE_DIR
        self.current = None
        self._shutdown_preview = None
        self.last_game = None
        self.tilt = tilt
        self.rect = pygame.Rect(*rect_l) if rect_l else \
            pygame.Rect(0, 0, self.size[0], self.size[1])
        if cal:
            print("[MarqueeMark v%s] calibration: rect=%s tilt=%.2f rotate=%d"
                  % (VERSION, rect_l, tilt, self.rotate))

        # Live web-calibration session state (see process_calibration_queue).
        self.calibrating = False
        self.cal_work = None
        self._cal_sample = None  # lazily-loaded generic.png for art preview
        # Set when the user sleeps the panel from the admin page. While
        # true, the automatic (cab-power) wake path leaves the panel
        # alone — only an explicit Wake clears it.
        self.manual_sleep = False
        self.wake_count = 0  # bumped on every wake() so callers can force
                             # a redraw even when the selection is unchanged
        self.restore_callback = None  # set by main() — see _restore_last_display
        # Keep Ultrawide layouts available in Admin even while the active
        # physical display is the Mini Marquee.
        self.electro_config = load_electrocoin_config()
        self.electro_neosd = None

        self.blank()

    def set_layout_mode(self, mode):
        """Switch the live renderer between the portrait and Ultrawide UI.

        The browser's display-type selector persists this choice.  Each mode
        keeps its own calibration/layout data, so changing mode does not erase
        either setup.
        """
        if mode not in DISPLAY_MODES:
            return False
        # A display can retain an old frame after a previous mode change
        # (for example if HDMI woke slowly). Re-applying the already-saved
        # mode must redraw it too, otherwise Admin can truthfully report the
        # requested renderer while the panel still shows the old one.
        same_mode = mode == self.layout_mode
        self.calibrating = False
        self.cal_work = None
        self.layout_mode = mode
        self.electrocoin = mode == "ultrawide"
        if self.electrocoin:
            self.rotate = 0
            self.dpad_offset = 0
            self.tilt = 0.0
            self.size = self.phys
            self.rect = pygame.Rect(0, 0, self.size[0], self.size[1])
            self.electro_config = load_electrocoin_config()
            self.electro_neosd = self.last_game
        else:
            cal = load_calibration()
            rect_l, self.tilt, saved_rotate, self.dpad_offset = cal if cal else (None, 0.0, None, 0)
            self.rotate = (saved_rotate if saved_rotate is not None else 90) % 360
            self.size = (self.phys[1], self.phys[0]) if self.rotate in (90, 270) else self.phys
            self.rect = pygame.Rect(*rect_l) if rect_l else pygame.Rect(0, 0, self.size[0], self.size[1])
            # Preserve the independently saved Ultrawide library while Mini
            # Marquee is active; it is only rendered when switched back.
            self.electro_neosd = None
        self.current = None
        self._redraw_current()
        print("[MarqueeMark] %s display mode: %s" %
              ("reasserted" if same_mode else "active", mode))
        return True

    def _redraw_current(self):
        """Restore the intended content after a mode or HDMI change.

        State changes are still accepted while running headless, but image
        conversion is deferred until SDL has acquired a real display.
        """
        if self.headless or self.screen is None:
            return
        if self.restore_callback:
            self.restore_callback()
        elif self.last_game:
            self.show_game(self.last_game)
        else:
            self.show_idle()

    def _place(self, canvas, card, rect=None, tilt=None):
        """Put a window-sized card onto the canvas, tilt-corrected.

        rect/tilt default to the saved calibration; a live calibration
        session passes its own working values instead."""
        rect = self.rect if rect is None else rect
        tilt = self.tilt if tilt is None else tilt
        if tilt:
            card = pygame.transform.rotozoom(card, tilt, 1.0)
        canvas.blit(card, card.get_rect(center=rect.center))

    def _present(self, surf, rotate=None):
        """Rotate the composed logical canvas onto the physical screen.

        rotate defaults to the saved orientation; a live calibration
        session can preview a different one before it's saved."""
        rot = self.rotate if rotate is None else rotate
        if rot:
            surf = pygame.transform.rotate(surf, rot)
        self.screen.blit(surf, (0, 0))
        if not self.headless: pygame.display.flip()

    def _fit(self, img):
        """Fill the calibrated rectangle exactly — the rect IS the window."""
        surf = pygame.Surface(self.size)
        surf.fill(BG)
        img = pygame.transform.smoothscale(img, (self.rect.w, self.rect.h))
        self._place(surf, img)
        return surf

    def _electro_art(self, stem, target_size=None):
        """Load original card art, preferring an exact-size quality cache.

        A cached PNG is optional and lets especially text-heavy mini marquees
        be downsampled offline with a higher-quality filter than the runtime
        scaler.  The original art always remains the fallback.
        """
        if not stem:
            return None
        paths = []
        if target_size:
            paths.append(os.path.join(self.card_cache_dir,
                                      "%s--%dx%d.png" % (stem, target_size[0], target_size[1])))
        paths.append(os.path.join(self.art_dir, stem + ".png"))
        for path in paths:
            try:
                if os.path.isfile(path):
                    return pygame.image.load(path).convert()
            except (pygame.error, OSError):
                pass
        return None

    def _electro_base(self):
        cfg = self.electro_config
        if cfg.get("background_type") == "color": return None
        directory = BASE_DIR if cfg.get("base_source") == "custom" else self.art_dir
        try: return pygame.image.load(os.path.join(directory, cfg["base"])).convert()
        except (pygame.error, OSError): return None

    def _electro_surface(self):
        surf = pygame.Surface(self.phys); surf.fill(BG)
        diagnostic = BUILTIN_LAYOUTS.get(self.electro_config.get("layout_id"), {})
        source_height = diagnostic.get("viewport_height", ELECTROCOIN_VIEWPORT_HEIGHT)
        vp = pygame.Rect(0, 0, self.phys[0], min(source_height, self.phys[1]))
        if self.electro_config.get("background_type") == "color": surf.fill(_colour_rgb(self.electro_config.get("background_color")), vp)
        base = self._electro_base()
        if base: surf.blit(pygame.transform.smoothscale(base, vp.size), vp)
        sx, sy = vp.w / ELECTROCOIN_CANVAS_WIDTH, vp.h / source_height
        for r, card in zip(self.electro_config["windows"], self.electro_config["cards"]):
            target = pygame.Rect(round(r[0]*sx), round(r[1]*sy), round(r[2]*sx), round(r[3]*sy))
            stem = self.electro_neosd["short"] if card["source"] == "neosd" and self.electro_neosd else card["art"] if card["source"] == "fixed" else ""
            art = self._electro_art(stem, target.size)
            if art:
                # Exact-size caches are already quality-downsampled offline.
                # Other art retains the normal runtime smooth scaling.
                card = art if art.get_size() == target.size else pygame.transform.smoothscale(art, target.size)
                surf.blit(card, target)
        return surf

    def _show_electrocoin(self):
        if self.headless or self.screen is None:
            return
        # A fresh Ultrawide setup deliberately has no assumed cabinet art.
        # Keep the panel dark until the owner explicitly sends a layout.
        if not self.electro_config.get("layout_sent", True):
            self.blank()
            return
        if not self.calibrating: self._fade_to(self._electro_surface())

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
        if self.calibrating:
            return  # a live calibration session owns the screen
        self.last_game = game
        if self.electrocoin:
            self.electro_neosd = game
        if self.headless or self.screen is None:
            return
        if self.electrocoin:
            self._show_electrocoin(); return
        path = os.path.join(self.art_dir, "%s.png" % game["short"])
        if os.path.exists(path):
            surf = self._fit(pygame.image.load(path).convert())
        else:
            # No art for this game: fall back to the generic marquee,
            # the same image the overlay and idle state use. Only if
            # that is missing too do we draw a text card.
            fallback = os.path.join(self.art_dir, GENERIC + ".png")
            if os.path.exists(fallback):
                surf = self._fit(pygame.image.load(fallback).convert())
            else:
                surf = self._text_card(game)
        self._fade_to(surf)

    def blank(self):
        if self.calibrating:
            return
        surf = pygame.Surface(self.size)
        surf.fill(BG)
        self._present(surf)
        self.current = surf

    def show_idle(self):
        """Generic marquee for when no game can be identified."""
        if self.calibrating:
            return
        self.last_game = None
        if self.electrocoin:
            self.electro_neosd = None
        if self.headless or self.screen is None:
            return
        if self.electrocoin:
            self._show_electrocoin(); return
        path = os.path.join(self.art_dir, GENERIC + ".png")
        if os.path.exists(path):
            self._fade_to(self._fit(pygame.image.load(path).convert()))
        else:
            self.blank()

    def show_shutdown_countdown(self, remaining, total, elapsed=0):
        """Render a native reverse Neo Geo boot sequence during power-off.

        This deliberately contains no video playback or decoded frames.  It
        keeps the animation responsive on the Pi while matching the familiar
        boot progression in reverse: finished boot screen, logo collapse and
        mirror, GAME OVER, then a clean black panel before power-off.
        """
        if self.calibrating or self.screen is None:
            return
        # The Neo Geo sequence is artwork for the ultrawide cabinet display.
        # A mini marquee still shuts down safely, but should simply blank
        # rather than receive wide-panel art it cannot display correctly.
        if not self.electrocoin:
            self.blank()
            return
        surf = pygame.Surface(self.size); surf.fill(BG)
        w, h = self.size
        # The wide marquee only uses its visible 380px viewport; leave any
        # unused lower framebuffer area black as the regular layouts do.
        panel = pygame.Rect(0, 0, w, min(ELECTROCOIN_VIEWPORT_HEIGHT, h))
        stage = pygame.Surface(panel.size); stage.fill((0, 0, 0))
        stage_w, stage_h = stage.get_size()
        boot_hold = min(0.90, total * .18)
        flip_end = boot_hold + min(1.20, total * .24)
        game_over_at = max(flip_end, total - 2.4)
        blackout_at = max(game_over_at + .9, total - .55)

        logo_font = pygame.font.SysFont("Times New Roman", max(28, int(stage_h * .25)), bold=True)
        sub_font = pygame.font.SysFont(None, max(16, int(stage_h * .085)), bold=True)
        snk_font = pygame.font.SysFont(None, max(18, int(stage_h * .09)), bold=True)
        logo_white = logo_font.render("NEO·GEO", True, (242, 242, 242))
        logo_black = logo_font.render("NEO·GEO", True, (8, 8, 8))

        if elapsed < boot_hold:
            # The completed classic boot screen.
            stage.blit(logo_white, logo_white.get_rect(center=(stage_w // 2, int(stage_h * .38))))
            specs = sub_font.render("MAX 330 MEGA", True, (236, 236, 236))
            progear = sub_font.render("PRO-GEAR SPEC", True, (236, 236, 236))
            snk = snk_font.render("SNK", True, (236, 236, 236))
            stage.blit(specs, specs.get_rect(center=(stage_w // 2, int(stage_h * .56))))
            stage.blit(progear, progear.get_rect(center=(stage_w // 2, int(stage_h * .65))))
            stage.blit(snk, snk.get_rect(center=(stage_w // 2, int(stage_h * .79))))
        elif elapsed < flip_end:
            # Collapse the finished logo, then expand its mirrored inverse.
            progress = (elapsed - boot_hold) / max(.01, flip_end - boot_hold)
            if progress < .5:
                scale = max(.02, 1.0 - progress * 2.0)
                shrinking = pygame.transform.smoothscale(logo_white,
                    (max(1, round(logo_white.get_width() * scale)), logo_white.get_height()))
                stage.blit(shrinking, shrinking.get_rect(center=(stage_w // 2, stage_h // 2)))
            else:
                stage.fill((248, 248, 248))
                scale = max(.02, (progress - .5) * 2.0)
                mirrored = pygame.transform.flip(logo_black, True, False)
                expanding = pygame.transform.smoothscale(mirrored,
                    (max(1, round(mirrored.get_width() * scale)), mirrored.get_height()))
                stage.blit(expanding, expanding.get_rect(center=(stage_w // 2, stage_h // 2)))
        elif elapsed < game_over_at:
            stage.fill((248, 248, 248))
            mirrored = pygame.transform.flip(logo_black, True, False)
            stage.blit(mirrored, mirrored.get_rect(center=(stage_w // 2, stage_h // 2)))
        elif elapsed < blackout_at:
            game_font = pygame.font.SysFont(None, max(28, int(stage_h * .18)), bold=True)
            game_over = game_font.render("GAME OVER", True, (242, 242, 242))
            detail = sub_font.render("CABINET POWER OFF", True, (174, 174, 174))
            stage.blit(game_over, game_over.get_rect(center=(stage_w // 2, int(stage_h * .46))))
            stage.blit(detail, detail.get_rect(center=(stage_w // 2, int(stage_h * .63))))
        # Final phase remains solid black until the Pi actually halts.
        surf.blit(stage, panel)
        self._present(surf)
        self.current = surf

    def start_shutdown_preview(self, seconds=10, restore=True):
        """Play the shutdown art on demand, never requesting power-off."""
        self.wake(force=True)
        self._shutdown_preview = {"started": time.monotonic(), "seconds": seconds,
                                  "restore": restore}
        self.show_shutdown_countdown(seconds, seconds, 0)

    def show_startup_splash(self, seconds=4):
        """Play a native Neo Geo startup sequence after KMS is available."""
        if self.headless or self.screen is None or not self.electrocoin:
            return False
        started = time.monotonic()
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= seconds:
                break
            surf = pygame.Surface(self.size); surf.fill(BG)
            panel = pygame.Rect(0, 0, self.size[0],
                                min(ELECTROCOIN_VIEWPORT_HEIGHT, self.size[1]))
            stage = pygame.Surface(panel.size); stage.fill((0, 0, 0))
            stage_w, stage_h = stage.get_size()
            logo_font = pygame.font.SysFont(
                "Times New Roman", max(28, int(stage_h * .25)), bold=True)
            sub_font = pygame.font.SysFont(None, max(16, int(stage_h * .085)),
                                          bold=True)
            snk_font = pygame.font.SysFont(None, max(18, int(stage_h * .09)),
                                          bold=True)
            logo = logo_font.render("NEO·GEO", True, (242, 242, 242))
            # Begin black, expand the logo into place, then hold the completed
            # boot card long enough for a cold HDMI panel to display it.
            if elapsed >= .35:
                progress = min(1.0, (elapsed - .35) / .9)
                shown = pygame.transform.smoothscale(
                    logo, (max(1, round(logo.get_width() * progress)),
                           logo.get_height()))
                stage.blit(shown, shown.get_rect(
                    center=(stage_w // 2, int(stage_h * .38))))
                if progress >= 1.0:
                    specs = sub_font.render("MAX 330 MEGA", True, (236, 236, 236))
                    progear = sub_font.render("PRO-GEAR SPEC", True, (236, 236, 236))
                    snk = snk_font.render("SNK", True, (30, 92, 230))
                    stage.blit(specs, specs.get_rect(
                        center=(stage_w // 2, int(stage_h * .56))))
                    stage.blit(progear, progear.get_rect(
                        center=(stage_w // 2, int(stage_h * .65))))
                    stage.blit(snk, snk.get_rect(
                        center=(stage_w // 2, int(stage_h * .79))))
            surf.blit(stage, panel)
            self._present(surf)
            if not self.pump():
                break
            time.sleep(0.05)
        return True

    def _update_shutdown_preview(self):
        preview = self._shutdown_preview
        if not preview:
            return
        elapsed = time.monotonic() - preview["started"]
        if elapsed < preview["seconds"]:
            self.show_shutdown_countdown(max(0, int(preview["seconds"] - elapsed + .999)),
                                         preview["seconds"], elapsed)
            return
        self._shutdown_preview = None
        if not preview.get("restore", True):
            self.blank()
        elif self.last_game:
            self.show_game(self.last_game)
        else:
            self.show_idle()

    def sleep(self):
        """Release the screen and cut video output so the panel's driver
        board loses signal and drops to standby (backlight off)."""
        if self.screen is None or self.calibrating:
            return
        if self.headless:
            return
        # (manual_sleep is set by the caller for admin-page sleeps)
        pygame.display.quit()
        self.screen = None
        _fb_blank(4)
        print("[MarqueeMark] display sleeping")

    def wake(self, force=False):
        """Restore video output and re-acquire the screen.

        force=True is the admin page's explicit Wake; it also clears the
        manual-sleep latch. Automatic wakes respect that latch."""
        if force:
            self.manual_sleep = False
        elif self.manual_sleep:
            return
        if self.screen is not None:
            return
        if self.headless:
            return
        _fb_blank(0)
        try:
            pygame.display.init()
            pygame.mouse.set_visible(False)
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        except pygame.error as e:
            self.headless = True
            self.screen = pygame.Surface(self.phys)
            print("[MarqueeMark] HDMI wake failed (%s); continuing headless" % e)
        self.current = None
        self.wake_count += 1
        print("[MarqueeMark] display awake")

    def _retry_headless_display(self):
        """Re-acquire HDMI after a slow panel has finished starting up.

        This is intentionally called from pump(), which runs on pygame's
        main thread.  A browser-admin process may run with no HDMI at all;
        retries remain quiet until a real display becomes available.
        """
        if not self.headless or self.manual_sleep:
            return
        now = time.monotonic()
        if now < self._headless_retry_at:
            return
        self._headless_retry_at = now + 2.0
        try:
            pygame.display.init()
            pygame.mouse.set_visible(False)
            screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        except pygame.error:
            return

        self.screen = screen
        self.headless = False
        self.phys = screen.get_size()
        if self.electrocoin:
            self.size = self.phys
            self.rect = pygame.Rect(0, 0, self.size[0], self.size[1])
        else:
            self.size = (self.phys[1], self.phys[0]) \
                if self.rotate in (90, 270) else self.phys
        self.current = None
        self.wake_count += 1
        print("[MarqueeMark] HDMI display detected; output restored")
        if self.calibrating:
            self._render_calibration_frame()
        else:
            self._redraw_current()

    def pump(self):
        self._update_shutdown_preview()
        if self.headless:
            self._retry_headless_display()
            return True
        if self.screen is None:  # asleep — nothing to pump
            return True
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False
        return True

    # -------------------------------------------- web calibration session
    #
    # Driven by CAL_QUEUE, filled by the admin page's HTTP handlers
    # (running in a different thread) and drained here, on the main
    # thread, once per loop tick — see process_calibration_queue() call
    # sites in main(). This lets a browser move/resize/flip/tilt the
    # marquee live while the service keeps running, no SSH required.

    def process_display_queue(self):
        """Apply admin-page Sleep/Wake requests. Main thread only."""
        while True:
            try:
                cmd = DISPLAY_QUEUE.get_nowait()
            except queue.Empty:
                break
            if self.calibrating:
                continue  # a live calibration session owns the screen
            if cmd[0] == "sleep":
                self.manual_sleep = True
                self.sleep()
            elif cmd[0] == "wake":
                self.wake(force=True)
                self.current = None  # force a redraw on the next show_*
            elif cmd[0] == "shutdown_preview":
                self.start_shutdown_preview()
            elif cmd[0] == "shutdown_sequence":
                self.start_shutdown_preview(restore=False)

    def process_calibration_queue(self):
        changed = False
        while True:
            try:
                cmd = CAL_QUEUE.get_nowait()
            except queue.Empty:
                break
            changed = True
            self._handle_cal_cmd(cmd)
        if changed and self.calibrating:
            self._render_calibration_frame()

    def _handle_cal_cmd(self, cmd):
        op = cmd[0]
        if op == "display_mode":
            self.set_layout_mode(cmd[1]); return
        if op == "electro_config" and self.electrocoin:
            self.electro_config = electro_config(cmd[1]); self._show_electrocoin(); return
        if op == "enter":
            if self.screen is None:
                self.wake()
            self.calibrating = True
            self.cal_work = {"x": self.rect.x, "y": self.rect.y,
                             "w": self.rect.w, "h": self.rect.h,
                             "tilt": self.tilt, "rotate": self.rotate,
                             "dpad_offset": self.dpad_offset,
                             "preview": "pattern"}
            return
        if not self.calibrating or self.cal_work is None:
            return  # stray command with no active session — ignore
        w = self.cal_work
        if op == "exit":
            self.calibrating = False
            self.cal_work = None
            self._restore_last_display()
            return
        if op == "save":
            self.rect = pygame.Rect(w["x"], w["y"], w["w"], w["h"])
            self.tilt = w["tilt"]
            self.rotate = w["rotate"]
            self.dpad_offset = w["dpad_offset"]
            save_calibration([self.rect.x, self.rect.y, self.rect.w, self.rect.h],
                             self.tilt, self.rotate, self.dpad_offset)
            print("[MarqueeMark v%s] calibration saved: rect=%s tilt=%.2f "
                  "rotate=%d dpad_offset=%d"
                  % (VERSION, [self.rect.x, self.rect.y, self.rect.w, self.rect.h],
                     self.tilt, self.rotate, self.dpad_offset))
            self.calibrating = False
            self.cal_work = None
            self._restore_last_display()
            return
        if op == "move":
            _, direction, step = cmd
            dx, dy = physical_delta(w["dpad_offset"], direction, step)
            w["x"] += dx
            w["y"] += dy
        elif op == "dpad":
            # Cycle the empirical D-pad correction 0->90->180->270->0.
            # Purely a labeling change — doesn't move or resize anything,
            # just changes which way future arrow presses go.
            w["dpad_offset"] = (w.get("dpad_offset", 0) + 90) % 360
        elif op == "size":
            _, delta = cmd
            neww = max(40, w["w"] + delta)
            w["w"] = neww
            w["h"] = round(neww * MARQUEE_ASPECT)
        elif op == "tilt":
            _, delta = cmd
            w["tilt"] = round(w["tilt"] + delta, 2)
        elif op == "flip":
            w["rotate"] = 270 if w["rotate"] == 90 else 90
        elif op == "preview":
            _, mode = cmd
            w["preview"] = mode if mode in ("pattern", "art") else "pattern"
        self._clamp_cal_work()

    def _clamp_cal_work(self):
        w = self.cal_work
        w["w"] = max(40, min(w["w"], self.size[0]))
        w["h"] = max(40, min(w["h"], self.size[1]))
        w["x"] = max(-w["w"] + 20, min(w["x"], self.size[0] - 20))
        w["y"] = max(-w["h"] + 20, min(w["y"], self.size[1] - 20))

    def _render_calibration_frame(self):
        if self.headless or self.screen is None:
            return
        w = self.cal_work
        rect = pygame.Rect(w["x"], w["y"], w["w"], w["h"])
        surf = pygame.Surface(self.size)
        surf.fill(BG)
        drew_art = False
        if w["preview"] == "art":
            if self._cal_sample is None:
                spath = os.path.join(self.art_dir, GENERIC + ".png")
                if os.path.isfile(spath):
                    self._cal_sample = pygame.image.load(spath).convert()
            if self._cal_sample:
                card = pygame.transform.smoothscale(self._cal_sample, (rect.w, rect.h))
                self._place(surf, card, rect=rect, tilt=w["tilt"])
                drew_art = True
        if not drew_art:
            box = pygame.Surface((rect.w, rect.h))
            box.fill((0, 60, 0))
            pygame.draw.rect(box, (255, 255, 255), box.get_rect(), 4)
            pygame.draw.line(box, (255, 0, 0), (rect.w // 2, 0), (rect.w // 2, rect.h), 2)
            pygame.draw.line(box, (255, 0, 0), (0, rect.h // 2), (rect.w, rect.h // 2), 2)
            for gx in range(0, rect.w, max(20, rect.w // 10)):
                pygame.draw.line(box, (0, 120, 0), (gx, 0), (gx, rect.h), 1)
            for gy in range(0, rect.h, max(20, rect.h // 10)):
                pygame.draw.line(box, (0, 120, 0), (0, gy), (rect.w, gy), 1)
            corner = min(rect.w, rect.h) // 8
            for cx, cy in [(0, 0), (rect.w - corner, 0), (0, rect.h - corner),
                          (rect.w - corner, rect.h - corner)]:
                pygame.draw.rect(box, (255, 220, 0), (cx, cy, corner, corner), 3)
            self._place(surf, box, rect=rect, tilt=w["tilt"])
        self._present(surf, rotate=w["rotate"])

    def _restore_last_display(self):
        """After leaving a calibration session, redraw whatever should be
        showing. This differs by mode — NeoSD mode looks up the last known
        game; --manual mode looks up the admin page's selection — so
        main() sets restore_callback appropriately. Falls back to the
        NeoSD-style lookup if nothing set one (keeps this class usable
        standalone, e.g. under --calibrate)."""
        if self.restore_callback:
            self.restore_callback()
            return
        state = load_state()
        active = state.get("active") if state else None
        if active:
            self.show_game(active)
        else:
            self.blank()


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
        dpad_offset = cal[3]
    else:
        r = default_rect()
        tilt = 0.0
        dpad_offset = 0

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
        sys.stdout.write("\r  rect x=%-5d y=%-5d w=%-5d h=%-5d tilt=%-6.1f "
                         "dpad=%-3d step=%-3d   "
                         % (r.x, r.y, r.w, r.h, tilt, dpad_offset, steps[step_i]))
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
                dx, dy = physical_delta(dpad_offset, "up", s)
                r.x += dx; r.y += dy
            elif ch == "\x1b[B":
                dx, dy = physical_delta(dpad_offset, "down", s)
                r.x += dx; r.y += dy
            elif ch == "\x1b[D":
                dx, dy = physical_delta(dpad_offset, "left", s)
                r.x += dx; r.y += dy
            elif ch == "\x1b[C":
                dx, dy = physical_delta(dpad_offset, "right", s)
                r.x += dx; r.y += dy
            elif ch == "d":
                # If the arrows feel backwards or rotated on this specific
                # panel, cycle the correction — same as the admin page's
                # "D-pad" button.
                dpad_offset = (dpad_offset + 90) % 360
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
                # Preserve whatever rotate is currently in effect (which
                # may have been set via the web "Flip" button) so this
                # offline save doesn't silently drop it.
                save_calibration([r.x, r.y, r.w, r.h], tilt, display.rotate,
                                 dpad_offset)
                print("\nsaved %s tilt=%.2f rotate=%d dpad_offset=%d"
                      % ([r.x, r.y, r.w, r.h], tilt, display.rotate, dpad_offset))
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

def _poll_sleep_source(url):
    """True = should sleep, False = stay awake, None = couldn't reach it.

    Reads another MarqueeMark's /current endpoint, which reports the
    running game or a null short name when its NeoSD link is gone (cab
    off). Read-only, so the machine being polled needs no changes."""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=2) as r:
            data = json.loads(r.read().decode())
        return data.get("short") is None
    except Exception:
        return None


def _poweroff_pi():
    """Ask systemd to halt after the cabinet-off countdown.

    The service deliberately has a narrowly-scoped sudoers permission for
    this one command (installed by install.sh); there is no web endpoint that
    can invoke it directly.
    """
    try:
        # The cabinet-loss path has already rendered its full countdown.
        # Tell the systemd stop hook not to play the manual reboot animation
        # a second time. RuntimeDirectory ensures this marker cannot survive
        # a reboot or outlive the service.
        CABINET_POWEROFF_MARKER.touch()
        # SDL releases the DRM framebuffer as systemd stops this service.
        # Without blanking fb0 first, the Linux console can flash a final
        # shutdown line over the completed black frame.  This permission is
        # already deliberately scoped by install.sh to this one framebuffer
        # attribute.
        subprocess.run(["sudo", "-n", "/usr/bin/tee", "/sys/class/graphics/fb0/blank"],
                       input=b"1\n", stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False, timeout=1)
        subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", "poweroff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[MarqueeMark] clean power-off requested")
    except (OSError, subprocess.SubprocessError) as e:
        print("[MarqueeMark] could not request power-off: %s" % e)


def run_neosd(args, display, publish, overlay):
    """Normal mode: this panel follows a NeoSD Pro on the local USB port."""
    last = None
    idle_shown = False
    lost_cycles = 0
    neosd_connected_once = False
    shutdown_started = None
    shutdown_requested = False
    while True:
        try:
            with serial.Serial(args.port, 115200, timeout=0.2) as port:
                neosd_connected_once = True
                display.wake()
                if shutdown_started is not None:
                    print("[MarqueeMark] NeoSD USB link restored — shutdown cancelled")
                shutdown_started = None
                shutdown_requested = False
                set_cabinet_shutdown_runtime("armed" if load_cabinet_shutdown_config()["enabled"] else "disabled")
                print("[MarqueeMark v%s] listening on %s" % (VERSION, args.port))
                idle_shown = False
                lost_cycles = 0

                restored = boot_game(load_state())
                if restored:
                    print("[MarqueeMark] restoring NGH-%s %s (slot %s)"
                          % (restored["ngh"], restored["short"], restored["slot"]))
                    display.show_game(restored)
                    publish(restored)
                    last = (restored["ngh"], restored["short"])
                else:
                    print("[MarqueeMark] no known game yet, showing generic marquee")
                    display.show_idle()
                    publish(None)

                for frame in frames(port):
                    display.process_display_queue()
                    display.process_calibration_queue()
                    if not display.pump():
                        pygame.quit()
                        sys.exit(0)
                    if frame is None:
                        continue
                    game = parse_frame(frame)
                    key = (game["ngh"], game["short"])
                    if key == last:
                        continue
                    last = key
                    print("[MarqueeMark] NGH-%s %s \"%s\" (slot %s)"
                          % (game["ngh"], game["short"], game["title"], game["slot"]))
                    save_state(game)
                    display.show_game(game)
                    publish(game)
        except (serial.SerialException, OSError):
            shutdown = load_cabinet_shutdown_config()
            shutdown_active = shutdown["enabled"] and neosd_connected_once
            if not idle_shown:
                print("[MarqueeMark] no cart link — idle (%s)" % args.idle)
                if not shutdown_active:
                    if args.idle == "generic":
                        display.show_idle()
                    else:
                        display.blank()
                publish(None)
                idle_shown = True
            last = None
            lost_cycles += 1
            if shutdown_active:
                if shutdown_started is None:
                    shutdown_started = time.monotonic()
                    shutdown_requested = False
                    print("[MarqueeMark] cabinet link lost — clean shutdown in %ss" % shutdown["delay_seconds"])
                    display.wake()
                elapsed = time.monotonic() - shutdown_started
                remaining = max(0, int(shutdown["delay_seconds"] - elapsed + .999))
                if shutdown_requested:
                    set_cabinet_shutdown_runtime("powering-off", 0)
                else:
                    set_cabinet_shutdown_runtime("countdown", remaining)
                    display.show_shutdown_countdown(remaining, shutdown["delay_seconds"], elapsed)
                    if elapsed >= shutdown["delay_seconds"]:
                        shutdown_requested = True
                        set_cabinet_shutdown_runtime("powering-off", 0)
                        _poweroff_pi()
            else:
                shutdown_started = None
                shutdown_requested = False
                set_cabinet_shutdown_runtime("waiting-for-link" if shutdown["enabled"] else "disabled")
            if (not shutdown_active and lost_cycles >= 10 and
                    not args.keep_awake and not display.calibrating):
                display.sleep()
            for _ in range(10):
                display.process_display_queue()
                display.process_calibration_queue()
                if not display.pump():
                    pygame.quit()
                    sys.exit(0)
                # Redraw during the one-second reconnect wait so the power-off
                # scan line is an actual animation, not a sequence of stills.
                if shutdown_active and not shutdown_requested:
                    elapsed = time.monotonic() - shutdown_started
                    remaining = max(0, int(shutdown["delay_seconds"] - elapsed + .999))
                    set_cabinet_shutdown_runtime("countdown", remaining)
                    display.show_shutdown_countdown(remaining, shutdown["delay_seconds"], elapsed)
                    if elapsed >= shutdown["delay_seconds"]:
                        shutdown_requested = True
                        set_cabinet_shutdown_runtime("powering-off", 0)
                        _poweroff_pi()
                time.sleep(0.1)


def run_manual(args, display, publish):
    """--manual: no NeoSD Pro on this panel. The marquee is picked by hand
    from the admin page. Optionally mirrors another MarqueeMark's cab-power
    state via --sleep-source; otherwise sleep is entirely manual."""

    def restore():
        # Called after Save/Cancel in a live calibration session, so the
        # panel goes back to whatever it should be showing. In --manual
        # mode that is the admin page's selection, not NeoSD state.
        want = read_selection()
        if want:
            display.show_game({"short": want, "title": want, "ngh": "---"})
        else:
            display.show_idle()

    display.restore_callback = restore

    shown = "__unset__"
    auto_asleep = False
    countdown = 0
    last_wake_count = display.wake_count

    print("[MarqueeMark v%s] manual mode (no NeoSD on this panel)" % VERSION)
    if args.art_source:
        print("[MarqueeMark] art library: %s" % args.art_source)
        for name in (read_selection(), GENERIC):
            if name:
                fetch_remote_art(args.art_source, name, args.art)
    if args.sleep_source:
        print("[MarqueeMark] mirroring cab power from %s" % args.sleep_source)

    while True:
        display.process_display_queue()
        display.process_calibration_queue()
        if not display.pump():
            pygame.quit()
            sys.exit(0)

        if not display.calibrating:
            # Catches every wake, whichever way it happened: the admin
            # page's Wake button, or the automatic --sleep-source path
            # below. Without this, waking after a manual Sleep left the
            # panel black until the page was refreshed and the marquee
            # re-picked — the selection on disk hadn't changed, so the
            # "did the selection change?" check below never redrew it.
            if display.wake_count != last_wake_count:
                last_wake_count = display.wake_count
                shown = "__unset__"

            if args.sleep_source and not args.keep_awake:
                countdown -= 1
                if countdown <= 0:
                    countdown = 10  # ~1s between polls
                    state = _poll_sleep_source(args.sleep_source)
                    if state is not None:  # None = unreachable: hold as-is
                        if state and not auto_asleep:
                            print("[MarqueeMark] source reports cab off, sleeping")
                            display.sleep()
                            auto_asleep = True
                        elif not state and auto_asleep:
                            print("[MarqueeMark] source reports cab on, waking")
                            display.wake()  # respects a manual sleep latch
                            auto_asleep = False
                            shown = "__unset__"

            if display.screen is not None:
                want = read_selection()
                if want != shown:
                    shown = want
                    print("[MarqueeMark] showing: %s" % (want or "generic"))
                    if args.art_source:
                        # Refresh from the primary on every change, so
                        # replacing art there propagates without touching
                        # this Pi. Falls back to the cached copy offline.
                        fetch_remote_art(args.art_source, want or GENERIC,
                                         args.art)
                    if want:
                        display.show_game({"short": want, "title": want,
                                          "ngh": "---"})
                    else:
                        display.show_idle()
                    publish({"short": want} if want else None)

        time.sleep(0.1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--art", default=ART_DIR)
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--rotate", type=int, default=90, choices=[0, 90, 180, 270],
                    help="starting orientation for a panel that has never been "
                         "calibrated. Defaults to 90 because a mini marquee is "
                         "portrait; use the admin page's Flip button if it "
                         "comes up upside down (that choice is saved and "
                         "overrides this flag from then on). 0/180 are only "
                         "for an unusual landscape mount.")
    ap.add_argument("--calibrate", action="store_true",
                    help="interactive terminal calibration, then exit")
    ap.add_argument("--keep-awake", action="store_true",
                    help="never sleep the display automatically")
    ap.add_argument("--idle", choices=["blank", "generic"], default="blank",
                    help="with no NeoSD link: blank (default; marquee goes dark "
                         "with the cab) or generic (stays lit)")
    ap.add_argument("--manual", action="store_true",
                    help="no NeoSD Pro on this panel: pick the marquee by hand "
                         "from the admin page. Use this for a second marquee "
                         "on its own Pi, or for any cab without a NeoSD Pro.")
    ap.add_argument("--layout", choices=["mini", "ultrawide"], default=None,
                    help="initial display type: mini (original portrait marquee) or ultrawide")
    ap.add_argument("--electrocoin", action="store_true",
                    help="deprecated compatibility alias; uses the Ultrawide Marquee display type")
    ap.add_argument("--art-source", default=None,
                    help="--manual only: base URL of the primary MarqueeMark "
                         "(e.g. http://marquee.local:8080). Art is pulled from "
                         "there and cached locally, so the library lives in one "
                         "place instead of being copied to every panel.")
    ap.add_argument("--sleep-source", default=None,
                    help="--manual only: URL of another MarqueeMark's /current "
                         "endpoint (e.g. http://marquee.local:8080/current). "
                         "This panel then sleeps and wakes with that cabinet. "
                         "Omit to control sleep by hand from the admin page.")
    args = ap.parse_args()

    # systemd sends SIGUSR1 only for a real machine reboot/power-off. Queue
    # the renderer work so Pygame continues to run exclusively on this main
    # thread rather than doing display work inside the signal handler.
    signal.signal(signal.SIGUSR1, queue_shutdown_sequence)

    initial_mode = load_display_mode("ultrawide" if args.electrocoin else (args.layout or "mini"))
    display = Display(args.art, rotate=args.rotate, layout_mode=initial_mode)

    if args.calibrate:
        calibrate(display)
        pygame.quit()
        return

    overlay = None
    try:
        overlay = OverlayServer(args.art, args.http_port, display)
        overlay.manual = args.manual  # admin page adapts to the mode
        overlay.art_source = args.art_source
        overlay.start()
    except OSError as e:
        print("[MarqueeMark] overlay disabled (%s)" % e)

    # Show boot art before any saved-layout reassertion can redraw the last
    # marquee. The normal game restore then becomes the single handoff out of
    # the startup sequence.
    if SYSTEM_BOOT_MARKER.exists():
        SYSTEM_BOOT_MARKER.unlink(missing_ok=True)
        if display.show_startup_splash():
            print("[MarqueeMark] native startup splash complete")

    # ``Display(...)`` receives the persisted type as its initial hint, but
    # HDMI/KMS can retain the old framebuffer during a cold service start.
    # Reassert an explicitly saved choice after startup so the physical panel
    # is redrawn with the same renderer Admin reports. Do not do this before
    # first-run setup: an unset Pi should retain its normal CLI fallback.
    configured_mode = saved_display_mode()
    if configured_mode:
        display.set_layout_mode(configured_mode)
        print("[MarqueeMark] startup display mode enforced: %s" % configured_mode)

    def publish(game):
        if overlay:
            overlay.publish(game)

    if args.manual:
        run_manual(args, display, publish)
    else:
        run_neosd(args, display, publish, overlay)


if __name__ == "__main__":
    main()
