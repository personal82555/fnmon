#!/usr/bin/env python3
"""Generate a system-monitoring-themed icon for the fnmon fpk."""

from PIL import Image, ImageDraw, ImageFont
import math

SIZE = 256
OUT = '/vol2/1000/HD2/fpk/fnmon-icon.png'

draw_icon = ImageDraw.Draw(Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0)))
img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Colors
BG_DARK = (16, 22, 36)
ACCENT = (56, 189, 248)
GREEN = (74, 222, 128)
YELLOW = (251, 191, 36)
RED = (248, 113, 113)
WHITE = (230, 240, 255)
CARD = (30, 41, 59)

cx, cy = SIZE//2, SIZE//2
r = SIZE//2 - 16

# Rounded square background
draw.rounded_rectangle([4, 4, SIZE-5, SIZE-5], radius=28, fill=BG_DARK)

# -- Gauge arc --
arc_r = r - 10
for deg in range(60, 241):
    rad = math.radians(deg)
    x1 = cx + int((arc_r-2) * math.cos(rad))
    y1 = cy + int((arc_r-2) * math.sin(rad))
    x2 = cx + int(arc_r * math.cos(rad))
    y2 = cy + int(arc_r * math.sin(rad))
    # green 60-120, yellow 120-180, red 180-240
    if deg < 120:
        c = GREEN
    elif deg < 180:
        c = YELLOW
    else:
        c = RED
    draw.line([(x1, y1), (x2, y2)], fill=c, width=4)

# Gauge needle at ~80°
needle_deg = 100
rad = math.radians(needle_deg)
nx = cx + int(arc_r * 0.7 * math.cos(rad))
ny = cy + int(arc_r * 0.7 * math.sin(rad))
draw.line([(cx, cy), (nx, ny)], fill=WHITE, width=3)
draw.ellipse([cx-5, cy-5, cx+5, cy+5], fill=ACCENT)

# -- Bar chart bars at bottom left --
bar_x, bar_y = cx - arc_r + 5, cy + 8
bar_w, bar_h = 8, 30
for i, (h, c) in enumerate([(24, GREEN), (16, YELLOW), (24, ACCENT)]):
    bx = bar_x + i * (bar_w + 4)
    draw.rectangle([bx, bar_y + bar_h - h, bx + bar_w, bar_y + bar_h], fill=c, width=0)

# -- Small CPU chip top-left --
chip_x, chip_y = cx - arc_r + 5, cy - arc_r + 5
draw.rounded_rectangle([chip_x, chip_y, chip_x+24, chip_y+24], radius=3, fill=ACCENT)
draw.rectangle([chip_x+4, chip_y+4, chip_x+20, chip_y+20], fill=(20, 40, 60))
# Dot
draw.rectangle([chip_x+10, chip_y+9, chip_x+14, chip_y+15], fill=(30, 80, 120))

# -- Signal dots (network) top right --
sig_x, sig_y = cx + arc_r - 20, cy - arc_r + 5
for i in range(3):
    sh = 6 + i * 5
    draw.rectangle([sig_x + i * 5, sig_y + 16 - sh, sig_x + i * 5 + 3, sig_y + 16], fill=ACCENT if i < 2 else (100, 150, 200))

# Resize for 128
img_128 = img.resize((128, 128), Image.LANCZOS if hasattr(Image, 'LANCZOS') else Image.ANTIALIAS)
img.save(OUT)
img_128.save('/vol2/1000/HD2/fpk/fnmon-icon-128.png')
print(f'✅ 256x256 saved: {OUT}')
print(f'✅ 128x128 saved: /vol2/1000/HD2/fpk/fnmon-icon-128.png')

# Also save 256 copy
img.save('/vol2/1000/HD2/fpk/fnmon-icon-256.png')
print(f'✅ 256x256 saved: /vol2/1000/HD2/fpk/fnmon-icon-256.png')
