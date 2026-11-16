"""Safa app icon (R3 §1): one mark from one geometry — a rounded-square
#111111 tile with a #2a2a2a rim and five capsule waveform bars: the first
two grey #6b6b6b (original), the rest amber #f2b33d (clean).

The generator is pure-python (packaging/windows/launcher/make_icon.py);
these tests parse its .ico/.png/.svg outputs and check real pixels.
"""
from __future__ import annotations

import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging" / "windows" / "launcher"))

import make_icon  # noqa: E402

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
AMBER = (0xF2, 0xB3, 0x3D)
GREY = (0x6B, 0x6B, 0x6B)
TILE = (0x11, 0x11, 0x11)


# ---- tiny decoders for our own formats (no dependencies) ----------------------


def _png_wh(blob: bytes) -> tuple[int, int]:
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert blob[12:16] == b"IHDR"
    return struct.unpack(">II", blob[16:24])


def _png_pixels(blob: bytes) -> list[list[tuple[int, int, int, int]]]:
    """Decode the filter-0 RGBA8 PNGs make_icon writes."""
    w, h = _png_wh(blob)
    pos, idat = 8, b""
    while pos < len(blob):
        ln = struct.unpack(">I", blob[pos:pos + 4])[0]
        tag = blob[pos + 4:pos + 8]
        if tag == b"IDAT":
            idat += blob[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4 + 1
    return [
        [tuple(raw[y * stride + 1 + x * 4: y * stride + 5 + x * 4])
         for x in range(w)]
        for y in range(h)
    ]


def _ico_entries(blob: bytes) -> list[tuple[int, bytes]]:
    """[(size, payload)] from an .ico blob."""
    assert blob[:4] == b"\x00\x00\x01\x00", "bad .ico header"
    (count,) = struct.unpack("<H", blob[4:6])
    out = []
    for i in range(count):
        e = 6 + i * 16
        b_w = blob[e]
        size = 256 if b_w == 0 else b_w
        img_len, img_off = struct.unpack("<II", blob[e + 8:e + 16])
        out.append((size, blob[img_off:img_off + img_len]))
    return out


# ---- .ico ---------------------------------------------------------------------


def test_ico_contains_every_size(tmp_path):
    out = make_icon.build(tmp_path / "safa.ico")
    entries = dict(_ico_entries(out.read_bytes()))
    assert set(entries) == set(ICO_SIZES)
    assert 256 in entries


def test_ico_payloads_are_valid(tmp_path):
    out = make_icon.build(tmp_path / "safa.ico")
    for size, payload in _ico_entries(out.read_bytes()):
        if payload[:8] == b"\x89PNG\r\n\x1a\n":
            assert _png_wh(payload) == (size, size)
        else:  # BMP variant: BITMAPINFOHEADER, height doubled for the mask
            bi_size, bi_w, bi_h = struct.unpack("<Iii", payload[:12])
            assert bi_size == 40 and bi_w == size and bi_h == size * 2
    entries = dict(_ico_entries(out.read_bytes()))
    assert entries[256][:8] == b"\x89PNG\r\n\x1a\n", "256 must be PNG"


# ---- pixels --------------------------------------------------------------------


def test_middle_bar_centre_is_amber():
    px = _png_pixels(make_icon.png_image(make_icon._pixels(256)))
    assert px[128][128][:3] == AMBER
    assert px[128][128][3] == 255


def test_first_bars_are_grey_last_are_amber():
    px = _png_pixels(make_icon.png_image(make_icon._pixels(256)))
    # bar centres at 256: x ≈ 38.4 / 83.2 / 128 / 172.8 / 217.6, all at y=128
    for x, col in ((38, GREY), (83, GREY), (173, AMBER), (218, AMBER)):
        assert px[128][x][:3] == col, (x, px[128][x])


def test_tile_corner_transparent_bg_is_dark_tile():
    px = _png_pixels(make_icon.png_image(make_icon._pixels(256)))
    assert px[0][0][3] == 0, "canvas corner must be transparent"
    assert px[4][4][3] == 0
    # between bar 0 and bar 1, mid height: inside the tile, ~#111111
    r, g, b, a = px[128][60]
    assert a == 255 and abs(r - 0x11) <= 2 and abs(g - 0x11) <= 2 \
        and abs(b - 0x11) <= 2, (r, g, b, a)


def test_16px_variant_pixel_aligned():
    px = _png_pixels(make_icon.png_image(make_icon._pixels(16)))
    # hand-tuned 16 px: bar_w 2, gap 1 → bar centres x = 2,5,8,11,14
    assert px[8][8][:3] == AMBER       # middle bar
    assert px[8][2][:3] == GREY        # first bar
    assert px[8][14][:3] == AMBER      # last bar
    assert px[8][3][:3] == TILE        # the 1-px gap between bars
    assert px[0][0][3] == 0


def test_png_header_roundtrip(tmp_path):
    for size in (32, 180):
        out = make_icon.write_png(tmp_path / f"i{size}.png", size)
        assert _png_wh(out.read_bytes()) == (size, size)


# ---- svg / favicon ---------------------------------------------------------------


def test_svg_wellformed_and_branded(tmp_path):
    out = make_icon.write_svg(tmp_path / "favicon.svg")
    tree = ET.parse(out)
    assert tree.getroot().tag.endswith("svg")
    text = out.read_text(encoding="utf-8")
    assert "#f2b33d" in text and "#6b6b6b" in text
    assert "#111111" in text


def test_favicon_dir_outputs(tmp_path):
    make_icon.write_favicon(tmp_path)
    svg = tmp_path / "favicon.svg"
    ico = tmp_path / "favicon.ico"
    apple = tmp_path / "apple-touch-icon.png"
    for f in (svg, ico, apple):
        assert f.is_file(), f
    ET.parse(svg)
    assert set(dict(_ico_entries(ico.read_bytes()))) == {16, 32, 48}
    assert _png_wh(apple.read_bytes()) == (180, 180)


def test_icns_still_png_payloads(tmp_path):
    blob = make_icon.write_icns(tmp_path / "i.icns").read_bytes()
    assert blob[:4] == b"icns"
    assert b"ic08" in blob and b"ic09" in blob
