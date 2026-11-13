# SPDX-License-Identifier: Apache-2.0
"""docs/char.md rule one: the prompt `char action add … --dry-run` prints is the prompt the real `video-set` sends.

The real `video-set` runs here from the argv `char` builds, end to end up to the clip: its facing check is answered
offline, and its clip call (`batch.run_logged`, the child `sprite-gen video`) is caught instead of run. What it would
have sent is compared with the plan, byte for byte, along with the provider, model and resolution on that call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sprite_gen import cli
from sprite_gen.char import char_plan
from sprite_gen.video import batch

FIXTURE = Path(__file__).parents[1] / "fixtures" / "facing" / "right-key.png"


def _character(**spec):
    return {"id": "hero", "description": "The small green frog knight",
            "spec": {"provider": "dmx", "model": "doubao-seedance-2-0-260128", "resolution": "480p", "direction": "side",
                     "facing": "right", "key": "green", "body_plan": "biped", "body_height": 200, "fps": None,
                     "walk_start": "as-given", **spec}}


def _sent(monkeypatch, argv: list[str]) -> list[str]:
    calls: list[list[str]] = []
    monkeypatch.setattr(batch.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("right", {}))
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)

    def caught(cmd, log):
        calls.append([str(c) for c in cmd])
        return 1  # no clip: the item fails right after the call, nothing else runs
    monkeypatch.setattr(batch, "run_logged", caught)
    assert cli.main(argv) == 1
    assert len(calls) == 1
    return calls[0]


@pytest.mark.parametrize("state,motion,seconds,spec", [
    ("idle", "The frog breathes slowly, its throat pulsing.", None, {}),
    ("walk", "The frog waddles forward in place.", None, {"body_plan": "quadruped"}),
    ("attack", "The frog lunges and stabs with its tiny sword, then steps back.", 1.5, {}),
    ("attack", "The frog lunges and stabs.", None, {"facing": "left"}),
    ("hop", "The frog crouches and hops straight up, landing where it started.", 0.8, {"body_plan": "legless"}),
], ids=["idle", "walk-quadruped", "attack-seconds", "attack-left", "own-state"])
def test_the_dry_run_prompt_is_the_one_video_set_sends(tmp_path, monkeypatch, state, motion, seconds, spec):
    character = _character(**spec)
    action = {"name": f"{state}-a", "state": state, "motion": motion, "action_seconds": seconds}
    argv = ["video-set", *char_plan.video_set_argv(character, action, base=FIXTURE, out_dir=tmp_path / "v1")]
    cmd = _sent(monkeypatch, argv)
    planned = char_plan.planned_prompt(character, action)["prompt"]
    assert cmd[cmd.index("--prompt") + 1] == planned
    s = character["spec"]
    assert cmd[cmd.index("--provider") + 1] == s["provider"] and cmd[cmd.index("--model") + 1] == s["model"]
    assert cmd[cmd.index("--resolution") + 1] == s["resolution"]


def test_fps_is_passed_only_when_set_since_out_fps_must_be_above_the_clips_rate():
    action = {"name": "idle", "state": "idle", "motion": "breathes", "action_seconds": None}
    assert "--out-fps" not in char_plan.video_set_argv(_character(), action, base=FIXTURE, out_dir=Path("v1"))
    argv = char_plan.video_set_argv(_character(fps=30.0), action, base=FIXTURE, out_dir=Path("v1"))
    assert argv[argv.index("--out-fps") + 1] == "30"
