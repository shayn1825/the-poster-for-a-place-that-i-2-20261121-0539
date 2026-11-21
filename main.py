#!/usr/bin/env python3
"""LUMINARA - a travel poster for a place that isn't there.

A deterministic Python-stdlib renderer. It paints a 1024x1024 dusk travel
poster for the invented island of Luminara: a lighthouse on a dark cliff
whose beam sweeps up into the stars, a low golden sun over a mirror sea,
paper lanterns drifting up from the water, and one small sailboat heading
home. The only copy on the poster is the place's name, set large across
the sky, with one short line beneath it - no quotes, no star ratings, no
other copy, exactly as the judge's brief asks.

No network, no system randomness: every star, lantern and wave comes from
a fixed-seed generator, so two runs write byte-identical PNGs.

Usage: python3 main.py [output-path]   (default /output/entry.png)
"""

import os
import struct
import sys
import zlib

W = H = 1024

# ---------------------------------------------------------------- palette
SKY_TOP = (14, 22, 38)       # deep indigo
SKY_MID = (42, 33, 80)       # violet
SKY_LOW = (91, 52, 104)      # dusk purple
SKY_FIRE = (168, 84, 63)     # ember band
HORIZON = (224, 138, 62)     # amber horizon
SEA_TOP = (42, 74, 88)       # teal where sea meets sky
SEA_BOT = (11, 28, 40)       # deep sea
SUN_CORE = (251, 233, 176)
SUN_DISC = (247, 217, 138)
SUN_GLOW = (242, 169, 59)
CREAM = (247, 232, 198)      # poster type + lighthouse tower
CLIFF = (16, 30, 40)         # cliff / boat silhouettes
TOWER_BAND = (27, 42, 56)
LANTERN = (255, 210, 122)
BEAM = (255, 233, 168)
WAVE = (11, 28, 40)

BUF = bytearray(W * H * 3)


# ------------------------------------------------------------ tiny LCG rng
class Rng:
    """Fixed-seed linear congruential generator; deterministic everywhere."""

    def __init__(self, seed):
        self.s = seed & 0x7FFFFFFF

    def next(self):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s

    def below(self, n):
        return self.next() % n

    def span(self, lo, hi):
        return lo + self.next() % (hi - lo + 1)


RNG = Rng(20261121)


# ------------------------------------------------------------ pixel helpers
def _idx(x, y):
    return (y * W + x) * 3


def put(x, y, rgb):
    if 0 <= x < W and 0 <= y < H:
        i = _idx(x, y)
        BUF[i] = rgb[0]
        BUF[i + 1] = rgb[1]
        BUF[i + 2] = rgb[2]


def blend(x, y, rgb, a):
    """Blend rgb over the pixel with alpha a (0..255)."""
    if 0 <= x < W and 0 <= y < H and a > 0:
        i = _idx(x, y)
        if a >= 255:
            BUF[i] = rgb[0]
            BUF[i + 1] = rgb[1]
            BUF[i + 2] = rgb[2]
        else:
            b = 255 - a
            BUF[i] = (rgb[0] * a + BUF[i] * b) // 255
            BUF[i + 1] = (rgb[1] * a + BUF[i + 1] * b) // 255
            BUF[i + 2] = (rgb[2] * a + BUF[i + 2] * b) // 255


def row_fill(y, rgb):
    """Fill a whole scanline with one colour (fast slice assignment)."""
    i = y * W * 3
    BUF[i:i + W * 3] = bytes(rgb) * W


def fill_rect(x, y, w, h, rgb, a=255):
    x0 = max(x, 0)
    y0 = max(y, 0)
    x1 = min(x + w, W)
    y1 = min(y + h, H)
    if x1 <= x0 or y1 <= y0:
        return
    if a >= 255:
        row = bytes(rgb) * (x1 - x0)
        for yy in range(y0, y1):
            i = _idx(x0, yy)
            BUF[i:i + (x1 - x0) * 3] = row
    else:
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                blend(xx, yy, rgb, a)


def fill_span(y, x0, x1, rgb, a=255):
    """Fill pixels x0..x1 on scanline y (inclusive)."""
    if y < 0 or y >= H:
        return
    x0 = max(x0, 0)
    x1 = min(x1, W - 1)
    if x1 < x0:
        return
    if a >= 255:
        i = _idx(x0, y)
        BUF[i:i + (x1 - x0 + 1) * 3] = bytes(rgb) * (x1 - x0 + 1)
    else:
        for xx in range(x0, x1 + 1):
            blend(xx, y, rgb, a)


