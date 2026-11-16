"""Generate safa.ico — original mark: a teal circle (Safa = clarity) with
a white audio waveform. Writes a multi-size .ico (16/32/48 px, 32-bit
BGRA + AND mask). No dependencies — raw pixel math + struct packing.

Run:  python make_icon.py  →  safa.ico next to this script.
"""
from __future__ import annotations

import struct
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


if __name__ == "__main__":
    out = build(Path(__file__).with_name("safa.ico"))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
