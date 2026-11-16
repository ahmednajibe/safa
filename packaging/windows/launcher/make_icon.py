"""Generate the Safa mark — one geometry, every format.

Tile: rounded square inset 0.04·size, corner radius 0.22·size, fill
#111111 with a size/32-px rim #2a2a2a so the edge reads on a black
taskbar. Waveform: five capsule bars centred — the first two #6b6b6b
(original), the rest #f2b33d (clean). "before → after".
No dependencies — raw pixel math + struct packing, 4×4 supersampled.

Run:  python make_icon.py              → safa.ico next to this script
      python make_icon.py --ico OUT    → multi-size .ico (16…256 px)
      python make_icon.py --png OUT [--size N]  → RGBA .png (N×N)
      python make_icon.py --icns OUT   → Apple .icns (PNG payloads)
      python make_icon.py --svg OUT    → SVG of the same geometry
      python make_icon.py --favicon DIR → favicon.svg/.ico + apple-touch-icon.png
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

TILE = (0x11, 0x11, 0x11)    # --surface
RIM = (0x2A, 0x2A, 0x2A)     # --raised
ORIG = (0x6B, 0x6B, 0x6B)    # --wave-original
CLEAN = (0xF2, 0xB3, 0x3D)   # --data

INSET = 0.04          # tile inset, fraction of size
RADIUS = 0.22         # tile corner radius, fraction of size
RIM_DIV = 32.0        # rim width = size/32 px (1 px at 32)
BAR_W = 0.10          # capsule width, fraction of size
BAR_GAP = 0.075       # gap between capsules, fraction of size
BAR_HEIGHTS = (0.28, 0.50, 0.72, 0.50, 0.28)   # fraction of size
ORIG_BARS = 2         # first N bars are grey; the rest amber

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
FAVICON_SIZES = (16, 32, 48)
SS = 4                # supersample grid per axis


def _hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _geom(size: int) -> dict:
    """All geometry in pixels for one canvas size. 16 px is hand-tuned:
    2-px bars on whole pixels, and the rim dropped (0.5 px just muddies)."""
    if size == 16:
        return {"inset": INSET * size, "radius": RADIUS * size, "rim": 0.0,
                "bar_w": 2.0, "gap": 1.0, "heights": (4.0, 7.0, 10.0, 7.0, 4.0)}
    return {"inset": INSET * size, "radius": RADIUS * size,
            "rim": size / RIM_DIV, "bar_w": BAR_W * size,
            "gap": BAR_GAP * size,
            "heights": tuple(h * size for h in BAR_HEIGHTS)}


def _bars(size: int, gm: dict) -> list[tuple[float, float, tuple]]:
    """(centre_x, height, rgb) per capsule bar, centred on the canvas."""
    n = len(gm["heights"])
    total = n * gm["bar_w"] + (n - 1) * gm["gap"]
    x0 = (size - total) / 2
    return [
        (x0 + gm["bar_w"] / 2 + i * (gm["bar_w"] + gm["gap"]),
         gm["heights"][i], ORIG if i < ORIG_BARS else CLEAN)
        for i in range(n)
    ]


def _rrect_d(x: float, y: float, cx: float, cy: float,
             hx: float, hy: float, r: float) -> float:
    """Signed distance to a centred rounded rect (negative inside)."""
    dx = abs(x - cx) - (hx - r)
    dy = abs(y - cy) - (hy - r)
    ox, oy = max(dx, 0.0), max(dy, 0.0)
    return (ox * ox + oy * oy) ** 0.5 + min(max(dx, dy), 0.0) - r


def _sample(x: float, y: float, size: int, gm: dict, bars) -> tuple:
    """Topmost shape's RGBA at point (x, y)."""
    for cx, h, col in bars:  # capsules have fully rounded ends (r = w/2)
        if _rrect_d(x, y, cx, size / 2, gm["bar_w"] / 2, h / 2,
                    gm["bar_w"] / 2) <= 0:
            return (*col, 255)
    half = size / 2 - gm["inset"]
    d = _rrect_d(x, y, size / 2, size / 2, half, half, gm["radius"])
    if d <= 0:
        return (*(RIM if gm["rim"] and d > -gm["rim"] else TILE), 255)
    return (0, 0, 0, 0)


