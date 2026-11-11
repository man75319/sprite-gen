# SPDX-License-Identifier: Apache-2.0
"""Doubao Seedance clips through DMXAPI's OpenAI-Responses-shaped `/v1/responses` route.

The relay's video models are not on xAI's `/videos/*` shape. Submitting is one `POST /responses` whose
`input` is a list of parts, and the answer carries only a task id:

    POST <base>/responses
    {"model": "doubao-seedance-2-0-260128",
     "input": [{"type": "text", "text": "<prompt>"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,…"}, "role": "first_frame"}],
     "ratio": "16:9", "resolution": "720p", "duration": 6, "generate_audio": false}

    -> {"id": "task_…"}

The result is asked for by submitting *again* with the relay's query model, whose answer is an OpenAI
Responses envelope whose `output_text` is itself a JSON string:

    POST <base>/responses {"model": "seedance-2-0-get", "input": "task_…"}
    -> {"request_id": "task_…",
        "output": [{"type": "message", "content": [{"type": "output_text",
                    "text": "{\"content\":{\"video_url\":\"https://…/clip.mp4?…\"}}"}]}]}

So this module owns submit → poll → download → publish for that shape, and hands the same record
`video._poll_publish` hands back (data, request id, polls, elapsed) so the verb's report is unchanged.
A clip bought here is never bought twice: any ending after the submit names the request id.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import address, refusal, retry
from .base import announce_api_billing
from .dmx_provider import AUTH_ENV, resolve_credential
from .xai import NO_ANSWER, TransportError, error_body, read_whole

DEFAULT_MODEL = "doubao-seedance-2-0-260128"
# The relay's query models, one per Seedance generation: which one answers is the family of the model submitted.
QUERY_MODELS = {"doubao-seedance-2-0": "seedance-2-0-get", "doubao-seedance-2-5": "seedance-2-5-get"}
QUERY_MODEL_ENV = "SPRITE_GEN_DMX_VIDEO_QUERY_MODEL"

DURATION_MIN, DURATION_MAX = 4, 15
RESOLUTIONS = ("480p", "720p", "1080p", "4k")
# Seedance's own ratio vocabulary. sprite-gen's `--aspect-ratio` names a superset: 3:2 and 2:3 have no
# Seedance equivalent and are refused rather than rounded to a nearby ratio.
RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive")
UNSUPPORTED_RATIOS = ("3:2", "2:3")

POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = int(os.environ.get("SPRITE_GEN_VIDEO_TIMEOUT_SECONDS", "600"))
DOWNLOAD_ATTEMPTS = 3
DONE_STATUSES = ("done", "complete", "completed", "succeeded", "success")
FAILED_STATUSES = ("failed", "error", "expired", "cancelled", "canceled")

_MP4_URL = re.compile(r"https?://[^\s\"'\\]+\.mp4(?:\?[^\s\"'\\]*)?")


@dataclass
class Published:
    """What `video._poll_publish` reports, for this transport: the bytes and how they were come by."""

    data: bytes
    request_id: str
    model: str | None
    duration_reported: float | None
    host: str | None
    polls: int
    polls_refused: int
    polls_unanswered: int
    downloads: int
    elapsed_seconds: float


def query_model(model: str) -> str:
    """The relay model that answers for `model`'s task: its family's `-get`, else the environment's, else 2.0's."""
    named = (os.environ.get(QUERY_MODEL_ENV) or "").strip()
    if named:
        return named
    for family, query in QUERY_MODELS.items():
        if model.startswith(family):
            return query
    return QUERY_MODELS["doubao-seedance-2-0"]


def build_body(*, model: str, prompt: str, image: str | None, last_frame: str | None,
               aspect_ratio: str | None, resolution: str, duration: int,
               generate_audio: bool | None) -> dict[str, Any]:
    """The submit body. Every refusal that can be made without spending money is made before this is called."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if image is not None:
        parts.append({"type": "image_url", "image_url": {"url": image}, "role": "first_frame"})
    if last_frame is not None:
        parts.append({"type": "image_url", "image_url": {"url": last_frame}, "role": "last_frame"})
    body: dict[str, Any] = {"model": model, "input": parts, "resolution": resolution, "duration": duration}
    if aspect_ratio:
        body["ratio"] = aspect_ratio
    if generate_audio is not None:
        body["generate_audio"] = generate_audio
    return body


