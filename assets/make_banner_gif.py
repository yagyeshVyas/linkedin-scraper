"""
Generate the animated pipeline banner used in README.md.

    .venv/Scripts/python.exe assets/make_banner_gif.py

Dark-themed, dashboard-matching loop: each stage of the pipeline lights up in
sequence (config -> scraper -> progress.json -> dashboard), a packet travels
the arrows, and a LIVE pulse + ban-risk gauge run at the bottom. Pillow is a
venv-only dependency (not in Requirements.txt — this is a dev/docs tool).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 760, 210
BG = (15, 23, 42)          # slate-900 (dashboard dark)
PANEL = (30, 41, 59)       # slate-800
PANEL_ACTIVE = (56, 189, 248, 60)  # sky glow
BORDER = (51, 65, 85)
BORDER_ACTIVE = (56, 189, 248)
TEXT = (226, 232, 240)
MUTED = (148, 163, 184)
GREEN = (52, 211, 153)
AMBER = (251, 191, 36)
CYAN = (56, 189, 248)

NODES = [
    ("config.py", "credentials, titles, proxies"),
    ("linkedin_scraper.py", "login, search, extract"),
    ("output/progress.json", "46 profiles saved"),
    ("dashboard.html", "live preview + health"),
]
NX, NY, NW, NH = 18, 74, 168, 56
GAP = (W - 2 * NX - 4 * NW) // 3  # arrow width between nodes


def font(size):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE = font(15)
F_LABEL = font(13)
F_SUB = font(9)
F_SMALL = font(10)

FRAMES = 36
DURATION_MS = 110


def draw_node(draw, i, active, t):
    x = NX + i * (NW + GAP)
    glow = 6 if active else 0
    for g in range(glow, 0, -1):
        draw.rounded_rectangle(
            [x - g, NY - g, x + NW + g, NY + NH + g], radius=12,
            outline=PANEL_ACTIVE if active else BORDER, width=1)
    draw.rounded_rectangle([x, NY, x + NW, NY + NH], radius=10,
                           fill=PANEL, outline=BORDER_ACTIVE if active else BORDER, width=2)
    draw.text((x + NW // 2, NY + 12), NODES[i][0], font=F_LABEL,
              fill=TEXT if active else MUTED, anchor="ma")
    draw.text((x + NW // 2, NY + NH - 15), NODES[i][1], font=F_SUB,
              fill=(56, 189, 248) if active else MUTED, anchor="ma")
    if active:  # pulsing corner dot
        r = 3 + t % 3
        draw.ellipse([x + 6, NY + NH - 8 - r, x + 6 + 2 * r, NY + NH - 8 + r],
                     fill=CYAN)


def draw_arrow(draw, i, progress):
    x0 = NX + i * (NW + GAP) + NW
    x1 = x0 + GAP
    y = NY + NH // 2
    active = 0 < progress < 1
    color = BORDER_ACTIVE if active else BORDER
    draw.line([x0, y, x1 - 6, y], fill=color, width=3)
    draw.polygon([(x1 - 2, y), (x1 - 8, y - 5), (x1 - 8, y + 5)], fill=color)
    if active:  # traveling packet
        px = x0 + (x1 - x0) * progress
        draw.ellipse([px - 4, y - 4, px + 4, y + 4], fill=CYAN)


def draw_bottom(draw, t):
    y = 168
    # Ban-risk gauge
    risk = min(55, round(55 * (t + 1) / FRAMES))  # climbs to 55 over the loop
    color = GREEN if risk < 30 else AMBER if risk < 60 else (251, 113, 133)
    draw.text((NX, y - 14), "SESSION HEALTH — LIVE", font=F_SUB, fill=MUTED)
    draw.rounded_rectangle([NX, y, NX + 300, y + 8], radius=4, fill=BORDER)
    if risk:
        draw.rounded_rectangle([NX, y, NX + 300 * risk / 100, y + 8], radius=4, fill=color)
    draw.text((NX + 308, y - 4), f"ban risk {risk}/100", font=F_SMALL, fill=TEXT)
    # LIVE pulse
    pulse = 160 + int(95 * abs(1 - 2 * ((t % 20) / 20)))
    draw.rounded_rectangle([W - NX - 84, y - 12, W - NX, y + 16], radius=12,
                           fill=(56, 189, 248, 40), outline=CYAN, width=2)
    draw.ellipse([W - NX - 74, y - 4, W - NX - 66, y + 4], fill=(pulse, 100, 100, 255) if False else CYAN)
    draw.text((W - NX - 60, y + 2), "LIVE", font=F_SMALL, fill=CYAN, anchor="lm")


def frame(t):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.text((NX, 22), "LinkedIn People Scraper  →  live dashboard", font=F_TITLE, fill=TEXT)
    draw.line([NX, 46, W - NX, 46], fill=BORDER, width=1)

    active = min(3, t // (FRAMES // 4))
    for i in range(4):
        draw_node(draw, i, i == active, t)
    for i in range(3):
        phase_progress = (t % (FRAMES // 4)) / (FRAMES // 4)
        draw_arrow(draw, i, phase_progress if i == active else 0)
    draw_bottom(draw, t)
    return img


def main():
    out = Path(__file__).parent / "dashboard-demo.gif"
    frames = [frame(t) for t in range(FRAMES)]
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=DURATION_MS, loop=0, optimize=True)
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB, {FRAMES} frames, "
          f"{FRAMES * DURATION_MS / 1000:.1f}s loop)")


if __name__ == "__main__":
    main()
