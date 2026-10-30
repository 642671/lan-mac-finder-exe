"""Generate the Windows PNG and ICO assets from the LAN MAC Finder design."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def linear_color(start: tuple[int, int, int], end: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(start[index] + (end[index] - start[index]) * t) for index in range(3))


def generate_icon() -> tuple[Path, Path]:
    size = 1024
    gradient = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = gradient.load()
    start = (8, 127, 122)
    middle = (13, 111, 139)
    end = (23, 78, 166)
    for y in range(size):
        for x in range(size):
            t = max(0.0, min(1.0, (x + y) / (2 * (size - 1))))
            color = (
                linear_color(start, middle, t / 0.58)
                if t < 0.58
                else linear_color(middle, end, (t - 0.58) / 0.42)
            )
            pixels[x, y] = (*color, 255)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=224, fill=255)
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(icon)

    highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    highlight_draw = ImageDraw.Draw(highlight)
    highlight_draw.ellipse(
        (112, 74, 912, 886),
        outline=(255, 255, 255, 82),
        width=24,
    )
    icon.alpha_composite(highlight)

    ring = (430, 420)
    radius = 248
    draw.ellipse(
        (
            ring[0] - radius,
            ring[1] - radius,
            ring[0] + radius,
            ring[1] + radius,
        ),
        outline=(255, 255, 255, 245),
        width=76,
    )
    draw.line(
        (615, 605, 846, 836),
        fill=(255, 255, 255, 245),
        width=92,
    )
    draw.ellipse((569, 559, 661, 651), fill=(255, 255, 255, 245))
    draw.ellipse((800, 790, 892, 882), fill=(255, 255, 255, 245))

    nodes = [(430, 420), (318, 320), (542, 320), (430, 542)]
    for index, node in enumerate(nodes):
        node_radius = 48 if index == 0 else 38
        draw.ellipse(
            (
                node[0] - node_radius,
                node[1] - node_radius,
                node[0] + node_radius,
                node[1] + node_radius,
            ),
            fill=(224, 255, 250, 255) if index else (255, 255, 255, 255),
        )
    draw.line((350, 352, 402, 392), fill=(255, 255, 255, 220), width=28)
    draw.line((510, 352, 458, 392), fill=(255, 255, 255, 220), width=28)
    draw.line((430, 468, 430, 505), fill=(255, 255, 255, 220), width=28)

    png_path = ASSETS / "lan-mac-finder.png"
    ico_path = ASSETS / "lan-mac-finder.ico"
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(png_path)
    icon.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return png_path, ico_path


if __name__ == "__main__":
    png, ico = generate_icon()
    print(f"Generated: {png}")
    print(f"Generated: {ico}")
