# SPDX-License-Identifier: Apache-2.0
"""Doubao Seedream images through DMXAPI's OpenAI-Responses-shaped `/responses` route.

The relay's own catalogue is not on the OpenAI Images API: `POST /v1/images/generations` answers
`404 openai_error` for every `doubao-seedream-*` model, and its documentation puts both the image models
(Seedream) and the video models (Seedance) on `POST /v1/responses`. So this adapter speaks that route
and nothing else:

    POST <base>/responses
    {"model": "doubao-seedream-5-0-pro-260628",
     "input": "<prompt>",
     "size": "1K" | "1.5K" | "2K" | "<w>x<h>",
     "output_format": "png", "response_format": "b64_json", "watermark": false}

and reads the image back out of `data[0].b64_json` (or `data[0].url`, when the relay answers a link
instead), exactly as `openai_provider` reads the Images API. Reference images go in the same body's
`image` field — a string for one, a list for 2..10 (the relay's documented multi-image form) — as
`data:image/...;base64,` URIs.

Seedream returns no alpha channel and the route has no `background` knob, so this provider declares
`chroma`: the orchestrator keys the background out deterministically, and — because the engine's own key
line is only injected when *it* chose chroma for another reason — the provider asks for the planned key
in the prompt itself (`chroma.KEY_BACKGROUND_TEXT`), unless the prompt already names one.

Billed per call against `DMX_API_KEY`, so like `openai` it is never a default: it runs only when
`--provider dmx` (or another verb's explicit `--provider dmx`) names it, and every call says the charge
on stderr before the request leaves.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError

from . import address, refusal, retry
from .base import (
    GEN_TIMEOUT_SECONDS,
    PROVIDER_DMX,
    TRANSPARENCY_CHROMA,
    GenRequest,
    ProviderRun,
    announce_api_billing,
    env_api_key,
    publish_png,
)
from .xai import NO_ANSWER, error_body, read_whole, time_left

AUTH_ENV = "DMX_API_KEY"
AUTH_SOURCE_API_KEY = AUTH_ENV

DEFAULT_MODEL = "doubao-seedream-5-0-pro-260628"
# Seedream has no quality ladder: `--quality` is refused rather than dropped from a body the caller pays for.
SUPPORTED_QUALITIES = ("auto",)
# The relay's documented resolution tiers; the model's own default is 2K, which is not used here so a
# call without a size costs and returns the same thing every time.
RESOLUTION_TIERS = {"1k": "1K", "1.5k": "1.5K", "2k": "2K"}
DEFAULT_RESOLUTION = "1k"
# One reference is `image: "<uri>"`; 2..10 is `image: ["<uri>", ...]` (the relay's multi-image form).
MAX_REFS = 10
# The documented pixel window for an explicit size, and the ratio window.
MIN_PIXELS = 1280 * 720
MAX_PIXELS = 4_624_220
MIN_RATIO, MAX_RATIO = 1 / 16, 16

# The 1K tier's pixel size per ratio, each inside the documented window (its ratio exactly right) so the
# ratio a caller asks for is the ratio that comes back. `--resolution` scales the same ratio, never swaps it.
ASPECT_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "4:3": (1152, 864),
    "3:4": (864, 1152),
    "3:2": (1248, 832),
    "2:3": (832, 1248),
    "16:9": (1536, 864),
    "9:16": (864, 1536),
    "2:1": (1440, 720),
    "1:2": (720, 1440),
    "3:1": (1728, 576),
    "1:3": (576, 1728),
    "4:1": (2048, 512),
    "1:4": (512, 2048),
}
TIER_SCALE = {"1k": 1.0, "1.5k": 1.5, "2k": 2.0}

# The vision model a facing/lettering check reads on, over the same route. A relay's vision model, not one of
# its image models: `inspect_image` asks a question about an attached image. Overridable for another relay.
VISION_MODEL_ENV = "SPRITE_GEN_DMX_VISION_MODEL"
DEFAULT_VISION_MODEL = "doubao-seed-2-1-pro-260915"

_REFERENCE_MIMES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/bmp": "bmp"}


def resolve_credential(env: Mapping[str, str] | None = None) -> str:
    """The relay's API key (`base.env_api_key`), or fail loud naming the variable."""
    key = env_api_key(AUTH_ENV, env, who="dmx-gen", also=". DMXAPI bills this key per call.")
    if key:
        return key
    raise SystemExit(
        f"dmx-gen: no DMXAPI credential — {AUTH_ENV} is not set.\n"
        f"  export {AUTH_ENV}=<your DMXAPI key> (dmxapi.cn → 令牌). This provider never falls back to "
        "another key or login."
    )


def _round16(value: float) -> int:
    return max(16, int(round(value / 16.0)) * 16)


