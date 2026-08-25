from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import sys
import threading
import ctypes
from pathlib import Path


LOG = logging.getLogger(__name__)
_WINDOWS_DLL_DIRECTORY_LOCK = threading.Lock()


def _is_frozen_windows() -> bool:
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _set_windows_dll_directory(path: str | None) -> None:
    """Set the process-wide DLL directory used by Windows child processes."""
    set_directory = ctypes.windll.kernel32.SetDllDirectoryW
    set_directory.argtypes = [ctypes.c_wchar_p]
    set_directory.restype = ctypes.c_int
    if not set_directory(path):
        raise ctypes.WinError()


def _popen_sc2(command: list[str], *, cwd: str) -> subprocess.Popen[bytes]:
    options = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if not _is_frozen_windows():
        return subprocess.Popen(command, **options)

    # PyInstaller adds its extraction directory to the process-wide Windows DLL
    # search path. External programs inherit that setting and SC2 then tries to
    # load the bundled GUI DLLs instead of its own. Clear it only while creating
    # the child, then restore it immediately for the frozen GUI process.
    restore_directory = getattr(sys, "_MEIPASS", None)
    restore_path = str(restore_directory) if restore_directory else None
    with _WINDOWS_DLL_DIRECTORY_LOCK:
        _set_windows_dll_directory(None)
        try:
            return subprocess.Popen(command, **options)
        finally:
            _set_windows_dll_directory(restore_path)


def _valid_executable(path: Path) -> bool:
    return path.is_file() and path.name.casefold() in {"sc2_x64.exe", "sc2.exe"}


def discover_sc2_executable(explicit: str | Path | None = None) -> Path:
    """Find SC2 using explicit config, ExecuteInfo.txt, or standard installs."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    configured = os.environ.get("SC2_EXECUTABLE")
    if configured:
        candidates.append(Path(configured).expanduser())

    execute_info = Path.home() / "Documents" / "StarCraft II" / "ExecuteInfo.txt"
    if execute_info.is_file():
        text = execute_info.read_text(encoding="utf-8", errors="ignore").replace("\x00", "")
        match = re.search(r"^\s*executable\s*=\s*(.+?)\s*$", text, re.MULTILINE | re.IGNORECASE)
        if match:
            candidates.append(Path(match.group(1).strip()))

    install_roots = [
        Path(r"C:\Program Files (x86)\StarCraft II"),
        Path(r"C:\Program Files\StarCraft II"),
    ]
    for root in install_roots:
        versions = root / "Versions"
        if versions.is_dir():
            version_dirs = sorted(
                versions.glob("Base*"),
                key=lambda item: int(item.name[4:]) if item.name[4:].isdigit() else -1,
                reverse=True,
            )
            for version_dir in version_dirs:
                candidates.append(version_dir / "SC2_x64.exe")

    checked: list[str] = []
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        checked.append(str(resolved))
        if _valid_executable(resolved):
            LOG.debug("SC2 executable discovered: %s", resolved)
            return resolved
    raise FileNotFoundError(
        "StarCraft II API executable was not found. Checked: " + ", ".join(checked)
    )


def choose_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


class SC2Process:
    """Launch a visible SC2 client with Blizzard's documented API arguments."""

    def __init__(
        self,
        executable: Path,
        host: str,
        port: int,
        window_width: int = 1280,
        window_height: int = 800,
    ) -> None:
        self.executable = executable
        self.host = host
        self.port = port
        self.window_width = window_width
        self.window_height = window_height
        self.handle: subprocess.Popen[bytes] | None = None

    def launch(self) -> None:
        if self.handle is not None:
            raise RuntimeError("SC2 process has already been launched")
        command = [
            str(self.executable),
            "-listen",
            self.host,
            "-port",
            str(self.port),
            "-displayMode",
            "0",
            "-windowwidth",
            str(self.window_width),
            "-windowheight",
            str(self.window_height),
            "-windowx",
            "30",
            "-windowy",
            "30",
        ]
        LOG.info("Launching StarCraft II API client: %s", " ".join(command))
        # Blizzard's reference s2client-api StartProcess switches to Support64
        # before CreateProcess. SC2 exits early when launched from Base* here.
        game_root = self.executable.parents[2]
        support_directory = game_root / (
            "Support64" if self.executable.stem.casefold().endswith("_x64") else "Support"
        )
        if not support_directory.is_dir():
            raise FileNotFoundError(f"SC2 support directory not found: {support_directory}")
        LOG.debug("SC2 launch working directory: %s", support_directory)
        self.handle = _popen_sc2(command, cwd=str(support_directory))
        LOG.info("StarCraft II launched, pid=%s, api=%s:%s", self.handle.pid, self.host, self.port)

    def terminate_if_running(self, reason: str = "SC2 did not exit after the API quit request") -> None:
        if self.handle is None or self.handle.poll() is not None:
            return
        LOG.warning("%s; terminating pid=%s", reason, self.handle.pid)
        self.handle.terminate()
        try:
            self.handle.wait(timeout=5)
        except subprocess.TimeoutExpired:
            LOG.error("SC2 still did not exit; killing pid=%s", self.handle.pid)
            self.handle.kill()
            self.handle.wait(timeout=5)
