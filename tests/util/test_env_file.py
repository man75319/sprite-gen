# SPDX-License-Identifier: Apache-2.0
"""`sprite_gen.util.env_file`: the checkout's `.env` read on start, never over the shell, never from the working dir."""

from __future__ import annotations

import os
from pathlib import Path

from sprite_gen.util import env_file


def test_lines_are_read_with_quotes_comments_export_and_references():
    text = "\n".join([
        "# a comment",
        "",
        "export OPENAI_API_KEY=sk-test",
        'DMX_API_KEY="${OPENAI_API_KEY}"',
        "SPRITE_GEN_OPENAI_BASE_URL='https://relay.example/v1'",
        "BROKEN LINE",
        "EMPTY=",
    ])
    assert env_file.parse(text, environ={}) == {
        "OPENAI_API_KEY": "sk-test", "DMX_API_KEY": "sk-test",
        "SPRITE_GEN_OPENAI_BASE_URL": "https://relay.example/v1", "EMPTY": ""}


def test_a_reference_reads_the_environment_first():
    assert env_file.parse("B=${A}", environ={"A": "shell"}) == {"B": "shell"}


def test_load_never_replaces_a_variable_the_shell_set_and_puts_tools_on_path(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SG_TEST_NEW=from-file\nSG_TEST_SET=from-file\n", encoding="utf-8")
    (tmp_path / ".tools" / "webp" / "bin").mkdir(parents=True)
    monkeypatch.delenv(env_file.ENV_FILE_VAR, raising=False)
    monkeypatch.delenv("SG_TEST_NEW", raising=False)
    monkeypatch.setenv("SG_TEST_SET", "from-shell")
    monkeypatch.setenv("PATH", "")
    loaded = env_file.load(tmp_path)
    assert os.environ["SG_TEST_NEW"] == "from-file" and os.environ["SG_TEST_SET"] == "from-shell"
    assert loaded == {"SG_TEST_NEW": "file", "PATH": "tools"}
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path / ".tools" / "webp" / "bin")
    assert env_file.load(tmp_path) == {}  # twice: nothing added again


def test_off_reads_nothing_and_a_named_file_is_read_instead(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SG_TEST_X=repo\n", encoding="utf-8")
    other = tmp_path / "elsewhere.env"
    other.write_text("SG_TEST_X=named\n", encoding="utf-8")
    monkeypatch.delenv("SG_TEST_X", raising=False)
    monkeypatch.setenv(env_file.ENV_FILE_VAR, "off")
    assert "SG_TEST_X" not in env_file.load(tmp_path) and "SG_TEST_X" not in os.environ
    monkeypatch.setenv(env_file.ENV_FILE_VAR, str(other))
    env_file.load(tmp_path)
    assert os.environ["SG_TEST_X"] == "named"


def test_the_checkout_is_found_beside_the_package():
    root = env_file.repo_root()
    assert root is not None and (root / "sprite_gen" / "util" / "env_file.py").is_file()


def test_the_example_lists_the_keys_and_holds_no_value_and_env_is_ignored():
    root = env_file.repo_root()
    example = env_file.parse((root / ".env.example").read_text(encoding="utf-8"), environ={})
    assert {"DMX_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"} <= set(example)
    assert all(not v or v.startswith("https://") or v in ("off", "gpt-image-2.5-flare", ".sprite-gen-config") for v in example.values())
    assert "/.env" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_a_folder_named_relative_in_the_file_is_the_files_own(tmp_path, monkeypatch):
    """`SPRITE_GEN_CONFIG_DIR=.sprite-gen-config` is the checkout's folder wherever sprite-gen is run from; an absolute
    one, and one the shell set, are as given."""
    monkeypatch.delenv(env_file.ENV_FILE_VAR, raising=False)
    monkeypatch.delenv("SPRITE_GEN_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path.parent)
    (tmp_path / ".env").write_text("SPRITE_GEN_CONFIG_DIR=.sprite-gen-config\n", encoding="utf-8")
    env_file.load(tmp_path)
    assert os.environ["SPRITE_GEN_CONFIG_DIR"] == str(tmp_path / ".sprite-gen-config")
    absolute = tmp_path / "abs"
    monkeypatch.delenv("SPRITE_GEN_CONFIG_DIR")
    (tmp_path / ".env").write_text(f"SPRITE_GEN_CONFIG_DIR={absolute}\n", encoding="utf-8")
    env_file.load(tmp_path)
    assert os.environ["SPRITE_GEN_CONFIG_DIR"] == str(absolute)
    monkeypatch.setenv("SPRITE_GEN_CONFIG_DIR", "from-shell")
    env_file.load(tmp_path)
    assert os.environ["SPRITE_GEN_CONFIG_DIR"] == "from-shell"
