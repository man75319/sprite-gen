# SPDX-License-Identifier: Apache-2.0
"""`export-phaser`: video-loop strips packed into one Phaser character atlas.

The contract is the one a Phaser game checks of a character atlas: every frame one untrimmed cell of the size
the meta names, inside the PNG, no two overlapping, each state's ground point on the one anchor the meta names
(origin = anchor / cell), and one animation per state whose frame names are all in the atlas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.compose import export_phaser as ep


def _blob(w: int, h: int, box: tuple[int, int, int, int], colour=(200, 180, 40)) -> Image.Image:
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (*colour, 255)), box[:2])
    return im


def _strip(folder: Path, state: str, cells: list[Image.Image], *, fps=24.0, loop=True, body_h=400, kind=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    w, h = cells[0].size
    sheet = Image.new("RGBA", (w * len(cells), h), (0, 0, 0, 0))
    for i, c in enumerate(cells):
        sheet.paste(c, (i * w, 0))
    sheet.save(folder / f"side-{state}.strip.png")
    meta = {"frames": len(cells), "w": w, "h": h, "fps": fps, "delay_ms": round(1000 / fps, 2), "loop": loop,
            "kind": kind or ("periodic" if loop else "once"), "body_h": body_h, "state": state}
    (folder / f"side-{state}.strip.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder


def _feet(im: Image.Image, anchor: tuple[int, int], cell: tuple[int, int]) -> tuple[float, int]:
    """Centroid x and bottom row of a frame's opaque pixels, relative to the anchor."""
    box = ep._bbox(im)
    return ep._centroid_x(im) - anchor[0], box[3] - anchor[1]


def _set(tmp_path: Path) -> list[tuple[str, Path]]:
    # A walk standing at x 30..70, bottom 90, in 100x100 cells; a lunge that starts at x 40..80, bottom 120 in a
    # wider, taller canvas and moves 60 px forward; both from the same body.
    walk = [_blob(100, 100, (30 + d, 40, 70 + d, 90)) for d in (0, 2, 0, -2)]
    lunge = [_blob(200, 130, (40 + d, 70, 80 + d, 120)) for d in (0, 30, 60)]
    return [("walk", _strip(tmp_path / "walk", "walk", walk)),
            ("attack", _strip(tmp_path / "attack", "attack", lunge, loop=False, fps=19.2))]


def test_every_frame_is_one_cell_on_one_feet_anchor_and_the_meta_names_every_frame(tmp_path):
    atlas, js, meta, report = ep.build(_set(tmp_path), name="frog")
    cw, ch = meta["cell"]
    rects = {n: f["frame"] for n, f in js["frames"].items()}
    assert {(r["w"], r["h"]) for r in rects.values()} == {(cw, ch)}
    assert all(not f["trimmed"] and f["sourceSize"] == {"w": cw, "h": ch} for f in js["frames"].values())
    assert js["meta"]["size"] == {"w": atlas.width, "h": atlas.height} and js["meta"]["image"] == "frog.png"
    assert all(r["x"] >= 0 and r["y"] >= 0 and r["x"] + r["w"] <= atlas.width and r["y"] + r["h"] <= atlas.height
               for r in rects.values())
    boxes = list(rects.values())
    assert not any(a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]
                   for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert meta["origin"] == [round(meta["anchor"][0] / cw, 6), round(meta["anchor"][1] / ch, 6)]
    assert all(f["pivot"] == {"x": meta["origin"][0], "y": meta["origin"][1]} for f in js["frames"].values())
    declared = {a["prefix"] + str(i).zfill(a["zeroPad"]) for a in meta["anims"] for i in range(a["count"])}
    assert declared == set(js["frames"])
    assert [(a["key"], a["count"], a["frameRate"], a["repeat"]) for a in meta["anims"]] == [
        ("frog-walk", 4, 24.0, -1), ("frog-attack", 3, 19.2, 0)]
    assert report["warnings"] == []


