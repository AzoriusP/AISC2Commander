from __future__ import annotations

import os

from aisc2commander import cli
from aisc2commander.settings import (
    load_env_file,
    load_openai_api_key,
    mask_api_key,
    read_openai_api_key,
    save_openai_api_key,
)


def test_load_openai_api_key_from_env_style_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "openai.env"
    path.write_text("# local secret\nOPENAI_API_KEY='sk-test-value'\n", encoding="utf-8")
    assert load_openai_api_key(path)
    assert os.environ["OPENAI_API_KEY"] == "sk-test-value"


def test_existing_environment_key_has_priority(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    path = tmp_path / "openai.env"
    path.write_text("OPENAI_API_KEY=file-key\n", encoding="utf-8")
    assert not load_openai_api_key(path)
    assert os.environ["OPENAI_API_KEY"] == "environment-key"


def test_load_env_file_only_accepts_allowlisted_names(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("UNSAFE_SETTING", raising=False)
    path = tmp_path / "llm.env"
    path.write_text(
        "LLM_PROVIDER=ollama\nOLLAMA_MODEL=qwen3.6\nUNSAFE_SETTING=value\n",
        encoding="utf-8",
    )
    loaded = load_env_file(path, {"LLM_PROVIDER", "OLLAMA_MODEL"})
    assert loaded == ("LLM_PROVIDER", "OLLAMA_MODEL")
    assert os.environ["OLLAMA_MODEL"] == "qwen3.6"
    assert "UNSAFE_SETTING" not in os.environ


def test_save_openai_api_key_replaces_key_and_preserves_other_lines(tmp_path) -> None:
    path = tmp_path / "openai.env"
    path.write_text(
        "# local secret\nOPENAI_API_KEY=old-key\nUNRELATED=value\nOPENAI_API_KEY=duplicate\n",
        encoding="utf-8",
    )

    assert save_openai_api_key(path, "sk-proj-new-1234") == "sk-proj-new-1234"
    assert read_openai_api_key(path) == "sk-proj-new-1234"
    saved = path.read_text(encoding="utf-8")
    assert saved.count("OPENAI_API_KEY=") == 1
    assert "UNRELATED=value" in saved
    assert "old-key" not in saved
    assert mask_api_key("sk-proj-new-1234") == "sk-…1234"


def test_save_openai_api_key_rejects_empty_or_multiline_values(tmp_path) -> None:
    path = tmp_path / "openai.env"
    for invalid in ("", "   ", "first\nsecond"):
        try:
            save_openai_api_key(path, invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid key: {invalid!r}")


def test_cli_uses_executable_directory_for_bundled_install(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "AISC2CommanderBackend.exe"
    executable.write_bytes(b"test")
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli.sys, "executable", str(executable))

    assert cli.application_root() == tmp_path
