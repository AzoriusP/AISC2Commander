from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from types import SimpleNamespace
from tkinter import ttk

import pytest

from aisc2commander.gui import (
    CommanderGUI,
    ComputerPlayerChoice,
    GameConnectionChoice,
    GameMapChoice,
    _coordinate_ticks,
    _map_viewport,
    _responsive_scale,
    _terminate_named_process,
    find_project_root,
)


def test_gui_finds_project_root() -> None:
    assert (find_project_root() / "scripts" / "run.ps1").is_file()


def test_gui_uses_executable_directory_for_bundled_install(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "AISC2CommanderGUI.exe"
    executable.write_bytes(b"test")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert find_project_root() == tmp_path


def test_game_map_choice_requires_explicit_valid_source(tmp_path) -> None:
    local = tmp_path / "Custom.SC2Map"
    local.write_bytes(b"map")
    assert GameMapChoice("local", str(local)).command_arguments() == ("--map", str(local.resolve()))
    assert GameMapChoice("battlenet", "  Published Map  ").command_arguments() == (
        "--battlenet-map",
        "Published Map",
    )
    assert GameMapChoice("local", str(local)).profile_key().startswith("local:")
    assert GameMapChoice("local", str(local)).display_name() == "Custom"
    assert GameMapChoice("battlenet", "  Published Map  ").profile_key() == (
        "battlenet:published map"
    )


def test_computer_player_choice_builds_cli_spec() -> None:
    choice = ComputerPlayerChoice("zerg", "very_hard", "rush")
    assert choice.cli_spec() == "zerg,very_hard,rush"


def test_game_connection_choice_builds_official_multiplayer_arguments() -> None:
    assert GameConnectionChoice().command_arguments() == ()
    assert GameConnectionChoice("host", "192.0.2.10", 5001).command_arguments() == (
        "--multiplayer",
        "host",
        "--game-host",
        "192.0.2.10",
        "--network-port",
        "5001",
    )
    with pytest.raises(ValueError, match="IPv4"):
        GameConnectionChoice("join", "not-an-ip", 5001).command_arguments()


def test_responsive_scale_tracks_both_dimensions_and_clamps() -> None:
    assert _responsive_scale(920, 780, 920, 780, 0.65, 1.15) == 1.0
    assert _responsive_scale(690, 585, 920, 780, 0.65, 1.15) == 0.75
    assert _responsive_scale(300, 200, 920, 780, 0.65, 1.15) == 0.65
    assert _responsive_scale(1800, 1500, 920, 780, 0.65, 1.15) == 1.15


def test_map_coordinate_ticks_include_bounds_and_50_unit_intervals() -> None:
    assert _coordinate_ticks(8.0, 136.0) == (8.0, 50.0, 100.0, 136.0)
    assert _coordinate_ticks(8.0, 152.0) == (8.0, 50.0, 100.0, 150.0)
    assert _coordinate_ticks(0.0, 256.0) == (
        0.0,
        50.0,
        100.0,
        150.0,
        200.0,
        250.0,
    )


def test_map_viewport_preserves_world_coordinate_aspect_ratio() -> None:
    left, top, right, bottom = _map_viewport(800, 600, 8, 8, 136, 152)
    assert (right - left) / (bottom - top) == pytest.approx(128 / 144)
    assert 0 <= left < right <= 800
    assert 0 <= top < bottom <= 600


@pytest.mark.skipif(os.name != "nt", reason="Tk window layout validation")
def test_responsive_windows_keep_main_actions_and_map_footer_visible() -> None:
    root = tk.Tk()
    root.attributes("-alpha", 0.0)
    gui = CommanderGUI(root)
    try:
        root.geometry("600x507")
        root.update()
        root_scaler = getattr(root, "_responsive_scaler")
        root_scaler._apply()
        root.update()

        action_widths = [
            widget.winfo_width()
            for widget in (
                gui.start_button,
                gui.stop_button,
                gui.api_key_button,
                gui.map_button,
                gui.plan_button,
            )
        ]
        assert min(action_widths) > 0
        assert all(
            widget.winfo_width() >= widget.winfo_reqwidth()
            for widget in (
                gui.start_button,
                gui.stop_button,
                gui.api_key_button,
                gui.map_button,
                gui.plan_button,
            )
        )
        assert gui.input_row.winfo_y() + gui.input_row.winfo_height() <= root.winfo_height()

        inspected: dict[str, int] = {}

        def inspect_dialog() -> None:
            dialog = next(
                child for child in root.winfo_children() if isinstance(child, tk.Toplevel)
            )
            dialog.geometry("672x468")
            dialog.update()
            getattr(dialog, "_responsive_scaler")._apply()
            dialog.update()

            def descendants(widget: tk.Misc) -> list[tk.Misc]:
                children = list(widget.winfo_children())
                return children + [nested for child in children for nested in descendants(child)]

            action = next(
                widget
                for widget in descendants(dialog)
                if isinstance(widget, ttk.Button) and widget.cget("text") == "用此地图启动"
            )
            inspected["bottom"] = action.winfo_rooty() + action.winfo_height()
            inspected["window_bottom"] = dialog.winfo_rooty() + dialog.winfo_height()
            dialog.destroy()

        root.after(75, inspect_dialog)
        assert gui._choose_game_map(include_race=True) is None
        assert inspected["bottom"] <= inspected["window_bottom"]
    finally:
        gui.closing = True
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Windows process validation")
def test_force_stop_fallback_validates_executable_name_before_exact_pid() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert not _terminate_named_process(child.pid, {"sc2_x64.exe"})
        assert child.poll() is None
        assert _terminate_named_process(child.pid, {"python.exe", "pythonw.exe"})
        child.wait(timeout=5)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()


def test_listen_button_starts_and_stops_continuous_listener(monkeypatch, tmp_path) -> None:
    class FakeListener:
        is_speaking = False

        def __init__(self, on_segment, **kwargs) -> None:
            self.on_segment = on_segment
            self.kwargs = kwargs
            self.started = False
            self.stopped = False

        def start(self) -> None:
            self.started = True

        def stop(self, *, flush=True) -> None:
            self.stopped = flush

    class FakeWidget:
        def __init__(self) -> None:
            self.values: dict[str, object] = {}

        def configure(self, **kwargs) -> None:
            self.values.update(kwargs)

    class FakeStatus:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    listeners: list[FakeListener] = []

    def create_listener(*args, **kwargs):
        listener = FakeListener(*args, **kwargs)
        listeners.append(listener)
        return listener

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("aisc2commander.gui.VoiceCommandListener", create_listener)
    monkeypatch.setattr(
        "aisc2commander.gui.threading.Thread",
        lambda **_kwargs: SimpleNamespace(start=lambda: None),
    )

    gui = CommanderGUI.__new__(CommanderGUI)
    gui.project_root = tmp_path
    gui.server_ready = True
    gui.closing = False
    gui.listening_running = False
    gui.listening_transcribing = False
    gui.recording_running = False
    gui.recording_transcribing = False
    gui.voice_recorder = None
    gui.voice_listener = None
    gui.voice_transcriber = SimpleNamespace(transcribe=lambda _path: "")
    gui.voice_segment_queue = None
    gui.voice_transcription_thread = None
    gui.voice_sentence_count = 0
    gui.voice_api_key = ""
    gui.voice_provider = "openai"
    gui.whisper_model = "small"
    gui.voice_silence_seconds = 0.8
    gui.voice_min_speech_seconds = 0.25
    gui.voice_max_utterance_seconds = 15.0
    gui.voice_vad_rms = 0.008
    gui.voice_vad_calibration_seconds = 1.0
    gui.voice_vad_noise_multiplier = 2.5
    gui.voice_vad_release_multiplier = 1.6
    gui.listen_button = FakeWidget()
    gui.record_button = FakeWidget()
    gui.status_text = FakeStatus()
    gui.root = SimpleNamespace(after=lambda *_args: None)
    gui._append_message = lambda *_args: None

    gui._toggle_listening()
    assert listeners[0].started
    assert listeners[0].kwargs["calibration_seconds"] == 1.0
    assert listeners[0].kwargs["noise_multiplier"] == 2.5
    assert gui.listening_running
    assert gui.listen_button.values["text"] == "停止监听"
    assert gui.listen_button.values["state"] == "normal"
    assert gui.record_button.values["state"] == "disabled"

    gui._toggle_listening()
    assert not gui.listening_running
    assert listeners[0].stopped
    assert gui.listen_button.values["text"] == "正在完成最后一句…"
    assert gui.listen_button.values["state"] == "disabled"
    gui._listening_finished()
    assert gui.listen_button.values["text"] == "开始监听"
    assert gui.record_button.values["state"] == "normal"


def test_completed_voice_sentence_uses_same_command_endpoint_and_auto_sends(tmp_path) -> None:
    class FakeWidget:
        def __init__(self) -> None:
            self.values: dict[str, object] = {}

        def configure(self, **kwargs) -> None:
            self.values.update(kwargs)

    class FakeStatus:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    path = tmp_path / "sentence.wav"
    path.write_bytes(b"test")
    gui = CommanderGUI.__new__(CommanderGUI)
    gui.listening_running = False
    gui.listening_transcribing = False
    gui.recording_running = False
    gui.recording_transcribing = False
    gui.voice_listener = None
    gui.listen_button = FakeWidget()
    gui.record_button = FakeWidget()
    gui.status_text = FakeStatus()
    gui.server_ready = True
    gui.closing = False
    gui.voice_sentence_count = 0
    gui.voice_segment_queue = queue.Queue()
    gui.voice_transcription_thread = SimpleNamespace()
    gui.voice_transcriber = SimpleNamespace(
        transcribe=lambda _path: "选中的建筑制造5个农民"
    )
    messages: list[tuple[str, str]] = []
    requests: list[tuple[str, object]] = []
    gui._append_message = lambda role, text: messages.append((role, text))
    gui._post_ui = lambda callback, *args: callback(*args)

    def request(path_name, payload, timeout):
        requests.append((path_name, payload))
        return {"job_id": "cmd-0001"}

    gui._request_json = request
    segment_queue: queue.Queue = queue.Queue()
    segment_queue.put(path)
    segment_queue.put(None)
    gui._voice_transcription_loop(segment_queue)

    assert requests == [
        ("/command", {"text": "选中的建筑制造5个农民"}),
    ]
    assert any("正在自动发送：选中的建筑制造5个农民" in text for _, text in messages)
    assert any("cmd-0001" in text for _, text in messages)
    assert gui.voice_sentence_count == 1


def test_record_button_transcribes_to_input_without_auto_sending(monkeypatch, tmp_path) -> None:
    class FakeWidget:
        def __init__(self) -> None:
            self.values: dict[str, object] = {}

        def configure(self, **kwargs) -> None:
            self.values.update(kwargs)

    class FakeStatus:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    class FakeInput:
        def __init__(self) -> None:
            self.value = ""
            self.focused = False

        def delete(self, *_args) -> None:
            self.value = ""

        def insert(self, _index, text: str) -> None:
            self.value = text

        def focus_set(self) -> None:
            self.focused = True

    audio_path = tmp_path / "single.wav"
    audio_path.write_bytes(b"audio")

    class FakeRecorder:
        elapsed_seconds = 1.25

        def __init__(self) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

        def stop(self):
            return audio_path

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    recorder = FakeRecorder()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("aisc2commander.gui.StreamingWavRecorder", lambda: recorder)
    monkeypatch.setattr("aisc2commander.gui.threading.Thread", ImmediateThread)

    gui = CommanderGUI.__new__(CommanderGUI)
    gui.project_root = tmp_path
    gui.voice_provider = "openai"
    gui.whisper_model = "small"
    gui.voice_api_key = ""
    gui.voice_transcriber = SimpleNamespace(transcribe=lambda _path: "让选中的陆战队员前往A3")
    gui.voice_transcription_thread = None
    gui.voice_recorder = None
    gui.listening_running = False
    gui.listening_transcribing = False
    gui.recording_running = False
    gui.recording_transcribing = False
    gui.server_ready = True
    gui.closing = False
    gui.record_button = FakeWidget()
    gui.listen_button = FakeWidget()
    gui.status_text = FakeStatus()
    gui.input_box = FakeInput()
    gui.command_plan_store = SimpleNamespace(resolve_invocation=lambda _text: None)
    gui.root = SimpleNamespace(after=lambda *_args: None)
    gui._append_message = lambda *_args: None
    gui._post_ui = lambda callback, *args: callback(*args)
    sends: list[bool] = []
    gui._send = lambda: sends.append(True)

    gui._toggle_recording()
    assert recorder.started
    assert gui.recording_running
    assert gui.record_button.values["text"] == "停止录音"
    assert gui.listen_button.values["state"] == "disabled"

    gui._toggle_recording()
    assert not gui.recording_running
    assert not gui.recording_transcribing
    assert gui.input_box.value == "让选中的陆战队员前往A3"
    assert gui.input_box.focused
    assert sends == []
    assert gui.record_button.values["text"] == "开始录音"
    assert gui.listen_button.values["state"] == "normal"
    assert gui.status_text.value == "语音已转写，可编辑后发送"
