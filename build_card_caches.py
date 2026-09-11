#!/usr/bin/env python3
"""Build high-quality, exact-size mini-marquee image caches.

The live renderer uses these optional ``name--WIDTHxHEIGHT.png`` files when
they exist.  Keeping them separate preserves each original artwork file and
avoids doing a soft real-time downscale on every redraw.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageFilter


# The locked red Neo Geo layouts plus the four Electrocoin windows.  The
# latter are not all identical in the original cabinet artwork.
TARGET_SIZES = ((231, 306), (186, 278), (160, 232), (149, 227), (149, 232),
                (116, 188), (120, 188), (121, 188))
REFERENCE_PREFIXES = ("electrocoin-", "neogeo-", "ultrawide-")
CACHE_SUFFIX = re.compile(r"--\d+x\d+$")


def game_art_paths(art_dir: Path):
    for source in sorted(art_dir.glob("*.png")):
        stem = source.stem
        if CACHE_SUFFIX.search(stem) or stem.startswith(REFERENCE_PREFIXES):
            continue
        if stem == "generic":
            continue
        yield source


def build(source: Path, target: tuple[int, int], cache_dir: Path, overwrite: bool) -> bool:
    output = cache_dir / f"{source.stem}--{target[0]}x{target[1]}.png"
    if output.exists() and not overwrite:
        return False
    with Image.open(source) as image:
        image = image.convert("RGB").resize(target, Image.Resampling.LANCZOS)
        # Keep this deliberately mild: it restores edge definition after
        # downsampling without the blockiness of nearest-neighbour scaling.
        image = image.filter(ImageFilter.UnsharpMask(radius=0.65, percent=75, threshold=3))
        image.save(output, optimize=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("art_dir", type=Path)
    parser.add_argument("--cache-dir", type=Path,
                        help="directory for generated variants (default: ../cache/mini-marquees)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    cache_dir = args.cache_dir or args.art_dir.parent / "cache" / "mini-marquees"
    cache_dir.mkdir(parents=True, exist_ok=True)

    made = skipped = 0
    for source in game_art_paths(args.art_dir):
        for target in TARGET_SIZES:
            if build(source, target, cache_dir, args.overwrite):
                made += 1
            else:
                skipped += 1
    print(f"Built {made} quality caches; kept {skipped} existing caches.")


if __name__ == "__main__":
    main()
