# -*- coding: utf-8 -*-
"""Per-pixel-alpha rendering for the FlowBar pill (Windows layered window).

Why this exists: the original pill used a tk.Canvas with a Windows colorkey
(`-transparentcolor`). That gives BINARY transparency — a pixel is either fully
opaque or fully transparent — so the anti-aliased rounded edges and the soft
glow leave a visible 1px fringe ("обрамление"), and tk.Canvas itself doesn't
anti-alias, so the rounded caps look jagged ("пиксели").

This module renders the pill into a PIL RGBA image at SUPER-SAMPLED resolution
(SS×) and downsamples with LANCZOS → smooth, anti-aliased edges with a real
alpha channel. The image is pushed to a WS_EX_LAYERED window via
UpdateLayeredWindow, which honours per-pixel alpha → no colorkey, no fringe.

The drawing primitives here mirror the old _draw_* methods (pill, glow,
waveform, spinner, dot, mic, X, timer/label text, cancel/confirm buttons) so
the visual result matches what users already know, just smooth.

Pure rendering + the Win32 blit. The FlowBar owns geometry, animation state and
hit-testing; it calls render_pill(...) → Image and blit_layered(hwnd, img, x, y).
"""
from __future__ import annotations

import ctypes
import math
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageDraw

# Super-sampling factor: draw at SS× then downscale for anti-aliasing.
SS = 3


# ── Win32 layered-window blit ────────────────────────────────────────────────

_ULW_ALPHA = 0x02
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


def make_layered(hwnd: int) -> None:
    """Add WS_EX_LAYERED to a window so UpdateLayeredWindow can drive it."""
    user32 = ctypes.windll.user32
    ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex | _WS_EX_LAYERED)


def blit_layered(hwnd: int, image: Image.Image, x: int, y: int) -> None:
    """Push a PIL RGBA image to a layered window at screen position (x, y),
    honouring per-pixel alpha. Premultiplies alpha as UpdateLayeredWindow wants."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    w, h = image.size
    # UpdateLayeredWindow expects premultiplied BGRA, top-down. Do it
    # vectorized with numpy — a per-pixel Python loop would tank the frame rate.
    arr = np.frombuffer(image.convert("RGBA").tobytes("raw", "RGBA"),
                        dtype=np.uint8).reshape(h, w, 4).astype(np.uint16)
    a = arr[:, :, 3:4]
    arr[:, :, :3] = arr[:, :, :3] * a // 255          # premultiply
    bgra = arr[:, :, [2, 1, 0, 3]].astype(np.uint8)   # RGBA → BGRA
    raw = bgra.tobytes()

    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h            # negative → top-down DIB
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0        # BI_RGB

    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0,
                                  ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, bytes(raw), len(raw))
    old = gdi32.SelectObject(mem_dc, hbmp)

    size = _SIZE(w, h)
    src = _POINT(0, 0)
    dst = _POINT(int(x), int(y))
    blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)

    user32.UpdateLayeredWindow(
        hwnd, screen_dc, ctypes.byref(dst), ctypes.byref(size),
        mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), _ULW_ALPHA)

    gdi32.SelectObject(mem_dc, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)


# ── PIL drawing helpers (super-sampled, mirror the old _draw_* primitives) ────

def _hex(c: str) -> tuple[int, int, int]:
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


def _rounded(draw: ImageDraw.ImageDraw, x, y, w, h, fill, alpha=255):
    """A pill/capsule = rounded rectangle with radius h/2."""
    r = h / 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=r,
                           fill=(fill[0], fill[1], fill[2], alpha))


def render_pill(spec: dict) -> Image.Image:
    """Render the whole pill for one frame into an RGBA image.

    `spec` carries everything the FlowBar computed for this frame (sizes,
    colours, animation phases, state, bars, etc). Kept as a plain dict so the
    rendering stays decoupled from the widget. Returns a downsampled,
    anti-aliased RGBA image sized (canvas_w, canvas_h)."""
    cw, ch = spec["canvas_w"], spec["canvas_h"]
    big = Image.new("RGBA", (cw * SS, ch * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)

    s = SS
    ox = spec["ox"] * s
    oy = spec["oy"] * s
    pw = spec["pill_w"] * s
    ph = spec["pill_h"] * s
    color = _hex(spec["color"])

    # Glow (soft outer halo) — concentric translucent pills fading outward.
    glow = spec.get("glow", 0.0)
    if glow > 0.01:
        layers = 5
        pulse = spec.get("glow_pulse", 1.0)
        for i in range(layers, 0, -1):
            pad = i * 3 * s
            a = int(255 * (1.0 - i / (layers + 1)) * glow * pulse * 0.55)
            if a <= 0:
                continue
            _rounded(d, ox - pad, oy - pad, pw + 2 * pad, ph + 2 * pad, color, a)

    # Pill body.
    body = _hex(spec["pill_color"])
    if spec.get("bg_light"):
        ring = max(2, 2 * s)
        _rounded(d, ox - ring, oy - ring, pw + 2 * ring, ph + 2 * ring,
                 _hex("#0d0d0d"))
    _rounded(d, ox, oy, pw, ph, body)

    cx = ox + pw / 2
    cy = oy + ph / 2

    state = spec["state"]
    if state in ("recording", "listening"):
        _draw_bars(d, spec, cx, cy, color, s)
    elif state in ("loading", "processing"):
        _draw_spinner(d, spec, cx, cy, color, s)
    elif state == "idle":
        r = spec["idle_dot_r"] * s
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  fill=(color[0], color[1], color[2], 255))
    elif state == "error":
        _draw_x(d, cx, cy, _hex("#ff5b5b"), spec, s)

    # Downsample → anti-aliased.
    return big.resize((cw, ch), Image.LANCZOS)


def _draw_bars(d, spec, cx, cy, color, s):
    bars = spec.get("bars", [])
    n = max(1, len(bars))
    avail = spec["bars_avail"] * s
    step = avail / n
    bar_w = max(2 * s, int(step * 0.62))
    x0 = cx - avail / 2 + (step - bar_w) / 2
    bar_max = spec["bar_max"] * s
    for i, hr in enumerate(bars):
        bh = max(3 * s, int(hr * bar_max))
        bx = x0 + i * step
        d.rounded_rectangle([bx, cy - bh / 2, bx + bar_w, cy + bh / 2],
                            radius=bar_w / 2,
                            fill=(color[0], color[1], color[2], 255))


def _draw_spinner(d, spec, cx, cy, color, s):
    n = 10
    bg = (0x1e, 0x1e, 0x1e)
    r_in = spec["spin_r_in"] * s
    r_out = spec["spin_r_out"] * s
    width = max(1, spec["spin_w"] * s)
    ang0 = spec["spin_angle"]
    for i in range(n):
        ang = math.radians(ang0 + i * (360 / n))
        a = (i + 1) / n
        x1 = cx + r_in * math.cos(ang)
        y1 = cy + r_in * math.sin(ang)
        x2 = cx + r_out * math.cos(ang)
        y2 = cy + r_out * math.sin(ang)
        col = tuple(int(color[k] * a + bg[k] * (1 - a)) for k in range(3))
        d.line([x1, y1, x2, y2], fill=(col[0], col[1], col[2], 255),
               width=int(width))


def _draw_x(d, cx, cy, color, spec, s):
    r = spec.get("x_r", 7) * s
    w = max(1, spec.get("x_w", 2) * s)
    col = (color[0], color[1], color[2], 255)
    d.line([cx - r, cy - r, cx + r, cy + r], fill=col, width=int(w))
    d.line([cx - r, cy + r, cx + r, cy - r], fill=col, width=int(w))