def _post(url: str, token: str, body: dict, *, deadline: float | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=_left(deadline)) as response:
            raw = read_whole(response, _left(deadline), deadline).decode("utf-8")
            return response.status, (json.loads(raw) if raw else {})
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit("video: dmx answered something that is not JSON") from exc
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, error_body(exc, _left(deadline), deadline)
        except NO_ANSWER as cut:
            raise SystemExit("video: dmx request failed or timed out; the server may have accepted it") from cut
    except NO_ANSWER as exc:
        raise SystemExit("video: dmx request failed or timed out; the server may have accepted it") from exc


def _left(deadline: float | None) -> float:
    if deadline is None:
        return POLL_TIMEOUT_SECONDS
    return max(1.0, deadline - time.monotonic())


def _ask(url: str, token: str, body: dict, *, who: str, label: str, deadline: float | None = None) -> tuple[int, Any]:
    """One POST, sent again as `retry` says (the relay's 429 only)."""
    status, reply, _headers, _asks = retry.ask(
        lambda: (*_post(url, token, body, deadline=deadline), None), provider=address.DMX, who=who, label=label,
        kind=refusal.provider_code, deadline=deadline,
    )
    return status, reply


def _output_text(reply: Any) -> str:
    """The `output_text` parts of a Responses envelope, concatenated (the relay puts its JSON there)."""
    if not isinstance(reply, dict):
        return ""
    texts: list[str] = []
    for item in reply.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "".join(texts)


def _answer(reply: Any) -> dict[str, Any]:
    """The relay's answer, parsed: its `output_text` JSON when the task is done, else what the envelope says."""
    content: dict[str, Any] = {}
    text = _output_text(reply)
    if text:
        try:
            parsed = json.loads(text)
        except ValueError:
            match = _MP4_URL.search(text)
            return {"video_url": match.group(0)} if match else {}
        if isinstance(parsed, dict):
            held = parsed.get("content")
            content = held if isinstance(held, dict) else parsed
    return content


def _video_url(answer: dict[str, Any]) -> str | None:
    url = answer.get("video_url") or answer.get("url")
    return url if isinstance(url, str) and url.lower().startswith(("http://", "https://")) else None


def _failure(answer: dict[str, Any], reply: Any) -> str | None:
    status = answer.get("status") or (reply.get("status") if isinstance(reply, dict) else None)
    if isinstance(status, str) and status.lower() in FAILED_STATUSES:
        return f"ended with status={status!r}"
    error = answer.get("error") or (reply.get("error") if isinstance(reply, dict) else None)
    if error:
        return f"ended with an error: {error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)}"
    return None


