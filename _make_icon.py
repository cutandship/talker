"""Run once to generate icon.ico next to this file."""
from PIL import Image, ImageDraw
import math

def make_frame(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # Background circle — dark
    d.ellipse([0, 0, s - 1, s - 1], fill="#1a1a1a")

    # Subtle gradient ring
    for i in range(3):
        off = i
        d.ellipse([off, off, s - 1 - off, s - 1 - off],
                  outline="#2a2a2a", width=1)

    # Mic body — rounded rect
    bw = s * 0.22
    bh = s * 0.36
    bx = (s - bw) / 2
    by = s * 0.16
    r = bw / 2
    teal = "#00d4aa"
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=r, fill=teal)

    # Mic arc (stand bow)
    arc_margin = s * 0.18
    arc_top    = s * 0.32
    arc_bot    = s * 0.68
    d.arc([arc_margin, arc_top, s - arc_margin, arc_bot],
          start=0, end=180, fill=teal, width=max(2, round(s * 0.045)))

    # Vertical stem
    stem_x = s / 2
    stem_top = arc_bot - s * 0.01
    stem_bot = s * 0.78
    lw = max(2, round(s * 0.045))
    d.line([(stem_x, stem_top), (stem_x, stem_bot)], fill=teal, width=lw)

    # Horizontal base
    base_w = s * 0.26
    base_y = stem_bot
    d.line([(stem_x - base_w / 2, base_y), (stem_x + base_w / 2, base_y)],
           fill=teal, width=lw)

    return img


sizes = [16, 32, 48, 64, 128, 256]
frames = [make_frame(s) for s in sizes]

out = frames[-1]  # largest as primary
out.save(
    "icon.ico",
    format="ICO",
    sizes=[(s, s) for s in sizes],
    append_images=frames[:-1],
)
print("icon.ico created")
