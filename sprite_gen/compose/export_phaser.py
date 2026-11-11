# SPDX-License-Identifier: Apache-2.0
"""Pack `video-loop` strips into one Phaser character atlas: `<name>.png`, `<name>.json`, `<name>.meta.json`.

`export-aseprite` describes a composed run (pipeline A). The video pipeline's motions end as one strip per
state (`<item>.strip.png` + `<item>.strip.json`, one cell size each), which no engine export read. This packs
the strips a character has into the shape a Phaser game loads with `this.load.atlas(key, png, json)`:

- every frame is one uniform cell, untrimmed, so the whole atlas shares one origin;
- that origin is the feet: each state's frames are placed so its ground point lands on one common anchor,
  so `sprite.y` is the ground line in every state and switching animations never makes the body jump;
- each frame carries that origin as `pivot` (Phaser sets a sprite's origin from it on every frame change, and
  `setFlipX` mirrors about it), and `<name>.meta.json` lists `cell`, `anchor`, `origin` and one animation per
  state (`key`, `prefix`, `count`, `zeroPad`, `frameRate`, `repeat`) for `anims.generateFrameNames`.

A state's ground point: for a loop, the lowest row any of its frames reaches and the mean alpha centroid of its
frames (a step's lifted foot or forward lean does not pull it); for a cut played once, its first frame's
bottom row and centroid (a lunge or a fall moves away from where it started, which is what the anchor must
not follow). Pixels are never redrawn: cells are moved by whole pixels, and only `--max-cell` resamples.
"""

from __future__ import annotations

import argparse
import io
import json
import math
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_set
from sprite_gen.util.console import utf8_streams

PAD = 2
"""Transparent margin around every cell, in the cell and between packed cells: WebGL's linear filter samples
neighbouring texels, and a cell flush against the next bleeds its colour in (worst when scaled)."""
ALPHA_FLOOR = 8
MAX_SIDE = 2048
"""Above this the atlas may not fit an older device's texture limit (4096 is today's common one)."""
WIDTH_CANDIDATES = (256, 512, 1024, 2048, 4096, 8192)


@dataclass(frozen=True)
class Strip:
    state: str
    path: Path
    cells: list[Image.Image]
    fps: float
    loop: bool
    body_h: int | None


@dataclass(frozen=True)
class Anchor:
    x: float
    y: float


def _package_version() -> str:
    try:
        return package_version("sprite-gen")
    except PackageNotFoundError:  # pragma: no cover - bare checkout
        return "unknown"


def _strip_json(path: Path) -> Path:
    """`path` itself when it is a strip JSON, else the one `*.strip.json` in the folder."""
    if path.is_file():
        return path
    if not path.is_dir():
        raise ValueError(f"{path}: no such strip JSON or loop folder")
    found = sorted(path.glob("*.strip.json"))
    if len(found) != 1:
        raise ValueError(f"{path}: expected one *.strip.json, found {len(found)}"
                         + (f" ({', '.join(p.name for p in found)})" if found else "") + "; name the file itself")
    return found[0]