def _size(request: GenRequest) -> str:
    """The body's `size`: a tier name, or `"<w>x<h>"` for an asked-for ratio, inside the documented window.

    `--resolution` and `--aspect-ratio` are not alternatives here: the ratio fixes the shape and the tier
    scales it, so a 4:1 strip at `--resolution 2k` is a 2K-scaled 4:1 size rather than one of the two flags
    being silently dropped.
    """
    resolution = (request.resolution or DEFAULT_RESOLUTION).lower()
    if resolution not in RESOLUTION_TIERS:
        raise SystemExit(
            f"dmx-gen: unsupported resolution {request.resolution!r}; expected one of {', '.join(RESOLUTION_TIERS)}"
        )
    ratio = request.aspect_ratio
    if ratio in (None, "", "auto"):
        return RESOLUTION_TIERS[resolution]
    if ratio not in ASPECT_SIZES:
        raise SystemExit(
            f"dmx-gen: aspect ratio {ratio!r} has no Seedream size; expected one of {', '.join(ASPECT_SIZES)}"
        )
    base_width, base_height = ASPECT_SIZES[ratio]
    scale = TIER_SCALE[resolution]
    # A tier that would exceed the window is cut to it, keeping the ratio: both sides shrink by the same factor.
    if base_width * base_height * scale * scale > MAX_PIXELS:
        scale = math.sqrt(MAX_PIXELS / (base_width * base_height))
    width, height = _round16(base_width * scale), _round16(base_height * scale)
    # Rounding to 16 can leave the product a hair outside the window; nudge the long side, never the ratio's side.
    while width * height < MIN_PIXELS:
        width, height = width + 16, height + 16
    while width * height > MAX_PIXELS:
        width, height = width - 16, height - 16
    if not (MIN_RATIO <= width / height <= MAX_RATIO):
        raise SystemExit(f"dmx-gen: aspect ratio {ratio!r} is outside Seedream's {MIN_RATIO:.4g}..{MAX_RATIO} window")
    return f"{width}x{height}"


def _reference(path: Path) -> str:
    """One reference image as a `data:` URI (the relay's documented Base64 form); refused before the send."""
    try:
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            mime = Image.MIME.get(source.format)
        if mime not in _REFERENCE_MIMES:
            raise ValueError("reference must be PNG, JPEG, WebP or BMP")
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise SystemExit(f"dmx-gen: invalid reference image {path}") from exc
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _prompt(request: GenRequest) -> str:
    """The caller's prompt, plus the planned key background when the run keys one out and the prompt names none.

    The engine injects its own key line only when *it* chose chroma over a native provider (`--ref` step-down);
    a chroma-only provider that adds nothing would be keying whatever background the model felt like — white,
    a gradient — and `key_transparent` removes nothing. One sentence, the same one the engine uses.
    """
    prompt = request.prompt
    if not request.chroma_key:
        return prompt
    from . import chroma as chroma_mod

    if chroma_mod.named_key_background(prompt) is not None:
        return prompt
    return prompt.rstrip() + " " + chroma_mod.KEY_BACKGROUND_TEXT[request.chroma_key]


def _body(request: GenRequest) -> dict[str, Any]:
    """The `/responses` body, every refusal decided offline before a credential is read or a socket opened."""
    if not request.prompt.strip():
        raise SystemExit("dmx-gen: empty prompt")
    if request.native_alpha:
        raise SystemExit("dmx-gen: Seedream returns no alpha channel; generate on a chroma key instead")
    if len(request.refs) > MAX_REFS:
        raise SystemExit(f"dmx-gen: at most {MAX_REFS} reference images are supported")
    if request.quality is not None and request.quality not in SUPPORTED_QUALITIES:
        raise SystemExit(
            f"dmx-gen: Seedream has no quality level, so {request.quality!r} cannot be honoured; "
            "use --resolution (1k|1.5k|2k) or --aspect-ratio, or another provider"
        )
    body: dict[str, Any] = {
        "model": request.model or DEFAULT_MODEL,
        "input": _prompt(request),
        "size": _size(request),
        "output_format": "png",
        "response_format": "b64_json",
        "watermark": False,
    }
    if request.refs:
        references = [_reference(Path(ref)) for ref in request.refs]
        body["image"] = references[0] if len(references) == 1 else references
    return body


TRANSPORT_FAILED = "dmx-gen: request failed or timed out; no automatic retry (the server may have accepted it)"


def _send(url: str, token: str, body: dict) -> tuple[int, Any, Any]:
    """POST one request and return (status, parsed JSON, headers); a transport error is terminal."""
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=GEN_TIMEOUT_SECONDS) as response:
            raw = read_whole(response, GEN_TIMEOUT_SECONDS, None).decode("utf-8")
            return response.status, (json.loads(raw) if raw else {}), response.headers
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit("dmx-gen: response was not valid JSON") from exc
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, error_body(exc, GEN_TIMEOUT_SECONDS, None), exc.headers
        except NO_ANSWER as cut:
            raise SystemExit(TRANSPORT_FAILED) from cut
    except NO_ANSWER as exc:
        raise SystemExit(TRANSPORT_FAILED) from exc