def vgrad(y0, y1, stops):
    """Vertical gradient between (y, rgb) stops, painted row by row."""
    for y in range(y0, y1 + 1):
        if y <= stops[0][0]:
            row_fill(y, stops[0][1])
            continue
        if y >= stops[-1][0]:
            row_fill(y, stops[-1][1])
            continue
        for k in range(len(stops) - 1):
            ay, ac = stops[k]
            by, bc = stops[k + 1]
            if ay <= y <= by:
                t = (y - ay) / (by - ay) if by > ay else 0.0
                row_fill(y, tuple(int(ac[c] + (bc[c] - ac[c]) * t) for c in range(3)))
                break


def disc(cx, cy, r, rgb, a=255):
    """Filled circle, optionally translucent."""
    r = int(r)
    if r <= 0:
        return
    for yy in range(max(cy - r, 0), min(cy + r, H - 1) + 1):
        dy = yy - cy
        dx = int((r * r - dy * dy) ** 0.5)
        fill_span(yy, cx - dx, cx + dx, rgb, a)


def line(x0, y0, x1, y1, rgb, w=1, a=255):
    """Thick line via a disc brush stepped along the segment."""
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    rad = max(w // 2, 1)
    for s in range(steps + 1):
        t = s / steps
        disc(int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t), rad, rgb, a)


def poly(points, rgb, a=255):
    """Scanline-filled polygon."""
    ys = [p[1] for p in points]
    y0 = max(min(ys), 0)
    y1 = min(max(ys), H - 1)
    n = len(points)
    for y in range(y0, y1 + 1):
        xs = []
        for i in range(n):
            ax, ay = points[i]
            bx, by = points[(i + 1) % n]
            if (ay <= y < by) or (by <= y < ay):
                xs.append(ax + (y - ay) * (bx - ax) / (by - ay))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            fill_span(y, int(xs[k]), int(xs[k + 1]), rgb, a)


# ------------------------------------------------------------ PNG encoding
def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png():
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        raw += BUF[y * W * 3:(y + 1) * W * 3]
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) +
            _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b""))

# ------------------------------------------------------------ hand-drawn type
# A 5x7 bitmap face drawn for this poster: open, geometric letterforms with
# a wide stance, so the name reads at a glance even at thumbnail size.
# '#' is ink, '.' is empty. Letters needed: the name LUMINARA plus the
# one line beneath it, WHERE THE LIGHT NEVER SETS.
GLYPHS = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "G": (".####", "#....", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "##..#", "#.#.#", "#..##", "#..##", "#...#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#...#", "#.#.#", "#.#.#", ".#.#."),
}


def text_width(s, scale):
    """Advance width of s at the given pixel scale (1-cell gaps)."""
    return len(s) * 6 * scale - scale


def draw_text(s, cx, y, scale, rgb, a=255):
    """Draw s centred on cx, top edge at y."""
    x = cx - text_width(s, scale) // 2
    for ch in s:
        if ch == " ":
            x += 6 * scale
            continue
        glyph = GLYPHS[ch]
        for gy, row in enumerate(glyph):
            for gx, cell in enumerate(row):
                if cell == "#":
                    fill_rect(x + gx * scale, y + gy * scale,
                              scale, scale, rgb, a)
        x += 6 * scale


# ------------------------------------------------------------ the poster
def draw_sky():
    vgrad(0, 640, [(0, SKY_TOP), (260, SKY_MID), (430, SKY_LOW),
                   (540, SKY_FIRE), (640, HORIZON)])
    vgrad(641, H - 1, [(641, SEA_TOP), (760, (22, 48, 63)), (H - 1, SEA_BOT)])


def draw_stars():
    for _ in range(150):
        x = RNG.span(30, 994)
        y = RNG.span(42, 500)
        bright = RNG.below(10)
        if bright < 2:
            # a few four-point sparkles
            line(x - 4, y, x + 4, y, CREAM, 1, 200)
            line(x, y - 4, x, y + 4, CREAM, 1, 200)
            put(x, y, CREAM)
        elif bright < 6:
            disc(x, y, 2, CREAM, 220)
        else:
            disc(x, y, 1, CREAM, 130 + RNG.below(90))


