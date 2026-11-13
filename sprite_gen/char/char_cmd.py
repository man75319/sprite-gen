# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen char` — characters, their actions, and each action's generations kept as versions (docs/char.md).

    char new <id> --ref <png> --description "…" --body-height N [spec…]
    char list | char show <id>
    char spec <id> [spec…] [--description "…"]
    char action add <id> <name> --state S --motion "…" [--action-seconds S] [--dry-run]
    char action regen <id> <name> [--motion "…"] [--action-seconds S] [--dry-run]
    char action use <id> <name> <version>
    char action rm <id> <name> [--purge]
    char build <id>

Every subcommand takes `--json`: stdout is then one JSON object (`ok` true, or false with `error`), and what the
steps print goes to stderr. A version's own run log is `vN/video-set.log`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.char import char_plan, ledger
from sprite_gen.char.ledger import CharError
from sprite_gen.compose import export_phaser
from sprite_gen.frames import cutout as cutout_mod
from sprite_gen.gen.facing import FACINGS
from sprite_gen.video import batch as batch_mod
from sprite_gen.video import body_plan as body_mod
from sprite_gen.video import canvas as canvas_mod

KEYS = cutout_mod.KEY_TARGETS  # key name -> its RGB, the one table
DIRECTIONS = ("side",)  # v1 (docs/char.md section 2)
# Where a cut-out reference is laid on its key: as scripts/myautosprite/make_key_canvas.py lays it.
KEY_CANVAS = {"size": 1024, "height": 600, "baseline": 900, "alpha_floor": 64}

# Flags of the locked spec: an action command that carries one is refused (docs/char.md section 2).
LOCKED_FLAGS = ("--provider", "--model", "--resolution", "--direction", "--facing", "--key", "--body-plan",
                "--body-height", "--fps", "--out-fps", "--walk-start", "--base", "--character", "--description", "--ref")


# ---- parsing ----------------------------------------------------------------------------------------------------

def _spec_flags(p: argparse.ArgumentParser, *, defaults: bool) -> None:
    d = ledger.SPEC_DEFAULTS
    def dflt(key: str) -> Any:
        return d[key] if defaults else None
    p.add_argument("--provider", default=dflt("provider"), help=f"clip provider (default {d['provider']})")
    p.add_argument("--model", default=dflt("model"), help=f"clip model (default {d['model']})")
    p.add_argument("--resolution", default=dflt("resolution"), help=f"clip resolution (default {d['resolution']})")
    p.add_argument("--direction", choices=DIRECTIONS, default=dflt("direction"), help="the view (v1: side only)")
    p.add_argument("--facing", choices=FACINGS, default=dflt("facing"), help=f"which way the side view faces (default {d['facing']})")
    p.add_argument("--key", choices=sorted(KEYS), default=dflt("key"), help=f"chroma key (default {d['key']})")
    p.add_argument("--body-plan", choices=body_mod.PLANS, default=dflt("body_plan"), help=f"what it stands on (default {d['body_plan']})")
    p.add_argument("--body-height", type=int, default=None, help="standing height of every loop, px" + (" (required)" if defaults else ""))
    p.add_argument("--fps", type=float, default=None, help="video-set --out-fps, above the clip's own rate (needs RIFE); 0 or omitted: as filmed")
    p.add_argument("--walk-start", choices=batch_mod.WALK_START_MODES, default=dflt("walk_start"), help=f"default {d['walk_start']}")


def _locked_traps(p: argparse.ArgumentParser) -> None:
    """The locked flags on an action command: taken, so the refusal can say why, never applied."""
    p.set_defaults(_locked=[])
    for flag in LOCKED_FLAGS:
        p.add_argument(flag, dest="_locked", action=_TrapAction, nargs="?", help=argparse.SUPPRESS)


