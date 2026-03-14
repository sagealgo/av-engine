#!/usr/bin/env python3
"""Unified AV renderer engine. Config-driven, no AI needed for re-renders.

Usage:
    python3 engine.py preset.json                     # render
    python3 engine.py preset.json --seed 42           # override seed
    python3 engine.py preset.json --duration 20       # override duration
    python3 engine.py preset.json --output my.mp4     # override output path
    python3 engine.py --template my_preset.json       # generate starter config
"""

import argparse
import array
import json
import math
import random
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

# ═══════════════════════════════════════════════════════════════════════════
# §1  CONFIG
# ═══════════════════════════════════════════════════════════════════════════

TEMPLATE = {
    "_comment": "AV engine config. Edit and run: python3 engine.py thisfile.json",
    "meta": {
        "name": "My Video",
        "seed": 2026,
        "duration": 15,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "output": "output.mp4",
    },
    "sources": {
        "video": None,
        "images": [],
        "generators": ["constellation_field", "geometry_field", "sun_moon"],
        "text_screens": [],
        "build_mutations": True,
        "build_diffusions": True,
        "build_extreme": True,
    },
    "colors": {
        "bg": [8, 8, 18],
        "primary": [40, 100, 255],
        "secondary": [255, 60, 60],
        "accent": [255, 215, 0],
        "highlight": [230, 230, 245],
        "muted": [140, 140, 160],
        "warm": [255, 180, 100],
    },
    "text": {
        "vibes": ["GOOD VIBES", "✨", "POSITIVITY"],
        "font_size_base": 55,
    },
    "timeline": [
        {"style": "chaos",     "pct": 13, "intensity": [0.9, 1.0],  "texts": ["✨"]},
        {"style": "calm",      "pct": 20, "intensity": [0.05, 0.12]},
        {"style": "growth",    "pct": 20, "intensity": [0.15, 0.55]},
        {"style": "descent",   "pct": 20, "intensity": [0.3, 0.9]},
        {"style": "abyss",     "pct": 14, "intensity": [0.85, 1.0]},
        {"style": "catharsis", "pct": 6,  "intensity": [0.05, 0.08]},
        {"style": "resolve",   "pct": 7,  "intensity": [0.05, 0.1]},
    ],
    "motifs": {
        "enabled": True,
        "items": [
            {"type": "orbiting_square", "color": "primary", "size": 60, "orbit_radius": 300, "speed": 1.5},
            {"type": "orbiting_circle", "color": "secondary", "size": 40, "orbit_radius": 250, "speed": 2.0},
            {"type": "constellation", "color": "highlight", "n_stars": 6, "radius": 100},
            {"type": "drifting_polygons", "count": 3, "sides": 3, "colors": ["primary", "secondary", "accent"]},
        ],
    },
    "audio": {
        "mode": "synth",
        "original_volume": 0.65,
        "synth_volume": 0.45,
        "bpm": 110,
        "layers": ["sub_bass", "hook_stabs", "pad", "arps", "chaos_stabs", "kick", "glitch_pops", "resolve_chord"],
    },
}


def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    merged = json.loads(json.dumps(TEMPLATE))
    _deep_merge(merged, cfg)
    return merged


def _deep_merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def resolve_color(name, palette):
    if isinstance(name, list):
        return tuple(name)
    return tuple(palette.get(name, [255, 255, 255]))


# ═══════════════════════════════════════════════════════════════════════════
# §2  PROCEDURAL GENERATORS
# ═══════════════════════════════════════════════════════════════════════════

def _draw_constellation(draw, cx, cy, radius, n_stars, color, seed_off=0):
    rng = random.Random(seed_off)
    stars = []
    for _ in range(n_stars):
        a = rng.uniform(0, 2 * math.pi)
        r = rng.uniform(radius * 0.2, radius)
        sx, sy = int(cx + r * math.cos(a)), int(cy + r * math.sin(a))
        stars.append((sx, sy))
        draw.ellipse([sx - 3, sy - 3, sx + 3, sy + 3], fill=color)
    for i in range(len(stars) - 1):
        if rng.random() < 0.6:
            draw.line([stars[i], stars[i + 1]], fill=color, width=1)
    if len(stars) > 2 and rng.random() < 0.3:
        draw.line([stars[-1], stars[0]], fill=color, width=1)


def _draw_low_poly(draw, cx, cy, radius, sides, fill_col, outline_col):
    pts = []
    for i in range(sides):
        a = 2 * math.pi * i / sides - math.pi / 2
        r = radius * random.uniform(0.8, 1.0)
        pts.append((int(cx + r * math.cos(a)), int(cy + r * math.sin(a))))
    draw.polygon(pts, fill=fill_col, outline=outline_col)


def gen_dark_bg(W, H, pal):
    bg = resolve_color("bg", pal)
    img = Image.new("RGB", (W, H), bg)
    px = img.load()
    for y in range(H):
        for x in range(W):
            px[x, y] = tuple(max(0, min(255, c + random.randint(-4, 4))) for c in bg)
    return img


def gen_gradient(W, H, pal, c1="bg", c2="primary", angle=135):
    col1, col2 = resolve_color(c1, pal), resolve_color(c2, pal)
    img = Image.new("RGB", (W, H))
    px = img.load()
    rad = math.radians(angle)
    ca, sa = math.cos(rad), math.sin(rad)
    for y in range(H):
        for x in range(W):
            t = max(0.0, min(1.0, ((x / W - 0.5) * ca + (y / H - 0.5) * sa) + 0.5))
            px[x, y] = tuple(int(col1[i] + (col2[i] - col1[i]) * t) for i in range(3))
    return img


def gen_constellation_field(W, H, pal):
    bg = resolve_color("bg", pal)
    hi = resolve_color("highlight", pal)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    for i in range(random.randint(5, 10)):
        _draw_constellation(draw, random.randint(50, W - 50), random.randint(50, H - 50),
                            random.randint(80, 250), random.randint(4, 8), hi, i)
    return img


