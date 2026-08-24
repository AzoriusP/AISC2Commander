from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path, allowed_names: set[str]) -> tuple[str, ...]:
    """Load allowlisted local settings without overwriting process environment."""

    if not path.is_file():
        return ()
    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if name not in allowed_names or os.getenv(name):
            continue
        value = raw_value.strip().strip('"').strip("'")
        if not value:
            continue
        os.environ[name] = value
        loaded.append(name)
    return tuple(loaded)


def load_openai_api_key(path: Path) -> bool:
    """Load a local key file into the process environment without logging the secret.

    An existing OPENAI_API_KEY always wins. The file accepts either
    ``OPENAI_API_KEY=...`` or a single raw key line.
    """

    if os.getenv("OPENAI_API_KEY"):
        return False
    if not path.is_file():
        return False

    raw_key = ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, value = line.split("=", 1)
            if name.strip() != "OPENAI_API_KEY":
                continue
            raw_key = value.strip()
        else:
            raw_key = line
        break

    key = raw_key.strip().strip('"').strip("'")
    if not key or key in {"sk-your-key-here", "your_api_key_here"}:
        return False
    os.environ["OPENAI_API_KEY"] = key
    return True


def read_openai_api_key(path: Path) -> str:
    """Read the configured OpenAI key without changing the process environment."""

    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, value = line.split("=", 1)
            if name.strip() != "OPENAI_API_KEY":
                continue
            return value.strip().strip('"').strip("'")
        return line.strip().strip('"').strip("'")
    return ""


def mask_api_key(key: str) -> str:
    """Return a safe representation suitable for UI messages and logs."""

    value = key.strip()
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-4:]}"


def save_openai_api_key(path: Path, key: str) -> str:
    """Atomically persist an OpenAI key while retaining unrelated file lines."""

    value = key.strip()
    if not value:
        raise ValueError("API Key 不能为空")
    if "\n" in value or "\r" in value:
        raise ValueError("API Key 不能包含换行符")

    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    updated: list[str] = []
    replaced = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            name, _old_value = stripped.split("=", 1)
            if name.strip() == "OPENAI_API_KEY":
                if not replaced:
                    updated.append(f"OPENAI_API_KEY={value}")
                    replaced = True
                continue
        updated.append(raw_line)
    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(f"OPENAI_API_KEY={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return value