def read_strip(state: str, path: Path, *, flip: bool = False) -> Strip:
    """One state's cells, cut from the strip PNG beside its JSON."""
    meta_path = _strip_json(path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    png = meta_path.with_name(meta_path.name[: -len(".json")] + ".png")
    count, w, h = int(meta["frames"]), int(meta["w"]), int(meta["h"])
    with Image.open(png) as im:
        sheet = im.convert("RGBA")
    if sheet.size != (count * w, h):
        raise ValueError(f"{png}: {sheet.width}x{sheet.height}, but {meta_path.name} says {count} cells of {w}x{h}")
    cells = [sheet.crop((i * w, 0, (i + 1) * w, h)) for i in range(count)]
    if flip:
        cells = [c.transpose(Image.Transpose.FLIP_LEFT_RIGHT) for c in cells]
    fps = float(meta.get("fps") or 0) or (1000.0 / float(meta["delay_ms"]))
    body_h = meta.get("body_h")
    return Strip(state, meta_path, cells, fps, bool(meta.get("loop")), int(body_h) if body_h else None)


def _alpha(cell: Image.Image) -> np.ndarray:
    a = np.asarray(cell.getchannel("A"), dtype=np.float32)
    return np.where(a > ALPHA_FLOOR, a, 0.0)


def _bbox(cell: Image.Image) -> tuple[int, int, int, int] | None:
    a = _alpha(cell)
    rows, cols = np.flatnonzero(a.any(axis=1)), np.flatnonzero(a.any(axis=0))
    if rows.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _centroid_x(cell: Image.Image) -> float | None:
    a = _alpha(cell)
    total = float(a.sum())
    if total <= 0:
        return None
    return float((a.sum(axis=0) * np.arange(a.shape[1], dtype=np.float32)).sum() / total)


def ground_anchor(strip: Strip) -> Anchor:
    """The state's feet point (module docstring): a loop's lowest row and mean centroid, a once cut's first frame."""
    boxes = [_bbox(c) for c in strip.cells]
    xs = [_centroid_x(c) for c in strip.cells]
    if strip.loop:
        bottoms = [b[3] for b in boxes if b is not None]
        known = [x for x in xs if x is not None]
        if not bottoms:
            raise ValueError(f"{strip.state}: every frame is empty")
        return Anchor(sum(known) / len(known), float(max(bottoms)))
    first = next((i for i, b in enumerate(boxes) if b is not None), None)
    if first is None:
        raise ValueError(f"{strip.state}: every frame is empty")
    return Anchor(float(xs[first]), float(boxes[first][3]))


def plan_cell(strips: list[Strip], anchors: list[Anchor]) -> tuple[tuple[int, int], tuple[int, int], list[tuple[int, int]]]:
    """One cell that holds every frame of every state with each state's anchor on one point.

    Returns the cell size, the common anchor in it, and per state the whole-pixel offset its frames are pasted at.
    """
    left = right = up = down = 0
    shifts: list[tuple[int, int]] = []
    for strip, anchor in zip(strips, anchors):
        ax, ay = round(anchor.x), round(anchor.y)
        shifts.append((ax, ay))
        for cell in strip.cells:
            box = _bbox(cell)
            if box is None:
                continue
            left, right = max(left, ax - box[0]), max(right, box[2] - ax)
            up, down = max(up, ay - box[1]), max(down, box[3] - ay)
    anchor_px = (left + PAD, up + PAD)
    cell = (left + right + 2 * PAD, up + down + 2 * PAD)
    offsets = [(anchor_px[0] - ax, anchor_px[1] - ay) for ax, ay in shifts]
    return cell, anchor_px, offsets


def _resample(cell: Image.Image, size: tuple[int, int]) -> Image.Image:
    # Premultiplied: a straight-alpha resample pulls the transparent pixels' colour into the edge.
    return cell.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


@dataclass(frozen=True)
class Packed:
    width: int
    height: int
    places: dict[str, tuple[int, int]]


def _shelf(names: list[str], cw: int, ch: int, width: int) -> Packed:
    x = y = PAD
    places: dict[str, tuple[int, int]] = {}
    for name in names:
        if x + cw + PAD > width:
            x, y = PAD, y + ch + PAD
        places[name] = (x, y)
        x += cw + PAD
    height = -(-(y + ch + PAD) // 4) * 4
    return Packed(width, height, places)


def pack(names: list[str], cw: int, ch: int) -> Packed:
    """Rows of equal cells in the candidate width whose sheet is squarest (then smallest)."""
    need = cw + 2 * PAD
    tried = [_shelf(names, cw, ch, w) for w in WIDTH_CANDIDATES if w >= need] or [_shelf(names, cw, ch, need)]
    return min(tried, key=lambda p: (max(p.width, p.height) / min(p.width, p.height), p.width * p.height))


def _zero_pad(count: int) -> int:
    return max(2, len(str(count - 1)))


def build(
    loops: list[tuple[str, Path]],
    *,
    name: str,
    flip: bool = False,
    max_cell: int = 0,
) -> tuple[Image.Image, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The atlas image, its Phaser JSON hash, its meta and a report. Nothing is written."""
    if not loops:
        raise ValueError("no --loop given")
    states = [s for s, _ in loops]
    if len(set(states)) != len(states):
        raise ValueError(f"a state is named twice: {', '.join(states)}")
    strips = [read_strip(state, path, flip=flip) for state, path in loops]
    heights = {s.state: s.body_h for s in strips}
    if len({h for h in heights.values() if h is not None}) > 1:
        raise ValueError("the strips were cut at different body heights "
                         f"({', '.join(f'{k}={v}' for k, v in heights.items())}): the character would change size "
                         "between animations; cut them again with one --body-height (video-set does that for a set)")
    anchors = [ground_anchor(s) for s in strips]
    cell, anchor_px, offsets = plan_cell(strips, anchors)
    scale = 1.0
    if max_cell and max(cell) > max_cell:
        scale = max_cell / max(cell)
    out_cell = (max(1, round(cell[0] * scale)), max(1, round(cell[1] * scale)))
    out_anchor = (round(anchor_px[0] * scale), round(anchor_px[1] * scale))
    origin = (round(out_anchor[0] / out_cell[0], 6), round(out_anchor[1] / out_cell[1], 6))

    frames: dict[str, Image.Image] = {}
    anims: list[dict[str, Any]] = []
    for strip, (ox, oy) in zip(strips, offsets):
        pad = _zero_pad(len(strip.cells))
        prefix = f"{name}_{strip.state}_"
        for i, src in enumerate(strip.cells):
            canvas = Image.new("RGBA", cell, (0, 0, 0, 0))
            canvas.paste(src, (ox, oy))  # onto an empty canvas: a straight copy (a mask would square the alpha)
            frames[f"{prefix}{i:0{pad}d}"] = canvas if scale == 1.0 else _resample(canvas, out_cell)
        anims.append({"key": f"{name}-{strip.state}", "prefix": prefix, "count": len(strip.cells), "zeroPad": pad,
                      "frameRate": round(strip.fps, 3), "repeat": -1 if strip.loop else 0})

    names = list(frames)
    packed = pack(names, *out_cell)
    atlas = Image.new("RGBA", (packed.width, packed.height), (0, 0, 0, 0))
    for frame_name, (x, y) in packed.places.items():
        atlas.paste(frames[frame_name], (x, y))
    w, h = out_cell
    json_hash = {
        "frames": {
            frame_name: {
                "frame": {"x": x, "y": y, "w": w, "h": h},
                "rotated": False,
                "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h},
                "sourceSize": {"w": w, "h": h},
                "pivot": {"x": origin[0], "y": origin[1]},
            }
            for frame_name, (x, y) in packed.places.items()
        },
        "meta": {
            "app": "sprite-gen export-phaser",
            "version": _package_version(),
            "image": f"{name}.png",
            "format": "RGBA8888",
            "size": {"w": packed.width, "h": packed.height},
            "scale": "1",
        },
    }
    meta = {"name": name, "cell": list(out_cell), "anchor": list(out_anchor), "origin": list(origin), "anims": anims}
    warnings: list[str] = []
    if max(packed.width, packed.height) > MAX_SIDE:
        warnings.append(f"atlas {packed.width}x{packed.height} is over {MAX_SIDE}: an older device's texture limit may "
                        "not hold it; give --max-cell (or fewer frames)")
    report = {
        "ok": True,
        "states": [{"state": s.state, "strip": str(s.path), "frames": len(s.cells), "frameRate": a["frameRate"],
                    "repeat": a["repeat"], "ground": [round(g.x, 2), round(g.y, 2)]}
                   for s, a, g in zip(strips, anims, anchors)],
        "cell": list(out_cell),
        "anchor": list(out_anchor),
        "origin": list(origin),
        "scale": round(scale, 6),
        "flip": flip,
        "atlas": [packed.width, packed.height],
        "fill": round(len(names) * w * h / (packed.width * packed.height), 4),
        "warnings": warnings,
    }
    return atlas, json_hash, meta, report


def _loop_arg(text: str) -> tuple[str, Path]:
    state, sep, path = text.partition("=")
    if not sep or not state.strip() or not path.strip():
        raise argparse.ArgumentTypeError(f"--loop {text!r}: expected STATE=PATH (a loop folder or its *.strip.json)")
    if not all(ch.isalnum() or ch in "-_" for ch in state.strip()):
        raise argparse.ArgumentTypeError(f"--loop {text!r}: a state is letters, digits, '-' or '_'")
    return state.strip(), Path(path.strip()).expanduser()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--loop", dest="loops", action="append", type=_loop_arg, required=True, metavar="STATE=PATH",
                        help="one per animation: its name and a video-loop folder (or the *.strip.json in it); "
                             "the animation key is NAME-STATE and its frames NAME_STATE_00…")
    parser.add_argument("--name", required=True, help="the texture key and file stem: <name>.png, .json, .meta.json")
    parser.add_argument("--out-dir", required=True, type=Path, help="where the three files go (e.g. a game's public/atlas)")
    parser.add_argument("--flip", action="store_true",
                        help="mirror every frame: for strips facing left in a game whose sprites face right")
    parser.add_argument("--max-cell", type=int, default=0,
                        help="scale the uniform cell down so its longer side is at most this many px (0: as cut)")


def run(loops: list[tuple[str, Path]], name: str, out_dir: Path, flip: bool = False, max_cell: int = 0) -> int:
    try:
        if not name or not all(ch.isalnum() or ch in "-_" for ch in name):
            raise ValueError(f"--name {name!r}: letters, digits, '-' or '_'")
        if max_cell < 0:
            raise ValueError("--max-cell must be 0 or more")
        atlas, json_hash, meta, report = build(loops, name=name, flip=flip, max_cell=max_cell)
        out_dir = out_dir.expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        atlas.save(buf, format="PNG", optimize=True)
        paths = {kind: out_dir / f"{name}{kind}" for kind in (".png", ".json", ".meta.json")}
        atomic_write_set({
            paths[".png"]: buf.getvalue(),
            paths[".json"]: json.dumps(json_hash, ensure_ascii=False, indent=2) + "\n",
            paths[".meta.json"]: json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        })
        report["files"] = {kind.lstrip("."): str(p) for kind, p in paths.items()}
        report["png_kb"] = math.ceil(len(buf.getvalue()) / 1024)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    for line in report["warnings"]:
        print(f"warning: {line}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
