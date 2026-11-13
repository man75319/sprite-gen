# SPDX-License-Identifier: Apache-2.0
"""One action of a character as the `video-set` run that films it, and the prompt that run sends (docs/char.md section 1).

Rule one of docs/char.md: the prompt `char … --dry-run` shows is the prompt the run sends. `video_set_argv` is the
one place a character's spec becomes flags, and `planned_prompt` builds the prompt from the same values the way
`video-set` does (`batch.run_set` → `batch.run_item` → `batch.clip_prompt_parts`), without its paid facing check.
tests/char/test_char_plan.py drives the real `video-set` with that argv (its clip call caught) and holds the two equal;
a real run also compares its `clip.report.json` prompt with the plan and records `prompt_match`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sprite_gen.video import batch as batch_mod
from sprite_gen.video import body_plan as body_mod
from sprite_gen.video import play as play_mod


def item_name(character: dict[str, Any], action: dict[str, Any]) -> str:
    """The item folder video-set makes for the action: `<direction>-<state>`."""
    return f"{character['spec']['direction']}-{action['state']}"


def video_set_argv(character: dict[str, Any], action: dict[str, Any], *, base: Path, out_dir: Path) -> list[str]:
    """The `sprite-gen video-set` arguments (after the verb) that film one action of the character."""
    spec = character["spec"]
    state = action["state"]
    argv = [
        "--provider", spec["provider"],
        "--model", spec["model"],
        "--base", f"{spec['direction']}={base}",
        "--states", state,
        "--facing", spec["facing"],
        "--body-plan", spec["body_plan"],
        "--character", character["description"],
        "--resolution", spec["resolution"],
        "--key", spec["key"],
        "--walk-start", spec["walk_start"],
        "--body-height", str(int(spec["body_height"])),
        "--motion", f"{state}={action['motion']}",
        "--out-dir", str(out_dir),
    ]
    if spec.get("fps"):
        argv += ["--out-fps", f"{float(spec['fps']):g}"]
    if action.get("action_seconds"):
        argv += ["--action-seconds", f"{state}={float(action['action_seconds']):g}"]
    return argv


def planned_prompt(character: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """The clip prompt the action's video-set sends, and what goes with it, built as `batch.run_item` builds it."""
    spec = character["spec"]
    direction, state = spec["direction"], action["state"]
    play = None
    extra: dict[str, Any] = {}
    plan = None
    if action.get("action_seconds"):
        # run_set gives an item with --action-seconds the duration asked for (none here), not dmx's default 4 s
        plan = batch_mod.action_plan(float(action["action_seconds"]), None, spec["provider"])
        play = play_mod.LOOP
        extra = {"pinned": False, "repeats": plan["repeats_asked"], "action_seconds": plan["seconds"]}
    parts = batch_mod.clip_prompt_parts(direction, state, character["description"], facing=spec["facing"],
                                        motion=action["motion"], handed=None,
                                        body_plan=body_mod.parse_all([spec["body_plan"]]) or None, play=play, **extra)
    return {
        "prompt": parts.text,
        "play": play_mod.resolve(state, play, prog="char"),
        **({"action": plan} if plan else {}),
        **({"notes": parts.notes} if parts.notes else {}),
    }
