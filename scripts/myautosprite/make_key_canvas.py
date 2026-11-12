"""Put a cut-out drawing on a flat chroma canvas, framed for `sprite-gen video`.

`sprite-gen video-canvas` wants a still whose corners are already one flat key colour; a drawing that came
from paper (or from a generator's flat background) has none. This does that one step: crop away anything
below `--crop-bottom`, scale the subject to `--height`, stand it on `--baseline` and fill the rest with the
key. Nothing else is touched — the art is the art.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

KEY = (0, 255, 0, 255)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--height", type=int, default=600, help="subject height in the canvas")
    parser.add_argument("--baseline", type=int, default=900, help="y of the subject's lowest pixel")
    parser.add_argument("--crop-bottom", type=int, default=None, help="drop everything below this y (a watermark)")
    args = parser.parse_args()

    image = Image.open(args.source).convert("RGBA")
    if args.crop_bottom is not None:
        image = image.crop((0, 0, image.width, args.crop_bottom))
    box = image.getchannel("A").getbbox()
    if box is None:
        raise SystemExit(f"{args.source}: nothing opaque to place")
    subject = image.crop(box)
    scale = args.height / subject.height
    subject = subject.resize((max(1, round(subject.width * scale)), args.height), Image.LANCZOS)

    canvas = Image.new("RGBA", (args.size, args.size), KEY)
    left = (args.size - subject.width) // 2
    top = args.baseline - subject.height
    canvas.alpha_composite(subject, (left, top))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(args.out)
    print(f"{args.out}: {canvas.width}x{canvas.height}, subject {subject.width}x{subject.height} "
          f"at ({left},{top}), corners {canvas.getpixel((0, 0))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