def gen_geometry_field(W, H, pal):
    bg = resolve_color("bg", pal)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    colors = [resolve_color(c, pal) for c in ["primary", "secondary", "accent", "highlight"]]
    for _ in range(random.randint(8, 18)):
        cx, cy = random.randint(0, W), random.randint(0, H)
        r = random.randint(30, 200)
        sides = random.choice([3, 4, 5, 6, 8])
        col = random.choice(colors)
        dim = tuple(max(0, c - 60) for c in col)
        _draw_low_poly(draw, cx, cy, r, sides, dim, col)
    return img


def gen_iconic_shapes(W, H, pal):
    bg = resolve_color("bg", pal)
    pri = resolve_color("primary", pal)
    sec = resolve_color("secondary", pal)
    hi = resolve_color("highlight", pal)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    s = random.randint(150, 350)
    sx = W // 2 - s // 2 + random.randint(-100, 100)
    sy = H // 3 - s // 2 + random.randint(-100, 100)
    draw.rectangle([sx, sy, sx + s, sy + s], fill=pri, outline=hi, width=3)
    cr = random.randint(80, 200)
    ccx = W // 2 + random.randint(-100, 100)
    ccy = 2 * H // 3 + random.randint(-100, 100)
    draw.ellipse([ccx - cr, ccy - cr, ccx + cr, ccy + cr], fill=sec, outline=hi, width=3)
    return img


