"""Draws Budget Tool's icon (static/icon.ico): a dark rounded square with three rising sun-yellow budget bars,
in the colors of the app. Pure Python (no image library needed): each size is drawn 4x larger and averaged down
for smooth edges, saved as PNG, and packed into one .ico with 16-256 pixel versions.

    py launcher/make_icon.py
"""
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "static", "icon.ico")
SIZES = (16, 24, 32, 48, 64, 128, 256)
BG = (0x1F, 0x1F, 0x21)      # page background
EDGE = (0x36, 0x36, 0x38)    # charcoal
SUN = (0xEE, 0xDE, 0x75)     # the accent


def inside_rounded(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def color_at(u, v):
    """The design in a 0..1 square: returns (r, g, b, a)."""
    if not inside_rounded(u, v, 0.02, 0.02, 0.98, 0.98, 0.20):
        return (0, 0, 0, 0)
    if not inside_rounded(u, v, 0.06, 0.06, 0.94, 0.94, 0.16):
        return EDGE + (255,)
    # three rising bars on a baseline
    bars = ((0.20, 0.36, 0.58), (0.42, 0.58, 0.40), (0.64, 0.80, 0.22))  # (left, right, top)
    base = 0.78
    for left, right, top in bars:
        if inside_rounded(u, v, left, top, right, base, 0.035):
            return SUN + (255,)
    if 0.14 <= u <= 0.86 and base + 0.03 <= v <= base + 0.075:
        return SUN + (255,)
    return BG + (255,)


def render(size, ss=4):
    rows = []
    n = size * ss
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    r, g, b, a = color_at((x * ss + sx + 0.5) / n, (y * ss + sy + 0.5) / n)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            a = acc[3]
            row += bytes((acc[0] // a, acc[1] // a, acc[2] // a, a // (ss * ss)) if a else (0, 0, 0, 0))
        rows.append(bytes(row))
    return rows


def png(size, rows):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    images = [(s, png(s, render(s))) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in images:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    with open(OUT, "wb") as f:
        f.write(header + entries + blobs)
    with open(os.path.join(HERE, "icon-preview.png"), "wb") as f:  # for looking at it; not used by the app
        f.write(images[-1][1])
    print(f"Wrote {os.path.normpath(OUT)} ({', '.join(str(s) for s in SIZES)} px)")


if __name__ == "__main__":
    main()
