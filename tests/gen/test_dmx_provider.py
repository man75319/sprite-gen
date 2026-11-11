# SPDX-License-Identifier: Apache-2.0
"""The DMXAPI `/responses` provider: what a call sends, what it publishes, and what it refuses.

The relay's route is not the OpenAI Images API (that one answers 404 for every `doubao-seedream-*` model), so
these cases pin the body shape the relay documents — `input`, `size`, `image` — and the two answers an image
can come back in (inline Base64 first, a link second). Nothing reaches the network: a local replacement for
`urlopen` stands in for the relay.
"""
from __future__ import annotations

import base64
import io
import json

import pytest
import urllib.error
from PIL import Image

from http_answer import Answer
from sprite_gen import gen
from sprite_gen.gen import dmx_provider as dmx
from sprite_gen.gen import retry

KEY = "relay-token"


def encoded_image(fmt="PNG", *, key=False):
    """A 12x8 fixture: a 4x4 subject block on a plain or magenta field."""
    image = Image.new("RGB", (12, 8), (255, 0, 255) if key else (20, 90, 200))
    for x in range(4, 8):
        for y in range(2, 6):
            image.putpixel((x, y), (20, 90, 200))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("DMX_API_KEY", KEY)
    monkeypatch.delenv("SPRITE_GEN_DMX_BASE_URL", raising=False)
    state = {"status": 200, "body": {"data": [{"b64_json": encoded_image()}], "usage": {"total_tokens": 3000}},
             "headers": {}, "calls": [], "slept": []}
    monkeypatch.setattr(retry, "_sleep", state["slept"].append)

    def open_request(request, *, timeout=None):
        state["calls"].append((request, timeout))
        body = Answer(state["body"], status=state["status"], headers=state["headers"])
        if state["status"] == 200:
            return body
        raise urllib.error.HTTPError(request.full_url, state["status"], "error", state["headers"], body)

    monkeypatch.setattr(dmx.urllib.request, "urlopen", open_request)
    return state


def _body(request):
    return json.loads(request.data)


def test_a_call_posts_the_documented_responses_body_and_publishes_the_png(tmp_path, api):
    result = gen.generate_image("dmx", "a blue square", tmp_path / "out.png", model="doubao-seedream-5-0-pro-260628")
    request, _timeout = api["calls"][0]
    assert request.full_url == "https://www.dmxapi.cn/v1/responses"
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert _body(request) == {
        "model": "doubao-seedream-5-0-pro-260628",
        "input": "a blue square",
        # No `--resolution`, so the fixed 1K tier rather than the model's own 2K default: the same call costs and
        # returns the same thing every time.
        "size": "1K",
        "output_format": "png",
        "response_format": "b64_json",
        "watermark": False,
    }
    with Image.open(result.out) as image:
        assert image.format == "PNG" and image.size == (12, 8)
    report = result.to_dict()
    assert report["provider"] == "dmx" and report["model"] == "doubao-seedream-5-0-pro-260628"
    assert report["extra"]["transport"] == "dmx-responses"
    assert report["extra"]["image_answer"] == "b64_json"
    assert report["extra"]["usage"] == {"total_tokens": 3000}
    assert KEY not in json.dumps(report)


def test_transparent_asks_for_the_planned_key_and_keys_it_out(tmp_path, api):
    """Seedream returns no alpha, so the provider asks for the key the engine will remove — one sentence, added
    only because this run keys the background out and the prompt names none."""
    api["body"] = {"data": [{"b64_json": encoded_image(key=True)}]}
    result = gen.generate_image("dmx", "a blue square", tmp_path / "keyed.png", transparent=True)
    sent = _body(api["calls"][0][0])["input"]
    assert "pure magenta chroma-key fill (#FF00FF)" in sent and sent.startswith("a blue square")
    assert result.alpha["strategy"] == "chroma"
    with Image.open(result.out) as image:
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((6, 4))[3] == 255


