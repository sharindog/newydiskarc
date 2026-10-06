"""Draw the application icon: packaging/macos/icon.png and icon.icns (needs Pillow).

python packaging/macos/make_icon.py
"""

import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024 * 4  # supersampled canvas


def lerp(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def main() -> None:
    # Rounded square with a warm vertical gradient, inset like macOS app icons.
    margin, radius = int(S * 0.098), int(S * 0.18)
    gradient = Image.new("RGB", (1, S))
    top, bottom = (255, 204, 51), (255, 128, 0)
    for y in range(S):
        gradient.putpixel((0, y), lerp(top, bottom, y / (S - 1)))
    gradient = gradient.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (margin, margin, S - margin, S - margin), radius=radius, fill=255
    )
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow = mask.filter(ImageFilter.GaussianBlur(S // 80))
    icon.paste((0, 0, 0, 90), (0, S // 120), shadow)
    icon.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(icon)
    white = (255, 255, 255, 255)
    # Cloud
    cx, cy = S // 2, int(S * 0.44)
    u = S // 100
    for x, y, r in ((-14, 4, 13), (2, -6, 17), (17, 5, 12)):
        draw.ellipse(
            (cx + (x - r) * u, cy + (y - r) * u, cx + (x + r) * u, cy + (y + r) * u), white
        )
    draw.rounded_rectangle((cx - 27 * u, cy + 2 * u, cx + 29 * u, cy + 17 * u), 8 * u, white)
    # Down arrow below the cloud
    orange = (255, 140, 0, 255)
    draw.rectangle((cx - 4 * u, cy - 2 * u, cx + 4 * u, cy + 20 * u), orange)
    draw.rectangle((cx - 4 * u, cy + 17 * u, cx + 4 * u, cy + 26 * u), white)
    draw.polygon([(cx - 13 * u, cy + 24 * u), (cx + 13 * u, cy + 24 * u), (cx, cy + 37 * u)], white)

    icon = icon.resize((1024, 1024), Image.LANCZOS)
    icon.save(os.path.join(HERE, "icon.png"))
    icon.save(
        os.path.join(HERE, "icon.icns"),
        sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)],
    )


if __name__ == "__main__":
    main()
