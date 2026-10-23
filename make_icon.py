"""Draws the PepeMonitor frog icon (frog.ico + frog.png). Run: python make_icon.py"""
from pathlib import Path
from PIL import Image, ImageDraw

GREEN = (109, 179, 63)
GREEN_DARK = (52, 110, 30)
LIP = (178, 82, 58)
LIP_DARK = (120, 45, 30)
WHITE = (250, 250, 245)
PUPIL = (25, 25, 25)
FRAME = (40, 48, 60)
RGB_BARS = [(220, 70, 60), (90, 190, 70), (70, 130, 230)]


def frog(size: int) -> Image.Image:
    s = size * 4  # supersample, then scale down for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = s / 100
    w = max(1, round(2.5 * u))

    def box(x0, y0, x1, y1):
        return [x0 * u, y0 * u, x1 * u, y1 * u]

    # head behind the monitor
    d.ellipse(box(6, 22, 94, 82), fill=GREEN, outline=GREEN_DARK, width=w)
    # eyes
    for x0 in (14, 50):
        d.ellipse(box(x0, 4, x0 + 36, 42), fill=WHITE, outline=GREEN_DARK, width=w)
        d.ellipse(box(x0 + 12, 18, x0 + 26, 34), fill=PUPIL)
        # droopy eyelid over the top of the eye
        d.chord(box(x0, 4, x0 + 36, 42), 180, 360, fill=GREEN, outline=GREEN_DARK, width=w)
        d.line([((x0 + 1) * u, 23 * u), ((x0 + 35) * u, 23 * u)], fill=GREEN_DARK, width=w)
    # wide lips
    d.rounded_rectangle(box(22, 47, 78, 59), radius=6 * u, fill=LIP, outline=LIP_DARK, width=w)
    d.line([(25 * u, 53 * u), (75 * u, 53 * u)], fill=LIP_DARK, width=w)
    # monitor the frog peeks over: screen with R/G/B bars + stand
    d.rounded_rectangle(box(12, 62, 88, 92), radius=4 * u, fill=FRAME, outline=GREEN_DARK, width=w)
    for i, color in enumerate(RGB_BARS):
        x0 = 17 + i * 22.7
        d.rectangle(box(x0, 67, x0 + 20.7, 87), fill=color)
    d.rectangle(box(44, 92, 56, 96), fill=FRAME)
    d.rounded_rectangle(box(34, 95, 66, 99), radius=2 * u, fill=FRAME)
    return img.resize((size, size), Image.LANCZOS)


here = Path(__file__).parent
big = frog(256)
big.save(here / "frog.png")
big.save(here / "frog.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icons written")