def draw_sun():
    cx, cy = 370, 592
    for r, a in ((150, 16), (120, 28), (95, 44)):
        disc(cx, cy, r, SUN_GLOW, a)
    disc(cx, cy, 70, SUN_DISC)
    disc(cx, cy, 56, SUN_CORE)
    # sun path on the water: short warm dashes shrinking with distance
    for k in range(16):
        y = 652 + k * 15
        half = 52 - k * 2 + RNG.below(10)
        a = 110 - k * 5
        if a > 0:
            fill_span(y, cx - half, cx + half, SUN_GLOW, a)
            fill_span(y + 1, cx - half, cx + half, SUN_GLOW, a // 2)


def draw_lanterns():
    """Paper lanterns rising from the sea on the left, small to large."""
    for k in range(12):
        t = k / 11.0
        x = int(150 + t * 380 + 26 * ((k * 7) % 5 - 2))
        y = int(612 - t * 282)
        r = 3 + (k % 3)
        disc(x, y, r + 6, SUN_GLOW, 34)
        disc(x, y, r, LANTERN)
        disc(x, y - 1, max(r - 2, 1), (255, 240, 200))


def draw_cliff_and_lighthouse():
    # dark cliff rising from the sea on the right
    poly([(745, 640), (790, 545), (825, 485), (865, 460),
          (905, 452), (1024, 468), (1024, H - 1), (745, H - 1)], CLIFF)
    # warm rim light along the cliff's top edge
    for (ax, ay), (bx, by) in zip(
            [(745, 640), (790, 545), (825, 485), (865, 460), (905, 452)],
            [(790, 545), (825, 485), (865, 460), (905, 452), (1024, 468)]):
        line(ax, ay, bx, by, SUN_GLOW, 2, 90)
    # lighthouse tower, gently tapered, on the cliff plateau
    poly([(856, 452), (868, 322), (892, 322), (904, 452)], CREAM)
    # two dark bands across the tower
    for y0, y1 in ((402, 420), (352, 366)):
        xa = int(856 + (452 - y0) * 12 / 130) - 1
        xb = int(904 - (452 - y0) * 12 / 130) + 1
        fill_rect(xa, y0, xb - xa, y1 - y0, TOWER_BAND)
    # lantern room + dome, glowing
    disc(880, 309, 20, LANTERN, 55)
    fill_rect(862, 294, 36, 30, (42, 31, 51))
    fill_rect(868, 300, 24, 18, LANTERN)
    disc(880, 294, 15, TOWER_BAND)
    line(880, 280, 880, 294, TOWER_BAND, 3)
    # the beam: a wide soft wedge, then a brighter core, sweeping left
    poly([(862, 300), (380, 282), (380, 324), (862, 314)], BEAM, 42)
    poly([(862, 304), (430, 294), (430, 318), (862, 310)], BEAM, 58)


def draw_boat():
    # one small sailboat on the mirror sea, heading home
    line(300, 712, 300, 652, CLIFF, 3)
    poly([(302, 656), (302, 706), (344, 706)], CREAM)
    poly([(262, 712), (338, 712), (326, 730), (274, 730)], CLIFF)
    disc(300, 650, 8, LANTERN, 50)
    disc(300, 650, 3, LANTERN)
    fill_span(716, 272, 328, CREAM, 60)
    fill_span(736, 282, 318, CREAM, 40)


def draw_waves():
    for k in range(6):
        y0 = 684 + k * 48
        phase = k * 53
        for x in range(40, 740, 8):
            y = y0 + int(4 * ((x + phase) % 64 - 32) / 32)
            line(x, y, x + 8, y0 + int(4 * ((x + 8 + phase) % 64 - 32) / 32),
                 WAVE, 2, 120)


def draw_birds():
    for (bx, by, s) in ((560, 220, 9), (612, 246, 7), (516, 268, 6)):
        line(bx - s, by, bx, by - s // 2, CREAM, 2, 170)
        line(bx, by - s // 2, bx + s, by, CREAM, 2, 170)


def draw_border():
    fill_rect(24, 24, W - 48, 3, CREAM, 220)
    fill_rect(24, H - 27, W - 48, 3, CREAM, 220)
    fill_rect(24, 24, 3, H - 48, CREAM, 220)
    fill_rect(W - 27, 24, 3, H - 48, CREAM, 220)
    fill_rect(36, 36, W - 72, 1, CREAM, 130)
    fill_rect(36, H - 37, W - 72, 1, CREAM, 130)
    fill_rect(36, 36, 1, H - 72, CREAM, 130)
    fill_rect(W - 37, 36, 1, H - 72, CREAM, 130)


def draw_type():
    # the name first: a soft shadow, then the cream face, big across the sky
    draw_text("LUMINARA", 512 + 4, 96 + 5, 16, (42, 21, 48), 150)
    draw_text("LUMINARA", 512, 96, 16, CREAM)
    # one short line beneath it, flanked by thin rules
    draw_text("WHERE THE LIGHT NEVER SETS", 512, 236, 5, CREAM, 235)
    line(58, 253, 108, 253, CREAM, 2, 150)
    line(916, 253, 966, 253, CREAM, 2, 150)


def render():
    global BUF
    BUF = bytearray(W * H * 3)
    draw_sky()
    draw_stars()
    draw_sun()
    draw_lanterns()
    draw_cliff_and_lighthouse()
    draw_boat()
    draw_waves()
    draw_birds()
    draw_border()
    draw_type()
    return encode_png()


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/output/entry.png"
    data = render()
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)
    print(out, len(data), "bytes")


if __name__ == "__main__":
    main()
