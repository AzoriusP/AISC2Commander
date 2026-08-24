from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import aisc2commander.sc2.process as process_module
from aisc2commander.sc2.process import _popen_sc2, discover_sc2_executable


def test_explicit_sc2_executable_is_accepted(tmp_path: Path) -> None:
    executable = tmp_path / "SC2_x64.exe"
    executable.touch()
    assert discover_sc2_executable(executable) == executable.resolve()


def test_frozen_windows_launch_does_not_leak_pyinstaller_dll_directory(
    monkeypatch,
) -> None:
    dll_directories: list[str | None] = []
    popen_calls: list[tuple[list[str], dict[str, object]]] = []
    fake_handle = SimpleNamespace(pid=1234)

    monkeypatch.setattr(process_module, "_is_frozen_windows", lambda: True)
    monkeypatch.setattr(
        process_module,
        "_set_windows_dll_directory",
        lambda path: dll_directories.append(path),
    )
    monkeypatch.setattr(process_module.sys, "_MEIPASS", r"C:\bundle", raising=False)

    def fake_popen(command, **options):
        popen_calls.append((command, options))
        return fake_handle

    monkeypatch.setattr(process_module.subprocess, "Popen", fake_popen)

    handle = _popen_sc2(["SC2_x64.exe", "-listen", "127.0.0.1"], cwd=r"G:\SC2\Support64")

    assert handle is fake_handle
    assert dll_directories == [None, r"C:\bundle"]
    assert popen_calls[0][1]["cwd"] == r"G:\SC2\Support64"
    assert popen_calls[0][1]["stdin"] is process_module.subprocess.DEVNULL
