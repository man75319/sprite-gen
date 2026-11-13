# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen char …` with video-set swapped for a stand-in that spends nothing: the Studio's `--fake` mode
(`npm --prefix studio run dev:fake`). Everything else is the real CLI: ledger, lock, versions, export-phaser.

The stand-in writes what a finished video-set run leaves (set.report.json, the clip and its report, a loop strip),
over STUDIO_FAKE_SECONDS (default 12) with a log line a second, so the page's job polling and log tail have
something to follow. The clip's report carries the planned prompt, so prompt_match is true; a motion that says FAIL
fails like a refused clip. Each version is drawn in its own colour, so switching versions is visible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

from sprite_gen import cli
from sprite_gen.char import char_cmd, char_plan, ledger

REPO = Path(__file__).resolve().parents[2]
FFMPEG = REPO / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
COLOURS = [(220, 90, 60), (60, 140, 220), (90, 190, 90), (200, 160, 40), (170, 90, 200)]


def _strip(path: Path, state: str, colour: tuple[int, int, int], frames: int = 8, w: int = 96, h: int = 128) -> None:
    sheet = Image.new("RGBA", (w * frames, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for i in range(frames):
        x0 = i * w
        bob = (i % 4) * 3 if state != "walk" else 0
        step = (i - frames // 2) * 3 if state == "walk" else 0
        draw.rectangle([x0 + 30 + step, 20 + bob, x0 + 66 + step, 110], fill=(*colour, 255))  # body
        draw.ellipse([x0 + 34 + step, 2 + bob, x0 + 62 + step, 30 + bob], fill=(*colour, 255))  # head
        draw.rectangle([x0 + 32, 110, x0 + 44, h - 1], fill=(40, 40, 40, 255))  # feet on the bottom row
        draw.rectangle([x0 + 52, 110, x0 + 64, h - 1], fill=(40, 40, 40, 255))
    sheet.save(path)


def _clip(path: Path, version: int, colour: tuple[int, int, int]) -> None:
    hexc = "0x%02x%02x%02x" % colour
    if FFMPEG.is_file():
        vf = (f"drawbox=x=0:y=0:w=iw:h=ih:color={hexc}@0.35:t=fill,"
              f"drawtext=text='v{version} frame %{{n}}':x=20:y=20:fontsize=32:fontcolor=white")
        code = subprocess.call([str(FFMPEG), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=480x480:rate=24:duration=3",
                                "-vf", vf, "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.DEVNULL)
        if code == 0:
            return
    path.write_bytes(b"")  # no ffmpeg: an empty file, the player shows an error


def fake_video_set(argv: list[str], log: Path) -> int:
    seconds = float(os.environ.get("STUDIO_FAKE_SECONDS", "12"))
    out = Path(argv[argv.index("--out-dir") + 1])
    state = argv[argv.index("--states") + 1]
    motion = argv[argv.index("--motion") + 1].split("=", 1)[1]
    cid, name = out.parts[-4], out.parts[-2]
    version = int(out.name.lstrip("v"))
    character, action = ledger.load_character(cid), ledger.load_action(cid, name)
    colour = COLOURS[(version - 1) % len(COLOURS)]
    item = out / f"side-{state}"
    loop = item / "loop"
    loop.mkdir(parents=True)
    with log.open("w", encoding="utf-8") as fh:
        fh.write(f"[fake] video-set {' '.join(argv)}\n")
        fh.flush()
        steps = max(1, int(seconds))
        for i in range(steps):
            time.sleep(seconds / steps)
            fh.write(f"[fake] filming {cid}/{name} v{version}: {i + 1}/{steps}\n")
            fh.flush()
        ok = "FAIL" not in motion
        _clip(item / "clip.mp4", version, colour)
        prompt = char_plan.planned_prompt(character, action)["prompt"]
        (item / "clip.report.json").write_text(json.dumps({"prompt": prompt}, ensure_ascii=False), encoding="utf-8")
        if ok:
            _strip(loop / f"side-{state}.strip.png", state, colour)
            body_h = int(argv[argv.index("--body-height") + 1])
            (loop / f"side-{state}.strip.json").write_text(json.dumps(
                {"frames": 8, "w": 96, "h": 128, "fps": 12.0, "delay_ms": 83.33, "loop": True, "kind": "periodic",
                 "body_h": body_h, "state": state}))
        rec = {"item": f"side-{state}", "ok": ok, **({"loop": {"seam_ratio": 0.1}} if ok else {"error": "clip refused (fake)"})}
        (out / "set.report.json").write_text(json.dumps({"items": [rec], "warnings": [], "failed": [] if ok else [rec["item"]]}))
        fh.write(f"[fake] {'done' if ok else 'failed'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    char_cmd.run_video_set = fake_video_set
    sys.exit(cli.main(sys.argv[1:]))