def _pixels(size: int) -> list[list[tuple[int, int, int, int]]]:
    """RGBA pixel grid, top-down, box-filtered from SS×SS subsamples
    (premultiplied average so edges carry no dark halo). Pixels whose
    nearest shape boundary is farther than a half-diagonal are resolved
    from the centre alone — SDFs are 1-Lipschitz so the result is exact;
    only edge pixels pay the supersample cost."""
    gm = _geom(size)
    bars = _bars(size, gm)
    n, bound = SS * SS, 0.71          # ~sqrt(2)/2 px
    c2, half = size / 2, size / 2 - gm["inset"]
    bw2, rim = gm["bar_w"] / 2, gm["rim"]
    px = []
    for y in range(size):
        row = []
        for x in range(size):
            cxp, cyp = x + 0.5, y + 0.5
            dtile = _rrect_d(cxp, cyp, c2, c2, half, half, gm["radius"])
            tile_certain = (
                dtile > bound
                or dtile < -(rim + bound)
                or (rim and -rim + bound < dtile < -bound)
            )
            fast = None
            if tile_certain:
                hit, certain = None, True
                for bx, h, col in bars:
                    d = _rrect_d(cxp, cyp, bx, c2, bw2, h / 2, bw2)
                    if d <= -bound:
                        if hit is None:
                            hit = (*col, 255)
                    elif d < bound:
                        certain = False
                        break
                if certain:
                    if hit is not None:
                        fast = hit
                    elif dtile <= 0:
                        fast = (*(RIM if rim and dtile > -rim else TILE), 255)
                    else:
                        fast = (0, 0, 0, 0)
            if fast is not None:
                row.append(fast)
                continue
            sr = sg = sb = sa = 0
            for sy in range(SS):
                for sx in range(SS):
                    r, g, b, a = _sample(x + (sx + 0.5) / SS,
                                         y + (sy + 0.5) / SS, size, gm, bars)
                    sr, sg, sb, sa = sr + r * a, sg + g * a, sb + b * a, sa + a
            row.append((sr // sa, sg // sa, sb // sa, sa // n) if sa
                       else (0, 0, 0, 0))
        px.append(row)
    return px


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


def _ico_image(px: list[list[tuple[int, int, int, int]]]) -> bytes:
    """32-bit BMP icon payload (XOR + AND mask) for sizes below 256."""
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
    # AND mask: 1 = transparent (edge AA keeps its alpha in the XOR plane).
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


def build(path: Path, sizes=ICO_SIZES) -> Path:
    """Multi-size .ico — BMP payloads below 256, PNG for 256 (every tool
    reads that pairing: Explorer, csc /win32icon, Inno SetupIconFile)."""
    images = []
    for s in sizes:
        px = _pixels(s)
        images.append(_ico_image(px) if s < 256 else png_image(px))
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


def _svg_text(size: int = 256) -> str:
    """The same geometry as SVG: outer rim rect + inner tile rect +
    five capsule bars (favicons, README, docs)."""
    gm = _geom(size)
    inset, rim = gm["inset"], gm["rim"]
    inner = inset + rim
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}">',
        f'<rect x="{inset:g}" y="{inset:g}" '
        f'width="{size - 2 * inset:g}" height="{size - 2 * inset:g}" '
        f'rx="{gm["radius"]:g}" fill="{_hex(RIM)}"/>',
        f'<rect x="{inner:g}" y="{inner:g}" '
        f'width="{size - 2 * inner:g}" height="{size - 2 * inner:g}" '
        f'rx="{max(gm["radius"] - rim, 0):g}" fill="{_hex(TILE)}"/>',
    ]
    for cx, h, col in _bars(size, gm):
        w = gm["bar_w"]
        parts.append(
            f'<rect x="{cx - w / 2:g}" y="{(size - h) / 2:g}" '
            f'width="{w:g}" height="{h:g}" rx="{w / 2:g}" fill="{_hex(col)}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def write_svg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_svg_text(), encoding="utf-8")
    return path


def write_favicon(dir_: Path) -> Path:
    """Browser icon set: favicon.svg, favicon.ico (16/32/48),
    apple-touch-icon.png (180)."""
    dir_.mkdir(parents=True, exist_ok=True)
    write_svg(dir_ / "favicon.svg")
    build(dir_ / "favicon.ico", sizes=FAVICON_SIZES)
    write_png(dir_ / "apple-touch-icon.png", 180)
    return dir_


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
    elif args[0] == "--svg":
        out = write_svg(Path(args[1]))
    elif args[0] == "--favicon":
        out = write_favicon(Path(args[1]))
    else:
        raise SystemExit(__doc__)
    print(f"wrote {out}")
