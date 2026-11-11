# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video --provider dmx`: the relay's submit → poll → download shape, offline.

The relay answers a submit with a task id only, and the clip comes back from a second POST that names the
query model; the url lives inside a JSON string in an OpenAI Responses envelope. These cases pin that shape
and the two things the engine must never do with a paid request: lose the id, or submit a second one.
"""
from __future__ import annotations

import json

import pytest

from http_answer import Answer
from sprite_gen.gen import dmx_video

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
TASK = "task_synthetic1"


def _envelope(url: str | None, *, status: str | None = None) -> dict:
    content: dict = {"video_url": url} if url else {}
    if status:
        content["status"] = status
    return {"request_id": TASK,
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps({"content": content})}]}]}


@pytest.fixture
def relay(monkeypatch):
    state = {"calls": [], "script": [], "slept": []}
    monkeypatch.setattr(dmx_video.retry, "_sleep", state["slept"].append)

    def open_request(request, *, timeout=None):
        body = json.loads(request.data) if request.data else None
        state["calls"].append((request.get_method(), request.full_url, body))
        if request.get_method() == "GET":
            return Answer(MP4, status=200)
        reply = state["script"].pop(0)
        return Answer(reply, status=200)

    monkeypatch.setattr(dmx_video.urllib.request, "urlopen", open_request)
    return state


def _body(**overrides):
    values = dict(model="doubao-seedance-2-0-260128", prompt="the fox breathes", image="data:image/png;base64,AAA",
                  last_frame=None, aspect_ratio=None, resolution="480p", duration=4, generate_audio=False)
    values.update(overrides)
    return dmx_video.build_body(**values)


def test_a_submit_carries_the_prompt_and_the_first_frame_then_polls_until_the_url(tmp_path, relay):
    relay["script"] = [{"id": TASK},                     # submit
                       _envelope(None, status="running"),  # first poll: not ready
                       _envelope("https://relay.example/i/c.mp4?x=1")]  # done
    published = dmx_video.submit_poll_publish(body=_body(), out=tmp_path / "clip.mp4", token="k", sleep=relay["slept"].append)
    submit = relay["calls"][0]
    assert submit[1] == "https://www.dmxapi.cn/v1/responses"
    assert submit[2]["input"] == [
        {"type": "text", "text": "the fox breathes"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}, "role": "first_frame"},
    ]
    assert submit[2]["duration"] == 4 and submit[2]["resolution"] == "480p"
    # The poll names the query model and the task id — never the generation model again.
    assert relay["calls"][1][2] == {"model": "seedance-2-0-get", "input": TASK}
    assert published.request_id == TASK and published.polls == 2
    assert (tmp_path / "clip.mp4").read_bytes() == MP4


def test_the_closing_frame_rides_as_last_frame(tmp_path, relay):
    relay["script"] = [{"id": TASK}, _envelope("https://relay.example/i/c.mp4")]
    dmx_video.submit_poll_publish(body=_body(last_frame="data:image/png;base64,BBB"),
                                  out=tmp_path / "c.mp4", token="k")
    roles = [part.get("role") for part in relay["calls"][0][2]["input"] if part["type"] == "image_url"]
    assert roles == ["first_frame", "last_frame"]


def test_a_failed_task_ends_loud_and_names_the_request(tmp_path, relay):
    relay["script"] = [{"id": TASK}, _envelope(None, status="failed")]
    with pytest.raises(SystemExit, match=TASK):
        dmx_video.submit_poll_publish(body=_body(), out=tmp_path / "c.mp4", token="k")


def test_a_download_that_is_not_an_mp4_is_refused_before_publishing(tmp_path, relay, monkeypatch):
    relay["script"] = [{"id": TASK}, _envelope("https://relay.example/i/c.mp4")]
    monkeypatch.setattr(dmx_video, "_download", lambda url, *, deadline=None: b"<html>nope</html>")
    with pytest.raises(SystemExit, match="not an mp4"):
        dmx_video.submit_poll_publish(body=_body(), out=tmp_path / "c.mp4", token="k")
    assert not (tmp_path / "c.mp4").exists()


def test_the_query_model_follows_the_family_and_can_be_named(monkeypatch):
    monkeypatch.delenv(dmx_video.QUERY_MODEL_ENV, raising=False)
    assert dmx_video.query_model("doubao-seedance-2-0-260128") == "seedance-2-0-get"
    assert dmx_video.query_model("doubao-seedance-2-5-260628") == "seedance-2-5-get"
    monkeypatch.setenv(dmx_video.QUERY_MODEL_ENV, "seedance-x-get")
    assert dmx_video.query_model("doubao-seedance-2-0-260128") == "seedance-x-get"


def test_the_url_is_read_out_of_the_envelope_and_the_text_form():
    reply = _envelope("https://relay.example/i/c.mp4?sig=1")
    assert dmx_video._video_url(dmx_video._answer(reply)) == "https://relay.example/i/c.mp4?sig=1"
    # A text answer that is not JSON but carries a clip link is still read.
    assert dmx_video._answer({"output": [{"content": [{"type": "output_text",
                                                       "text": "done: https://x.example/a.mp4"}]}]})["video_url"] == \
        "https://x.example/a.mp4"
