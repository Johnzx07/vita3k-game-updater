"""Draw the Vita Pulse mark into a multi-resolution Windows icon."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 1024
S = SIZE / 512


def scale(value):
    return round(value * S)


def main():
    ASSETS.mkdir(exist_ok=True)
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    art = ImageDraw.Draw(image)
    art.rounded_rectangle((scale(14), scale(14), scale(498), scale(498)), radius=scale(112), fill="#101b30", outline="#344c69", width=scale(5))
    art.ellipse((scale(66), scale(66), scale(446), scale(446)), outline="#31445d", width=scale(4))
    art.ellipse((scale(98), scale(98), scale(414), scale(414)), outline="#315e73", width=scale(3))
    points = [(scale(117), scale(160)), (scale(256), scale(376)), (scale(395), scale(160))]
    glow = Image.new("RGBA", image.size)
    g = ImageDraw.Draw(glow)
    g.line(points, fill=(91, 223, 222, 130), width=scale(74), joint="curve")
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(scale(13))))
    art = ImageDraw.Draw(image)
    art.line(points, fill="#66d9e9", width=scale(52), joint="curve")
    art.line([(scale(256), scale(376)), (scale(395), scale(160))], fill="#9b87ff", width=scale(52), joint="curve")
    for x, color in [(117, "#d6fff5"), (395, "#ded2ff")]:
        art.ellipse((scale(x - 13), scale(147), scale(x + 13), scale(173)), fill=color)
    for x, radius, color in [(221, 5, "#5789a7"), (256, 8, "#80f0de"), (291, 5, "#5789a7")]:
        art.ellipse((scale(x-radius), scale(129-radius), scale(x+radius), scale(129+radius)), fill=color)
    image.save(ASSETS / "vita-pulse.png")
    image.save(ASSETS / "vita-pulse.ico", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print(f"Created {ASSETS / 'vita-pulse.ico'}")


if __name__ == "__main__":
    main()

