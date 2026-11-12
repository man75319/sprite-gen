# SPDX-License-Identifier: Apache-2.0
"""A motion asked for at its own length (`video-set --action-seconds`, `video-prompt --action-seconds`): the clip is the
least the provider films that holds two of it, the prompt asks for as many as fill the clip, it is filmed unpinned and
its loop searched, and the record says how many were asked for and how many the clip held. Synthetic fixtures only."""

from __future__ import annotations

import json

import pytest

from sprite_gen.video import batch, clip_prompt
from test_play import still, stub_set

CRAB = "It thrusts its big front claw forward and snaps the pincer shut, then pulls it back to its ready pose."


@pytest.mark.parametrize("seconds,requested,provider,duration,repeats", [
    (1.0, None, "dmx", 4, 4),    # Seedance films 4 s at least: four of a one-second strike
    (1.5, None, "dmx", 4, 3),
    (3.0, None, "dmx", 6, 2),    # two of it at least, so the loop can be found
    (1.0, None, "xai", 2, 2),    # Grok films 1 s: two of it
    (1.0, None, None, 2, 2),     # no provider named: Grok, the default
    (1.0, 8, "dmx", 8, 8),       # --duration wins
    (5.0, 4, "dmx", 4, 2),       # never fewer than two asked for
])
def test_the_clip_and_the_count_follow_from_the_motions_length(seconds, requested, provider, duration, repeats) -> None:
    plan = batch.action_plan(seconds, requested, provider)
    assert (plan["duration"], plan["repeats_asked"]) == (duration, repeats)
    assert plan["clip_min"] == batch.CLIP_DURATION_MIN.get(provider or "xai")


def test_a_length_of_nothing_is_refused() -> None:
    with pytest.raises(SystemExit, match="over 0"):
        batch.action_plan(0, None, "dmx")


def test_built_in_phase_timings_are_scaled_to_one_motion() -> None:
    scaled = batch.scale_timings("a windup (about 0.5 s), a strike (about 0.25 s), a recovery (about 0.25 s).", 2.0)
    assert scaled == "a windup (about 1 s), a strike (about 0.5 s), a recovery (about 0.5 s)."
    assert batch.scale_timings("no timings here", 1.0) == "no timings here"


def test_the_prompt_asks_for_the_count_and_no_return_to_the_first_frame() -> None:
    record = clip_prompt.plan_prompt(direction="side", state="attack", character="The red crab", motion=CRAB,
                                     action_seconds=1.0, provider="dmx")
    assert record["action"]["repeats_asked"] == 4 and record["duration"] == 4
    assert batch.ACTION_REPEAT_TEXT.format(n=4) in record["prompt"]
    assert record["last_frame"] is None and record["cycle"] == "periodic"
    built_in = clip_prompt.plan_prompt(direction="side", state="attack", action_seconds=1.0, provider="dmx")["prompt"]
    timings = [float(t) for t in batch._TIMING.findall(built_in)]
    assert timings and abs(sum(timings) - 1.0) < 0.05


def test_without_it_an_attack_is_filmed_as_before() -> None:
    record = clip_prompt.plan_prompt(direction="side", state="attack", character="The red crab", motion=CRAB)
    assert "action" not in record and record["last_frame"] == "canvas" and record["cycle"] == "pinned"
    assert "times in a row" not in record["prompt"]


def test_video_set_films_it_unpinned_and_records_what_the_clip_held(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "CLIP_PROVIDER", "dmx")
    report, seen = stub_set(tmp_path, monkeypatch, bases={"side": still(tmp_path / "side.png")}, states=["attack", "idle"],
                            action_seconds={"attack": 1.0}, motion={"attack": CRAB})
    assert report["ok"] == 2, [r.get("error") for r in report["items"]]
    rows = {r["item"]: r for r in report["items"]}
    assert seen["side-attack"]["last_frame"] is None and seen["side-attack"]["cycle"] == "periodic"
    assert batch.ACTION_REPEAT_TEXT.format(n=4) in seen["side-attack"]["prompt"]
    action = rows["side-attack"]["action"]
    assert action["repeats_asked"] == 4 and action["duration"] == 4 and rows["side-attack"]["clip"]["duration"] == 4
    # the stub's clip is 24 frames and its loop 24: one of it, at 1 s
    assert action["repeats_seen"] == 1.0 and action["cut_seconds"] == 1.0
    # the idle beside it keeps its pin and its cut
    assert seen["side-idle"]["cycle"] == "pinned" and "action" not in rows["side-idle"]


def test_video_set_refuses_it_with_a_mode_that_is_no_loop(tmp_path, monkeypatch) -> None:
    called = []
    monkeypatch.setattr(batch, "run_item", lambda **kw: called.append(kw))
    with pytest.raises(SystemExit, match="--action-seconds"):
        batch.run_set(bases={"side": still(tmp_path / "side.png")}, states=["punch"], root=tmp_path / "set",
                      character=None, duration=None, resolution="480p", key="green", concurrency=1, force=False, gap=0,
                      play={"punch": "once-return"}, action_seconds={"punch": 1.0})
    assert called == []


@pytest.mark.parametrize("values,match", [(["attack=0"], "over 0"), (["attack=fast"], "not a number")])
def test_the_flag_takes_seconds_over_nothing(values, match) -> None:
    with pytest.raises(SystemExit, match=match):
        batch._parse_seconds(values)
