# SPDX-License-Identifier: Apache-2.0
"""What `sprite-gen char` keeps on disk (docs/char.md sections 2 and 3).

    <chars>/<id>/character.json          schema, description, spec, spec_rev, actions (their order)
    <chars>/<id>/ref.<ext>               the reference as given, byte for byte, under its own format's extension
    <chars>/<id>/cutout.png              the reference with a transparent background: every later step starts here
    <chars>/<id>/base.png                the cut-out laid on the key: the still video-set films
    <chars>/<id>/actions/<name>/action.json
    <chars>/<id>/actions/<name>/vN/      one video-set run, never rewritten after it ends
    <chars>/<id>/build/                  `char build`
    <chars>/<id>/_trash/                 `char action rm` without --purge
    <chars>/<id>/.lock                   one job per character

`<chars>` is `SPRITE_GEN_CHARS_DIR`, or `work/chars` in the checkout.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sprite_gen.spec.runio import atomic_write_text

CHARACTER_SCHEMA = "sprite-gen-char/1"
ACTION_SCHEMA = "sprite-gen-char-action/1"
VERSION_SCHEMA = "sprite-gen-char-version/1"

# An id, an action name and a state: one path segment, and a word export-phaser takes as a state and a texture key.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

# The locked spec (docs/char.md section 2): set on the character, inherited by every action.
SPEC_DEFAULTS: dict[str, Any] = {
    "provider": "dmx",
    "model": "doubao-seedance-2-0-260128",
    "resolution": "480p",
    "direction": "side",
    "facing": "right",
    "key": "green",
    "body_plan": "biped",
    "body_height": None,  # required
    "fps": None,  # None: as filmed; a number is video-set --out-fps and must be above the clip's own rate
    "walk_start": "as-given",
}
SPEC_FIELDS = tuple(SPEC_DEFAULTS)


class CharError(Exception):
    """A refusal `sprite-gen char` reports as `{"ok": false, "error": …}`; nothing was spent."""


def chars_dir() -> Path:
    env = os.environ.get("SPRITE_GEN_CHARS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "work" / "chars"


def check_name(value: str, what: str) -> str:
    if not NAME_RE.match(value or ""):
        raise CharError(f"{what} {value!r}: lowercase letters, digits and '-', starting with a letter or digit, at most 48")
    return value


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# On Windows a file another process has open cannot be replaced, and one being replaced cannot be opened: both
# fail with PermissionError for a moment. The Studio runs `char show` every second or so while a job writes
# action.json, so each side waits the other out instead of failing the job (that is how a regen once died
# before filming, leaving its vN behind).
SHARING_RETRIES = 40
SHARING_WAIT_S = 0.05


def _retry_sharing(fn):
    for attempt in range(SHARING_RETRIES):
        try:
            return fn()
        except PermissionError:
            if attempt == SHARING_RETRIES - 1:
                raise
            time.sleep(SHARING_WAIT_S)
    raise AssertionError("unreachable")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_retry_sharing(lambda: path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise CharError(f"{path} not found") from None
    except ValueError as exc:
        raise CharError(f"{path}: not JSON ({exc})") from None
    if not isinstance(data, dict):
        raise CharError(f"{path}: not a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _retry_sharing(lambda: atomic_write_text(path, text))


# ---- characters -------------------------------------------------------------------------------------------------

def char_dir(cid: str) -> Path:
    return chars_dir() / check_name(cid, "character id")


def load_character(cid: str) -> dict[str, Any]:
    path = char_dir(cid) / "character.json"
    if not path.is_file():
        raise CharError(f"no character {cid!r} (sprite-gen char new {cid} --ref … --description … --body-height …)")
    data = read_json(path)
    if data.get("schema") != CHARACTER_SCHEMA:
        raise CharError(f"{path}: schema {data.get('schema')!r}, expected {CHARACTER_SCHEMA!r}")
    return data


def save_character(data: dict[str, Any]) -> None:
    data["updated"] = now()
    write_json(char_dir(data["id"]) / "character.json", data)


def list_ids() -> list[str]:
    root = chars_dir()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "character.json").is_file() and NAME_RE.match(p.name))


# ---- actions and versions ---------------------------------------------------------------------------------------

def action_dir(cid: str, name: str) -> Path:
    return char_dir(cid) / "actions" / check_name(name, "action name")


def load_action(cid: str, name: str) -> dict[str, Any]:
    path = action_dir(cid, name) / "action.json"
    if not path.is_file():
        raise CharError(f"character {cid!r} has no action {name!r}")
    data = read_json(path)
    if data.get("schema") != ACTION_SCHEMA:
        raise CharError(f"{path}: schema {data.get('schema')!r}, expected {ACTION_SCHEMA!r}")
    return data


def save_action(cid: str, data: dict[str, Any]) -> None:
    data["updated"] = now()
    write_json(action_dir(cid, data["name"]) / "action.json", data)


def version_dir(cid: str, name: str, n: int) -> Path:
    return action_dir(cid, name) / f"v{int(n)}"


def next_version(cid: str, name: str, action: dict[str, Any]) -> int:
    """The number the next version takes: past every listed version and every vN folder on disk, so a folder a
    failed run left behind without listing it is never reused (version numbers only grow)."""
    on_disk = []
    root = action_dir(cid, name)
    if root.is_dir():
        on_disk = [int(p.name[1:]) for p in root.iterdir() if p.is_dir() and re.fullmatch(r"v\d+", p.name)]
    return max([*action.get("versions", []), *on_disk], default=0) + 1


def load_version(cid: str, name: str, n: int) -> dict[str, Any]:
    return read_json(version_dir(cid, name, n) / "version.json")


def stale(version: dict[str, Any], character: dict[str, Any]) -> bool:
    """A version filmed under another spec than the character's now (docs/char.md section 2, 改规格)."""
    return version.get("spec") != character["spec"]


def rel(path: Path) -> str:
    """A path as the Studio serves it: relative to the chars dir, with forward slashes."""
    return path.resolve().relative_to(chars_dir()).as_posix()


# ---- the lock ---------------------------------------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        # os.kill(pid, 0) on Windows terminates the process, so ask the kernel instead
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_path(cid: str) -> Path:
    return char_dir(cid) / ".lock"


def lock_holder(cid: str) -> dict[str, Any] | None:
    """The live job holding the character's lock, or None (no lock, or a lock left by a process that is gone)."""
    path = lock_path(cid)
    try:
        holder = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(holder, dict) and pid_alive(int(holder.get("pid") or 0)):
        return holder
    return None


@contextmanager
def locked(cid: str, what: str) -> Iterator[None]:
    """One job per character: a second paid run, or an edit under a running one, is refused before it starts."""
    path = lock_path(cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = lock_holder(cid)
            if holder is not None:
                raise CharError(f"character {cid!r} is busy: {holder.get('what')} (pid {holder.get('pid')}, "
                                f"since {holder.get('since')}); wait for it to end") from None
            path.unlink(missing_ok=True)  # left by a process that is gone
            _mark_interrupted(cid)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "what": what, "since": now()}, fh)
        break
    else:
        raise CharError(f"cannot take the lock {path}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _mark_interrupted(cid: str) -> None:
    """Versions a dead job left `running` are failed, so nothing shows them as still in progress."""
    root = char_dir(cid) / "actions"
    if not root.is_dir():
        return
    for vfile in root.glob("*/v*/version.json"):
        try:
            v = read_json(vfile)
        except CharError:
            continue
        if v.get("status") == "running":
            v.update(status="failed", ok=False, error="interrupted: the job ended before it finished")
            write_json(vfile, v)