def _ask(url: str, token: str, body: dict, *, label: str = "gen") -> tuple[int, Any, int]:
    """POST one request, sent again as `retry` says; `SPRITE_GEN_GEN_TIMEOUT_SECONDS` bounds the whole call."""
    deadline = time.monotonic() + GEN_TIMEOUT_SECONDS
    status, reply, _headers, asks = retry.ask(
        lambda: _send(url, token, body), provider=address.DMX, who="dmx", label=label,
        kind=refusal.provider_code, deadline=deadline,
    )
    return status, reply, asks


def _fetch(url: str, token: str) -> bytes:
    """A relay-answered image URL, read whole (the signed link on the relay's own storage host)."""
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=GEN_TIMEOUT_SECONDS) as response:
            return read_whole(response, GEN_TIMEOUT_SECONDS, None)
    except NO_ANSWER as exc:
        raise SystemExit("dmx-gen: the answered image could not be downloaded; nothing published") from exc


def _publish(item: dict, path: Path, *, token: str) -> str:
    """Publish the answered image (inline Base64 preferred) and say which form it came in."""
    encoded = item.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise SystemExit("dmx-gen: response image is invalid; nothing published") from exc
        publish_png(data, path, label="dmx-gen")
        return "b64_json"
    url = item.get("url")
    if isinstance(url, str) and url:
        publish_png(_fetch(url, token), path, label="dmx-gen")
        return "url"
    raise SystemExit("dmx-gen: response has no b64_json or url image; nothing published")


class DmxProvider:
    name = PROVIDER_DMX
    # Seedream has no alpha output and the route has no `background` knob: the background is keyed downstream.
    transparency = TRANSPARENCY_CHROMA

    def inspect_facing(self, path: Path, workdir: Path) -> tuple[str, dict]:
        from . import facing_vision as vision
        from .facing import INSPECTION_PROMPT
        return self._inspect(path, INSPECTION_PROMPT, vision.FACING_LABEL, detail="low")

    def inspect_image(self, path: Path, workdir: Path, prompt: str, *, label: str = "vision check") -> tuple[str, dict]:
        return self._inspect(path, prompt, label, detail="high")

    def _inspect(self, path: Path, prompt: str, label: str, *, detail: str) -> tuple[str, dict]:
        from . import facing_vision as vision

        token = resolve_credential()
        announce_api_billing(self.name, AUTH_ENV, f" ({label}).")
        model = (os.environ.get(VISION_MODEL_ENV) or "").strip() or DEFAULT_VISION_MODEL
        status, reply, _asks = _ask(address.url(address.DMX, "/responses"), token,
                                    vision.request_body(path, model, prompt=prompt, detail=detail),
                                    label="facing-vision")
        text, metadata = vision.response_text(status, reply, label=label)
        return text, {**metadata, "auth_source": AUTH_SOURCE_API_KEY, "transport": "dmx-responses"}

    def generate(self, request: GenRequest, workdir: Path) -> ProviderRun:
        body = _body(request)
        token = resolve_credential()
        announce_api_billing(
            self.name,
            AUTH_ENV,
            f" (model={body['model']}, size={body['size']}, refs={len(request.refs)}).",
        )
        started = time.monotonic()
        status, reply, asks = _ask(address.url(address.DMX, "/responses"), token, body)
        refused = refusal.read(self.name, reply) if status != 200 else None
        if status in (401, 403):
            raise SystemExit(
                f"dmx-gen: credential {AUTH_SOURCE_API_KEY} rejected (HTTP {status}){refused.suffix()}; "
                f"check the key's value and that it may call {body['model']}{refusal.said(reply, token)}"
            )
        if refused is not None:
            raise SystemExit(
                f"dmx-gen: {refused.reason('image request failed')} (HTTP {status}){refused.suffix()}; "
                f"{retry.tail(asks)}{refusal.said(reply, token)}"
            )
        items = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise SystemExit("dmx-gen: expected exactly one image in the response; nothing published")
        answered = _publish(items[0], request.raw, token=token)
        usage = reply.get("usage") if isinstance(reply, dict) else None
        return ProviderRun(
            provider=self.name,
            elapsed_seconds=time.monotonic() - started,
            model=body["model"],
            extra={
                "auth_source": AUTH_SOURCE_API_KEY,
                "transport": "dmx-responses",
                "endpoint": "/responses",
                "size": body["size"],
                "image_answer": answered,
                **({"usage": usage} if isinstance(usage, dict) else {}),
            },
        )