def gen_sun_moon(W, H, pal):
    bg = resolve_color("bg", pal)
    acc = resolve_color("accent", pal)
    wrm = resolve_color("warm", pal)
    hi = resolve_color("highlight", pal)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    scx, scy, sr = W // 3, H // 3, 120
    for i in range(12):
        a = 2 * math.pi * i / 12
        draw.line([(scx, scy), (int(scx + (sr + 40) * math.cos(a)), int(scy + (sr + 40) * math.sin(a)))], fill=acc, width=3)
    draw.ellipse([scx - sr, scy - sr, scx + sr, scy + sr], fill=wrm, outline=acc, width=3)
    mcx, mcy, mr = 2 * W // 3, 2 * H // 3, 100
    draw.ellipse([mcx - mr, mcy - mr, mcx + mr, mcy + mr], fill=hi, outline=tuple(max(0, c - 50) for c in hi), width=2)
    draw.ellipse([mcx - mr + 40, mcy - mr - 10, mcx + mr - 20, mcy + mr - 10], fill=bg)
    _draw_constellation(draw, W // 2, H // 2, 300, 12, tuple(max(0, c - 30) for c in hi), 42)
    return img


def gen_wireframe_cube(W, H, pal, rot=0.4, line_color="primary"):
    bg = resolve_color("bg", pal)
    lc = resolve_color(line_color, pal)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2
    s = min(W, H) // 4
    cr, sr = math.cos(rot), math.sin(rot)
    verts = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    def proj(v):
        rx = v[0] * cr - v[2] * sr
        rz = v[0] * sr + v[2] * cr
        sc = 1.2 / (3 + rz)
        return (int(cx + rx * s * sc), int(cy + v[1] * s * sc))
    pts = [proj((v[0] * s, v[1] * s, v[2] * s)) for v in verts]
    for a, b in [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]:
        draw.line([pts[a], pts[b]], fill=lc, width=2)
    return img


def gen_jellyfish(W, H, pal, color="primary", t_offset=0):
    bg = resolve_color("bg", pal)
    jc = resolve_color(color, pal)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    cx, cy, br = W // 2, H // 2 - 100, 180
    draw.ellipse([cx - br, cy - br, cx + br, cy + int(br * 0.6)], outline=jc, width=3)
    for i in range(8):
        x0 = cx - br + i * (br * 2 // 7) + 20
        pts = [(x0 + int(30 * math.sin(j * 0.5 + t_offset + i * 0.3)), cy + int(br * 0.5) + j * 25) for j in range(20)]
        if len(pts) >= 2:
            draw.line(pts, fill=jc, width=2)
    return img


def gen_text_screen(W, H, pal, lines=None):
    bg = resolve_color("bg", pal)
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    if not lines:
        return img
    try:
        from PIL import ImageFont
        fonts = {}
        for _, _, sz in lines:
            if sz not in fonts:
                fonts[sz] = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", sz)
    except Exception:
        fonts = {sz: draw.getfont() for _, _, sz in lines}
    total_h = sum(sz + 20 for _, _, sz in lines)
    y = (H - total_h) // 2
    for text, color_name, sz in lines:
        col = resolve_color(color_name, pal)
        draw.text((W // 2, y), text, fill=col, font=fonts.get(sz), anchor="mt")
        y += sz + 20
    return img


GENERATORS = {
    "dark_bg": gen_dark_bg,
    "gradient": gen_gradient,
    "constellation_field": gen_constellation_field,
    "geometry_field": gen_geometry_field,
    "iconic_shapes": gen_iconic_shapes,
    "sun_moon": gen_sun_moon,
    "wireframe_cube": gen_wireframe_cube,
    "jellyfish": gen_jellyfish,
    "text_screen": gen_text_screen,
}


# ═══════════════════════════════════════════════════════════════════════════
# §3  EFFECTS
# ═══════════════════════════════════════════════════════════════════════════

class FX:
    """All effects take (img, ...) and return img. W, H set at runtime."""
    W = 1080
    H = 1920

    @staticmethod
    def rgb(img, dx=15):
        r, g, b = img.split()
        return Image.merge("RGB", (ImageChops.offset(r, dx, 0), g, ImageChops.offset(b, -dx, 0)))

    @staticmethod
    def glitch(img, n=15, s=50):
        out = img.copy()
        W, H = FX.W, FX.H
        for _ in range(n):
            y = random.randint(0, H - s - 1); h = random.randint(3, s); dx = random.randint(-40, 40)
            strip = img.crop((max(0, -dx), y, min(W, W - dx), y + h))
            out.paste(strip, (max(0, dx), y))
        return out

    @staticmethod
    def hue(img, amt=80):
        hsv = img.convert("HSV"); h, s, v = hsv.split()
        return Image.merge("HSV", (h.point(lambda p: (p + amt) % 256), s, v)).convert("RGB")

    @staticmethod
    def zoom(img, f=1.5, cx=0.5, cy=0.5):
        if f <= 1.0: return img
        W, H = FX.W, FX.H
        nw, nh = int(W / f), int(H / f)
        left, top = int((W - nw) * cx), int((H - nh) * cy)
        return img.crop((left, top, left + nw, top + nh)).resize((W, H), Image.LANCZOS)

    @staticmethod
    def scan(img, gap=3):
        out = img.copy(); draw = ImageDraw.Draw(out)
        for y in range(0, FX.H, gap): draw.line([(0, y), (FX.W, y)], fill=(0, 0, 0), width=1)
        return out

    @staticmethod
    def pix(img, b=24):
        W, H = FX.W, FX.H
        return img.resize((W // b, H // b), Image.NEAREST).resize((W, H), Image.NEAREST)

    @staticmethod
    def poster(img, bits=2):
        mask = (0xFF >> (8 - bits)) << (8 - bits)
        return img.point(lambda p: p & mask)

    @staticmethod
    def solar(img, t=128): return img.point(lambda p: 255 - p if p > t else p)
    @staticmethod
    def edges(img): return img.filter(ImageFilter.FIND_EDGES)
    @staticmethod
    def negate(img): return ImageChops.invert(img)
    @staticmethod
    def contrast(img, f=3.0): return ImageEnhance.Contrast(img).enhance(f)
    @staticmethod
    def sat(img, f=4.0): return ImageEnhance.Color(img).enhance(f)
    @staticmethod
    def bright(img, f=1.5): return ImageEnhance.Brightness(img).enhance(f)
    @staticmethod
    def rotate(img, a=15): return img.rotate(a, resample=Image.BICUBIC, expand=False, fillcolor=(0, 0, 0))

    @staticmethod
    def mirror(img):
        W, H = FX.W, FX.H
        left = img.crop((0, 0, W // 2, H)); out = img.copy()
        out.paste(left.transpose(Image.FLIP_LEFT_RIGHT), (W // 2, 0)); return out

    @staticmethod
    def quarter(img):
        W, H = FX.W, FX.H
        q = img.crop((0, 0, W // 2, H // 2)); out = Image.new("RGB", (W, H))
        out.paste(q.resize((W // 2, H // 2)), (0, 0))
        out.paste(q.transpose(Image.FLIP_LEFT_RIGHT).resize((W // 2, H // 2)), (W // 2, 0))
        out.paste(q.transpose(Image.FLIP_TOP_BOTTOM).resize((W // 2, H // 2)), (0, H // 2))
        out.paste(q.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM).resize((W // 2, H // 2)), (W // 2, H // 2))
        return out

    @staticmethod
    def pixel_sort(img, intensity=0.5):
        W, H = FX.W, FX.H
        out = img.copy(); px = out.load()
        for _ in range(max(1, int(H * intensity * 0.2))):
            y = random.randint(0, H - 1); start = random.randint(0, W // 3); end = random.randint(W // 2, W)
            pixels = sorted([(px[x, y], sum(px[x, y])) for x in range(start, end)], key=lambda p: p[1])
            for i, x in enumerate(range(start, end)): px[x, y] = pixels[i][0]
        return out

    @staticmethod
    def spiral_warp(img, t, strength=10):
        W, H = FX.W, FX.H
        out = Image.new("RGB", (W, H))
        for x in range(W):
            shift = int(strength * math.sin(2 * math.pi * x / W * 3 + t * 6))
            col = img.crop((x, 0, x + 1, H)); py = shift % H
            out.paste(col, (x, py))
            if py > 0: out.paste(col.crop((0, 0, 1, H - py)), (x, py - H))
        return out

    @staticmethod
    def recursive_zoom(img, depth=3, t=0):
        W, H = FX.W, FX.H
        out = img.copy()
        for d in range(depth):
            scale = 0.5 ** (d + 1); sw, sh = int(W * scale), int(H * scale)
            if sw < 4 or sh < 4: break
            small = FX.hue(img, int(40 * (d + 1) + t * 30)).resize((sw, sh), Image.LANCZOS)
            out.paste(small, (W // 2 - sw // 2, H // 2 - sh // 2))
        return out

    @staticmethod
    def feedback(img, prev, strength=0.5):
        if prev is None: return img
        return Image.blend(img, prev, strength)

    @staticmethod
    def nine_grid(imgs, t=0):
        W, H = FX.W, FX.H
        out = Image.new("RGB", (W, H)); gw, gh = W // 3, H // 3
        for i in range(min(9, len(imgs))):
            cell = imgs[(i + int(t * 3)) % len(imgs)].resize((gw, gh), Image.LANCZOS)
            out.paste(cell, ((i % 3) * gw, (i // 3) * gh))
        return out

    @staticmethod
    def text(img, txt, size=80):
        out = img.copy(); draw = ImageDraw.Draw(out)
        W, H = FX.W, FX.H
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
        except Exception:
            font = None
        x = random.randint(20, max(21, W - size * len(txt) // 3))
        y = random.randint(20, max(21, H - size - 20))
        draw.text((x + 3, y + 3), txt, fill=(0, 0, 0), font=font)
        draw.text((x, y), txt, fill=(255, 255, 255), font=font)
        return out


# ═══════════════════════════════════════════════════════════════════════════
# §4  TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════

def tr_crossfade(a, b, t): return Image.blend(a, b, t)

def tr_datamosh(a, b, t):
    out = a.copy()
    for _ in range(int(5 + t * 25)):
        y = random.randint(0, FX.H - 30); h = random.randint(5, 50)
        out.paste(b.crop((0, y, FX.W, min(FX.H, y + h))), (random.randint(-25, 25), y))
    return out

def tr_wipe(a, b, t):
    sx = int(FX.W * t); out = a.copy(); out.paste(b.crop((sx, 0, FX.W, FX.H)), (sx, 0)); return out

def tr_zoom_through(a, b, t):
    if t < 0.5: return FX.zoom(a, 1.0 + t * 3)
    return FX.zoom(b, max(1.0, 4.0 - (t - 0.5) * 6))

def tr_pixel_dissolve(a, b, t):
    out = a.copy(); block = max(4, int(40 * (1 - t)))
    for by in range(0, FX.H, block):
        for bx in range(0, FX.W, block):
            if random.random() < t:
                out.paste(b.crop((bx, by, min(FX.W, bx + block), min(FX.H, by + block))), (bx, by))
    return out

def tr_swirl(a, b, t): return Image.blend(FX.hue(a, int(t * 60)), b, t)
def tr_triple_layer(a, b, t): return Image.blend(Image.blend(a, FX.negate(a), min(1.0, t * 2)), b, t)

def tr_channel_swap(a, b, t):
    ar, ag, ab = a.split(); br, bg, bb = b.split()
    if t < 0.33: return Image.merge("RGB", (br, ag, ab))
    elif t < 0.66: return Image.merge("RGB", (br, bg, ab))
    return Image.merge("RGB", (br, bg, bb))

ALL_TR = [tr_crossfade, tr_datamosh, tr_wipe, tr_zoom_through, tr_pixel_dissolve, tr_swirl, tr_triple_layer, tr_channel_swap]


# ═══════════════════════════════════════════════════════════════════════════
# §5  MOTIF OVERLAY
# ═══════════════════════════════════════════════════════════════════════════

def apply_motifs(img, t_val, motif_cfg, palette):
    if not motif_cfg.get("enabled"):
        return img
    out = img.copy()
    draw = ImageDraw.Draw(out)
    W, H = FX.W, FX.H

    for item in motif_cfg.get("items", []):
        mtype = item["type"]

        if mtype == "orbiting_square":
            col = resolve_color(item.get("color", "primary"), palette)
            sz = item.get("size", 60) + int(20 * math.sin(t_val * 3))
            orb = item.get("orbit_radius", 300)
            spd = item.get("speed", 1.5)
            cx = int(W / 2 + orb * math.cos(t_val * spd))
            cy = int(H / 3 + (orb * 0.66) * math.sin(t_val * spd * 1.2))
            draw.rectangle([cx - sz // 2, cy - sz // 2, cx + sz // 2, cy + sz // 2], outline=col, width=4)

        elif mtype == "orbiting_circle":
            col = resolve_color(item.get("color", "secondary"), palette)
            r = item.get("size", 40) + int(15 * math.sin(t_val * 2.5))
            orb = item.get("orbit_radius", 250)
            spd = item.get("speed", 2.0)
            cx = int(W / 2 + orb * math.cos(t_val * spd + math.pi))
            cy = int(2 * H / 3 + (orb * 0.72) * math.sin(t_val * spd * 0.87 + 1))
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=4)

        elif mtype == "constellation":
            col = resolve_color(item.get("color", "highlight"), palette)
            n = item.get("n_stars", 6)
            rad = item.get("radius", 100)
            drift_x = int(W * 0.7 + 100 * math.sin(t_val))
            drift_y = int(H * 0.2 + 50 * math.cos(t_val))
            _draw_constellation(draw, drift_x, drift_y, rad, n, col, int(t_val * 10))

        elif mtype == "drifting_polygons":
            count = item.get("count", 3)
            sides = item.get("sides", 3)
            cols = [resolve_color(c, palette) for c in item.get("colors", ["primary"])]
            for i in range(count):
                tx = int(W * (0.2 + 0.6 * ((i + t_val * 0.3) % 1.0)))
                ty = int(H * (0.1 + 0.8 * ((i * 0.37 + t_val * 0.15) % 1.0)))
                _draw_low_poly(draw, tx, ty, 25 + i * 10, sides, None, cols[i % len(cols)])

    return out


# ═══════════════════════════════════════════════════════════════════════════
# §6  SOURCE PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def extract_video_frames(video_path, W, H, tmpdir):
    out_pattern = str(tmpdir / "src_%03d.jpg")
    subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-vf", f"fps=3,scale={W}:{H}",
                    "-q:v", "3", out_pattern], check=True, capture_output=True)
    audio_path = tmpdir / "audio_orig.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le",
                        "-ar", "44100", "-ac", "1", str(audio_path)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        audio_path = None
    frames = sorted(tmpdir.glob("src_*.jpg"))
    return [Image.open(f).convert("RGB").resize((W, H), Image.LANCZOS) for f in frames[:40]], audio_path


def build_sources(cfg, W, H, palette, tmpdir):
    src_cfg = cfg["sources"]
    originals = []
    audio_path = None

    # Video frames
    if src_cfg.get("video"):
        vpath = Path(src_cfg["video"]).expanduser()
        if vpath.exists():
            print(f"  Extracting frames from {vpath}...")
            vid_imgs, audio_path = extract_video_frames(vpath, W, H, tmpdir)
            originals.extend(vid_imgs)
            print(f"  {len(vid_imgs)} video frames extracted")

    # Image files
    for p in src_cfg.get("images", []):
        pp = Path(p).expanduser()
        if pp.exists():
            originals.append(Image.open(pp).convert("RGB").resize((W, H), Image.LANCZOS))

    # Procedural generators
    for gen_spec in src_cfg.get("generators", []):
        if isinstance(gen_spec, str):
            fn = GENERATORS.get(gen_spec)
            if fn:
                originals.append(fn(W, H, palette))
        elif isinstance(gen_spec, dict):
            fn = GENERATORS.get(gen_spec.get("type", ""))
            if fn:
                params = {k: v for k, v in gen_spec.items() if k != "type"}
                originals.append(fn(W, H, palette, **params))

    # Text screens
    for ts in src_cfg.get("text_screens", []):
        originals.append(gen_text_screen(W, H, palette, lines=ts.get("lines", [])))

    if not originals:
        originals.append(gen_dark_bg(W, H, palette))

    # Mutation tiers
    mutations = []
    if src_cfg.get("build_mutations", True):
        for img in originals:
            mutations.append(FX.hue(img, 80))
            mutations.append(FX.negate(img))
            mutations.append(FX.sat(FX.contrast(img, 2.0), 3.0))

    diffused = []
    if src_cfg.get("build_diffusions", True):
        n = len(originals)
        for i in range(n):
            j = (i + 3) % n
            a, b = originals[i], originals[j]
            for t in [0.3, 0.6]:
                ba = a.filter(ImageFilter.GaussianBlur(radius=int(t * 8)))
                bb = b.filter(ImageFilter.GaussianBlur(radius=int((1 - t) * 8)))
                diffused.append(Image.blend(ba, bb, t).filter(ImageFilter.SHARPEN))

    gen2 = []
    if diffused:
        for i in range(0, len(diffused), 3):
            gen2.append(FX.edges(diffused[i]))
            if i + 1 < len(diffused):
                gen2.append(Image.blend(diffused[i], diffused[min(i + 1, len(diffused) - 1)], 0.5))

    extreme = []
    if src_cfg.get("build_extreme", True):
        for img in random.sample(originals, min(12, len(originals))):
            extreme.append(FX.poster(FX.sat(FX.contrast(img, 3.5), 6.0), 2))
            extreme.append(FX.solar(FX.hue(img, random.randint(40, 200)), 120))

    pools = {"originals": originals, "mutations": mutations, "diffused": diffused, "gen2": gen2, "extreme": extreme}
    return pools, audio_path


# ═══════════════════════════════════════════════════════════════════════════
# §7  TIMELINE
# ═══════════════════════════════════════════════════════════════════════════

STYLE_SEG_LEN = {"chaos": 7, "calm": 17, "growth": 12, "descent": 8, "abyss": 8,
                 "catharsis": 999, "resolve": 15, "presentation": 45, "meditation": 30}


def _source_pool_for_style(style, pools):
    all_imgs = pools["originals"] + pools["mutations"] + pools["diffused"] + pools["gen2"] + pools["extreme"]
    mapping = {
        "chaos": pools["extreme"] + pools["mutations"],
        "calm": pools["originals"],
        "growth": pools["originals"] + pools["diffused"],
        "descent": pools["diffused"] + pools["gen2"] + pools["extreme"] + pools["mutations"],
        "abyss": all_imgs,
        "catharsis": pools["originals"],
        "resolve": pools["originals"],
        "presentation": pools["originals"],
        "meditation": pools["originals"] + pools["diffused"],
    }
    pool = mapping.get(style, all_imgs)
    return pool if pool else all_imgs


def build_timeline(cfg, pools, total_frames):
    all_imgs = pools["originals"] + pools["mutations"] + pools["diffused"] + pools["gen2"] + pools["extreme"]
    timeline_cfg = cfg["timeline"]
    vibes = cfg["text"]["vibes"]
    timeline = []

    # Convert pct to frame ranges
    acts = []
    frame_cursor = 0
    total_pct = sum(a.get("pct", 10) for a in timeline_cfg)
    for act_cfg in timeline_cfg:
        pct = act_cfg.get("pct", 10) / total_pct
        n_frames = int(total_frames * pct)
        acts.append((frame_cursor, frame_cursor + n_frames, act_cfg))
        frame_cursor += n_frames
    if acts:
        last = acts[-1]
        acts[-1] = (last[0], total_frames, last[2])

    for act_idx, (start, end, act_cfg) in enumerate(acts):
        style = act_cfg.get("style", "calm")
        intensity_lo, intensity_hi = act_cfg.get("intensity", [0.3, 0.7])
        texts = act_cfg.get("texts", [])
        seg_len = STYLE_SEG_LEN.get(style, 15)
        pool = _source_pool_for_style(style, pools)

        if style in ("catharsis", "resolve"):
            # Single or double segment for these quiet acts
            mid = (start + end) // 2
            seg1_text = [texts[0]] if texts else []
            seg2_text = [texts[1]] if len(texts) > 1 else []
            timeline.append({
                "start": start, "end": mid if end - start > 20 else end, "act": act_idx + 1,
                "style": style, "primary": pool[-1] if pool else all_imgs[0],
                "secondary": pool[0] if pool else all_imgs[0], "blend_pool": pool or all_imgs,
                "fx_intensity": intensity_lo, "trans_fn": tr_crossfade, "trans_frames": 15, "text": seg1_text,
            })
            if end - start > 20:
                timeline.append({
                    "start": mid, "end": end, "act": act_idx + 1, "style": style,
                    "primary": pool[0] if pool else all_imgs[0],
                    "secondary": Image.new("RGB", (FX.W, FX.H), resolve_color("bg", cfg["colors"])),
                    "blend_pool": pool or all_imgs, "fx_intensity": intensity_lo,
                    "trans_fn": tr_crossfade, "trans_frames": 12, "text": seg2_text,
                })
        else:
            seg_idx = 0
            f = start
            while f < end:
                seg_end = min(f + seg_len, end)
                pct_in_act = (f - start) / max(1, end - start)
                intensity = intensity_lo + (intensity_hi - intensity_lo) * pct_in_act
                txt = [texts[seg_idx % len(texts)]] if texts and texts[seg_idx % len(texts)] else []
                timeline.append({
                    "start": f, "end": seg_end, "act": act_idx + 1, "style": style,
                    "primary": random.choice(pool) if pool else all_imgs[0],
                    "secondary": random.choice(pool) if pool else all_imgs[0],
                    "blend_pool": pool or all_imgs, "fx_intensity": intensity,
                    "trans_fn": random.choice(ALL_TR), "trans_frames": min(seg_len // 2, 8),
                    "text": txt,
                })
                f = seg_end
                seg_idx += 1

    return timeline


# ═══════════════════════════════════════════════════════════════════════════
# §8  FRAME RENDERER
# ═══════════════════════════════════════════════════════════════════════════

def render_frame(fi, seg, all_imgs, prev_frame, motif_cfg, palette, total, fps):
    if not seg:
        return prev_frame or Image.new("RGB", (FX.W, FX.H), (0, 0, 0))

    local_pct = (fi - seg["start"]) / max(1, seg["end"] - seg["start"] - 1)
    intensity = seg["fx_intensity"]
    style = seg["style"]
    img = seg["primary"].copy()
    t_val = fi / fps

    # Transition zone
    ts = seg["end"] - seg["trans_frames"]
    if fi >= ts and seg["trans_fn"]:
        t = min(1.0, max(0.0, (fi - ts) / max(1, seg["trans_frames"])))
        img = seg["trans_fn"](img, seg["secondary"], t)

    # --- STYLE RENDERERS ---

    if style == "chaos":
        img = FX.glitch(img, n=int(25 + intensity * 30), s=int(50 + intensity * 60))
        img = FX.rgb(img, int(25 + intensity * 40))
        if random.random() < 0.5: img = FX.negate(img)
        if random.random() < 0.4: img = FX.pix(img, max(4, random.randint(4, 14)))
        if random.random() < 0.5: img = tr_datamosh(img, random.choice(seg["blend_pool"]), 0.5 + random.random() * 0.4)
        img = FX.sat(img, 3 + intensity * 3); img = FX.contrast(img, 1.5 + intensity)
        if random.random() < 0.3: img = FX.mirror(img)
        if random.random() < 0.25: img = FX.quarter(img)
        img = FX.zoom(img, 1.0 + 0.6 * abs(math.sin(fi * 0.4)), random.random(), random.random())

    elif style == "calm":
        zoom = 1.0 + local_pct * 0.08
        img = FX.zoom(img, zoom, 0.5 + 0.06 * math.sin(fi * 0.04), 0.5)
        img = FX.bright(img, 1.0 + 0.06 * math.sin(fi * 0.06))
        if random.random() < 0.12: img = FX.scan(img, 4)

    elif style == "growth":
        img = FX.rgb(img, int(2 + intensity * 10))
        if random.random() < 0.25: img = Image.blend(img, random.choice(seg["blend_pool"]), 0.15 + intensity * 0.15)
        if random.random() < intensity * 0.3: img = FX.glitch(img, n=int(3 + intensity * 10), s=int(10 + intensity * 25))
        if random.random() < 0.15: img = FX.pixel_sort(img, intensity * 0.4)
        if random.random() < 0.1: img = FX.mirror(img)
        img = FX.zoom(img, 1.0 + 0.1 * abs(math.sin(fi * 0.07)))
        img = FX.sat(img, 1.2 + intensity * 0.8)
        if random.random() < 0.15: img = FX.hue(img, int(20 * intensity))

    elif style == "descent":
        img = FX.glitch(img, n=int(12 + intensity * 20), s=int(25 + intensity * 45))
        img = FX.rgb(img, int(10 + intensity * 25))
        if random.random() < 0.4: img = tr_datamosh(img, random.choice(seg["blend_pool"]), 0.3 + intensity * 0.35)
        if random.random() < 0.25: img = FX.pix(img, max(4, int(18 * (1 - intensity))))
        if random.random() < 0.15: img = FX.negate(img)
        if random.random() < 0.2: img = FX.nine_grid(random.sample(all_imgs, min(9, len(all_imgs))), t_val)
        img = FX.sat(img, 1.5 + intensity * 2.5); img = FX.hue(img, int(fi * 2) % 256)
        if random.random() < 0.2: img = FX.spiral_warp(img, t_val, int(5 + intensity * 12))
        if prev_frame: img = FX.feedback(img, prev_frame, 0.1)

    elif style == "abyss":
        img = FX.glitch(img, n=int(20 + intensity * 30), s=int(40 + intensity * 80))
        img = FX.rgb(img, int(20 + intensity * 40))
        if random.random() < 0.5: img = tr_datamosh(img, random.choice(seg["blend_pool"]), 0.5 + random.random() * 0.4)
        if random.random() < 0.3: img = FX.pix(img, max(3, random.randint(3, 10)))
        if random.random() < 0.2: img = FX.negate(img)
        if random.random() < 0.2: img = FX.quarter(img)
        if random.random() < 0.2: img = FX.nine_grid(random.sample(all_imgs, min(9, len(all_imgs))), t_val)
        img = FX.sat(img, 2 + intensity * 4); img = FX.hue(img, int(fi * 3) % 256)
        if random.random() < 0.2: img = FX.recursive_zoom(img, 4, t_val)
        if random.random() < 0.25: img = FX.spiral_warp(img, t_val, int(10 + intensity * 20))
        if prev_frame: img = FX.feedback(img, prev_frame, 0.15)

    elif style == "catharsis":
        img = FX.bright(img, 1.1 + local_pct * 0.15)
        img = FX.zoom(img, 1.0 + local_pct * 0.05, 0.5, 0.5)

    elif style == "resolve":
        bg_img = Image.new("RGB", (FX.W, FX.H), resolve_color("bg", palette))
        img = Image.blend(img, bg_img, min(0.5, local_pct * 0.5))

    elif style == "presentation":
        zoom = 1.0 + 0.06 * math.sin(fi * 0.05); img = FX.zoom(img, zoom)
        img = FX.bright(img, 1.0 + 0.05 * math.sin(fi * 0.07))
        if random.random() < 0.1: img = FX.rgb(img, 2)

    elif style == "meditation":
        zoom = 1.0 + 0.08 * math.sin(fi * 0.03)
        img = FX.zoom(img, zoom, 0.5, 0.5 + 0.05 * math.sin(fi * 0.02))
        if random.random() < 0.2: img = FX.rgb(img, int(intensity * 5))
        if prev_frame: img = FX.feedback(img, prev_frame, 0.1)
        img = FX.bright(img, 1.0 + 0.08 * math.sin(fi * 0.04))

    # Motifs
    img = apply_motifs(img, t_val, motif_cfg, palette)

    # Text
    font_base = int(cfg_global.get("text", {}).get("font_size_base", 55))
    for txt in seg.get("text", []):
        if random.random() < 0.65 or style in ("chaos", "abyss"):
            img = FX.text(img, txt, size=font_base + seg["act"] * 6)

    return img


# ═══════════════════════════════════════════════════════════════════════════
# §9  AUDIO SYNTHESIS
# ═══════════════════════════════════════════════════════════════════════════

def synth_audio(path, cfg, dur, sr=44100):
    N = int(sr * dur); buf = [0.0] * N
    audio_cfg = cfg.get("audio", {})
    bpm = audio_cfg.get("bpm", 110)
    layers = set(audio_cfg.get("layers", []))

    def sine(f, t): return math.sin(2 * math.pi * f * t)
    def saw(f, t): return 2.0 * ((f * t) % 1.0) - 1.0
    def sq(f, t): return 1.0 if (f * t) % 1.0 < 0.5 else -1.0
    def tri(f, t): return 4.0 * abs((f * t) % 1.0 - 0.5) - 1.0
    def env_ad(t, attack, decay):
        if t < 0: return 0.0
        if t < attack: return t / attack if attack > 0 else 1.0
        e = t - attack; return max(0, 1.0 - e / decay) if e < decay and decay > 0 else 0.0
    def add_note(start_s, dur_s, freq, vol, wave_fn, atk=0.01, dec=0.5, detune=0.0, crush=0):
        si = max(0, int(start_s * sr)); ei = min(N, int((start_s + dur_s) * sr))
        for i in range(si, ei):
            t = (i - si) / sr; s = wave_fn(freq, t)
            if detune: s = (s + wave_fn(freq * (1 + detune), t)) / 2
            env = env_ad(t, atk, dec * dur_s)
            if crush > 0: levels = 2 ** crush; s = int(s * levels) / levels
            buf[i] += s * env * vol

    # Energy curve from timeline config
    tl = cfg.get("timeline", [])
    total_pct = sum(a.get("pct", 10) for a in tl)
    act_boundaries = []
    cursor = 0.0
    for a in tl:
        p = a.get("pct", 10) / total_pct
        lo, hi = a.get("intensity", [0.3, 0.7])
        act_boundaries.append((cursor, cursor + p, lo, hi, a.get("style", "calm")))
        cursor += p

    def energy_at(t_sec):
        prog = t_sec / dur
        for start, end, lo, hi, style in act_boundaries:
            if start <= prog < end:
                local = (prog - start) / (end - start) if end > start else 0
                return lo + (hi - lo) * local
        return 0.3

    if "sub_bass" in layers:
        for i in range(N):
            t = i / sr; e = energy_at(t)
            buf[i] += sine(55 + 5 * math.sin(t * 0.2), t) * 0.07 * max(0.2, e)

    if "hook_stabs" in layers:
        hook_end = dur * 0.15
        for ni in range(30):
            t0 = ni * 0.14
            if t0 >= hook_end: break
            add_note(t0, 0.06, random.choice([220, 330, 440]) * random.choice([1, 2]), 0.07, saw, atk=0.001, dec=0.3, crush=3)

    if "pad" in layers:
        pad_start = dur * 0.15; pad_end = dur * 0.6
        for i in range(max(0, int(pad_start * sr)), min(N, int(pad_end * sr))):
            t = i / sr; prog = (t - pad_start) / (pad_end - pad_start)
            env = min(1.0, prog * 3) * max(0, 1.0 - max(0, prog - 0.8) * 5)
            for f in [130.81, 164.81, 196, 261.63]:
                buf[i] += sine(f, t) * 0.025 * env

    if "arps" in layers:
        arps = [261.63, 329.63, 392, 440, 523.25, 659.25]
        arp_start = dur * 0.33; arp_end = dur * 0.73
        ni = 0
        while True:
            t0 = arp_start + ni * (60 / bpm) / 2
            if t0 >= arp_end: break
            add_note(t0, 0.12, arps[ni % len(arps)] * random.choice([1, 1, 2]), 0.04, tri, atk=0.005, dec=0.5, detune=0.003)
            ni += 1

    if "chaos_stabs" in layers:
        chaos_start = dur * 0.73; chaos_end = dur * 0.87
        for ni in range(80):
            t0 = chaos_start + ni * 0.05
            if t0 >= chaos_end: break
            add_note(t0, 0.04, random.uniform(100, 2500), 0.03, random.choice([saw, sq]), atk=0.001, dec=0.2, crush=random.randint(2, 4))

    if "kick" in layers:
        t = 0.0
        while t < dur - 0.2:
            e = energy_at(t)
            interval = 60 / (90 + e * 120)
            add_note(t, 0.03, 50, 0.08 * e, sine, atk=0.001, dec=0.3)
            t += interval

    if "glitch_pops" in layers:
        for _ in range(100):
            t0 = random.uniform(0, dur - 0.1); e = energy_at(t0)
            if random.random() > (0.6 if e > 0.7 else 0.12): continue
            add_note(t0, random.uniform(0.008, 0.025), random.uniform(300, 6000), 0.025, sq, atk=0.001, dec=0.15, crush=3)

    if "resolve_chord" in layers:
        res_start = dur * 0.93
        for i in range(max(0, int(res_start * sr)), min(N, int(dur * sr))):
            t = i / sr; prog = (t - res_start) / (dur - res_start) if dur > res_start else 0
            env = min(1.0, prog * 5) * max(0, 1.0 - prog * 0.4)
            buf[i] += sine(261.63, t) * 0.05 * env + sine(329.63, t) * 0.035 * env + sine(392, t) * 0.03 * env

    peak = max(abs(s) for s in buf) or 1.0; scale = 0.85 / peak
    samples = array.array("h", [max(-32767, min(32767, int(s * scale * 32767))) for s in buf])
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(samples.tobytes())


def mix_audio(orig_wav, synth_wav, out_wav, orig_vol, synth_vol):
    subprocess.run([
        "ffmpeg", "-y", "-i", str(orig_wav), "-i", str(synth_wav),
        "-filter_complex", f"[0:a]volume={orig_vol}[a1];[1:a]volume={synth_vol}[a2];[a1][a2]amix=inputs=2:duration=shortest[out]",
        "-map", "[out]", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", str(out_wav)
    ], check=True, capture_output=True)


# ═══════════════════════════════════════════════════════════════════════════
# §10  MAIN
# ═══════════════════════════════════════════════════════════════════════════

cfg_global = {}


def render(cfg, seed_override=None, dur_override=None, out_override=None):
    global cfg_global
    cfg_global = cfg
    t0 = time.time()

    meta = cfg["meta"]
    seed = seed_override or meta.get("seed", 2026)
    dur = dur_override or meta.get("duration", 15)
    fps = meta.get("fps", 30)
    W = meta.get("width", 1080)
    H = meta.get("height", 1920)
    total = fps * dur
    out = Path(out_override or meta.get("output", "output.mp4"))

    random.seed(seed)
    FX.W, FX.H = W, H
    palette = cfg.get("colors", TEMPLATE["colors"])

    print(f"AV Engine: {W}x{H} @ {fps}fps, {dur}s ({total} frames)")
    print(f"Seed: {seed} | Output: {out}")

    fdir = Path("_engine_frames")
    tmpdir = Path("_engine_tmp")
    if fdir.exists(): shutil.rmtree(fdir)
    if tmpdir.exists(): shutil.rmtree(tmpdir)
    fdir.mkdir(); tmpdir.mkdir()

    # Sources
    print("Building sources...")
    pools, orig_audio = build_sources(cfg, W, H, palette, tmpdir)
    n_src = sum(len(v) for v in pools.values())
    print(f"  {n_src} source images ({len(pools['originals'])} orig, {len(pools['mutations'])} mut, "
          f"{len(pools['diffused'])} diff, {len(pools['gen2'])} gen2, {len(pools['extreme'])} extreme)")

    # Timeline
    print("Building timeline...")
    timeline = build_timeline(cfg, pools, total)
    print(f"  {len(timeline)} segments")

    all_imgs = pools["originals"] + pools["mutations"] + pools["diffused"] + pools["gen2"] + pools["extreme"]

    # Render frames
    print("Rendering frames...")
    prev_frame = None
    motif_cfg = cfg.get("motifs", {"enabled": False})
    for fi in range(total):
        seg = None
        for s in timeline:
            if s["start"] <= fi < s["end"]: seg = s; break
        frame = render_frame(fi, seg, all_imgs, prev_frame, motif_cfg, palette, total, fps)
        frame.save(fdir / f"f_{fi:05d}.jpg", quality=90)
        prev_frame = frame
        if fi % fps == 0:
            elapsed = time.time() - t0; pct = (fi + 1) / total * 100
            eta = elapsed / (fi + 1) * (total - fi - 1) if fi > 0 else 0
            print(f"  Frame {fi + 1}/{total} ({pct:.0f}%) — {elapsed:.1f}s, ~{eta:.0f}s remain")
    print(f"  {total} frames in {time.time() - t0:.1f}s")

    # Audio
    audio_cfg = cfg.get("audio", {})
    mode = audio_cfg.get("mode", "synth")
    print("Synthesizing audio...")
    synth_path = fdir / "synth.wav"
    synth_audio(synth_path, cfg, dur)

    if mode == "mix" and orig_audio and orig_audio.exists():
        print("Mixing with original audio...")
        final_audio = fdir / "mixed.wav"
        mix_audio(orig_audio, synth_path, final_audio, audio_cfg.get("original_volume", 0.65), audio_cfg.get("synth_volume", 0.45))
    elif mode == "original" and orig_audio and orig_audio.exists():
        final_audio = orig_audio
    else:
        final_audio = synth_path

    # Encode
    print("Encoding video...")
    vid_tmp = fdir / "video_only.mp4"
    subprocess.run(["ffmpeg", "-y", "-framerate", str(fps), "-i", str(fdir / "f_%05d.jpg"),
                    "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                    "-movflags", "+faststart", str(vid_tmp)], check=True, capture_output=True)
    print("Muxing audio...")
    subprocess.run(["ffmpeg", "-y", "-i", str(vid_tmp), "-i", str(final_audio),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                    str(out)], check=True, capture_output=True)

    # Cleanup
    shutil.rmtree(fdir)
    shutil.rmtree(tmpdir, ignore_errors=True)
    total_time = time.time() - t0
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"\nDone! {out} ({size_mb:.1f}MB) in {total_time:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="AV Engine — config-driven video renderer")
    parser.add_argument("config", nargs="?", help="Path to JSON config file")
    parser.add_argument("--template", metavar="PATH", help="Generate a starter config template")
    parser.add_argument("--seed", type=int, help="Override seed")
    parser.add_argument("--duration", type=int, help="Override duration (seconds)")
    parser.add_argument("--output", help="Override output path")
    args = parser.parse_args()

    if args.template:
        with open(args.template, "w") as f:
            json.dump(TEMPLATE, f, indent=2)
        print(f"Template written to {args.template}")
        print("Edit the JSON, then run: python3 engine.py " + args.template)
        return

    if not args.config:
        parser.print_help()
        return

    cfg = load_config(args.config)
    render(cfg, seed_override=args.seed, dur_override=args.duration, out_override=args.output)


if __name__ == "__main__":
    main()
