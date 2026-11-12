"""Cut a flat background off a drawing by flood fill from the border only.

For art whose own colours sit close to the background (a gray-blue werewolf on gray-blue paper), a colour
matte also eats the subject's interior. Here only background pixels connected to the image border go:
anything enclosed by the drawing's outline stays, whatever its colour.

usage: python scripts/myautosprite/flood_cut.py <in> <out> [--tol 22] [--bg R,G,B]
"""
from __future__ import annotations

import argparse

import numpy as np
from PIL import Image, ImageFilter
from PIL import ImageDraw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--tol", type=float, default=22.0)
    ap.add_argument("--bg", default=None, help="R,G,B (default: median of the border)")
    a = ap.parse_args()

    rgb = np.asarray(Image.open(a.src).convert("RGB")).astype(np.float32)
    if a.bg:
        bg = np.array([float(v) for v in a.bg.split(",")], np.float32)
    else:
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        bg = np.median(border, axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(-1))
    near = dist < a.tol
    h, w = near.shape
    # flood from the border over "near background" pixels: a 1-px frame of background around everything
    pad = np.full((h + 2, w + 2), 255, np.uint8)
    pad[1:-1, 1:-1] = np.where(near, 255, 0)
    img = Image.fromarray(pad).copy()  # fromarray shares a read-only buffer; floodfill would write nothing
    ImageDraw.floodfill(img, (0, 0), 128, thresh=0)
    bgmask = np.asarray(img)[1:-1, 1:-1] == 128
    keep = Image.fromarray(np.where(bgmask, 0, 255).astype(np.uint8))
    # opening: specks of paper texture left in the background go, the drawing keeps its shape
    keep = keep.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    keep = np.asarray(keep) > 127
    alpha = Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    out = Image.open(a.src).convert("RGBA")
    out.putalpha(alpha)
    out.save(a.out)
    print(f"bg={bg.round(1).tolist()} kept={keep.mean():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