def test_a_loop_stands_on_its_mean_centroid_and_a_cut_played_once_on_its_first_frame(tmp_path):
    """The walk's sway averages out; the lunge's first frame is on the anchor and its later frames move away
    from it — the anchor does not follow the lunge, so the attack starts exactly where the walk stands."""
    atlas, js, meta, _ = ep.build(_set(tmp_path), name="frog")
    anchor, cell = tuple(meta["anchor"]), tuple(meta["cell"])

    def frame(name):
        r = js["frames"][name]["frame"]
        return atlas.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))

    walk_x = [_feet(frame(f"frog_walk_{i:02d}"), anchor, cell)[0] for i in range(4)]
    assert abs(sum(walk_x) / 4) <= 0.5
    assert all(_feet(frame(f"frog_walk_{i:02d}"), anchor, cell)[1] == 0 for i in range(4))
    first_x, first_y = _feet(frame("frog_attack_00"), anchor, cell)
    assert abs(first_x) <= 0.5 and first_y == 0
    assert _feet(frame("frog_attack_02"), anchor, cell)[0] == pytest.approx(first_x + 60, abs=0.5)


def test_flip_mirrors_every_frame_about_the_anchor(tmp_path):
    loops = [("attack", _strip(tmp_path / "a", "attack", [_blob(200, 130, (40 + d, 70, 80 + d, 120)) for d in (0, 60)],
                                loop=False))]
    atlas, js, meta, report = ep.build(loops, name="frog", flip=True)
    r = js["frames"]["frog_attack_01"]["frame"]
    moved = ep._centroid_x(atlas.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))) - meta["anchor"][0]
    assert report["flip"] is True and moved == pytest.approx(-60, abs=0.5)


def test_max_cell_scales_the_cell_and_its_anchor_together(tmp_path):
    _, js, meta, report = ep.build(_set(tmp_path), name="frog", max_cell=50)
    assert max(meta["cell"]) == 50 and 0 < report["scale"] < 1
    assert {(f["frame"]["w"], f["frame"]["h"]) for f in js["frames"].values()} == {tuple(meta["cell"])}
    assert 0 < meta["anchor"][0] < meta["cell"][0] and 0 < meta["anchor"][1] <= meta["cell"][1]


def test_strips_cut_at_different_body_heights_are_refused(tmp_path):
    loops = [("walk", _strip(tmp_path / "w", "walk", [_blob(50, 50, (10, 10, 40, 40))], body_h=400)),
             ("jump", _strip(tmp_path / "j", "jump", [_blob(50, 50, (10, 10, 40, 40))], body_h=300))]
    with pytest.raises(ValueError, match="different body heights"):
        ep.build(loops, name="frog")


def test_a_strip_png_that_disagrees_with_its_json_is_refused(tmp_path):
    folder = _strip(tmp_path / "w", "walk", [_blob(50, 50, (10, 10, 40, 40))] * 3)
    meta = json.loads((folder / "side-walk.strip.json").read_text())
    (folder / "side-walk.strip.json").write_text(json.dumps({**meta, "frames": 4}))
    with pytest.raises(ValueError, match="says 4 cells"):
        ep.build([("walk", folder)], name="frog")


def test_the_cli_writes_the_three_files_together_with_the_alignment_preview(tmp_path, capsys):
    loops = _set(tmp_path)
    out = tmp_path / "atlas"
    assert ep.run(loops, "frog", out) == 0
    assert sorted(p.name for p in out.iterdir()) == ["frog.align.png", "frog.json", "frog.meta.json", "frog.png"]
    report = json.loads(capsys.readouterr().out)
    with Image.open(out / "frog.png") as im:
        assert list(im.size) == report["atlas"]
    with Image.open(out / "frog.align.png") as im:  # a panel per state and one overlaid, each a cell wide
        assert im.width == 3 * report["cell"][0] * max(1, -(-240 // report["cell"][1]))
    assert "export-phaser" in cli.COMMANDS
    assert ep.run(loops, "frog", out, max_cell=-1) == 1


def test_an_effect_under_the_feet_in_a_few_loop_frames_does_not_lift_the_ground(tmp_path):
    """A slash whose impact burst reaches below the feet in one frame of ten: the other nine set the ground,
    so the attack stands on the same line as the walk, and the burst is reported hanging below it."""
    cells = [_blob(100, 160, (30, 40, 70, 90)) for _ in range(10)]
    burst = cells[6].copy()
    burst.paste(Image.new("RGBA", (30, 40), (255, 255, 255, 255)), (5, 110))
    cells[6] = burst
    walk = [_blob(100, 100, (30, 40 + d, 70, 90 + d)) for d in (0, -1, -2, -1)]  # a bob row by row: no gap
    loops = [("walk", _strip(tmp_path / "walk", "walk", walk)),
             ("attack", _strip(tmp_path / "attack", "attack", cells))]
    atlas, js, meta, report = ep.build(loops, name="wolf")
    anchor = tuple(meta["anchor"])

    def bottom(name):
        r = js["frames"][name]["frame"]
        return ep._bbox(atlas.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])))[3] - anchor[1]

    assert [bottom(f"wolf_walk_{i:02d}") for i in range(4)] == [0, -1, -2, -1]
    assert [bottom(f"wolf_attack_{i:02d}") for i in range(10) if i != 6] == [0] * 9
    assert bottom("wolf_attack_06") == 60
    assert [w for w in report["warnings"] if w.startswith("attack: frame(s) 6 ")]


