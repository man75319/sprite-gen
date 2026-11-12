# SPDX-License-Identifier: Apache-2.0
"""The checkout's own settings: `<repo>/.env` read into the environment, and `<repo>/.tools/*/bin` put on PATH.

A provider key, a relay address or a model default belongs to one machine, never to the repository: it lives in a
`.env` beside `pyproject.toml` (ignored by git; `.env.example` lists what it may hold). `sprite-gen` reads it on start,
so a run needs no wrapper script and works the same from any shell.

- `KEY=VALUE` per line; `#` starts a comment line; a leading `export ` is allowed; a value in single or double quotes
  is taken without them; `${NAME}` in a value is NAME as set so far (the environment's, or an earlier line's).
- A variable the environment already holds is never replaced: a key exported in the shell wins over the file.
- `SPRITE_GEN_ENV_FILE` names another file; `SPRITE_GEN_ENV_FILE=off` reads none (the test suite runs so); the
  tools below are binaries, no setting, and go on PATH either way.
- Only the checkout's own `.env` is read, never one in the working directory: a folder of downloaded files cannot
  hand a run its settings.
- `<repo>/.tools/<name>/bin` (the ffmpeg and libwebp a machine unpacked there, ignored by git) is put first on PATH.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_FILE_VAR = "SPRITE_GEN_ENV_FILE"
OFF = "off"
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def repo_root() -> Path | None:
    """The checkout this package runs from (`pyproject.toml` beside the package), or None for an installed wheel."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def parse(text: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    """The `KEY=VALUE` lines of `text`, quotes stripped and `${NAME}` filled from `environ` and the lines before."""
    known = dict(os.environ if environ is None else environ)
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        value = _REF.sub(lambda r: known.get(r.group(1), ""), value)
        values[name] = value
        known.setdefault(name, value)
    return values


def env_file(root: Path | None = None) -> Path | None:
    """The file to read: `SPRITE_GEN_ENV_FILE`, else `<repo>/.env`; None where it is off or there is no checkout."""
    named = os.environ.get(ENV_FILE_VAR)
    if named is not None:
        return None if named.strip().lower() in (OFF, "") else Path(named).expanduser()
    root = repo_root() if root is None else root
    return None if root is None else root / ".env"


def tool_dirs(root: Path | None = None) -> list[Path]:
    """`<repo>/.tools/*/bin` that exist, in name order."""
    root = repo_root() if root is None else root
    if root is None or not (root / ".tools").is_dir():
        return []
    return sorted(p for p in (root / ".tools").glob("*/bin") if p.is_dir())


def load(root: Path | None = None) -> dict[str, str]:
    """Read the env file into `os.environ` (never over a variable already set) and put the tool dirs on PATH.

    Returns the names it set, each with where from (`file` or `tools`); never a value."""
    loaded: dict[str, str] = {}
    path = env_file(root)
    if path is not None and path.is_file():
        for name, value in parse(path.read_text(encoding="utf-8-sig")).items():
            if name not in os.environ:
                os.environ[name] = value
                loaded[name] = "file"
    if put_tools_on_path(root):
        loaded["PATH"] = "tools"
    return loaded


def put_tools_on_path(root: Path | None = None) -> bool:
    """`tool_dirs` first on PATH (those not on it yet); whether any was added. Binaries, no setting: never off."""
    dirs = [str(d) for d in tool_dirs(root)]
    path_now = os.environ.get("PATH", "").split(os.pathsep)
    new = [d for d in dirs if d not in path_now]
    if new:
        os.environ["PATH"] = os.pathsep.join([*new, *filter(None, path_now)])
    return bool(new)