class _TrapAction(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace, values: Any, option_string: str | None = None) -> None:
        got = list(getattr(namespace, "_locked", None) or [])
        got.append(option_string)
        namespace._locked = got


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="char_cmd", required=True, metavar="<command>")

    def common(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--json", action="store_true", help="print one JSON object on stdout")
        return p

    p = common(sub.add_parser("new", help="create a character: its reference, description and locked spec"))
    p.add_argument("id")
    p.add_argument("--ref", required=True, type=Path, help="the reference still (a cut-out is laid on the key)")
    p.add_argument("--description", required=True, help="short subject phrase for every prompt (video-set --character)")
    _spec_flags(p, defaults=True)

    common(sub.add_parser("list", help="list characters"))
    p = common(sub.add_parser("show", help="a character: spec, actions, each action's versions"))
    p.add_argument("id")

    p = common(sub.add_parser("spec", help="change the description or the locked spec (versions filmed under another spec go stale)"))
    p.add_argument("id")
    p.add_argument("--description")
    _spec_flags(p, defaults=False)

    p = common(sub.add_parser("build", help="every action's current version in one Phaser atlas (export-phaser)"))
    p.add_argument("id")

    act = sub.add_parser("action", help="add, regenerate, pick, remove an action").add_subparsers(
        dest="action_cmd", required=True, metavar="<action command>")
    for name, text in (("add", "create an action and film its first version"), ("regen", "film a new version; earlier ones stay")):
        p = common(act.add_parser(name, help=text))
        p.add_argument("id")
        p.add_argument("name")
        if name == "add":
            p.add_argument("--state", required=True, help="idle / walk / attack / death … (the prompt's skeleton)")
            p.add_argument("--motion", required=True, help="the motion in your own words")
        else:
            p.add_argument("--motion", help="new motion text (kept for later versions)")
        p.add_argument("--action-seconds", type=float, help="how long one of the motion lasts (video-set --action-seconds)")
        p.add_argument("--dry-run", action="store_true", help="print the full prompt that would be sent; nothing is filmed")
        _locked_traps(p)
    p = common(act.add_parser("use", help="make an earlier version current"))
    p.add_argument("id")
    p.add_argument("name")
    p.add_argument("version", type=lambda s: int(s.lstrip("vV")))
    p = common(act.add_parser("rm", help="remove an action: into _trash/, or for good with --purge"))
    p.add_argument("id")
    p.add_argument("name")
    p.add_argument("--purge", action="store_true")


# ---- characters -------------------------------------------------------------------------------------------------

def _spec_from(kwargs: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    spec = dict(base)
    for key in ledger.SPEC_FIELDS:
        if kwargs.get(key) is not None:
            spec[key] = kwargs[key]
    if spec.get("fps") is not None and float(spec["fps"]) <= 0:
        spec["fps"] = None
    if spec.get("fps") is not None:
        spec["fps"] = float(spec["fps"])
    if not spec.get("body_height") or int(spec["body_height"]) <= 0:
        raise CharError("--body-height is required: export-phaser refuses strips cut at different heights")
    spec["body_height"] = int(spec["body_height"])
    if spec["direction"] not in DIRECTIONS:
        raise CharError(f"--direction {spec['direction']!r}: v1 films the side view only")
    if spec["provider"] not in batch_mod.CLIP_DURATION_MIN:
        raise CharError(f"--provider {spec['provider']!r}: one of {', '.join(sorted(batch_mod.CLIP_DURATION_MIN))}")
    return spec


def key_still(ref: Path, out: Path, key: str) -> dict[str, Any]:
    """The still video-set films: a cut-out laid on the key (as make_key_canvas.py lays it), an opaque still whose
    corners are already that key copied as it is."""
    try:
        image = Image.open(ref)
        image.load()
    except OSError as exc:
        raise CharError(f"--ref {ref}: not an image ({exc})") from None
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 255:
        c = KEY_CANVAS
        alpha = alpha.point(lambda a: a if a > c["alpha_floor"] else 0)
        rgba.putalpha(alpha)
        box = alpha.getbbox()
        if box is None:
            raise CharError(f"--ref {ref}: nothing opaque to place")
        subject = rgba.crop(box)
        scale = c["height"] / subject.height
        subject = subject.resize((max(1, round(subject.width * scale)), c["height"]), Image.LANCZOS)
        canvas = Image.new("RGBA", (c["size"], c["size"]), (*KEYS[key], 255))
        canvas.alpha_composite(subject, ((c["size"] - subject.width) // 2, c["baseline"] - subject.height))
        canvas.convert("RGB").save(out)
        return {"laid_on_key": True, "subject": [subject.width, subject.height]}
    try:
        corner = canvas_mod.corner_key(rgba)
        canvas_mod.resolve_key(corner, key)
    except SystemExit as exc:
        raise CharError(f"--ref {ref}: an opaque still must stand on a flat {key} key ({exc}); give a cut-out (transparent PNG) instead") from None
    rgba.convert("RGB").save(out)
    return {"laid_on_key": False, "corner": list(corner)}


def cmd_new(kw: dict[str, Any]) -> dict[str, Any]:
    cid = ledger.check_name(kw["id"], "character id")
    root = ledger.char_dir(cid)
    if (root / "character.json").exists():
        raise CharError(f"character {cid!r} already exists ({root})")
    ref = Path(kw["ref"]).expanduser().resolve()
    if not ref.is_file():
        raise CharError(f"--ref {ref}: not found")
    description = (kw.get("description") or "").strip()
    if not description:
        raise CharError("--description is empty")
    spec = _spec_from(kw, {**ledger.SPEC_DEFAULTS})
    root.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(ref, root / "ref.png")
        still = key_still(ref, root / "base.png", spec["key"])
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    character = {"schema": ledger.CHARACTER_SCHEMA, "id": cid, "description": description, "spec": spec, "spec_rev": 1,
                 "ref": "ref.png", "base": "base.png", "base_from": still, "actions": [], "created": ledger.now()}
    ledger.save_character(character)
    return {"character": _character_view(character)}


def cmd_spec(kw: dict[str, Any]) -> dict[str, Any]:
    cid = kw["id"]
    with ledger.locked(cid, "char spec"):
        character = ledger.load_character(cid)
        spec = _spec_from(kw, character["spec"])
        changed = sorted(k for k in ledger.SPEC_FIELDS if spec.get(k) != character["spec"].get(k))
        if "key" in changed:
            raise CharError("--key: the base still stands on the key; make a new character for another key")
        if changed:
            character["spec"] = spec
            character["spec_rev"] = int(character.get("spec_rev") or 1) + 1
        if kw.get("description") is not None:
            text = kw["description"].strip()
            if not text:
                raise CharError("--description is empty")
            character["description"] = text
        ledger.save_character(character)
    return {"changed": changed, "character": _character_view(character)}


def _version_view(cid: str, name: str, n: int, character: dict[str, Any]) -> dict[str, Any]:
    try:
        v = ledger.load_version(cid, name, n)
    except CharError as exc:
        return {"version": n, "status": "missing", "ok": False, "error": str(exc)}
    out = {k: v.get(k) for k in ("version", "status", "ok", "motion", "action_seconds", "started", "elapsed_seconds",
                                 "prompt_match", "seam", "error", "warnings", "files")}
    out["stale"] = ledger.stale(v, character)
    if v.get("status") == "running" and ledger.lock_holder(cid) is None:
        out.update(status="failed", ok=False, error="interrupted: the job ended before it finished")
    return out


def _action_view(cid: str, name: str, character: dict[str, Any]) -> dict[str, Any]:
    a = ledger.load_action(cid, name)
    versions = [_version_view(cid, name, n, character) for n in a.get("versions", [])]
    current = next((v for v in versions if v["version"] == a.get("current")), None)
    return {"name": name, "state": a["state"], "motion": a["motion"], "action_seconds": a.get("action_seconds"),
            "current": a.get("current"), "current_stale": bool(current and current["stale"]), "versions": versions}


def _character_view(character: dict[str, Any], *, actions: bool = False) -> dict[str, Any]:
    cid = character["id"]
    root = ledger.char_dir(cid)
    out = {k: character.get(k) for k in ("id", "description", "spec", "spec_rev", "created", "updated")}
    out["ref"] = ledger.rel(root / character["ref"])
    out["base"] = ledger.rel(root / character["base"])
    out["action_names"] = list(character.get("actions", []))
    holder = ledger.lock_holder(cid)
    out["busy"] = holder
    build = root / "build" / f"{cid}.meta.json"
    out["build"] = ledger.rel(build) if build.is_file() else None
    if actions:
        out["actions"] = [_action_view(cid, n, character) for n in character.get("actions", [])]
    return out


def cmd_list(kw: dict[str, Any]) -> dict[str, Any]:
    return {"characters": [_character_view(ledger.load_character(cid)) for cid in ledger.list_ids()]}


def cmd_show(kw: dict[str, Any]) -> dict[str, Any]:
    return {"character": _character_view(ledger.load_character(kw["id"]), actions=True)}


# ---- actions ----------------------------------------------------------------------------------------------------

def _refuse_locked(kw: dict[str, Any]) -> None:
    flags = sorted(set(kw.get("_locked") or []))
    if flags:
        raise CharError(f"{', '.join(flags)}: locked on the character, not set per action; "
                        "change it with `sprite-gen char spec` (the versions filmed before go stale)")


def _plan(character: dict[str, Any], action: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    base = ledger.char_dir(character["id"]) / character["base"]
    return {"argv": ["video-set", *char_plan.video_set_argv(character, action, base=base, out_dir=out_dir)],
            **char_plan.planned_prompt(character, action)}


def _action_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if not float(value) > 0:
        raise CharError(f"--action-seconds {value}: must be over 0")
    return float(value)


def cmd_action_add(kw: dict[str, Any]) -> dict[str, Any]:
    _refuse_locked(kw)
    cid, name = kw["id"], ledger.check_name(kw["name"], "action name")
    character = ledger.load_character(cid)
    if (ledger.action_dir(cid, name) / "action.json").exists():
        raise CharError(f"action {name!r} exists; film it again with `sprite-gen char action regen {cid} {name}`")
    motion = (kw.get("motion") or "").strip()
    if not motion:
        raise CharError("--motion is empty")
    action = {"schema": ledger.ACTION_SCHEMA, "name": name, "state": ledger.check_name(kw["state"], "state"),
              "motion": motion, "action_seconds": _action_seconds(kw.get("action_seconds")),
              "versions": [], "current": None, "created": ledger.now()}
    if kw.get("dry_run"):
        return {"dry_run": True, **_plan(character, action, ledger.version_dir(cid, name, 1))}
    with ledger.locked(cid, f"action add {name}"):
        character = ledger.load_character(cid)
        if name not in character["actions"]:
            character["actions"].append(name)
        ledger.save_action(cid, action)
        ledger.save_character(character)
        return _film(character, action)


def cmd_action_regen(kw: dict[str, Any]) -> dict[str, Any]:
    _refuse_locked(kw)
    cid, name = kw["id"], kw["name"]
    character = ledger.load_character(cid)
    action = ledger.load_action(cid, name)
    if kw.get("motion") is not None:
        if not kw["motion"].strip():
            raise CharError("--motion is empty")
        action["motion"] = kw["motion"].strip()
    if kw.get("action_seconds") is not None:
        action["action_seconds"] = _action_seconds(kw["action_seconds"])
    n = max(action["versions"], default=0) + 1
    if kw.get("dry_run"):
        return {"dry_run": True, **_plan(character, action, ledger.version_dir(cid, name, n))}
    with ledger.locked(cid, f"action regen {name}"):
        character = ledger.load_character(cid)
        action = {**ledger.load_action(cid, name), **{k: action[k] for k in ("motion", "action_seconds")}}
        ledger.save_action(cid, action)
        return _film(character, action)


def run_video_set(argv: list[str], log: Path) -> int:
    """`sprite-gen <argv>` (argv starts with the verb) in its own process, everything it prints into `log`."""
    with log.open("w", encoding="utf-8") as fh:
        return subprocess.call([sys.executable, "-m", "sprite_gen.cli", *argv],
                               stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)


def _film(character: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """One version: video-set into vN (its own out-dir), then the action's own atlas into vN/preview. Held under the lock."""
    cid, name = character["id"], action["name"]
    n = max(action["versions"], default=0) + 1
    vdir = ledger.version_dir(cid, name, n)
    vdir.mkdir(parents=True, exist_ok=False)
    plan = _plan(character, action, vdir)
    version: dict[str, Any] = {
        "schema": ledger.VERSION_SCHEMA, "version": n, "status": "running", "ok": None,
        "state": action["state"], "motion": action["motion"], "action_seconds": action.get("action_seconds"),
        "description": character["description"], "spec": character["spec"], "spec_rev": character.get("spec_rev"),
        "argv": ["sprite-gen", *plan["argv"]], "planned_prompt": plan["prompt"], "started": ledger.now(),
    }
    ledger.write_json(vdir / "version.json", version)
    action["versions"].append(n)
    ledger.save_action(cid, action)
    t0 = time.monotonic()
    log = vdir / "video-set.log"
    try:
        print(f"char: filming {cid}/{name} v{n}; log {log}", file=sys.stderr, flush=True)
        code = run_video_set(plan["argv"], log)
        version.update(_read_run(character, action, vdir, code, plan["prompt"]))
        if version["ok"]:
            version["files"] = _preview(character, action, vdir)
    except Exception as exc:  # the version records why it failed; the lock is let go by the caller
        version.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    version["status"] = "ok" if version["ok"] else "failed"
    version["elapsed_seconds"] = round(time.monotonic() - t0, 1)
    ledger.write_json(vdir / "version.json", version)
    if version["ok"]:
        action["current"] = n
        ledger.save_action(cid, action)
    return {"version": _version_view(cid, name, n, character), "action": _action_view(cid, name, character)}


def _read_run(character: dict[str, Any], action: dict[str, Any], vdir: Path, code: int, planned: str) -> dict[str, Any]:
    out: dict[str, Any] = {"exit_code": code, "warnings": []}
    report = vdir / "set.report.json"
    item_dir = vdir / char_plan.item_name(character, action)
    if not report.is_file():
        return {**out, "ok": False, "error": f"video-set ended ({code}) without set.report.json; see {vdir / 'video-set.log'}"}
    rep = json.loads(report.read_text(encoding="utf-8"))
    item = next((i for i in rep.get("items", []) if i.get("item") == item_dir.name), {})
    out["warnings"] = list(rep.get("warnings", []))
    clip_report = item_dir / "clip.report.json"
    if clip_report.is_file():
        sent = json.loads(clip_report.read_text(encoding="utf-8")).get("prompt")
        out["prompt_match"] = sent == planned
        if sent != planned:
            out["warnings"].append("the prompt sent differs from the one planned (docs/char.md rule one): compare "
                                   "planned_prompt with clip.report.json#prompt")
    if not item.get("ok"):
        return {**out, "ok": False, "error": item.get("error") or f"video-set failed ({code}); see {vdir / 'video-set.log'}"}
    out["seam"] = item.get("loop", {}).get("seam_ratio")
    return {**out, "ok": True}


def _export(loops: list[tuple[str, Path]], name: str, out_dir: Path) -> dict[str, Any]:
    """export-phaser into out_dir, what it prints sent to stderr; its report, or a CharError."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = export_phaser.run(loops=loops, name=name, out_dir=out_dir)
    text = buf.getvalue()
    print(text, file=sys.stderr, end="")
    # its warnings are `warning: …` lines, then the report: one indented JSON object from a line that is "{"
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line == "{"), None)
    try:
        report = json.loads("\n".join(lines[start:])) if start is not None else {}
    except ValueError:
        report = {}
    if code != 0:
        raise CharError(f"export-phaser: {report.get('error') or 'failed'}")
    return report


def _preview(character: dict[str, Any], action: dict[str, Any], vdir: Path) -> dict[str, Any]:
    loop = vdir / char_plan.item_name(character, action) / "loop"
    item = loop.parent
    report = _export([(action["name"], loop)], character["id"], vdir / "preview")
    files = {k: ledger.rel(Path(p)) for k, p in report.get("files", {}).items()}
    files.update({"clip": ledger.rel(item / "clip.mp4"), "clip_report": ledger.rel(item / "clip.report.json"),
                  "log": ledger.rel(vdir / "video-set.log")})
    return files


def cmd_action_use(kw: dict[str, Any]) -> dict[str, Any]:
    cid, name, n = kw["id"], kw["name"], int(kw["version"])
    with ledger.locked(cid, f"action use {name}"):
        character = ledger.load_character(cid)
        action = ledger.load_action(cid, name)
        if n not in action.get("versions", []):
            raise CharError(f"{cid}/{name} has no version {n} (versions: {action.get('versions')})")
        if not ledger.load_version(cid, name, n).get("ok"):
            raise CharError(f"{cid}/{name} v{n} did not finish; it cannot be current")
        action["current"] = n
        ledger.save_action(cid, action)
    return {"action": _action_view(cid, name, character)}


def cmd_action_rm(kw: dict[str, Any]) -> dict[str, Any]:
    cid, name = kw["id"], kw["name"]
    with ledger.locked(cid, f"action rm {name}"):
        character = ledger.load_character(cid)
        src = ledger.action_dir(cid, name)
        if not src.is_dir():
            raise CharError(f"character {cid!r} has no action {name!r}")
        if kw.get("purge"):
            shutil.rmtree(src)
            where = None
        else:
            dst = ledger.char_dir(cid) / "_trash" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            where = ledger.rel(dst)
        character["actions"] = [a for a in character["actions"] if a != name]
        ledger.save_character(character)
    return {"removed": name, "trash": where, "purged": bool(kw.get("purge"))}


def cmd_build(kw: dict[str, Any]) -> dict[str, Any]:
    cid = kw["id"]
    with ledger.locked(cid, "build"):
        character = ledger.load_character(cid)
        loops: list[tuple[str, Path]] = []
        skipped: list[dict[str, str]] = []
        for name in character["actions"]:
            action = ledger.load_action(cid, name)
            n = action.get("current")
            if not n:
                skipped.append({"action": name, "why": "no finished version"})
                continue
            if ledger.stale(ledger.load_version(cid, name, n), character):
                skipped.append({"action": name, "why": f"v{n} was filmed under another spec; regenerate it"})
                continue
            loops.append((name, ledger.version_dir(cid, name, n) / char_plan.item_name(character, action) / "loop"))
        if not loops:
            raise CharError(f"{cid}: no action to build ({'; '.join(s['action'] + ': ' + s['why'] for s in skipped) or 'no actions'})")
        report = _export(loops, cid, ledger.char_dir(cid) / "build")
    return {"built": [n for n, _ in loops], "skipped": skipped, "files": {k: ledger.rel(Path(p)) for k, p in report.get("files", {}).items()},
            "warnings": report.get("warnings", []), "alignment": report.get("alignment")}


# ---- the verb ---------------------------------------------------------------------------------------------------

COMMANDS = {
    ("new", None): cmd_new, ("list", None): cmd_list, ("show", None): cmd_show, ("spec", None): cmd_spec,
    ("build", None): cmd_build, ("action", "add"): cmd_action_add, ("action", "regen"): cmd_action_regen,
    ("action", "use"): cmd_action_use, ("action", "rm"): cmd_action_rm,
}


def _human(result: dict[str, Any]) -> str:
    if result.get("dry_run"):
        return result["prompt"]
    return json.dumps(result, ensure_ascii=False, indent=2)


def run(**kwargs: Any) -> int:
    as_json = bool(kwargs.get("json"))
    fn = COMMANDS[(kwargs["char_cmd"], kwargs.get("action_cmd"))]
    try:
        result = {"ok": True, **fn(kwargs)}
        code = 0
        filmed = result.get("version")
        if isinstance(filmed, dict) and filmed.get("ok") is False:  # filmed and failed: kept as a version, not current
            result.update(ok=False, error=f"v{filmed.get('version')} failed: {filmed.get('error')}")
            code = 1
    except (CharError, OSError, SystemExit) as exc:
        if isinstance(exc, SystemExit) and not isinstance(exc.code, str):
            raise
        result = {"ok": False, "error": str(exc.code if isinstance(exc, SystemExit) else exc)}
        code = 1
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif code and "version" not in result:
        print(f"char: {result['error']}", file=sys.stderr)
    else:
        print(_human(result))
    return code