def test_an_attack_looped_from_its_stance_stands_where_its_first_frame_does(tmp_path):
    """A `fixed` loop (a slash from a stance back to it): the slash reaching forward in its middle frames does not
    pull the anchor, so its first frame stands on the anchor exactly as the idle does — no jump between them."""
    idle = [_blob(200, 100, (80, 40, 120, 90)) for _ in range(4)]
    slash = [_blob(200, 100, (80, 40, 120, 90))] + [_blob(200, 100, (10, 40, 120, 90)) for _ in range(6)]         + [_blob(200, 100, (80, 40, 120, 90))]
    loops = [("idle", _strip(tmp_path / "idle", "idle", idle, kind="pinned")),
             ("attack", _strip(tmp_path / "attack", "attack", slash, kind="fixed"))]
    atlas, js, meta, report = ep.build(loops, name="wolf")
    anchor = tuple(meta["anchor"])

    def frame(name):
        r = js["frames"][name]["frame"]
        return atlas.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))

    assert _feet(frame("wolf_idle_00"), anchor, None) == pytest.approx((0, 0), abs=0.5)
    assert _feet(frame("wolf_attack_00"), anchor, None) == pytest.approx((0, 0), abs=0.5)
    assert _feet(frame("wolf_attack_07"), anchor, None) == pytest.approx((0, 0), abs=0.5)
    assert [a["repeat"] for a in meta["anims"]] == [-1, -1] and report["warnings"] == []


def test_a_state_whose_stance_lands_off_the_idles_is_reported(tmp_path):
    """An attack whose first frame holds an arm out: its centroid sits toward the arm, so the anchor puts its
    feet off the idle's. The export warns, naming the state and how far its stance stands from the idle's."""
    idle = [_blob(200, 100, (80, 40, 120, 90)) for _ in range(4)]
    armed = _blob(200, 100, (80, 40, 120, 90))
    armed.paste(Image.new("RGBA", (70, 12), (200, 180, 40, 255)), (10, 40))
    attack = [armed] + [_blob(200, 100, (80, 40, 120, 90)) for _ in range(3)]
    loops = [("idle", _strip(tmp_path / "idle", "idle", idle, kind="pinned")),
             ("attack", _strip(tmp_path / "attack", "attack", attack, kind="fixed"))]
    _, _, _, report = ep.build(loops, name="wolf")
    by = {r["state"]: r for r in report["alignment"]}
    assert by["idle"]["ok"] and by["idle"]["dx"] == 0 and by["idle"]["reference"] == "idle"
    assert not by["attack"]["ok"] and by["attack"]["dx"] > 10 and by["attack"]["dy"] == 0
    assert [w for w in report["warnings"] if w.startswith("attack: its stance stands +")]


def test_states_standing_on_one_stance_pass_the_alignment_check(tmp_path):
    _, _, _, report = ep.build(_set(tmp_path), name="frog")
    assert [(r["state"], r["ok"]) for r in report["alignment"]] == [("walk", True), ("attack", True)]