def test_a_prompt_that_already_names_a_key_is_not_given_a_second_line(tmp_path, api):
    api["body"] = {"data": [{"b64_json": encoded_image(key=True)}]}
    gen.generate_image("dmx", "a blue square on a flat green screen", tmp_path / "out.png", transparent=True,
                       chroma_key="green")
    assert _body(api["calls"][0][0])["input"].count("chroma-key fill") == 0


def test_one_reference_is_a_string_and_several_are_a_list(tmp_path, api):
    one = tmp_path / "one.png"
    Image.new("RGB", (12, 8), (10, 20, 30)).save(one)
    gen.generate_image("dmx", "keep this character", tmp_path / "a.png", refs=[one])
    assert isinstance(_body(api["calls"][0][0])["image"], str)
    assert _body(api["calls"][0][0])["image"].startswith("data:image/png;base64,")

    two = tmp_path / "two.png"
    Image.new("RGB", (12, 8), (40, 50, 60)).save(two)
    gen.generate_image("dmx", "keep this character", tmp_path / "b.png", refs=[one, two])
    assert isinstance(_body(api["calls"][1][0])["image"], list) and len(_body(api["calls"][1][0])["image"]) == 2


def test_resolution_and_aspect_ratio_both_land_and_stay_in_the_documented_window(tmp_path, api):
    gen.generate_image("dmx", "x", tmp_path / "a.png", resolution="2k")
    assert _body(api["calls"][0][0])["size"] == "2K"
    gen.generate_image("dmx", "x", tmp_path / "b.png", resolution="1k", aspect_ratio="4:1")
    width, height = (int(side) for side in _body(api["calls"][1][0])["size"].split("x"))
    assert width / height == pytest.approx(4, abs=0.02)
    assert 1280 * 720 <= width * height <= 4_624_220
    assert width % 16 == 0 and height % 16 == 0


def test_a_quality_level_this_model_cannot_honour_is_refused_before_the_send(tmp_path, api):
    with pytest.raises(SystemExit, match="no quality level"):
        gen.generate_image("dmx", "x", tmp_path / "out.png", quality="high")
    assert api["calls"] == []


def test_an_unasked_ratio_is_refused_rather_than_rounded(tmp_path, api):
    with pytest.raises(SystemExit, match="no Seedream size"):
        gen.generate_image("dmx", "x", tmp_path / "out.png", aspect_ratio="7:3")
    assert api["calls"] == []


def test_a_refusal_is_reported_with_the_relays_own_words(tmp_path, api):
    api["status"], api["body"] = 400, {"error": {"code": "invalid_request",
                                                 "message": "model \"x\" is not served by this plugin"}}
    with pytest.raises(SystemExit, match=r"HTTP 400.*not served by this plugin"):
        gen.generate_image("dmx", "x", tmp_path / "out.png")


def test_a_link_answer_is_downloaded_and_published(tmp_path, api, monkeypatch):
    """`image_answer: url` is the relay's other documented form; the bytes still decide, not the link."""
    png = base64.b64decode(encoded_image())

    def open_request(request, *, timeout=None):
        api["calls"].append((request, timeout))
        if request.get_method() == "GET":
            return Answer(png, status=200)
        return Answer({"data": [{"url": "https://relay.example/i/1.png"}]}, status=200)

    monkeypatch.setattr(dmx.urllib.request, "urlopen", open_request)
    result = gen.generate_image("dmx", "x", tmp_path / "out.png")
    assert result.extra["image_answer"] == "url"
    with Image.open(result.out) as image:
        assert image.size == (12, 8)


def test_the_provider_is_registered_and_is_never_a_default():
    assert "dmx" in gen.PROVIDERS
    assert isinstance(gen._make_provider("dmx", keep_session=False), dmx.DmxProvider)
    assert dmx.DmxProvider().transparency == "chroma"
    assert "dmx" in gen.EXPLICIT_ONLY_PROVIDERS
