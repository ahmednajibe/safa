"""Generate Safa icons — original mark: a teal circle (Safa = clarity)
with a white audio waveform. No dependencies — raw pixel math + struct
packing.

Run:  python make_icon.py              → safa.ico next to this script
      python make_icon.py --ico OUT    → multi-size .ico (16/32/48 px)
      python make_icon.py --png OUT [--size N]  → RGBA .png (N×N)
      python make_icon.py --icns OUT   → Apple .icns (PNG payloads)
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

TEAL = (14, 165, 164)      # #0EA5A4
TEAL_DARK = (11, 128, 127)
WHITE = (255, 255, 255)


def _pixels(size: int) -> list[list[tuple[int, int, int, int]]]:
    """RGBA pixel grid, top-down. Circle + symmetric waveform bars."""
    c = size / 2.0
    r = size * 0.44
    # waveform: 7 bars, heights symmetric about the middle bar
    heights = [0.18, 0.38, 0.62, 0.82, 0.62, 0.38, 0.18]
    bar_w = max(1.0, size * 0.055)
    gap = size * 0.045
    total_w = len(heights) * bar_w + (len(heights) - 1) * gap
    x0 = c - total_w / 2
    px = [[(0, 0, 0, 0) for _ in range(size)] for _ in range(size)]
    for y in range(size):
        for x in range(size):
            dx, dy = x + 0.5 - c, y + 0.5 - c
            d2 = dx * dx + dy * dy
            if d2 > r * r:
                continue
            # soft edge: darken the outer rim slightly
            base = TEAL_DARK if d2 > (r * 0.88) ** 2 else TEAL
            # waveform bars (white, with tiny vertical taper)
            for i, h in enumerate(heights):
                bx = x0 + i * (bar_w + gap)
                bh = h * r * 2 * 0.62
                if bx <= x + 0.5 <= bx + bar_w and abs(dy) <= bh / 2:
                    px[y][x] = (*WHITE, 255)
                    break
            else:
                px[y][x] = (*base, 255)
    return px


def _ico_image(px: list[list[tuple[int, int, int, int]]]) -> bytes:
    size = len(px)
    # BITMAPINFOHEADER — biHeight = 2*size (XOR + AND mask stacked)
    hdr = struct.pack(
        "<IIIHHIIIIII",
        40, size, size * 2, 1, 32, 0,
        size * size * 4 + size * (4 // 8), 0, 0, 0, 0,
    )
    xor = bytearray()
    for row in reversed(px):  # bottom-up BGRA
        for r, g, b, a in row:
            xor += bytes((b, g, r, a))
    # AND mask: 1 = transparent. All our pixels are either 0 or 255 alpha.
    mask_row_bytes = ((size + 31) // 32) * 4
    and_mask = bytearray()
    for row in reversed(px):
        bits = 0
        nbits = 0
        line = bytearray()
        for _i, (_r, _g, _b, a) in enumerate(row):
            bits = (bits << 1) | (0 if a else 1)
            nbits += 1
            if nbits == 8:
                line.append(bits)
                bits = nbits = 0
        if nbits:
            line.append(bits << (8 - nbits))
        line += b"\x00" * (mask_row_bytes - len(line))
        and_mask += line
    return hdr + bytes(xor) + bytes(and_mask)


def build(path: Path, sizes=(16, 32, 48)) -> Path:
    images = [_ico_image(_pixels(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = bytearray()
    for s, img in zip(sizes, images):
        entries += struct.pack(
            "<BBBBHHII", s if s < 256 else 0, s if s < 256 else 0,
            0, 0, 1, 32, len(img), offset)
        offset += len(img)
    path.write_bytes(header + bytes(entries) + b"".join(images))
    return path


def png_image(px: list[list[tuple[int, int, int, int]]]) -> bytes:
    """Minimal PNG encoder (RGBA8, filter 0) — enough for icons."""
    size = len(px)
    raw = b"".join(
        b"\x00" + b"".join(bytes((r, g, b, a)) for r, g, b, a in row)
        for row in px
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def write_png(path: Path, size: int = 256) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_image(_pixels(size)))
    return path


def write_icns(path: Path, sizes=(256, 512)) -> Path:
    """Apple icon container: each entry is a PNG payload keyed by an OSType
    (ic07=128, ic08=256, ic09=512, ic10=1024 retina-512, ic11/ic12 2x)."""
    tags = {128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}
    body = bytearray()
    for s in sizes:
        png = png_image(_pixels(s))
        body += tags[s] + struct.pack(">I", len(png) + 8) + png
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
    return path


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--ico":
        out = build(Path(args[1]) if len(args) > 1
                    else Path(__file__).with_name("safa.ico"))
    elif args[0] == "--png":
        size = int(args[args.index("--size") + 1]) if "--size" in args else 256
        out = write_png(Path(args[1]), size)
    elif args[0] == "--icns":
        out = write_icns(Path(args[1]))
    else:
        raise SystemExit(__doc__)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
