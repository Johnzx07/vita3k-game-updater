"""Make app and Windows icon assets from the owner's V3K artwork."""
from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SOURCE = ASSETS / "v3k-logo-source.png"


def main():
    if not SOURCE.is_file():
        raise SystemExit(f"Missing source artwork: {SOURCE}")
    with Image.open(SOURCE) as original:
        artwork = ImageOps.fit(original.convert("RGBA"), (1024, 1024), method=Image.Resampling.LANCZOS)
    artwork.save(ASSETS / "vita-pulse.png", optimize=True)
    artwork.save(
        ASSETS / "vita-pulse.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Created {ASSETS / 'vita-pulse.png'} and {ASSETS / 'vita-pulse.ico'}")


if __name__ == "__main__":
    main()
