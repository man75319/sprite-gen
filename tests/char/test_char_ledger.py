# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen char`: the ledger (docs/char.md sections 2, 3 and 6), with video-set replaced by a fake that writes what
a finished run leaves (set.report.json, the clip's report, a loop strip) and spends nothing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.char import char_cmd, char_plan, ledger


@pytest.fixture
def chars(tmp_path, monkeypatch):
    monkeypatch.setenv("SPRITE_GEN_CHARS_DIR", str(tmp_path / "chars"))
    return tmp_path / "chars"


def _cutout(path: Path) -> Path:
    im = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (120, 300), (150, 90, 40, 255)), (90, 60))
    im.save(path)
    return path


def char(capsys, *argv: str) -> dict:
    capsys.readouterr()
    code = cli.main(["char", *argv, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert (code == 0) == out["ok"], out
    return out


@pytest.fixture
def films(monkeypatch):
    """Every video-set call: its argv. A run whose motion says FAIL fails like a refused clip."""
    calls: list[list[str]] = []

    def fake(argv, log):
        calls.append(argv)
        out = Path(argv[argv.index("--out-dir") + 1])
        state = argv[argv.index("--states") + 1]
        motion = argv[argv.index("--motion") + 1].split("=", 1)[1]
        cid, name = out.parts[-4], out.parts[-2]
        character, action = ledger.load_character(cid), ledger.load_action(cid, name)
        item = out / f"side-{state}"
        loop = item / "loop"
        loop.mkdir(parents=True)
        log.write_text("fake video-set\n", encoding="utf-8")
        (item / "clip.mp4").write_bytes(b"mp4")
        (item / "clip.report.json").write_text(json.dumps({"prompt": char_plan.planned_prompt(character, action)["prompt"]}))
        ok = "FAIL" not in motion
        if ok:
            cells = [Image.new("RGBA", (80, 120), (0, 0, 0, 0)) for _ in range(4)]
            for i, c in enumerate(cells):
                c.paste(Image.new("RGBA", (30, 90), (200, 100, 50, 255)), (25 + i, 20))
            sheet = Image.new("RGBA", (320, 120), (0, 0, 0, 0))
            for i, c in enumerate(cells):
                sheet.paste(c, (i * 80, 0))
            sheet.save(loop / f"side-{state}.strip.png")
            (loop / f"side-{state}.strip.json").write_text(json.dumps(
                {"frames": 4, "w": 80, "h": 120, "fps": 24.0, "delay_ms": 41.67, "loop": True, "kind": "periodic",
                 "body_h": 200, "state": state}))
        item_rec = {"item": f"side-{state}", "ok": ok, **({"loop": {"seam_ratio": 0.12}} if ok else {"error": "clip refused"})}
        (out / "set.report.json").write_text(json.dumps({"items": [item_rec], "warnings": [], "failed": [] if ok else [item_rec["item"]]}))
        return 0 if ok else 1

    monkeypatch.setattr(char_cmd, "run_video_set", fake)
    return calls


def _new(capsys, tmp_path, cid="frog", *extra):
    return char(capsys, "new", cid, "--ref", str(_cutout(tmp_path / "ref.png")), "--description", "The frog", "--body-height", "200", *extra)


def test_a_cutout_reference_is_laid_on_the_key_and_the_spec_takes_its_defaults(chars, tmp_path, capsys):
    out = _new(capsys, tmp_path)["character"]
    assert out["spec"]["provider"] == "dmx" and out["spec"]["fps"] is None and out["spec"]["body_height"] == 200
    with Image.open(chars / "frog" / "base.png") as base:
        assert base.mode == "RGB" and base.getpixel((0, 0)) == (0, 255, 0)
    assert json.loads((chars / "frog" / "character.json").read_text())["schema"] == ledger.CHARACTER_SCHEMA


def _opaque(path: Path, corners, **save) -> Path:
    """An opaque still: a bilinear background through the four corner colours (TL, TR, BL, BR), a figure on it."""
    w, h = 300, 400
    bg = Image.new("RGB", (w, h))
    px = bg.load()
    for y in range(h):
        for x in range(w):
            u, v = x / (w - 1), y / (h - 1)
            px[x, y] = tuple(round(corners[0][i] * (1 - u) * (1 - v) + corners[1][i] * u * (1 - v)
                                   + corners[2][i] * (1 - u) * v + corners[3][i] * u * v) for i in range(3))
    bg.paste((60, 90, 170), (100, 60, 200, 340))
    bg.paste((255, 255, 255), (140, 90, 160, 110))  # a highlight inside the figure stays opaque
    bg.save(path, **save)
    return path


def _new_from(capsys, ref: Path) -> dict:
    return char(capsys, "new", "frog", "--ref", str(ref), "--description", "The frog", "--body-height", "200")["character"]


def _assert_cut_and_laid(root: Path, figure: tuple[int, int, int, int]) -> None:
    """cutout.png holds the figure alone on transparency; base.png is it on the green key."""
    with Image.open(root / "cutout.png") as cut:
        alpha = cut.getchannel("A")
        bbox = alpha.getbbox()  # a matte's soft erode may shave a px off the edge
        assert all(abs(got - want) <= 1 for got, want in zip(bbox, figure)), bbox
        cx, cy = (figure[0] + figure[2]) // 2, (figure[1] + figure[3]) // 2
        assert alpha.getpixel((0, 0)) == 0 and alpha.getpixel((cx, cy)) == 255
    with Image.open(root / "base.png") as base:
        assert base.mode == "RGB"
        assert base.getpixel((0, 0)) == (0, 255, 0) and base.getpixel((base.width - 1, base.height - 1)) == (0, 255, 0)


def test_a_transparent_reference_is_its_own_cutout(chars, tmp_path, capsys):
    out = _new(capsys, tmp_path)["character"]
    assert out["ref"] == "frog/ref.png" and out["cutout"] == "frog/cutout.png" and out["base"] == "frog/base.png"
    assert (chars / "frog" / "ref.png").read_bytes() == (tmp_path / "ref.png").read_bytes()
    assert json.loads((chars / "frog" / "character.json").read_text())["base_from"]["cutout"] == {"route": "alpha"}
    _assert_cut_and_laid(chars / "frog", (90, 60, 210, 360))


@pytest.mark.parametrize("corners", [
    [(255, 255, 255)] * 4,  # flat white
    [(253, 253, 241), (253, 245, 232), (255, 250, 234), (235, 227, 208)],  # uneven ivory: the white matte misses a corner
    [(0, 255, 0)] * 4,  # already on the key: cut out and laid again like any other
], ids=["white", "ivory-gradient", "green-key"])
def test_an_opaque_reference_is_cut_out_and_the_cutout_laid_on_the_key(chars, tmp_path, capsys, corners):
    ref = _opaque(tmp_path / "ref.png", corners)
    _new_from(capsys, ref)
    root = chars / "frog"
    assert (root / "ref.png").read_bytes() == ref.read_bytes()
    cut = json.loads((root / "character.json").read_text())["base_from"]["cutout"]
    assert cut["route"] != "alpha" and cut["opaque_border_px"] == 0
    _assert_cut_and_laid(root, (100, 60, 200, 340))


def test_a_jpeg_reference_is_kept_byte_for_byte_under_its_own_extension(chars, tmp_path, capsys):
    ref = _opaque(tmp_path / "upload.png", [(255, 255, 255)] * 4, format="JPEG", quality=92)  # a JPEG named .png
    out = _new_from(capsys, ref)
    assert out["ref"] == "frog/ref.jpg" and not (chars / "frog" / "ref.png").exists()
    assert (chars / "frog" / "ref.jpg").read_bytes() == ref.read_bytes()
    _assert_cut_and_laid(chars / "frog", (100, 60, 200, 340))


def test_an_opaque_reference_with_no_background_to_find_is_refused(chars, tmp_path, capsys):
    ref = tmp_path / "ref.png"
    Image.effect_noise((300, 400), 120).convert("RGB").save(ref)
    capsys.readouterr()
    assert cli.main(["char", "new", "frog", "--ref", str(ref), "--description", "x", "--body-height", "200", "--json"]) == 1
    assert "cut-out" in json.loads(capsys.readouterr().out)["error"]
    assert not (chars / "frog").exists()


def test_body_height_is_required(chars, tmp_path, capsys):
    capsys.readouterr()
    assert cli.main(["char", "new", "frog", "--ref", str(_cutout(tmp_path / "r.png")), "--description", "x", "--json"]) == 1
    assert "--body-height" in json.loads(capsys.readouterr().out)["error"]
    assert not (chars / "frog").exists()


def test_no_action_without_a_character_and_no_locked_flag_on_an_action(chars, tmp_path, capsys, films):
    capsys.readouterr()
    assert cli.main(["char", "action", "add", "frog", "idle", "--state", "idle", "--motion", "m", "--json"]) == 1
    assert "no character" in json.loads(capsys.readouterr().out)["error"]
    _new(capsys, tmp_path)
    for flag in (["--resolution", "720p"], ["--provider", "xai"], ["--body-height", "100"], ["--facing", "left"]):
        assert cli.main(["char", "action", "add", "frog", "idle", "--state", "idle", "--motion", "m", *flag, "--json"]) == 1
        assert flag[0] in json.loads(capsys.readouterr().out)["error"]
    assert films == []


def test_dry_run_writes_nothing_and_films_nothing(chars, tmp_path, capsys, films):
    _new(capsys, tmp_path)
    out = char(capsys, "action", "add", "frog", "idle", "--state", "idle", "--motion", "The frog breathes.", "--dry-run")
    assert out["dry_run"] and "The frog breathes." in out["prompt"] and out["argv"][0] == "video-set"
    assert films == [] and not (chars / "frog" / "actions").exists()


def test_regen_keeps_the_old_version_and_use_switches_back(chars, tmp_path, capsys, films):
    _new(capsys, tmp_path)
    v1 = char(capsys, "action", "add", "frog", "idle", "--state", "idle", "--motion", "breathes")
    assert v1["version"]["ok"] and v1["version"]["prompt_match"] and v1["action"]["current"] == 1
    v2 = char(capsys, "action", "regen", "frog", "idle", "--motion", "breathes deeper")
    assert v2["action"]["current"] == 2 and [v["version"] for v in v2["action"]["versions"]] == [1, 2]
    assert (chars / "frog/actions/idle/v1/preview/frog.png").is_file()
    assert (chars / "frog/actions/idle/v2/preview/frog.meta.json").is_file()
    used = char(capsys, "action", "use", "frog", "idle", "1")["action"]
    assert used["current"] == 1
    files = used["versions"][0]["files"]
    assert files["png"] == "frog/actions/idle/v1/preview/frog.png" and files["clip"] == "frog/actions/idle/v1/side-idle/clip.mp4"
    assert used["versions"][0]["motion"] == "breathes" and used["versions"][1]["motion"] == "breathes deeper"


def test_a_failed_version_is_kept_but_never_current(chars, tmp_path, capsys, films):
    _new(capsys, tmp_path)
    char(capsys, "action", "add", "frog", "idle", "--state", "idle", "--motion", "breathes")
    capsys.readouterr()
    assert cli.main(["char", "action", "regen", "frog", "idle", "--motion", "FAIL", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert not out["ok"] and "clip refused" in out["error"]
    shown = char(capsys, "show", "frog")["character"]["actions"][0]
    assert shown["current"] == 1 and shown["versions"][1]["status"] == "failed"
    capsys.readouterr()
    assert cli.main(["char", "action", "use", "frog", "idle", "2", "--json"]) == 1


def test_rm_moves_the_action_to_trash_and_leaves_the_others(chars, tmp_path, capsys, films):
    _new(capsys, tmp_path)
    char(capsys, "action", "add", "frog", "idle", "--state", "idle", "--motion", "breathes")
    char(capsys, "action", "add", "frog", "walk", "--state", "walk", "--motion", "walks")
    out = char(capsys, "action", "rm", "frog", "idle")
    assert out["trash"].startswith("frog/_trash/idle-") and (chars / out["trash"] / "v1" / "version.json").is_file()
    shown = char(capsys, "show", "frog")["character"]
    assert [a["name"] for a in shown["actions"]] == ["walk"] and shown["actions"][0]["current"] == 1
    char(capsys, "action", "rm", "frog", "walk", "--purge")
    assert not (chars / "frog/actions/walk").exists() and len(list((chars / "frog/_trash").iterdir())) == 1


def test_a_spec_change_makes_old_versions_stale_and_build_skips_them(chars, tmp_path, capsys, films):
    _new(capsys, tmp_path)
    char(capsys, "action", "add", "frog", "idle", "--state", "idle", "--motion", "breathes")
    char(capsys, "action", "add", "frog", "walk", "--state", "walk", "--motion", "walks")
    built = char(capsys, "build", "frog")
    assert built["built"] == ["idle", "walk"] and built["files"]["png"] == "frog/build/frog.png"
    assert char(capsys, "spec", "frog", "--description", "The big frog")["changed"] == []
    assert char(capsys, "spec", "frog", "--resolution", "720p")["changed"] == ["resolution"]
    shown = char(capsys, "show", "frog")["character"]
    assert shown["spec_rev"] == 2 and all(a["current_stale"] for a in shown["actions"])
    capsys.readouterr()
    assert cli.main(["char", "build", "frog", "--json"]) == 1  # nothing current under this spec
    char(capsys, "action", "regen", "frog", "walk")
    assert "--resolution" in films[-1] and films[-1][films[-1].index("--resolution") + 1] == "720p"
    built = char(capsys, "build", "frog")
    assert built["built"] == ["walk"] and built["skipped"][0]["action"] == "idle"


def test_one_job_per_character(chars, tmp_path, capsys, films, monkeypatch):
    _new(capsys, tmp_path)
    (chars / "frog" / ".lock").write_text(json.dumps({"pid": 4242, "what": "action add idle", "since": "now"}))
    monkeypatch.setattr(ledger, "pid_alive", lambda pid: pid == 4242)
    capsys.readouterr()
    assert cli.main(["char", "action", "add", "frog", "idle", "--state", "idle", "--motion", "m", "--json"]) == 1
    assert "busy" in json.loads(capsys.readouterr().out)["error"] and films == []
    monkeypatch.setattr(ledger, "pid_alive", lambda pid: False)  # its process is gone: the lock is taken over
    assert char(capsys, "action", "add", "frog", "idle", "--state", "idle", "--motion", "m")["version"]["ok"]
    assert not (chars / "frog" / ".lock").exists()


def test_a_version_folder_left_unlisted_is_never_reused(chars, tmp_path, capsys, films):
    _new(capsys, tmp_path)
    char(capsys, "action", "add", "frog", "idle", "--state", "idle", "--motion", "breathes")
    (chars / "frog/actions/idle/v2").mkdir()  # a run that died between making v2 and listing it
    dry = char(capsys, "action", "regen", "frog", "idle", "--dry-run")
    assert dry["argv"][dry["argv"].index("--out-dir") + 1].endswith("v3")
    out = char(capsys, "action", "regen", "frog", "idle")
    assert out["version"]["version"] == 3 and out["action"]["current"] == 3


def test_a_file_held_open_by_another_process_is_waited_for(chars, tmp_path, monkeypatch):
    """Windows refuses to replace a file another process is reading (the Studio polls `char show`)."""
    calls = []

    def flaky(path, text):
        calls.append(path)
        if len(calls) < 3:
            raise PermissionError(13, "Access is denied")
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(ledger, "atomic_write_text", flaky)
    monkeypatch.setattr(ledger, "SHARING_WAIT_S", 0)
    ledger.write_json(tmp_path / "a.json", {"x": 1})
    assert len(calls) == 3 and ledger.read_json(tmp_path / "a.json") == {"x": 1}
