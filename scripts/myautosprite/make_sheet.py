"""Lay one row per state out of the loops `video-set` cut, for a single look at the whole set.

Each row takes `--cols` evenly spaced cells from that state's `loop/cycle/` (the frames the loop plays),
scaled to `--cell` pixels with nearest-neighbour (pixel-crisp), on a light checker so transparency reads.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

CHECKER = ((232, 232, 232, 255), (246, 246, 246, 255))


def checker(width: int, height: int, size: int = 16) -> Image.Image:
    sheet = Image.new("RGBA", (width, height), CHECKER[1])
    for y in range(0, height, size):
        for x in range(0, width, size):
            if (x // size + y // size) % 2 == 0:
                sheet.paste(CHECKER[0], (x, y, min(x + size, width), min(y + size, height)))
    return sheet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-dir", required=True, type=Path)
    parser.add_argument("--states", default="idle,walk,attack,jump")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cols", type=int, default=8)
    parser.add_argument("--cell", type=int, default=160)
    parser.add_argument("--gap", type=int, default=4)
    parser.add_argument("--body", type=int, default=110,
                        help="with a loop's strip.json beside the cycle: scale each row so the character's body is "
                             "this many px, so rows read at the same size whatever their canvas")
    args = parser.parse_args()

    rows: list[Image.Image] = []
    for state in [s.strip() for s in args.states.split(",") if s.strip()]:
        cycle = next(iter(sorted(args.set_dir.glob(f"*-{state}/loop/cycle"))), None)
        if cycle is None:
            print(f"{state}: no cycle frames, skipped")
            continue
        frames = sorted(cycle.glob("frame-*.png"))
        if not frames:
            continue
        meta = next(iter(sorted(cycle.parent.glob("*.strip.json"))), None)
        body = json.loads(meta.read_text(encoding="utf-8")).get("body_h") if meta else None
        picks = [frames[round(i * (len(frames) - 1) / max(1, args.cols - 1))] for i in range(args.cols)]
        # What the character actually occupies decides the row's scale: the metadata's `body_h` is what the cut asked
        # for, and a loop whose frames were not scaled to it would otherwise read at another size than its neighbours.
        measured = []
        for path in picks:
            with Image.open(path) as probe:
                box = probe.convert("RGBA").getchannel("A").getbbox()
            if box:
                measured.append(box[3] - box[1])
        body = sorted(measured)[len(measured) // 2] if measured else body
        cells = []
        for path in picks:
            with Image.open(path) as image:
                frame = image.convert("RGBA")
                if body:
                    scale = args.body / body
                else:
                    scale = args.cell / max(frame.size)
                cells.append(frame.resize((max(1, round(frame.width * scale)), max(1, round(frame.height * scale))),
                                          Image.LANCZOS))
        rows.append((state, cells))

    if not rows:
        raise SystemExit("nothing to lay out")

    cell_w = max(cell.width for _state, cells in rows for cell in cells)
    cell_h = max(cell.height for _state, cells in rows for cell in cells)
    width = args.cols * (cell_w + args.gap) + args.gap
    height = len(rows) * (cell_h + args.gap) + args.gap
    sheet = checker(width, height)
    for row_index, (_state, cells) in enumerate(rows):
        top = args.gap + row_index * (cell_h + args.gap)
        for column, cell in enumerate(cells):
            left = args.gap + column * (cell_w + args.gap) + (cell_w - cell.width) // 2
            sheet.alpha_composite(cell, (left, top + (cell_h - cell.height)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.out)
    print(f"{args.out}: {len(rows)} rows x {args.cols} cols, cell {cell_w}x{cell_h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