def _download(url: str, *, deadline: float | None) -> bytes:
    """The signed link the relay answered, read whole. No bearer: the link carries its own signature."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_left(deadline)) as response:
            return read_whole(response, _left(deadline), deadline)
    except urllib.error.HTTPError as exc:
        raise TransportError(f"download refused with HTTP {exc.code}") from exc
    except NO_ANSWER as exc:
        raise TransportError("download got no answer") from exc


def _is_mp4(data: bytes) -> bool:
    return len(data) > 12 and data[4:8] == b"ftyp"


def _host(url: str) -> str | None:
    from urllib.parse import urlsplit
    return urlsplit(url).netloc or None


def submit_poll_publish(
    *,
    body: dict[str, Any],
    out: Path,
    token: str,
    verb: str = "video",
    sleep: Callable[[float], None] | None = None,
    poll_timeout: float | None = None,
) -> Published:
    """Submit one clip, poll until the relay answers a video url, download it, verify and publish it.

    The request id is the only thing that can buy the clip back, so every ending after the submit says it.
    """
    sleep = sleep or time.sleep
    timeout = POLL_TIMEOUT_SECONDS if poll_timeout is None else poll_timeout
    started = time.monotonic()
    deadline = started + timeout
    model = body.get("model")
    announce_api_billing(
        verb, AUTH_ENV,
        f" (model={model}, duration={body.get('duration')}s, resolution={body.get('resolution')}).",
    )
    status, reply = _ask(address.url(address.DMX, "/responses"), token, body, who="dmx", label=verb, deadline=deadline)
    if status in (401, 403):
        raise SystemExit(f"{verb}: the relay rejected {AUTH_ENV} (HTTP {status}): "
                         f"{refusal.said(reply, token)}")
    request_id = reply.get("id") or reply.get("request_id") if isinstance(reply, dict) else None
    if status not in (200, 202) or not request_id:
        refused = refusal.read("dmx", reply)
        raise SystemExit(f"{verb}: {refused.reason('generation request refused')} (HTTP {status})"
                         f"{refused.suffix()}{refusal.said(reply, token)}")
    request_id = str(request_id)

    query = query_model(str(model or ""))
    polls = polls_refused = polls_unanswered = failed_in_a_row = 0
    answer: dict[str, Any] = {}
    while True:
        polls += 1
        lost: str | None = None
        http: int | None = None
        reply: Any = None
        http, reply = _ask(address.url(address.DMX, "/responses"), token, {"model": query, "input": request_id},
                           who="dmx", label=f"{verb}-poll", deadline=deadline)
        answer = _answer(reply) if reply is not None else {}
        url = _video_url(answer)
        if url is not None:
            break
        failure = _failure(answer, reply)
        if failure is not None:
            raise SystemExit(f"{verb}: generation {request_id} {failure} (HTTP {http})"
                             f"{refusal.said(reply, token)}; nothing was written")
        if http in (401, 403):
            raise SystemExit(f"{verb}: generation {request_id} was accepted, but the relay rejected "
                             f"{AUTH_ENV} on poll {polls} (HTTP {http}); {_ask_again(request_id, out)}")
        if http in retry.GET_AGAIN:
            polls_refused += 1
            failed_in_a_row += 1
        else:
            failed_in_a_row = 0
        wait = retry.backoff(failed_in_a_row) if failed_in_a_row else POLL_INTERVAL_SECONDS
        if retry.too_late(deadline, wait):
            raise SystemExit(f"{verb}: generation {request_id} is still {http if http else 'unanswered'} after "
                             f"{polls} polls — poll timeout ({timeout:g}s); {_ask_again(request_id, out)}")
        sleep(wait)

    url = _video_url(answer) or ""
    downloads = 0
    data = b""
    while True:
        downloads += 1
        try:
            data = _download(url, deadline=deadline)
        except TransportError as failed:
            if downloads < DOWNLOAD_ATTEMPTS and not retry.too_late(deadline, retry.backoff(downloads)):
                print(f"[{verb}] download {downloads} of generation {request_id} failed ({failed.reason}); "
                      f"downloading again in {retry.backoff(downloads):g}s", file=sys.stderr)
                sleep(retry.backoff(downloads))
                continue
            raise SystemExit(f"{verb}: generation {request_id} is done, but its clip could not be downloaded "
                             f"({failed.reason}); {_ask_again(request_id, out)}") from failed
        if _is_mp4(data):
            break
        raise SystemExit(f"{verb}: generation {request_id} is done, but its download brought {len(data)} bytes "
                         f"that are not an mp4 (no ftyp box); {_ask_again(request_id, out)}")

    out.parent.mkdir(parents=True, exist_ok=True)
    staged = out.with_name(out.name + ".part")
    staged.write_bytes(data)
    os.replace(staged, out)
    return Published(
        data=data, request_id=request_id, model=str(model) if model else None,
        duration_reported=float(body["duration"]) if str(body.get("duration", "")).isdigit() else None,
        host=_host(url), polls=polls, polls_refused=polls_refused, polls_unanswered=polls_unanswered,
        downloads=downloads, elapsed_seconds=time.monotonic() - started,
    )


def _ask_again(request_id: str, out: Path) -> str:
    return (f"the request id is {request_id} — keep it: the clip is paid for and only a fresh submit buys another. "
            f"(`{out}` was not written)")
