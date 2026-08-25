from __future__ import annotations

import json
import base64
import ipaddress
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, ImageTk

from .agent.voice import (
    LocalWhisperTranscriber,
    OpenAITranscriber,
    StreamingWavRecorder,
    VoiceCommandListener,
)
from .command_plans import CommandPlanStore, parse_plan_control
from .map_capacity import MapCapacityCache, probe_map_capacity
from .map_points import (
    DEFAULT_PRESET_NAME,
    MapImageStore,
    MapPointStore,
    MapPreviewStore,
    map_profile_key,
)
from .sc2map_images import extract_embedded_map_image
from .settings import load_env_file, mask_api_key, read_openai_api_key, save_openai_api_key


CONTROL_URL = "http://127.0.0.1:8765"
TRANSCRIPTION_MODEL = "gpt-transcribe"
MAP_CANVAS_PADDING = 42.0
MAP_COORDINATE_INTERVAL = 50.0
RACE_OPTIONS = (
    ("人族（Terran）", "terran"),
    ("虫族（Zerg）", "zerg"),
    ("神族（Protoss）", "protoss"),
    ("随机（Random）", "random"),
)
RACE_LABELS = {value: label for label, value in RACE_OPTIONS}
COMPUTER_RACE_OPTIONS = (("无（不生成）", ""),) + RACE_OPTIONS
COMPUTER_DIFFICULTY_OPTIONS = (
    ("非常简单", "very_easy"),
    ("简单", "easy"),
    ("中等", "medium"),
    ("中等偏难", "medium_hard"),
    ("困难", "hard"),
    ("更困难", "harder"),
    ("非常困难", "very_hard"),
    ("作弊：视野", "cheat_vision"),
    ("作弊：资源", "cheat_money"),
    ("作弊：疯狂", "cheat_insane"),
)
COMPUTER_AI_BUILD_OPTIONS = (
    ("随机（Random）", "random"),
    ("快攻（Rush）", "rush"),
    ("时机进攻（Timing）", "timing"),
    ("强力部队（Power）", "power"),
    ("运营（Macro）", "macro"),
    ("空军（Air）", "air"),
)
COMPUTER_RACE_VALUES = {label: value for label, value in COMPUTER_RACE_OPTIONS}
COMPUTER_DIFFICULTY_VALUES = {label: value for label, value in COMPUTER_DIFFICULTY_OPTIONS}
COMPUTER_AI_BUILD_VALUES = {label: value for label, value in COMPUTER_AI_BUILD_OPTIONS}
COMPUTER_RACE_LABELS = {value: label for label, value in COMPUTER_RACE_OPTIONS}
COMPUTER_DIFFICULTY_LABELS = {
    value: label for label, value in COMPUTER_DIFFICULTY_OPTIONS
}
COMPUTER_AI_BUILD_LABELS = {value: label for label, value in COMPUTER_AI_BUILD_OPTIONS}
MULTIPLAYER_MODE_OPTIONS = (
    ("单机 / 对战电脑", "single"),
    ("创建联机对局（本机作为主机）", "host"),
    ("加入联机对局", "join"),
)
MULTIPLAYER_MODE_VALUES = {label: value for label, value in MULTIPLAYER_MODE_OPTIONS}
MULTIPLAYER_MODE_LABELS = {value: label for label, value in MULTIPLAYER_MODE_OPTIONS}


def _responsive_scale(
    width: int,
    height: int,
    base_width: int,
    base_height: int,
    minimum: float,
    maximum: float,
) -> float:
    if width <= 1 or height <= 1:
        return 1.0
    return max(minimum, min(maximum, width / base_width, height / base_height))


def _coordinate_ticks(
    minimum: float,
    maximum: float,
    interval: float = MAP_COORDINATE_INTERVAL,
) -> tuple[float, ...]:
    if not all(math.isfinite(value) for value in (minimum, maximum, interval)):
        return ()
    if maximum <= minimum or interval <= 0:
        return ()
    ticks: list[float] = []
    current = math.ceil(minimum / interval) * interval
    while current <= maximum + 1e-6:
        ticks.append(float(current))
        current += interval
    edge_clearance = interval * 0.25
    if not ticks or ticks[0] - minimum >= edge_clearance:
        ticks.insert(0, minimum)
    if not ticks or maximum - ticks[-1] >= edge_clearance:
        ticks.append(maximum)
    return tuple(ticks)


def _map_viewport(
    width: float,
    height: float,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    padding: float = MAP_CANVAS_PADDING,
) -> tuple[float, float, float, float]:
    world_width = max(max_x - min_x, 1.0)
    world_height = max(max_y - min_y, 1.0)
    available_width = max(width - 2 * padding, 1.0)
    available_height = max(height - 2 * padding, 1.0)
    scale = min(available_width / world_width, available_height / world_height)
    rendered_width = world_width * scale
    rendered_height = world_height * scale
    left = (width - rendered_width) / 2
    top = (height - rendered_height) / 2
    return left, top, left + rendered_width, top + rendered_height


class ResponsiveWindowScaler:
    """Scale a Tk window's complete widget tree as its client area changes."""

    _next_id = 0

    def __init__(
        self,
        window: tk.Toplevel | tk.Tk,
        base_width: int,
        base_height: int,
        *,
        minimum: float = 0.62,
        maximum: float = 1.15,
    ) -> None:
        type(self)._next_id += 1
        self.identifier = type(self)._next_id
        self.window = window
        self.base_width = base_width
        self.base_height = base_height
        self.minimum = minimum
        self.maximum = maximum
        self.scale = 1.0
        self.style = ttk.Style(window)
        self._after_id: str | None = None
        self._font_bases: dict[tk.Misc, dict[str, object]] = {}
        self._style_bases: dict[tk.Misc, dict[str, object]] = {}
        self._manager_bases: dict[tk.Misc, tuple[str, dict[str, tuple[int, ...]]]] = {}
        self._option_bases: dict[tk.Misc, dict[str, tuple[int, ...]]] = {}
        self._tree_column_bases: dict[ttk.Treeview, dict[str, tuple[int, int]]] = {}
        self._tag_bases: dict[tk.Text, dict[str, dict[str, object]]] = {}
        window.minsize(
            max(320, round(base_width * minimum)),
            max(220, round(base_height * minimum)),
        )
        window.bind("<Configure>", self._configured, add="+")
        window.after_idle(self.refresh)

    def _configured(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is not self.window:
            return
        self.refresh()

    def refresh(self) -> None:
        if not self.window.winfo_exists():
            return
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._after_id = self.window.after(35, self._apply)

    def _apply(self) -> None:
        self._after_id = None
        if not self.window.winfo_exists():
            return
        self.scale = _responsive_scale(
            self.window.winfo_width(),
            self.window.winfo_height(),
            self.base_width,
            self.base_height,
            self.minimum,
            self.maximum,
        )
        self._scale_widget_tree(self.window)

    def _scale_widget_tree(self, widget: tk.Misc) -> None:
        if widget is not self.window:
            self._scale_widget(widget)
        for child in widget.winfo_children():
            self._scale_widget_tree(child)

    def _font_base(self, value: object) -> dict[str, object] | None:
        if value in (None, ""):
            return None
        try:
            return tkfont.Font(root=self.window, font=value).actual()
        except tk.TclError:
            return None

    def _scaled_font(self, base: dict[str, object]) -> tuple[object, ...]:
        size = int(base.get("size", 10))
        sign = -1 if size < 0 else 1
        scaled_size = sign * max(6, round(abs(size) * self.scale))
        attributes: list[str] = []
        weight = str(base.get("weight", "normal"))
        slant = str(base.get("slant", "roman"))
        if weight != "normal":
            attributes.append(weight)
        if slant != "roman":
            attributes.append(slant)
        if bool(base.get("underline", False)):
            attributes.append("underline")
        if bool(base.get("overstrike", False)):
            attributes.append("overstrike")
        return (str(base.get("family", "TkDefaultFont")), scaled_size, *attributes)

    def _distance_parts(self, widget: tk.Misc, value: object) -> tuple[int, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, (int, float)):
            values = (value,)
        elif isinstance(value, (tuple, list)):
            values = value
        else:
            values = widget.tk.splitlist(value)
        result: list[int] = []
        for part in values:
            try:
                pixels = part if isinstance(part, (int, float)) else widget.winfo_pixels(part)
                result.append(round(float(pixels)))
            except (TypeError, ValueError, tk.TclError):
                return ()
        return tuple(result)

    def _scaled_distance(self, base: tuple[int, ...]) -> int | tuple[int, ...]:
        values = tuple(max(0, round(value * self.scale)) for value in base)
        return values[0] if len(values) == 1 else values

    def _style_name(self, original: str) -> str:
        return f"Responsive{self.identifier}.{original}"

    def _scale_ttk_style(self, widget: ttk.Widget) -> None:
        if isinstance(widget, (ttk.Scrollbar, ttk.Separator, ttk.Progressbar, ttk.Sizegrip)):
            return
        base = self._style_bases.get(widget)
        if base is None:
            original = str(widget.cget("style") or widget.winfo_class())
            scaled = self._style_name(original)
            original_config = self.style.configure(original) or {}
            if original_config:
                self.style.configure(scaled, **original_config)
            original_map = self.style.map(original) or {}
            if original_map:
                self.style.map(scaled, **original_map)
            font = self._font_base(self.style.lookup(original, "font"))
            padding = self._distance_parts(widget, self.style.lookup(original, "padding"))
            rowheight = self._distance_parts(widget, self.style.lookup(original, "rowheight"))
            base = {
                "scaled": scaled,
                "font": font,
                "padding": padding,
                "rowheight": rowheight,
            }
            self._style_bases[widget] = base
            widget.configure(style=scaled)
        options: dict[str, object] = {}
        font = base.get("font")
        if isinstance(font, dict):
            options["font"] = self._scaled_font(font)
        padding = base.get("padding")
        if isinstance(padding, tuple) and padding:
            options["padding"] = self._scaled_distance(padding)
        rowheight = base.get("rowheight")
        if isinstance(rowheight, tuple) and rowheight:
            options["rowheight"] = self._scaled_distance(rowheight)
        if options:
            self.style.configure(str(base["scaled"]), **options)

    def _scale_manager_padding(self, widget: tk.Misc) -> None:
        manager_base = self._manager_bases.get(widget)
        if manager_base is None:
            manager = widget.winfo_manager()
            if manager not in {"pack", "grid"}:
                return
            info = widget.pack_info() if manager == "pack" else widget.grid_info()
            bases = {
                name: parts
                for name in ("padx", "pady", "ipadx", "ipady")
                if (parts := self._distance_parts(widget, info.get(name, "")))
            }
            manager_base = (manager, bases)
            self._manager_bases[widget] = manager_base
        manager, bases = manager_base
        options = {name: self._scaled_distance(parts) for name, parts in bases.items()}
        if not options:
            return
        if manager == "pack":
            widget.pack_configure(**options)
        else:
            widget.grid_configure(**options)

    def _scale_widget_options(self, widget: tk.Misc) -> None:
        bases = self._option_bases.get(widget)
        if bases is None:
            keys = set(widget.keys())
            names = ("padx", "pady", "padding", "wraplength")
            bases = {
                name: parts
                for name in names
                if name in keys
                and (parts := self._distance_parts(widget, widget.cget(name)))
            }
            if isinstance(widget, tk.Canvas):
                height = self._distance_parts(widget, widget.cget("height"))
                if height and height[0] > 1:
                    bases["height"] = height
            self._option_bases[widget] = bases
        for name, parts in bases.items():
            try:
                widget.configure(**{name: self._scaled_distance(parts)})
            except tk.TclError:
                pass

    def _scale_direct_font(self, widget: tk.Misc) -> None:
        if widget in self._font_bases:
            base = self._font_bases[widget]
        elif "font" in widget.keys():
            base = self._font_base(widget.cget("font"))
            if base is None:
                return
            self._font_bases[widget] = base
        else:
            return
        try:
            widget.configure(font=self._scaled_font(base))
        except tk.TclError:
            pass

    def _scale_text_tags(self, widget: tk.Text) -> None:
        bases = self._tag_bases.get(widget)
        if bases is None:
            bases = {}
            for tag in widget.tag_names():
                font = self._font_base(widget.tag_cget(tag, "font"))
                spacing = self._distance_parts(widget, widget.tag_cget(tag, "spacing3"))
                if font is not None or spacing:
                    bases[tag] = {"font": font, "spacing3": spacing}
            self._tag_bases[widget] = bases
        for tag, base in bases.items():
            options: dict[str, object] = {}
            font = base.get("font")
            if isinstance(font, dict):
                options["font"] = self._scaled_font(font)
            spacing = base.get("spacing3")
            if isinstance(spacing, tuple) and spacing:
                options["spacing3"] = self._scaled_distance(spacing)
            if options:
                widget.tag_configure(tag, **options)

    def _scale_tree_columns(self, widget: ttk.Treeview) -> None:
        bases = self._tree_column_bases.get(widget)
        if bases is None:
            bases = {}
            for column in widget.cget("columns"):
                info = widget.column(column)
                bases[str(column)] = (int(info["width"]), int(info["minwidth"]))
            self._tree_column_bases[widget] = bases
        for column, (width, minwidth) in bases.items():
            widget.column(
                column,
                width=max(32, round(width * self.scale)),
                minwidth=max(24, round(minwidth * self.scale)),
            )

    def _scale_widget(self, widget: tk.Misc) -> None:
        if isinstance(widget, ttk.Widget):
            self._scale_ttk_style(widget)
        self._scale_direct_font(widget)
        self._scale_widget_options(widget)
        self._scale_manager_padding(widget)
        if isinstance(widget, tk.Text):
            self._scale_text_tags(widget)
        if isinstance(widget, ttk.Treeview):
            self._scale_tree_columns(widget)


@dataclass(frozen=True, slots=True)
class GameMapChoice:
    kind: str
    value: str

    def command_arguments(self) -> tuple[str, str]:
        if self.kind == "local":
            path = Path(self.value).expanduser()
            if path.suffix.casefold() != ".sc2map" or not path.is_file():
                raise ValueError("请选择存在的 .SC2Map 地图文件")
            return "--map", str(path.resolve())
        if self.kind == "battlenet":
            name = self.value.strip()
            if not name:
                raise ValueError("请输入 Battle.net 地图的完整名称")
            return "--battlenet-map", name
        raise ValueError(f"未知地图来源：{self.kind}")

    def profile_key(self) -> str:
        return map_profile_key(self.kind, self.value)

    def display_name(self) -> str:
        if self.kind == "local":
            return Path(self.value).expanduser().stem
        return self.value.strip()


@dataclass(frozen=True, slots=True)
class ComputerPlayerChoice:
    race: str
    difficulty: str = "easy"
    ai_build: str = "random"

    def cli_spec(self) -> str:
        return f"{self.race},{self.difficulty},{self.ai_build}"


@dataclass(frozen=True, slots=True)
class GameConnectionChoice:
    mode: str = "single"
    host_ip: str = ""
    network_port: int = 5001

    def command_arguments(self) -> tuple[str, ...]:
        if self.mode == "single":
            return ()
        if self.mode not in {"host", "join"}:
            raise ValueError(f"未知对局模式：{self.mode}")
        try:
            host_ip = str(ipaddress.IPv4Address(self.host_ip.strip()))
        except ipaddress.AddressValueError as error:
            raise ValueError("请输入主机可被另一位玩家访问的 IPv4 地址") from error
        try:
            network_port = int(self.network_port)
        except (TypeError, ValueError) as error:
            raise ValueError("联机起始端口必须是整数") from error
        if network_port < 1 or network_port > 65531:
            raise ValueError("联机起始端口必须在 1–65531 之间")
        return (
            "--multiplayer",
            self.mode,
            "--game-host",
            host_ip,
            "--network-port",
            str(network_port),
        )


def _terminate_named_process(pid: int, allowed_names: set[str]) -> bool:
    """Terminate one exact PID only after validating its executable basename."""

    if pid <= 0 or pid == os.getpid() or os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    query_name = kernel32.QueryFullProcessImageNameW
    query_name.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    query_name.restype = wintypes.BOOL
    terminate = kernel32.TerminateProcess
    terminate.argtypes = (wintypes.HANDLE, wintypes.UINT)
    terminate.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    process_terminate = 0x0001
    process_query_limited_information = 0x1000
    handle = open_process(
        process_terminate | process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        size = wintypes.DWORD(32768)
        path_buffer = ctypes.create_unicode_buffer(size.value)
        if not query_name(handle, 0, path_buffer, ctypes.byref(size)):
            return False
        executable_name = Path(path_buffer.value).name.casefold()
        if executable_name not in {name.casefold() for name in allowed_names}:
            return False
        return bool(terminate(handle, 1))
    finally:
        close_handle(handle)


def find_project_root() -> Path:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend((executable_dir, executable_dir.parent))
    candidates.extend((Path.cwd(), Path(__file__).resolve().parents[2]))
    for candidate in candidates:
        if (candidate / "scripts" / "run.ps1").is_file():
            return candidate
    raise FileNotFoundError("找不到项目目录中的 scripts\\run.ps1")


class CommanderGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("StarCraft II AI Commander")
        self.root.geometry("920x780")
        self.root.minsize(760, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.project_root = find_project_root()
        load_env_file(
            self.project_root / "config" / "voice.env",
            {
                "VOICE_TRANSCRIPTION_PROVIDER",
                "WHISPER_MODEL",
                "VOICE_SILENCE_SECONDS",
                "VOICE_MIN_SPEECH_SECONDS",
                "VOICE_MAX_UTTERANCE_SECONDS",
                "VOICE_VAD_RMS",
                "VOICE_VAD_CALIBRATION_SECONDS",
                "VOICE_VAD_NOISE_MULTIPLIER",
                "VOICE_VAD_RELEASE_MULTIPLIER",
            },
        )
        self.voice_provider = os.getenv("VOICE_TRANSCRIPTION_PROVIDER", "local").casefold()
        self.whisper_model = os.getenv("WHISPER_MODEL", "small")
        self.voice_silence_seconds = float(os.getenv("VOICE_SILENCE_SECONDS", "0.7"))
        self.voice_min_speech_seconds = float(os.getenv("VOICE_MIN_SPEECH_SECONDS", "0.25"))
        self.voice_max_utterance_seconds = float(os.getenv("VOICE_MAX_UTTERANCE_SECONDS", "10"))
        self.voice_vad_rms = float(os.getenv("VOICE_VAD_RMS", "0.008"))
        self.voice_vad_calibration_seconds = float(
            os.getenv("VOICE_VAD_CALIBRATION_SECONDS", "1.0")
        )
        self.voice_vad_noise_multiplier = float(
            os.getenv("VOICE_VAD_NOISE_MULTIPLIER", "2.5")
        )
        self.voice_vad_release_multiplier = float(
            os.getenv("VOICE_VAD_RELEASE_MULTIPLIER", "1.6")
        )
        self.last_event_id = 0
        self.server_instance_id = ""
        self.server_ready = False
        self.poll_running = False
        self.closing = False
        self.listening_running = False
        self.listening_transcribing = False
        self.voice_listener: VoiceCommandListener | None = None
        self.voice_transcriber: LocalWhisperTranscriber | OpenAITranscriber | None = None
        self.voice_segment_queue: queue.Queue[Path | None] | None = None
        self.voice_transcription_thread: threading.Thread | None = None
        self.voice_sentence_count = 0
        self.voice_api_key = ""
        self.recording_running = False
        self.recording_transcribing = False
        self.voice_recorder: StreamingWavRecorder | None = None
        self.send_running = False
        self.stop_running = False
        self.launch_started_at = 0.0
        self.launch_process: subprocess.Popen[bytes] | None = None
        self.commander_pid = 0
        self.sc2_pid = 0
        self.game_state: dict[str, object] = {}
        self.jobs: list[dict[str, object]] = []
        self.selected_map_choice: GameMapChoice | None = None
        self.selected_race = "terran"
        self.selected_computers: list[ComputerPlayerChoice] = []
        self.selected_multiplayer_mode = "single"
        self.selected_game_host_ip = ""
        self.selected_network_port = 5001
        self.map_capacity_cache = MapCapacityCache(
            self.project_root / "config" / "map_capacity.json"
        )
        self.map_point_store = MapPointStore(self.project_root / "config" / "map_points.json")
        self.map_preview_store = MapPreviewStore(self.project_root / "config" / "map_previews.json")
        self.map_image_store = MapImageStore(
            self.project_root / "config" / "map_images.json",
            self.project_root / "assets" / "map_images",
        )
        self.command_plan_store = CommandPlanStore(
            self.project_root / "config" / "command_plans.json"
        )
        self.command_plan_window: tk.Toplevel | None = None
        self.command_plan_combo: ttk.Combobox | None = None
        self.command_plan_choice: tk.StringVar | None = None
        self.command_plan_name: tk.StringVar | None = None
        self.command_plan_aliases: tk.StringVar | None = None
        self.command_plan_script: tk.Text | None = None
        self.command_plan_status: tk.StringVar | None = None
        self.command_plan_loaded_name = ""
        self.map_window: tk.Toplevel | None = None
        self.map_canvas: tk.Canvas | None = None
        self.map_point_list: tk.Listbox | None = None
        self.map_editor_profile_key = ""
        self.map_editor_display_name = ""
        self.map_editor_choice: GameMapChoice | None = None
        self.map_editor_state: dict[str, object] = {}
        self.map_preset_var: tk.StringVar | None = None
        self.map_preset_combo: ttk.Combobox | None = None
        self.map_preset_status: tk.StringVar | None = None
        self.map_preview_status: tk.StringVar | None = None
        self.map_image_button: ttk.Button | None = None
        self.map_background_source: Image.Image | None = None
        self.map_background_photo: ImageTk.PhotoImage | None = None
        self.map_background_render_key: tuple[int, int, str] | None = None
        self.map_background_path: Path | None = None
        self.map_background_origin = ""
        self.map_background_label = ""
        self._ui_events: queue.Queue[tuple[object, tuple[object, ...]]] = queue.Queue()

        self.status_text = tk.StringVar(value="未连接")
        self._configure_style()
        self._build_widgets()
        self._enable_responsive_scaling(self.root, 920, 780, minimum=0.65)
        self._append_message("system", "界面已启动。点击“开启对局”打开 SC2 和运行终端。")
        self.root.after(30, self._drain_ui_events)
        self.root.after(150, self._poll)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Action.TButton", font=("Microsoft YaHei UI", 11), padding=(14, 8))
        style.configure("Danger.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=(14, 8))
        style.configure(
            "Recording.TButton",
            font=("Microsoft YaHei UI", 11, "bold"),
            foreground="#b42318",
            padding=(14, 8),
        )

    def _enable_responsive_scaling(
        self,
        window: tk.Toplevel | tk.Tk,
        base_width: int,
        base_height: int,
        *,
        minimum: float = 0.62,
        maximum: float = 1.15,
    ) -> ResponsiveWindowScaler:
        scaler = ResponsiveWindowScaler(
            window,
            base_width,
            base_height,
            minimum=minimum,
            maximum=maximum,
        )
        setattr(window, "_responsive_scaler", scaler)
        return scaler

    @staticmethod
    def _refresh_responsive_scaling(window: tk.Misc) -> None:
        scaler = getattr(window, "_responsive_scaler", None)
        if isinstance(scaler, ResponsiveWindowScaler):
            scaler.refresh()

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="StarCraft II AI Commander", style="Title.TLabel").pack(side="left")
        self.status_label = ttk.Label(
            header,
            textvariable=self.status_text,
            style="Status.TLabel",
            foreground="#a33b20",
        )
        self.status_label.pack(side="right", padx=(12, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 12))
        for column in range(5):
            buttons.columnconfigure(column, weight=1, uniform="main_actions")
        self.start_button = ttk.Button(
            buttons,
            text="开启对局",
            command=self._start_project,
            style="Action.TButton",
        )
        self.start_button.grid(row=0, column=0, sticky="ew")
        self.stop_button = ttk.Button(
            buttons,
            text="强制停止",
            command=self._stop_project,
            style="Danger.TButton",
            state="disabled",
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self.api_key_button = ttk.Button(
            buttons,
            text="配置 API Key",
            command=self._open_api_key_dialog,
            style="Action.TButton",
        )
        self.api_key_button.grid(row=0, column=2, sticky="ew", padx=(10, 0))
        self.map_button = ttk.Button(
            buttons,
            text="地图点位",
            command=self._open_map_editor,
            style="Action.TButton",
        )
        self.map_button.grid(row=0, column=3, sticky="ew", padx=(10, 0))
        self.plan_button = ttk.Button(
            buttons,
            text="战术指令集",
            command=self._open_command_plan_editor,
            style="Action.TButton",
        )
        self.plan_button.grid(row=0, column=4, sticky="ew", padx=(10, 0))

        jobs_frame = ttk.LabelFrame(outer, text=" 指令运行状态 ", padding=(8, 6))
        jobs_frame.pack(fill="x", pady=(0, 12))
        jobs_frame.columnconfigure(0, weight=1)
        self.job_tree = ttk.Treeview(
            jobs_frame,
            columns=("id", "status", "selection", "progress", "instruction"),
            show="headings",
            height=5,
            selectmode="browse",
        )
        self.job_tree.heading("id", text="编号")
        self.job_tree.heading("status", text="状态")
        self.job_tree.heading("selection", text="提交时单位 ID")
        self.job_tree.heading("progress", text="进度 / 反馈")
        self.job_tree.heading("instruction", text="玩家指令")
        self.job_tree.column("id", width=92, minwidth=82, stretch=False, anchor="center")
        self.job_tree.column("status", width=90, minwidth=78, stretch=False, anchor="center")
        self.job_tree.column("selection", width=205, minwidth=120, stretch=True)
        self.job_tree.column("progress", width=280, minwidth=170, stretch=True)
        self.job_tree.column("instruction", width=330, minwidth=190, stretch=True)
        self.job_tree.grid(row=0, column=0, sticky="ew")
        self.job_tree.tag_configure("running", foreground="#8a5a00")
        self.job_tree.tag_configure("completed", foreground="#26734d")
        self.job_tree.tag_configure("failed", foreground="#b42318")
        job_scroll = ttk.Scrollbar(jobs_frame, orient="vertical", command=self.job_tree.yview)
        job_scroll.grid(row=0, column=1, sticky="ns")
        self.job_tree.configure(yscrollcommand=job_scroll.set)
        self.job_tree.insert("", "end", iid="placeholder", values=("—", "空闲", "尚无指令", ""))

        ttk.Label(outer, text="消息", font=("Microsoft YaHei UI", 10, "bold")).pack(
            anchor="w", pady=(0, 5)
        )
        self.messages = ScrolledText(
            outer,
            wrap="word",
            height=14,
            state="disabled",
            font=("Microsoft YaHei UI", 10),
            padx=10,
            pady=10,
            background="#111820",
            foreground="#d9e2ec",
            insertbackground="#ffffff",
            relief="flat",
        )
        self.messages.pack(fill="both", expand=True)
        self.messages.tag_configure("player_label", foreground="#58a6ff", font=("Microsoft YaHei UI", 10, "bold"))
        self.messages.tag_configure("assistant_label", foreground="#45c474", font=("Microsoft YaHei UI", 10, "bold"))
        self.messages.tag_configure("system_label", foreground="#9aa8b5", font=("Microsoft YaHei UI", 10, "bold"))
        self.messages.tag_configure("body", foreground="#e6edf3", spacing3=8)

        ttk.Label(
            outer,
            text="自然语言指令（Ctrl+Enter 发送）",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", pady=(12, 5))
        command_label = outer.pack_slaves()[-1]
        self.input_row = ttk.Frame(outer)
        self.input_row.pack(fill="x")
        self.input_row.columnconfigure(0, weight=1)
        self.input_row.rowconfigure(0, weight=1)
        self.input_box = tk.Text(
            self.input_row,
            height=4,
            wrap="word",
            font=("Microsoft YaHei UI", 11),
            padx=9,
            pady=7,
            relief="solid",
            borderwidth=1,
        )
        self.input_box.grid(row=0, column=0, sticky="nsew")
        self.input_box.bind("<Control-Return>", self._send_key)

        self.input_actions = ttk.Frame(self.input_row)
        self.input_actions.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        self.record_button = ttk.Button(
            self.input_actions,
            text="开始录音",
            command=self._toggle_recording,
            style="Action.TButton",
        )
        self.record_button.pack(side="left", fill="both", expand=True)
        self.listen_button = ttk.Button(
            self.input_actions,
            text="开始监听",
            command=self._toggle_listening,
            style="Action.TButton",
        )
        self.listen_button.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.send_button = ttk.Button(
            self.input_actions,
            text="发送指令",
            command=self._send,
            style="Action.TButton",
            state="disabled",
        )
        self.send_button.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.input_row.pack_configure(side="bottom", before=self.messages)
        command_label.pack_configure(side="bottom", before=self.messages)

    def _open_api_key_dialog(self) -> None:
        key_path = self.project_root / "config" / "openai.env"
        configured_key = os.getenv("OPENAI_API_KEY") or read_openai_api_key(key_path)

        dialog = tk.Toplevel(self.root)
        dialog.title("配置 OpenAI API Key")
        dialog.geometry("530x245")
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text="OpenAI API Key",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w")
        current_text = (
            f"当前配置：{mask_api_key(configured_key)}"
            if configured_key
            else "当前尚未配置 API Key"
        )
        ttk.Label(content, text=current_text).pack(anchor="w", pady=(5, 12))

        key_value = tk.StringVar()
        key_entry = ttk.Entry(content, textvariable=key_value, show="●", font=("Consolas", 10))
        key_entry.pack(fill="x")

        show_key = tk.BooleanVar(value=False)

        def toggle_visibility() -> None:
            key_entry.configure(show="" if show_key.get() else "●")

        ttk.Checkbutton(
            content,
            text="显示输入内容",
            variable=show_key,
            command=toggle_visibility,
        ).pack(anchor="w", pady=(7, 0))

        ttk.Label(
            content,
            text="密钥保存在 config\\openai.env；完整内容不会显示在消息或日志中。",
            foreground="#59636e",
        ).pack(anchor="w", pady=(7, 0))

        actions = ttk.Frame(content)
        actions.pack(fill="x", side="bottom", pady=(14, 0))
        ttk.Button(
            actions,
            text="保存",
            style="Action.TButton",
            command=lambda: self._save_api_key(dialog, key_value.get()),
        ).pack(side="right")

        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.bind("<Return>", lambda _event: self._save_api_key(dialog, key_value.get()))
        self._enable_responsive_scaling(dialog, 530, 245, minimum=0.75)
        key_entry.focus_set()

    def _save_api_key(self, dialog: tk.Toplevel, key: str) -> None:
        key_path = self.project_root / "config" / "openai.env"
        try:
            saved_key = save_openai_api_key(key_path, key)
        except (OSError, ValueError) as error:
            messagebox.showerror("保存失败", str(error), parent=dialog)
            return

        os.environ["OPENAI_API_KEY"] = saved_key
        masked = mask_api_key(saved_key)
        project_running = self.server_instance_id or (
            self.launch_process is not None and self.launch_process.poll() is None
        )
        effect = (
            "当前项目已经运行，请停止后重新启动以使用新配置。"
            if project_running
            else "下次点击“”时生效。"
        )
        dialog.destroy()
        self._append_message(
            "system",
            "OpenAI API Key 已配置成功。\n"
            f"配置：OPENAI_API_KEY={masked}\n"
            f"保存位置：{key_path}\n"
            f"{effect}\n"
            "LLM 提供方仍由 config\\llm.env 中的 LLM_PROVIDER 决定。",
        )

    def _post_ui(self, callback: object, *args: object) -> None:
        """Queue a main-thread UI callback without calling Tk from worker threads."""

        self._ui_events.put((callback, args))

    def _drain_ui_events(self) -> None:
        if self.closing:
            return
        while True:
            try:
                callback, args = self._ui_events.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)  # type: ignore[operator]
            except Exception as error:
                self.poll_running = False
                self._append_message("system", f"界面后台任务失败：{error}")
        self.root.after(30, self._drain_ui_events)

    def _start_project(self) -> None:
        if self.server_ready:
            self._append_message("system", "项目已经处于运行状态。")
            return
        script = self.project_root / "scripts" / "run.ps1"
        python = self.project_root / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            messagebox.showerror("无法启动", "缺少 .venv，请先运行 scripts\\bootstrap.ps1")
            return
        connection = self._choose_game_connection()
        if connection is None:
            return
        try:
            connection_arguments = connection.command_arguments()
        except ValueError as error:
            messagebox.showerror("联机配置不可用", str(error))
            return
        choice: GameMapChoice | None = None
        map_arguments: tuple[str, ...] = ()
        if connection.mode != "join":
            choice = self._choose_game_map(
                include_race=True,
                human_participants=2 if connection.mode == "host" else 1,
            )
            if choice is None:
                return
            try:
                map_arguments = choice.command_arguments()
            except ValueError as error:
                messagebox.showerror("地图不可用", str(error))
                return
        else:
            # Only RequestCreateGame selects a map; the joiner sends JoinGame.
            self.selected_computers = []
        computer_arguments: list[str] = []
        if self.selected_computers:
            for computer in self.selected_computers:
                computer_arguments.extend(("--computer", computer.cli_spec()))
        else:
            computer_arguments.append("--no-opponent")
        try:
            self.launch_process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-NoExit",
                    "-File",
                    str(script),
                    *map_arguments,
                    *connection_arguments,
                    "--race",
                    self.selected_race,
                    *computer_arguments,
                ],
                cwd=self.project_root,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except OSError as error:
            messagebox.showerror("启动失败", str(error))
            return
        self.launch_started_at = time.monotonic()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        if connection.mode == "host":
            self.status_text.set("主机已启动，正在等待另一位玩家…")
        elif connection.mode == "join":
            self.status_text.set("正在连接主机…")
        else:
            self.status_text.set("正在开启对局…")
        computer_summary = (
            "、".join(
                f"{RACE_LABELS[computer.race]} / "
                f"{COMPUTER_DIFFICULTY_LABELS[computer.difficulty]} / "
                f"{COMPUTER_AI_BUILD_LABELS[computer.ai_build]}"
                for computer in self.selected_computers
            )
            if self.selected_computers
            else "无"
        )
        if choice is None:
            map_summary = "地图：由主机决定"
        else:
            source = "本地地图" if choice.kind == "local" else "Battle.net 地图"
            map_summary = f"已选择{source}：{choice.value}"
        if connection.mode == "single":
            connection_summary = "模式：单机 / 对战电脑"
        else:
            connection_summary = (
                f"模式：{MULTIPLAYER_MODE_LABELS[connection.mode]}\n"
                f"主机 IPv4：{connection.host_ip}\n"
                f"SC2 官方联机端口：{connection.network_port}–{connection.network_port + 4}"
            )
        self._append_message(
            "system",
            f"{connection_summary}\n"
            f"{map_summary}\n"
            f"玩家种族：{RACE_LABELS[self.selected_race]}\n"
            f"电脑玩家（{len(self.selected_computers)}）：{computer_summary}\n"
            "已打开运行终端，正在等待 SC2 与本地 Agent 就绪。",
        )

    def _choose_game_connection(self) -> GameConnectionChoice | None:
        result: list[GameConnectionChoice] = []
        dialog = tk.Toplevel(self.root)
        dialog.title("选择对局模式")
        dialog.geometry("690x450")
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="选择对局模式", font=("Microsoft YaHei UI", 14, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            content,
            text=(
                "联机使用 Blizzard 官方 s2client-proto 主机/加入流程。SC2 负责网络传输、"
                "同步和校验；当前官方实现支持一名主机和一名加入者。"
            ),
            wraplength=650,
            foreground="#59636e",
        ).pack(anchor="w", pady=(6, 14))

        mode_label = tk.StringVar(
            value=MULTIPLAYER_MODE_LABELS.get(
                self.selected_multiplayer_mode,
                MULTIPLAYER_MODE_LABELS["single"],
            )
        )
        host_ip = tk.StringVar(value=self.selected_game_host_ip)
        network_port = tk.StringVar(value=str(self.selected_network_port))
        race_label = tk.StringVar(value=RACE_LABELS.get(self.selected_race, RACE_LABELS["terran"]))

        mode_row = ttk.Frame(content)
        mode_row.pack(fill="x", pady=(0, 12))
        ttk.Label(mode_row, text="模式：", width=17).pack(side="left")
        mode_combo = ttk.Combobox(
            mode_row,
            textvariable=mode_label,
            values=tuple(label for label, _value in MULTIPLAYER_MODE_OPTIONS),
            state="readonly",
        )
        mode_combo.pack(side="left", fill="x", expand=True)

        host_row = ttk.Frame(content)
        host_row.pack(fill="x", pady=(0, 10))
        ttk.Label(host_row, text="主机可达 IPv4：", width=17).pack(side="left")
        host_entry = ttk.Entry(host_row, textvariable=host_ip)
        host_entry.pack(side="left", fill="x", expand=True)

        port_row = ttk.Frame(content)
        port_row.pack(fill="x", pady=(0, 10))
        ttk.Label(port_row, text="联机起始端口：", width=17).pack(side="left")
        port_entry = ttk.Entry(port_row, textvariable=network_port)
        port_entry.pack(side="left", fill="x", expand=True)

        race_row = ttk.Frame(content)
        race_row.pack(fill="x", pady=(0, 10))
        ttk.Label(race_row, text="加入方种族：", width=17).pack(side="left")
        race_combo = ttk.Combobox(
            race_row,
            textvariable=race_label,
            values=tuple(label for label, _value in RACE_OPTIONS),
            state="readonly",
        )
        race_combo.pack(side="left", fill="x", expand=True)

        detail = tk.StringVar()
        ttk.Label(
            content,
            textvariable=detail,
            wraplength=650,
            foreground="#8a5a00",
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        def selected_mode() -> str:
            return MULTIPLAYER_MODE_VALUES.get(mode_label.get(), "single")

        def update_mode(*_args: object) -> None:
            mode = selected_mode()
            multiplayer_state = "normal" if mode != "single" else "disabled"
            host_entry.configure(state=multiplayer_state)
            port_entry.configure(state=multiplayer_state)
            race_combo.configure(state="readonly" if mode == "join" else "disabled")
            if mode == "single":
                detail.set("单机流程保持不变；下一步选择地图、种族和电脑玩家。")
            elif mode == "host":
                detail.set(
                    "填写另一位玩家实际可访问的本机 IPv4。双方必须填写相同地址和起始端口；"
                    "主机需允许连续 5 个端口通过防火墙/NAT。下一步由主机选择地图和种族。"
                )
            else:
                detail.set(
                    "填写主机提供的 IPv4 与起始端口。加入方不选择地图；启动后会等待官方 "
                    "JoinGame 与主机同步完成。"
                )

        mode_combo.bind("<<ComboboxSelected>>", update_mode)
        update_mode()

        def accept() -> None:
            mode = selected_mode()
            try:
                port = int(network_port.get())
            except ValueError:
                port = 5001 if mode == "single" else 0
            if mode == "single" and not 1 <= port <= 65531:
                port = 5001
            choice = GameConnectionChoice(mode, host_ip.get(), port)
            try:
                choice.command_arguments()
            except ValueError as error:
                messagebox.showerror("联机配置不可用", str(error), parent=dialog)
                return
            self.selected_multiplayer_mode = mode
            self.selected_game_host_ip = host_ip.get().strip()
            self.selected_network_port = port
            if mode == "join":
                self.selected_race = next(
                    (value for label, value in RACE_OPTIONS if label == race_label.get()),
                    "terran",
                )
            result.append(choice)
            dialog.destroy()

        actions = ttk.Frame(content)
        actions.pack(fill="x", side="bottom", pady=(18, 0))
        ttk.Button(actions, text="下一步", style="Action.TButton", command=accept).pack(
            side="right"
        )
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.bind("<Return>", lambda _event: accept())
        self._enable_responsive_scaling(dialog, 690, 450, minimum=0.72)
        dialog.wait_window()
        return result[0] if result else None

    def _choose_game_map(
        self,
        *,
        action_text: str = "用此地图启动",
        title: str = "选择地图",
        include_race: bool = False,
        human_participants: int = 1,
    ) -> GameMapChoice | None:
        """Select a map for either startup or offline point editing."""

        result: list[GameMapChoice] = []
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("1120x780" if include_race else "650x365")
        dialog.minsize(900, 680) if include_race else None
        dialog.resizable(True, True)
        dialog.transient(self.root)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="选择本局地图", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        ttk.Label(
            content,
            text=(
                "Commander 不再自动进入示例地图。请选择本地 .SC2Map，或输入已发布/缓存的 "
                "Battle.net 地图名称。"
            ),
            wraplength=1040 if include_race else 600,
            foreground="#59636e",
        ).pack(anchor="w", pady=(6, 14))

        selected = self.selected_map_choice
        kind = tk.StringVar(value=selected.kind if selected is not None else "local")
        local_path = tk.StringVar(
            value=selected.value if selected is not None and selected.kind == "local" else ""
        )
        battlenet_name = tk.StringVar(
            value=selected.value if selected is not None and selected.kind == "battlenet" else ""
        )
        race_label = tk.StringVar(value=RACE_LABELS.get(self.selected_race, RACE_LABELS["terran"]))
        capacity_status = tk.StringVar(
            value=(
                "选择地图后将通过 Blizzard 官方 API 读取本地图可用玩家容量。"
                if include_race
                else ""
            )
        )
        capacity_choice: list[GameMapChoice | None] = [None]
        probe_running = [False]
        computer_rows: list[tuple[tk.StringVar, tk.StringVar, tk.StringVar]] = []
        action_button: list[ttk.Button | None] = [None]

        local_row = ttk.Frame(content)
        local_row.pack(fill="x", pady=(0, 12))
        ttk.Radiobutton(local_row, text="本地 .SC2Map", value="local", variable=kind).pack(anchor="w")
        local_input = ttk.Frame(local_row)
        local_input.pack(fill="x", padx=(24, 0), pady=(5, 0))
        ttk.Entry(local_input, textvariable=local_path).pack(side="left", fill="x", expand=True)

        def browse() -> None:
            initial = self.project_root / "vendor" / "s2client-api" / "maps"
            selected = filedialog.askopenfilename(
                parent=dialog,
                title="选择 StarCraft II 地图",
                initialdir=str(initial if initial.is_dir() else self.project_root),
                filetypes=(("StarCraft II Map", "*.SC2Map"), ("所有文件", "*.*")),
            )
            if selected:
                local_path.set(selected)
                kind.set("local")
                if include_race:
                    refresh_capacity()

        ttk.Button(local_input, text="浏览…", command=browse).pack(side="left", padx=(8, 0))

        battle_row = ttk.Frame(content)
        battle_row.pack(fill="x")
        ttk.Radiobutton(
            battle_row,
            text="Battle.net 已发布/缓存地图",
            value="battlenet",
            variable=kind,
        ).pack(anchor="w")
        battle_input = ttk.Frame(battle_row)
        battle_input.pack(fill="x", padx=(24, 0), pady=(5, 0))
        battle_entry = ttk.Entry(battle_input, textvariable=battlenet_name)
        battle_entry.pack(side="left", fill="x", expand=True)
        if include_race:
            ttk.Button(
                battle_input,
                text="读取地图信息",
                command=lambda: refresh_capacity(force=True),
            ).pack(side="left", padx=(8, 0))
        ttk.Label(
            battle_row,
            text="必须使用完整地图名；能否创建取决于本机 Battle.net 缓存与当前 SC2 版本。",
            foreground="#8a5a00",
        ).pack(anchor="w", padx=(24, 0), pady=(4, 0))

        if include_race:
            race_row = ttk.Frame(content)
            race_row.pack(fill="x", pady=(12, 0))
            ttk.Label(race_row, text="玩家开局种族：", font=("Microsoft YaHei UI", 10, "bold")).pack(
                side="left"
            )
            ttk.Combobox(
                race_row,
                textvariable=race_label,
                values=tuple(label for label, _value in RACE_OPTIONS),
                state="readonly",
                width=22,
            ).pack(side="left", padx=(8, 0))

            capacity_row = ttk.Frame(content)
            capacity_row.pack(fill="x", pady=(10, 0))
            ttk.Label(
                capacity_row,
                textvariable=capacity_status,
                foreground="#59636e",
            ).pack(side="left", fill="x", expand=True)
            ttk.Button(
                capacity_row,
                text="刷新地图信息",
                command=lambda: refresh_capacity(force=True),
            ).pack(side="right")

            computer_box = ttk.LabelFrame(content, text=" 电脑玩家配置 ", padding=(8, 6))
            computer_box.pack(fill="both", expand=True, pady=(10, 0))
            computer_canvas = tk.Canvas(
                computer_box,
                highlightthickness=0,
                background="#f0f0f0",
                height=260,
            )
            computer_scroll = ttk.Scrollbar(
                computer_box,
                orient="vertical",
                command=computer_canvas.yview,
            )
            computer_canvas.configure(yscrollcommand=computer_scroll.set)
            computer_scroll.pack(side="right", fill="y")
            computer_canvas.pack(side="left", fill="both", expand=True)
            computer_grid = ttk.Frame(computer_canvas)
            computer_window = computer_canvas.create_window(
                (0, 0), window=computer_grid, anchor="nw"
            )

            def resize_computer_grid(_event: tk.Event[tk.Misc]) -> None:
                computer_canvas.itemconfigure(computer_window, width=computer_canvas.winfo_width())

            computer_canvas.bind("<Configure>", resize_computer_grid)
            computer_grid.bind(
                "<Configure>",
                lambda _event: computer_canvas.configure(
                    scrollregion=computer_canvas.bbox("all")
                ),
            )
            ttk.Label(
                content,
                text=(
                    "* 官方 RequestCreateGame 的 PlayerSetup 没有队伍和颜色字段；这两项由地图/SC2 "
                    "自动分配，界面不会保存无效设置。"
                ),
                foreground="#8a5a00",
                wraplength=1040,
            ).pack(anchor="w", pady=(5, 0))

            def render_computer_rows(max_players: int) -> None:
                for child in computer_grid.winfo_children():
                    child.destroy()
                computer_rows.clear()
                headers = ("槽位", "种族", "难度", "AI 风格", "队伍*", "颜色*")
                for column, header in enumerate(headers):
                    ttk.Label(
                        computer_grid,
                        text=header,
                        font=("Microsoft YaHei UI", 9, "bold"),
                    ).grid(row=0, column=column, padx=4, pady=(1, 5), sticky="w")
                for column in range(1, 6):
                    computer_grid.columnconfigure(column, weight=1)

                for index in range(max(0, max_players - human_participants)):
                    previous = (
                        self.selected_computers[index]
                        if index < len(self.selected_computers)
                        else None
                    )
                    race_var = tk.StringVar(
                        value=COMPUTER_RACE_LABELS.get(
                            previous.race if previous is not None else "",
                            COMPUTER_RACE_OPTIONS[0][0],
                        )
                    )
                    difficulty_var = tk.StringVar(
                        value=COMPUTER_DIFFICULTY_LABELS.get(
                            previous.difficulty if previous is not None else "easy",
                            COMPUTER_DIFFICULTY_LABELS["easy"],
                        )
                    )
                    ai_build_var = tk.StringVar(
                        value=COMPUTER_AI_BUILD_LABELS.get(
                            previous.ai_build if previous is not None else "random",
                            COMPUTER_AI_BUILD_LABELS["random"],
                        )
                    )
                    row = index + 1
                    ttk.Label(computer_grid, text=f"电脑 {index + 1}").grid(
                        row=row, column=0, padx=4, pady=3, sticky="w"
                    )
                    ttk.Combobox(
                        computer_grid,
                        textvariable=race_var,
                        values=tuple(label for label, _value in COMPUTER_RACE_OPTIONS),
                        state="readonly",
                        width=18,
                    ).grid(row=row, column=1, padx=4, pady=3, sticky="ew")
                    ttk.Combobox(
                        computer_grid,
                        textvariable=difficulty_var,
                        values=tuple(label for label, _value in COMPUTER_DIFFICULTY_OPTIONS),
                        state="readonly",
                        width=14,
                    ).grid(row=row, column=2, padx=4, pady=3, sticky="ew")
                    ttk.Combobox(
                        computer_grid,
                        textvariable=ai_build_var,
                        values=tuple(label for label, _value in COMPUTER_AI_BUILD_OPTIONS),
                        state="readonly",
                        width=18,
                    ).grid(row=row, column=3, padx=4, pady=3, sticky="ew")
                    ttk.Combobox(
                        computer_grid,
                        values=("地图 / SC2 自动",),
                        state="readonly",
                        width=18,
                    ).grid(row=row, column=4, padx=4, pady=3, sticky="ew")
                    team_widget = computer_grid.grid_slaves(row=row, column=4)[0]
                    team_widget.set("地图 / SC2 自动")
                    ttk.Combobox(
                        computer_grid,
                        values=("SC2 自动（不可指定）",),
                        state="readonly",
                        width=20,
                    ).grid(row=row, column=5, padx=4, pady=3, sticky="ew")
                    color_widget = computer_grid.grid_slaves(row=row, column=5)[0]
                    color_widget.set("SC2 自动（不可指定）")
                    computer_rows.append((race_var, difficulty_var, ai_build_var))
                self._refresh_responsive_scaling(dialog)

            def current_choice() -> GameMapChoice:
                choice = GameMapChoice(
                    kind.get(),
                    local_path.get() if kind.get() == "local" else battlenet_name.get(),
                )
                choice.command_arguments()
                return choice

            def capacity_finished(
                choice: GameMapChoice,
                max_players: int | None,
                error: Exception | None,
            ) -> None:
                probe_running[0] = False
                if not dialog.winfo_exists():
                    return
                try:
                    active_choice = current_choice()
                except ValueError:
                    return
                if active_choice != choice:
                    return
                if error is not None or max_players is None:
                    capacity_choice[0] = None
                    capacity_status.set(f"地图信息读取失败：{error}")
                    if action_button[0] is not None:
                        action_button[0].configure(state="disabled")
                    render_computer_rows(1)
                    messagebox.showerror(
                        "无法读取地图信息",
                        f"无法通过官方 SC2 API 读取此地图的玩家容量：\n{error}",
                        parent=dialog,
                    )
                    return
                if max_players < human_participants:
                    capacity_choice[0] = None
                    capacity_status.set(
                        f"此地图只有 {max_players} 个玩家槽位，无法容纳 "
                        f"{human_participants} 名真人玩家。"
                    )
                    if action_button[0] is not None:
                        action_button[0].configure(state="disabled")
                    render_computer_rows(max_players)
                    return
                capacity_choice[0] = choice
                capacity_status.set(
                    f"地图最多允许 {max_players} 名玩家；已预留 {human_participants} 个真人槽位，"
                    f"可配置 {max(0, max_players - human_participants)} 名电脑玩家。"
                )
                render_computer_rows(max_players)
                if action_button[0] is not None:
                    action_button[0].configure(state="normal")

            def refresh_capacity(force: bool = False) -> None:
                if probe_running[0]:
                    return
                try:
                    choice = current_choice()
                except ValueError as error:
                    messagebox.showerror("地图不可用", str(error), parent=dialog)
                    return
                capacity_choice[0] = None
                if action_button[0] is not None:
                    action_button[0].configure(state="disabled")
                if not force:
                    try:
                        cached = self.map_capacity_cache.get(choice.kind, choice.value)
                    except (OSError, ValueError):
                        cached = None
                    if cached is not None:
                        capacity_finished(choice, cached, None)
                        return
                probe_running[0] = True
                capacity_status.set(
                    "正在通过官方 SC2 API 读取地图容量；首次读取会临时启动并关闭 SC2…"
                )

                def work() -> None:
                    try:
                        max_players = probe_map_capacity(choice.kind, choice.value)
                        self.map_capacity_cache.put(choice.kind, choice.value, max_players)
                    except Exception as error:
                        self._post_ui(capacity_finished, choice, None, error)
                    else:
                        self._post_ui(capacity_finished, choice, max_players, None)

                threading.Thread(target=work, name="map-capacity", daemon=True).start()

            battle_entry.bind("<Return>", lambda _event: refresh_capacity(force=True))

        def accept() -> None:
            choice = GameMapChoice(
                kind.get(),
                local_path.get() if kind.get() == "local" else battlenet_name.get(),
            )
            try:
                choice.command_arguments()
            except ValueError as error:
                messagebox.showerror("地图不可用", str(error), parent=dialog)
                return
            if include_race:
                if capacity_choice[0] != choice:
                    messagebox.showerror(
                        "尚未读取地图信息",
                        "请先点击“刷新地图信息”，等待玩家容量读取完成。",
                        parent=dialog,
                    )
                    return
                selected_race = next(
                    (value for label, value in RACE_OPTIONS if label == race_label.get()),
                    "terran",
                )
                self.selected_race = selected_race
                selected_computers: list[ComputerPlayerChoice] = []
                for race_var, difficulty_var, ai_build_var in computer_rows:
                    computer_race = COMPUTER_RACE_VALUES.get(race_var.get(), "")
                    if not computer_race:
                        continue
                    selected_computers.append(
                        ComputerPlayerChoice(
                            race=computer_race,
                            difficulty=COMPUTER_DIFFICULTY_VALUES.get(
                                difficulty_var.get(), "easy"
                            ),
                            ai_build=COMPUTER_AI_BUILD_VALUES.get(
                                ai_build_var.get(), "random"
                            ),
                        )
                    )
                self.selected_computers = selected_computers
            self.selected_map_choice = choice
            result.append(choice)
            dialog.destroy()

        actions = ttk.Frame(content)
        actions.pack(fill="x", side="bottom", pady=(18, 0))
        action_button[0] = ttk.Button(
            actions,
            text=action_text,
            style="Action.TButton",
            command=accept,
            state="disabled" if include_race else "normal",
        )
        action_button[0].pack(side="right")
        actions.pack_configure(before=local_row)
        self._enable_responsive_scaling(
            dialog,
            1120 if include_race else 650,
            780 if include_race else 365,
            minimum=0.60 if include_race else 0.70,
        )
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.bind("<Return>", lambda _event: accept())
        if include_race and selected is not None:
            dialog.after(20, refresh_capacity)
        dialog.wait_window()
        return result[0] if result else None

    def _stop_project(self) -> None:
        if self.stop_running:
            return
        if not messagebox.askyesno(
            "强制停止",
            "确定关闭本项目启动的 StarCraft II、Commander 和运行终端吗？",
        ):
            return
        self.stop_running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.map_button.configure(state="normal")
        self.status_text.set("正在停止游戏与项目…")
        self.status_label.configure(foreground="#a33b20")
        self._append_message("system", "正在请求 Commander 关闭游戏与项目。")

        def work() -> None:
            try:
                self._request_json("/shutdown", {}, timeout=2.0)
            except Exception as error:
                killed = self._terminate_tracked_processes()
                self._post_ui(self._stop_request_finished, error, killed)
            else:
                self._post_ui(self._stop_request_finished, None, ())

        threading.Thread(target=work, name="gui-stop", daemon=True).start()

    def _stop_request_finished(
        self,
        error: Exception | None,
        killed: tuple[str, ...],
    ) -> None:
        if error is not None:
            if killed:
                self._append_message(
                    "system",
                    "控制接口不可用，已终止：" + "、".join(killed),
                )
                self._mark_stopped()
            else:
                self._append_message("system", f"停止失败：{error}")
                self.stop_running = False
                self.stop_button.configure(
                    state="normal" if self.server_instance_id or self.launch_process else "disabled"
                )
            return
        self._append_message("system", "停止请求已接受，等待 SC2 正常退出。")
        self.root.after(8000, self._force_stop_after_timeout)

    def _force_stop_after_timeout(self) -> None:
        if not self.stop_running:
            return

        def work() -> None:
            killed = self._terminate_tracked_processes()
            self._post_ui(self._force_stop_finished, killed)

        threading.Thread(target=work, name="gui-force-stop", daemon=True).start()

    def _force_stop_finished(self, killed: tuple[str, ...]) -> None:
        if killed:
            self._append_message("system", "停止超时，已强制终止：" + "、".join(killed))
        self._mark_stopped()

    def _terminate_tracked_processes(self) -> tuple[str, ...]:
        killed: list[str] = []
        launch = self.launch_process
        if launch is not None and launch.poll() is None:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                ["taskkill.exe", "/PID", str(launch.pid), "/T", "/F"],
                capture_output=True,
                creationflags=creation_flags,
                check=False,
            )
            if completed.returncode == 0:
                killed.append(f"运行终端 PID {launch.pid}（含子进程）")
        if _terminate_named_process(self.commander_pid, {"python.exe", "pythonw.exe"}):
            killed.append(f"Commander PID {self.commander_pid}")
        if _terminate_named_process(self.sc2_pid, {"sc2_x64.exe"}):
            killed.append(f"SC2 PID {self.sc2_pid}")
        return tuple(killed)

    def _mark_stopped(self) -> None:
        self.stop_running = False
        self.server_ready = False
        self.server_instance_id = ""
        self.commander_pid = 0
        self.sc2_pid = 0
        self.launch_started_at = 0.0
        self.launch_process = None
        self.status_text.set("已停止")
        self.status_label.configure(foreground="#a33b20")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.map_button.configure(state="normal")

    def _send_key(self, _event: tk.Event[tk.Misc]) -> str:
        self._send()
        return "break"

    def _send(self) -> None:
        if self.send_running:
            return
        text = self.input_box.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("没有指令", "请先输入或录制一条自然语言指令。")
            return
        if not self.server_ready:
            messagebox.showwarning("对局未就绪", "请先开启对局，並等待状态显示“已连接”。")
            return
        self.send_running = True
        self.send_button.configure(state="disabled")

        def work() -> None:
            try:
                response = self._request_json("/command", {"text": text}, timeout=3.0)
            except Exception as error:
                self._post_ui(self._send_finished, "", error)
            else:
                self._post_ui(self._send_finished, str(response.get("job_id", "")), None)

        threading.Thread(target=work, name="gui-send", daemon=True).start()

    def _send_finished(self, job_id: str, error: Exception | None) -> None:
        self.send_running = False
        self.send_button.configure(state="normal" if self.server_ready else "disabled")
        if error is not None:
            messagebox.showerror("发送失败", str(error))
            return
        self.input_box.delete("1.0", "end")
        self.input_box.focus_set()
        if job_id:
            self._append_message(
                "system",
                f"{job_id} 已进入顺序队列；新指令不会静默打断正在运行的指令。",
            )

    def _open_command_plan_editor(self) -> None:
        if self.command_plan_window is not None and self.command_plan_window.winfo_exists():
            self.command_plan_window.lift()
            return
        window = tk.Toplevel(self.root)
        window.title("战术指令集")
        window.geometry("760x620")
        window.minsize(650, 520)
        window.transient(self.root)
        self.command_plan_window = window

        outer = ttk.Frame(window, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text="语音只需说“执行计划1”；以下文本会绕过 LLM，按行快速执行。",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="普通行支持移动、攻击、生产、建造和升级。也支持等待秒数、资源、人口或生产完成。",
            foreground="#5e6b75",
        ).pack(anchor="w", pady=(4, 12))

        choose_row = ttk.Frame(outer)
        choose_row.pack(fill="x")
        ttk.Label(choose_row, text="计划：").pack(side="left")
        self.command_plan_choice = tk.StringVar()
        combo = ttk.Combobox(
            choose_row,
            textvariable=self.command_plan_choice,
            state="readonly",
            width=28,
        )
        combo.pack(side="left", fill="x", expand=True)
        combo.bind("<<ComboboxSelected>>", self._command_plan_selected)
        self.command_plan_combo = combo
        ttk.Button(choose_row, text="新增", command=self._new_command_plan).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(choose_row, text="删除", command=self._delete_command_plan).pack(
            side="left", padx=(8, 0)
        )

        details = ttk.Frame(outer)
        details.pack(fill="x", pady=(12, 8))
        details.columnconfigure(1, weight=1)
        ttk.Label(details, text="名称").grid(row=0, column=0, sticky="w")
        self.command_plan_name = tk.StringVar()
        ttk.Entry(details, textvariable=self.command_plan_name).grid(
            row=0, column=1, sticky="ew", padx=(10, 0)
        )
        ttk.Label(details, text="语音别名").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.command_plan_aliases = tk.StringVar()
        ttk.Entry(details, textvariable=self.command_plan_aliases).grid(
            row=1, column=1, sticky="ew", padx=(10, 0), pady=(8, 0)
        )
        ttk.Label(
            details,
            text="多个别名用逗号分隔，例如：一号计划, 开局经济",
            foreground="#6c7780",
        ).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(3, 0))

        ttk.Label(outer, text="计划文本（每行一步）", font=("Microsoft YaHei UI", 10, "bold")).pack(
            anchor="w", pady=(6, 5)
        )
        script = ScrolledText(
            outer,
            height=15,
            wrap="word",
            font=("Microsoft YaHei UI", 10),
            padx=8,
            pady=8,
        )
        script.pack(fill="both", expand=True)
        self.command_plan_script = script

        examples = (
            "等待指令：等待 3 秒 ｜ 等待矿物 400 ｜ 等待气体 100 ｜ "
            "等待空闲人口 5 ｜ 等待生产完成 ｜ # 注释"
        )
        ttk.Label(outer, text=examples, foreground="#5e6b75", wraplength=710).pack(
            anchor="w", pady=(8, 0)
        )
        self.command_plan_status = tk.StringVar(value="修改后点击保存")
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Label(footer, textvariable=self.command_plan_status).pack(side="left")
        ttk.Button(footer, text="填入执行口令", command=self._fill_command_plan_invocation).pack(
            side="right"
        )
        ttk.Button(
            footer,
            text="保存计划",
            command=self._save_command_plan,
        ).pack(side="right", padx=(0, 8))
        footer.pack_configure(side="bottom", before=script)

        def closed() -> None:
            window.destroy()
            self.command_plan_window = None
            self.command_plan_combo = None
            self.command_plan_choice = None
            self.command_plan_name = None
            self.command_plan_aliases = None
            self.command_plan_script = None
            self.command_plan_status = None
            self.command_plan_loaded_name = ""

        window.protocol("WM_DELETE_WINDOW", closed)
        self._enable_responsive_scaling(window, 760, 620, minimum=0.65)
        self._refresh_command_plan_choices()

    def _refresh_command_plan_choices(self, selected: str | None = None) -> None:
        combo = self.command_plan_combo
        choice = self.command_plan_choice
        if combo is None or choice is None:
            return
        names = tuple(plan.name for plan in self.command_plan_store.plans())
        combo.configure(values=names)
        wanted = selected if selected in names else (names[0] if names else "")
        choice.set(wanted)
        if wanted:
            self._load_command_plan(wanted)

    def _command_plan_selected(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self.command_plan_choice is not None:
            self._load_command_plan(self.command_plan_choice.get())

    def _load_command_plan(self, name: str) -> None:
        plan = self.command_plan_store.get(name)
        if (
            plan is None
            or self.command_plan_name is None
            or self.command_plan_aliases is None
            or self.command_plan_script is None
        ):
            return
        self.command_plan_loaded_name = plan.name
        self.command_plan_name.set(plan.name)
        self.command_plan_aliases.set(", ".join(plan.aliases))
        self.command_plan_script.delete("1.0", "end")
        self.command_plan_script.insert("1.0", "\n".join(plan.steps))
        if self.command_plan_status is not None:
            self.command_plan_status.set(f"已载入 {plan.name}（{len(plan.steps)} 行）")

    def _new_command_plan(self) -> None:
        if self.command_plan_window is None:
            return
        name = simpledialog.askstring(
            "新增指令计划",
            "计划名称（例如 计划2）：",
            parent=self.command_plan_window,
        )
        if not name:
            return
        if self.command_plan_name is None or self.command_plan_aliases is None or self.command_plan_script is None:
            return
        self.command_plan_loaded_name = ""
        self.command_plan_name.set(name.strip())
        self.command_plan_aliases.set("")
        self.command_plan_script.delete("1.0", "end")
        self.command_plan_script.insert(
            "1.0",
            "# 每行一条确定性指令\n选中的建筑生产19个农民\n等待生产完成",
        )
        if self.command_plan_status is not None:
            self.command_plan_status.set("新计划尚未保存")

    def _save_command_plan(self) -> None:
        if self.command_plan_name is None or self.command_plan_aliases is None or self.command_plan_script is None:
            return
        aliases = [
            value.strip()
            for value in self.command_plan_aliases.get().replace("，", ",").split(",")
            if value.strip()
        ]
        steps = self.command_plan_script.get("1.0", "end").splitlines()
        try:
            plan = self.command_plan_store.upsert(
                self.command_plan_name.get(),
                aliases,
                steps,
                replace_name=self.command_plan_loaded_name or None,
            )
        except (TypeError, ValueError) as error:
            messagebox.showerror("保存失败", str(error), parent=self.command_plan_window)
            return
        self.command_plan_loaded_name = plan.name
        self._refresh_command_plan_choices(plan.name)
        if self.command_plan_status is not None:
            self.command_plan_status.set(f"{plan.name} 已保存；现在可以说“执行{plan.name}”")

    def _delete_command_plan(self) -> None:
        name = self.command_plan_loaded_name
        if not name or self.command_plan_window is None:
            return
        if not messagebox.askyesno(
            "删除指令计划",
            f"确定删除“{name}”吗？",
            parent=self.command_plan_window,
        ):
            return
        self.command_plan_store.delete(name)
        self.command_plan_loaded_name = ""
        self._refresh_command_plan_choices()
        if self.command_plan_status is not None:
            self.command_plan_status.set(f"已删除 {name}")

    def _fill_command_plan_invocation(self) -> None:
        name = self.command_plan_name.get().strip() if self.command_plan_name is not None else ""
        if not name:
            return
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", f"执行{name}")
        self.input_box.focus_set()
        if self.command_plan_window is not None:
            self.command_plan_window.lift()

    def _open_map_editor(self) -> None:
        if self.map_window is not None and self.map_window.winfo_exists():
            self.map_window.lift()
            self._draw_map_editor()
            return
        choice = self._choose_game_map(action_text="打开点位配置", title="选择要配置的地图")
        if choice is None:
            return
        profile_key = choice.profile_key()
        display_name = choice.display_name()
        if not self.map_point_store.has_map(profile_key):
            legacy_profiles = tuple(
                name
                for name in self.map_point_store.map_names()
                if not name.startswith(("local:", "battlenet:"))
            )
            if len(legacy_profiles) == 1 and messagebox.askyesno(
                "导入旧版点位",
                (
                    f"检测到旧版地图点位“{legacy_profiles[0]}”。\n\n"
                    f"是否将它导入到地图“{display_name}”？原数据会保留。"
                ),
                parent=self.root,
            ):
                self.map_point_store.copy_map_if_missing(legacy_profiles[0], profile_key)
        had_presets = bool(self.map_point_store.preset_names(profile_key))
        active_preset = self.map_point_store.active_preset(profile_key)
        if not active_preset:
            active_preset = self.map_point_store.ensure_preset(profile_key, DEFAULT_PRESET_NAME)

        live_state = self.game_state
        if (
            self.server_ready
            and
            str(live_state.get("map_profile_key", "")) == profile_key
            and isinstance(live_state.get("bounds"), dict)
        ):
            editor_state = dict(live_state)
            preview_source = "live"
        else:
            cached = self.map_preview_store.get(profile_key)
            if cached is not None:
                editor_state = cached
                editor_state["units"] = []
                preview_source = "cached"
            else:
                editor_state = {
                    "map_name": display_name,
                    "bounds": {"min_x": 0.0, "min_y": 0.0, "max_x": 256.0, "max_y": 256.0},
                    "pathing_grid": None,
                    "units": [],
                }
                preview_source = "coordinate_grid"
        editor_state["map_profile_key"] = profile_key
        editor_state["preview_source"] = preview_source
        self.map_editor_profile_key = profile_key
        self.map_editor_display_name = display_name
        self.map_editor_choice = choice
        self.map_editor_state = editor_state
        self._load_map_background()
        self.map_preset_var = tk.StringVar(value=active_preset)
        self.map_preset_status = tk.StringVar(value="点位修改会自动保存")
        self.map_preview_status = tk.StringVar(value=self._map_preview_description())
        self._refresh_map_editor_points()

        window = tk.Toplevel(self.root)
        window.title("地图点位编辑器")
        window.geometry("940x650")
        window.minsize(760, 520)
        self.map_window = window

        outer = ttk.Frame(window, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text=f"地图：{display_name}",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        preview_row = ttk.Frame(outer)
        preview_row.pack(fill="x", pady=(3, 7))
        ttk.Label(
            preview_row,
            textvariable=self.map_preview_status,
            foreground="#8a5a00" if preview_source == "coordinate_grid" else "#59636e",
            wraplength=880,
        ).pack(side="left", fill="x", expand=True)

        preset_row = ttk.Frame(outer)
        preset_row.pack(fill="x", pady=(0, 9))
        ttk.Label(preset_row, text="点位预设：", font=("Microsoft YaHei UI", 10, "bold")).pack(
            side="left"
        )
        preset_combo = ttk.Combobox(
            preset_row,
            textvariable=self.map_preset_var,
            state="readonly",
            width=24,
        )
        preset_combo.pack(side="left", padx=(3, 8))
        preset_combo.bind("<<ComboboxSelected>>", self._map_preset_changed)
        self.map_preset_combo = preset_combo
        ttk.Button(preset_row, text="新增预设", command=self._new_map_preset).pack(side="left")
        ttk.Button(preset_row, text="重命名", command=self._rename_map_preset).pack(
            side="left", padx=(7, 0)
        )
        ttk.Button(preset_row, text="保存配置", command=self._confirm_map_preset_saved).pack(
            side="left", padx=(7, 0)
        )
        self.map_image_button = ttk.Button(
            preset_row,
            text=(
                "改用外部图片…"
                if self.map_background_origin == "embedded"
                else "更换高清图…"
                if self.map_background_source is not None
                else "设置高清图…"
            ),
            command=self._choose_map_background,
        )
        self.map_image_button.pack(side="left", padx=(7, 0))
        ttk.Label(preset_row, textvariable=self.map_preset_status, foreground="#26734d").pack(
            side="right"
        )
        self._refresh_map_preset_controls()

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, background="#101820", highlightthickness=1, highlightbackground="#52616b")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind("<Button-1>", self._map_canvas_clicked)
        canvas.bind("<Configure>", lambda _event: self._draw_map_editor())
        self.map_canvas = canvas

        sidebar = ttk.Frame(body, width=190, padding=(12, 0, 0, 0))
        sidebar.pack(side="right", fill="y")
        ttk.Label(sidebar, text="已定义点位", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        point_list = tk.Listbox(sidebar, width=24, height=20, font=("Consolas", 10))
        point_list.pack(fill="both", expand=True, pady=(6, 8))
        self.map_point_list = point_list
        ttk.Button(sidebar, text="删除选中点位", command=self._delete_selected_map_point).pack(fill="x")

        def closed() -> None:
            self.map_window = None
            self.map_canvas = None
            self.map_point_list = None
            self.map_preset_combo = None
            self.map_preset_var = None
            self.map_preset_status = None
            self.map_preview_status = None
            self.map_image_button = None
            self.map_editor_profile_key = ""
            self.map_editor_display_name = ""
            self.map_editor_choice = None
            self.map_editor_state = {}
            self._clear_map_background()
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", closed)
        self._enable_responsive_scaling(window, 940, 650, minimum=0.65)
        window.after(50, self._draw_map_editor)
        if not had_presets:
            self._append_message(
                "system",
                f"已为地图“{display_name}”创建空点位预设“{active_preset}”，可以直接开始配置。",
            )

    def _clear_map_background(self) -> None:
        source = self.map_background_source
        self.map_background_source = None
        self.map_background_photo = None
        self.map_background_render_key = None
        self.map_background_path = None
        self.map_background_origin = ""
        self.map_background_label = ""
        if source is not None:
            source.close()

    def _load_map_background(self) -> None:
        self._clear_map_background()
        path = self.map_image_store.get(self.map_editor_profile_key)
        if path is not None:
            try:
                with Image.open(path) as opened:
                    opened.load()
                    source = ImageOps.exif_transpose(opened).convert("RGB")
            except (OSError, ValueError):
                source = None
            if source is not None:
                self.map_background_source = source
                self.map_background_path = path
                self.map_background_origin = "custom"
                self.map_background_label = path.name
                return

        choice = self.map_editor_choice
        if choice is None or choice.kind != "local":
            return
        bounds = self.map_editor_state.get("bounds")
        preferred_aspect_ratio: float | None = None
        if isinstance(bounds, dict):
            try:
                world_width = float(bounds["max_x"]) - float(bounds["min_x"])
                world_height = float(bounds["max_y"]) - float(bounds["min_y"])
            except (KeyError, TypeError, ValueError):
                pass
            else:
                if world_width > 0 and world_height > 0:
                    preferred_aspect_ratio = world_width / world_height
        try:
            embedded = extract_embedded_map_image(
                Path(choice.value),
                preferred_aspect_ratio=preferred_aspect_ratio,
            )
            if embedded is None:
                return
            with Image.open(BytesIO(embedded.data)) as opened:
                opened.load()
                source = ImageOps.exif_transpose(opened).convert("RGB")
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        self.map_background_source = source
        self.map_background_origin = "embedded"
        self.map_background_label = embedded.name

    def _map_preview_description(self) -> str:
        if self.map_background_source is not None and self.map_background_origin == "custom":
            image_width, image_height = self.map_background_source.size
            return (
                f"正在使用自定义高清图 {image_width}×{image_height}；"
                "坐标边界仍以官方 SC2 API 数据为准。"
            )
        if self.map_background_source is not None and self.map_background_origin == "embedded":
            image_width, image_height = self.map_background_source.size
            return (
                f"正在使用 SC2Map 内嵌图片 {self.map_background_label} "
                f"（{image_width}×{image_height}）；坐标边界仍以官方 SC2 API 数据为准。"
            )
        source = str(self.map_editor_state.get("preview_source", "coordinate_grid"))
        return {
            "live": "正在使用官方 SC2 API 的实时地图边界与 pathing grid。",
            "cached": "正在使用此前由官方 SC2 API 缓存的地图边界与 pathing grid；无需开启对局。",
            "coordinate_grid": (
                "此地图尚无官方 API 预览，暂用 0–256 世界坐标网格；首次开启对局后会自动缓存官方预览。"
            ),
        }.get(source, "正在使用地图坐标网格。")

    def _choose_map_background(self) -> None:
        if not self.map_editor_profile_key:
            return
        initial_directory = self.project_root / "assets" / "map_images"
        if not initial_directory.is_dir():
            initial_directory = self.project_root
        selected = filedialog.askopenfilename(
            parent=self.map_window,
            title="选择该地图的高清俯视图",
            initialdir=str(initial_directory),
            filetypes=(
                ("地图图片", "*.png *.jpg *.jpeg *.webp"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
                ("WebP", "*.webp"),
            ),
        )
        if not selected:
            return
        source_path = Path(selected)
        try:
            with Image.open(source_path) as opened:
                opened.verify()
            with Image.open(source_path) as opened:
                image_width, image_height = opened.size
        except (OSError, ValueError) as error:
            messagebox.showerror("无法读取地图图片", str(error), parent=self.map_window)
            return
        if max(image_width, image_height) < 1024 and not messagebox.askyesno(
            "图片分辨率较低",
            (
                f"当前图片为 {image_width}×{image_height}，长边不足 1024 像素，放大后可能模糊。\n\n"
                "仍然关联这张图片吗？"
            ),
            parent=self.map_window,
        ):
            return
        bounds = self.map_editor_state.get("bounds")
        if isinstance(bounds, dict):
            try:
                world_ratio = (float(bounds["max_x"]) - float(bounds["min_x"])) / max(
                    float(bounds["max_y"]) - float(bounds["min_y"]),
                    1.0,
                )
            except (KeyError, TypeError, ValueError):
                world_ratio = 0.0
            image_ratio = image_width / max(image_height, 1)
            if (
                world_ratio > 0
                and abs(image_ratio / world_ratio - 1.0) > 0.08
                and not messagebox.askyesno(
                    "图片比例与地图不同",
                    (
                        "图片宽高比与官方可玩区域相差超过 8%，显示时会拉伸到坐标边界，"
                        "点位与图像地形可能无法准确重合。\n\n仍然关联吗？"
                    ),
                    parent=self.map_window,
                )
            ):
                return
        try:
            target = self.map_image_store.associate(
                self.map_editor_profile_key,
                source_path,
                width=image_width,
                height=image_height,
            )
        except (OSError, ValueError) as error:
            messagebox.showerror("保存地图图片失败", str(error), parent=self.map_window)
            return
        self._load_map_background()
        if self.map_preview_status is not None:
            self.map_preview_status.set(self._map_preview_description())
        if self.map_image_button is not None:
            self.map_image_button.configure(text="更换高清图…")
        self._draw_map_editor()
        self._append_message(
            "system",
            (
                f"已为地图“{self.map_editor_display_name}”关联高清图 "
                f"{image_width}×{image_height}，副本保存到 {target}。"
            ),
        )

    def _refresh_map_preset_controls(self) -> None:
        if not self.map_editor_profile_key or self.map_preset_var is None:
            return
        names = self.map_point_store.preset_names(self.map_editor_profile_key)
        if not names:
            names = (
                self.map_point_store.ensure_preset(
                    self.map_editor_profile_key,
                    DEFAULT_PRESET_NAME,
                ),
            )
        active = self.map_point_store.active_preset(self.map_editor_profile_key) or names[0]
        self.map_preset_var.set(active)
        if self.map_preset_combo is not None:
            self.map_preset_combo.configure(values=names)

    def _refresh_map_editor_points(self) -> None:
        if not self.map_editor_profile_key:
            return
        preset = self.map_preset_var.get() if self.map_preset_var is not None else None
        self.map_editor_state["points"] = [
            point.as_dict()
            for point in self.map_point_store.points(self.map_editor_profile_key, preset)
        ]

    def _map_preset_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if not self.map_editor_profile_key or self.map_preset_var is None:
            return
        try:
            active = self.map_point_store.set_active_preset(
                self.map_editor_profile_key,
                self.map_preset_var.get(),
            )
        except ValueError as error:
            messagebox.showerror("切换预设失败", str(error), parent=self.map_window)
            return
        self.map_preset_var.set(active)
        self._refresh_map_editor_points()
        if self.map_preset_status is not None:
            self.map_preset_status.set(f"正在使用：{active}")
        self._draw_map_editor()

    def _new_map_preset(self) -> None:
        if not self.map_editor_profile_key:
            return
        name = simpledialog.askstring("新增点位预设", "请输入新预设名称：", parent=self.map_window)
        if not name:
            return
        try:
            created = self.map_point_store.create_preset(self.map_editor_profile_key, name)
        except ValueError as error:
            messagebox.showerror("新增预设失败", str(error), parent=self.map_window)
            return
        self._refresh_map_preset_controls()
        if self.map_preset_var is not None:
            self.map_preset_var.set(created)
        self._refresh_map_editor_points()
        if self.map_preset_status is not None:
            self.map_preset_status.set(f"已新增并启用：{created}")
        self._draw_map_editor()

    def _rename_map_preset(self) -> None:
        if not self.map_editor_profile_key or self.map_preset_var is None:
            return
        current = self.map_preset_var.get()
        name = simpledialog.askstring(
            "重命名点位预设",
            "请输入新的预设名称：",
            initialvalue=current,
            parent=self.map_window,
        )
        if not name:
            return
        try:
            renamed = self.map_point_store.rename_preset(
                self.map_editor_profile_key,
                current,
                name,
            )
        except ValueError as error:
            messagebox.showerror("重命名失败", str(error), parent=self.map_window)
            return
        self._refresh_map_preset_controls()
        self.map_preset_var.set(renamed)
        if self.map_preset_status is not None:
            self.map_preset_status.set(f"已重命名为：{renamed}")
        self._append_message(
            "system",
            f"地图“{self.map_editor_display_name}”的点位预设已从“{current}”重命名为“{renamed}”。",
        )

    def _confirm_map_preset_saved(self) -> None:
        if not self.map_editor_profile_key or self.map_preset_var is None:
            return
        preset = self.map_preset_var.get()
        count = len(self.map_point_store.points(self.map_editor_profile_key, preset))
        if self.map_preset_status is not None:
            self.map_preset_status.set(f"已保存：{preset}（{count} 个点位）")
        self._append_message(
            "system",
            f"地图“{self.map_editor_display_name}”的点位预设“{preset}”已保存，共 {count} 个点位。",
        )

    def _draw_map_editor(self) -> None:
        canvas = self.map_canvas
        point_list = self.map_point_list
        if canvas is None or point_list is None or not canvas.winfo_exists():
            return
        bounds = self.map_editor_state.get("bounds")
        if not isinstance(bounds, dict):
            return
        try:
            min_x = float(bounds["min_x"])
            min_y = float(bounds["min_y"])
            max_x = float(bounds["max_x"])
            max_y = float(bounds["max_y"])
        except (KeyError, TypeError, ValueError):
            return
        width = max(canvas.winfo_width(), 100)
        height = max(canvas.winfo_height(), 100)
        left, top, right, bottom = _map_viewport(
            width,
            height,
            min_x,
            min_y,
            max_x,
            max_y,
        )

        def screen(x: float, y: float) -> tuple[float, float]:
            sx = left + (x - min_x) / max(max_x - min_x, 1.0) * (right - left)
            sy = bottom - (y - min_y) / max(max_y - min_y, 1.0) * (bottom - top)
            return sx, sy

        canvas.delete("all")
        has_background = False
        background_source = self.map_background_source
        if background_source is not None:
            rendered_width = max(1, round(right - left))
            rendered_height = max(1, round(bottom - top))
            render_key = (
                rendered_width,
                rendered_height,
                str(self.map_background_path or self.map_background_label),
            )
            if self.map_background_render_key != render_key or self.map_background_photo is None:
                resized = background_source.resize(
                    (rendered_width, rendered_height),
                    Image.Resampling.LANCZOS,
                )
                self.map_background_photo = ImageTk.PhotoImage(resized, master=canvas)
                self.map_background_render_key = render_key
            canvas.create_image(
                left,
                top,
                image=self.map_background_photo,
                anchor="nw",
            )
            has_background = True
        pathing = self.map_editor_state.get("pathing_grid")
        if not has_background and isinstance(pathing, dict):
            try:
                grid_width = int(pathing["width"])
                grid_height = int(pathing["height"])
                bits = int(pathing["bits_per_pixel"])
                grid_data = base64.b64decode(str(pathing["data"]), validate=True)
            except (KeyError, TypeError, ValueError):
                grid_width = grid_height = bits = 0
                grid_data = b""
            if grid_width > 0 and grid_height > 0 and bits in {1, 8}:
                cells_x = min(48, max(1, int(max_x - min_x)))
                cells_y = min(48, max(1, int(max_y - min_y)))
                for cell_y in range(cells_y):
                    y0 = min_y + (max_y - min_y) * cell_y / cells_y
                    y1 = min_y + (max_y - min_y) * (cell_y + 1) / cells_y
                    for cell_x in range(cells_x):
                        x0 = min_x + (max_x - min_x) * cell_x / cells_x
                        x1 = min_x + (max_x - min_x) * (cell_x + 1) / cells_x
                        px = min(grid_width - 1, max(0, int((x0 + x1) / 2)))
                        py = min(grid_height - 1, max(0, int((y0 + y1) / 2)))
                        linear = px + (grid_height - 1 - py) * grid_width
                        if bits == 8:
                            value = grid_data[linear] if linear < len(grid_data) else 0
                        else:
                            byte_index, bit_index = divmod(linear, 8)
                            value = (
                                (grid_data[byte_index] >> (7 - bit_index)) & 1
                                if byte_index < len(grid_data)
                                else 0
                            )
                        sx0, sy0 = screen(x0, y0)
                        sx1, sy1 = screen(x1, y1)
                        color = "#1d3540" if value else "#172027"
                        canvas.create_rectangle(sx0, sy1, sx1 + 1, sy0 + 1, fill=color, outline="")
        canvas.create_rectangle(left, top, right, bottom, outline="#d4dde5", width=2)

        x_ticks = _coordinate_ticks(min_x, max_x)
        for index, value in enumerate(x_ticks):
            sx, _ = screen(value, min_y)
            is_edge = index in {0, len(x_ticks) - 1}
            if not is_edge:
                canvas.create_line(
                    sx,
                    top,
                    sx,
                    bottom,
                    fill="#758795" if has_background else "#314653",
                    dash=(4, 4),
                )
            canvas.create_line(sx, top, sx, top - 5, fill="#d4dde5", width=2)
            canvas.create_line(sx, bottom, sx, bottom + 5, fill="#d4dde5", width=2)
            label = f"{value:g}"
            canvas.create_text(
                sx,
                bottom + 8,
                text=label,
                fill="#d4dde5",
                anchor="n",
                font=("Consolas", 9),
            )

        y_ticks = _coordinate_ticks(min_y, max_y)
        for index, value in enumerate(y_ticks):
            _, sy = screen(min_x, value)
            is_edge = index in {0, len(y_ticks) - 1}
            if not is_edge:
                canvas.create_line(
                    left,
                    sy,
                    right,
                    sy,
                    fill="#758795" if has_background else "#314653",
                    dash=(4, 4),
                )
            canvas.create_line(left - 5, sy, left, sy, fill="#d4dde5", width=2)
            canvas.create_line(right, sy, right + 5, sy, fill="#d4dde5", width=2)
            label = f"{value:g}"
            canvas.create_text(
                left - 8,
                sy,
                text=label,
                fill="#d4dde5",
                anchor="e",
                font=("Consolas", 9),
            )
        canvas.create_text(
            right + 18,
            bottom + 16,
            text="X",
            fill="#ffcf66",
            font=("Consolas", 10, "bold"),
        )
        canvas.create_text(
            left - 18,
            top - 16,
            text="Y",
            fill="#ffcf66",
            font=("Consolas", 10, "bold"),
        )

        units = self.map_editor_state.get("units", [])
        if isinstance(units, list):
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                try:
                    sx, sy = screen(float(unit["x"]), float(unit["y"]))
                except (KeyError, TypeError, ValueError):
                    continue
                color = "#ff5d5d" if unit.get("alliance") == "enemy" else "#48bfea"
                radius = 4 if unit.get("selected") else 3
                if unit.get("selected"):
                    color = "#ffe066"
                canvas.create_oval(sx - radius, sy - radius, sx + radius, sy + radius, fill=color, outline="")

        point_list.delete(0, "end")
        points = self.map_editor_state.get("points", [])
        if isinstance(points, list):
            for point in points:
                if not isinstance(point, dict):
                    continue
                try:
                    label = str(point["label"])
                    x, y = float(point["x"]), float(point["y"])
                except (KeyError, TypeError, ValueError):
                    continue
                sx, sy = screen(x, y)
                canvas.create_oval(sx - 7, sy - 7, sx + 7, sy + 7, fill="#ff9f43", outline="#ffffff")
                canvas.create_text(sx + 10, sy - 10, text=label, fill="#ffffff", anchor="sw", font=("Consolas", 10, "bold"))
                point_list.insert("end", f"{label:<6} {x:6.1f}, {y:6.1f}")
                point_list.itemconfig("end", foreground="#8a4b00")

    def _map_canvas_clicked(self, event: tk.Event[tk.Canvas]) -> None:
        canvas = self.map_canvas
        bounds = self.map_editor_state.get("bounds")
        if canvas is None or not isinstance(bounds, dict):
            return
        min_x, min_y = float(bounds["min_x"]), float(bounds["min_y"])
        max_x, max_y = float(bounds["max_x"]), float(bounds["max_y"])
        left, top, right, bottom = _map_viewport(
            canvas.winfo_width(),
            canvas.winfo_height(),
            min_x,
            min_y,
            max_x,
            max_y,
        )
        if not (left <= event.x <= right and top <= event.y <= bottom):
            return
        x = min_x + (event.x - left) / max(right - left, 1.0) * (max_x - min_x)
        y = min_y + (bottom - event.y) / max(bottom - top, 1.0) * (max_y - min_y)
        label = simpledialog.askstring(
            "设置地图点位",
            f"世界坐标 ({x:.1f}, {y:.1f})\n请输入点位名称（例如 A1）：",
            parent=self.map_window,
        )
        if not label:
            return
        preset = self.map_preset_var.get() if self.map_preset_var is not None else None
        try:
            point = self.map_point_store.upsert(
                self.map_editor_profile_key,
                label,
                x,
                y,
                bounds=(min_x, min_y, max_x, max_y),
                preset_name=preset,
            )
        except ValueError as error:
            messagebox.showerror("保存点位失败", str(error), parent=self.map_window)
            return
        self._refresh_map_editor_points()
        if self.map_preset_status is not None:
            self.map_preset_status.set(
                f"已自动保存 {point.label} = ({point.x:.1f}, {point.y:.1f})"
            )
        self._draw_map_editor()

    def _delete_selected_map_point(self) -> None:
        point_list = self.map_point_list
        if point_list is None or not point_list.curselection():
            return
        line = str(point_list.get(point_list.curselection()[0]))
        label = line.split()[0]
        preset = self.map_preset_var.get() if self.map_preset_var is not None else None
        if self.map_point_store.delete(self.map_editor_profile_key, label, preset):
            self._refresh_map_editor_points()
            if self.map_preset_status is not None:
                self.map_preset_status.set(f"已删除并自动保存：{label}")
            self._draw_map_editor()

    def _received_game_state(self, state: dict[str, object]) -> None:
        self.game_state = state
        profile_key = str(state.get("map_profile_key", ""))
        if profile_key and isinstance(state.get("bounds"), dict):
            self.map_preview_store.update(profile_key, state)
        if not self.map_editor_profile_key or profile_key != self.map_editor_profile_key:
            return
        self.map_editor_state = dict(state)
        self.map_editor_state["preview_source"] = "live"
        if self.map_preview_status is not None:
            self.map_preview_status.set(self._map_preview_description())
        self._refresh_map_preset_controls()
        self._refresh_map_editor_points()
        self._draw_map_editor()

    def _prepare_voice_transcriber(
        self,
        retry_action: str,
    ) -> LocalWhisperTranscriber | OpenAITranscriber | None:
        key_path = self.project_root / "config" / "openai.env"
        api_key = os.getenv("OPENAI_API_KEY") or read_openai_api_key(key_path)
        if self.voice_provider == "openai" and not api_key:
            messagebox.showwarning(
                "需要 OpenAI API Key",
                "LLM 语音转写需要 OpenAI API Key。请先完成配置。",
            )
            self._open_api_key_dialog()
            return None
        if self.voice_provider == "local" and not (
            self.project_root / ".voice-venv" / "Scripts" / "python.exe"
        ).is_file():
            messagebox.showwarning(
                "需要安装本地语音模型",
                f"请先运行 scripts\\setup-voice.ps1，然后重新点击{retry_action}。",
            )
            return None
        if self.voice_transcriber is None:
            self.voice_transcriber = (
                LocalWhisperTranscriber(self.project_root, self.whisper_model)
                if self.voice_provider == "local"
                else OpenAITranscriber(model=TRANSCRIPTION_MODEL, api_key=api_key)
            )
        self.voice_api_key = api_key
        return self.voice_transcriber

    def _toggle_recording(self) -> None:
        if self.recording_running:
            self._stop_recording()
            return
        if (
            self.recording_transcribing
            or self.listening_running
            or self.voice_transcription_thread is not None
        ):
            return
        self._start_recording()

    def _start_recording(self) -> None:
        if self.listening_running or self.voice_transcription_thread is not None:
            messagebox.showwarning("麦克风正在使用", "请先停止语音监听，再开始单次录音。")
            return
        transcriber = self._prepare_voice_transcriber("录音")
        if transcriber is None:
            return
        recorder = StreamingWavRecorder()
        try:
            recorder.start()
        except Exception as error:
            messagebox.showerror("无法开始录音", str(error))
            return
        self.voice_recorder = recorder
        self.recording_running = True
        self.recording_transcribing = False
        self.record_button.configure(
            state="normal",
            text="停止录音",
            style="Recording.TButton",
        )
        self.listen_button.configure(state="disabled")
        self.status_text.set("正在录音 · 点击“停止录音”完成")
        self._append_message(
            "system",
            "单次录音已开始。停止后会把转写结果填入输入框，您可以修改后再发送。",
        )
        self.root.after(200, self._update_manual_recording_status)

    def _update_manual_recording_status(self) -> None:
        recorder = self.voice_recorder
        if not self.recording_running or recorder is None:
            return
        self.status_text.set(
            f"正在录音 · {recorder.elapsed_seconds:.1f} 秒 · 点击“停止录音”完成"
        )
        self.root.after(200, self._update_manual_recording_status)

    def _stop_recording(self) -> None:
        recorder = self.voice_recorder
        if recorder is None:
            return
        self.recording_running = False
        self.recording_transcribing = True
        self.record_button.configure(
            state="disabled",
            text="正在转写…",
            style="Action.TButton",
        )
        self.listen_button.configure(state="disabled")
        self.status_text.set("录音已停止，正在转写…")
        try:
            path = recorder.stop()
        except Exception as error:
            self._recording_finished("", error)
            return
        transcriber = self.voice_transcriber

        def work() -> None:
            transcript = ""
            error: Exception | None = None
            try:
                if transcriber is None:
                    raise RuntimeError("语音转写器尚未初始化")
                transcript = transcriber.transcribe(path)
            except Exception as caught:
                error = caught
            finally:
                path.unlink(missing_ok=True)
            self._post_ui(self._recording_finished, transcript, error)

        threading.Thread(target=work, name="gui-single-recording-worker", daemon=True).start()

    def _recording_finished(self, transcript: str, error: Exception | None) -> None:
        self.recording_running = False
        self.recording_transcribing = False
        self.voice_recorder = None
        if self.closing:
            return
        self.record_button.configure(
            state="normal",
            text="开始录音",
            style="Action.TButton",
        )
        if not self.listening_running and self.voice_transcription_thread is None:
            self.listen_button.configure(state="normal")
        if error is not None:
            self.status_text.set("语音转写失败")
            self._append_message("system", f"单次录音转写失败：{error}")
            messagebox.showerror("语音转写失败", str(error))
            return
        self.input_box.delete("1.0", "end")
        self.input_box.insert("1.0", transcript)
        self.input_box.focus_set()
        self._append_message("system", f"单次录音已转写到输入框：{transcript}")
        auto_send = (
            self.command_plan_store.resolve_invocation(transcript) is not None
            or parse_plan_control(transcript) is not None
        )
        if auto_send and self.server_ready:
            self.status_text.set("识别到战术指令集口令，正在发送…")
            self._send()
        else:
            self.status_text.set("语音已转写，可编辑后发送")

    def _toggle_listening(self) -> None:
        if self.listening_running:
            self._stop_listening()
        elif (
            self.voice_transcription_thread is None
            and not self.recording_running
            and not self.recording_transcribing
        ):
            self._start_listening()

    def _start_listening(self) -> None:
        if not self.server_ready:
            messagebox.showwarning("对局未就绪", "请先开启对局并等待状态显示“已连接”，再开始语音监听。")
            return
        if self.recording_running or self.recording_transcribing:
            messagebox.showwarning("麦克风正在使用", "请先结束单次录音，再开始语音监听。")
            return
        transcriber = self._prepare_voice_transcriber("监听")
        if transcriber is None:
            return
        segment_queue: queue.Queue[Path | None] = queue.Queue()
        listener = VoiceCommandListener(
            self._queue_voice_segment,
            silence_seconds=self.voice_silence_seconds,
            minimum_speech_seconds=self.voice_min_speech_seconds,
            maximum_utterance_seconds=self.voice_max_utterance_seconds,
            minimum_rms=self.voice_vad_rms,
            calibration_seconds=self.voice_vad_calibration_seconds,
            noise_multiplier=self.voice_vad_noise_multiplier,
            release_multiplier=self.voice_vad_release_multiplier,
        )
        self.voice_segment_queue = segment_queue
        self.voice_transcription_thread = threading.Thread(
            target=self._voice_transcription_loop,
            args=(segment_queue,),
            name="gui-voice-command-worker",
            daemon=True,
        )
        self.voice_transcription_thread.start()
        try:
            listener.start()
        except Exception as error:
            segment_queue.put(None)
            self.voice_segment_queue = None
            self.voice_transcription_thread = None
            messagebox.showerror("无法开始监听", str(error))
            return
        self.voice_listener = listener
        self.listening_running = True
        self.listening_transcribing = False
        self.voice_sentence_count = 0
        self.listen_button.configure(
            state="normal",
            text="停止监听",
            style="Recording.TButton",
        )
        self.record_button.configure(state="disabled")
        self.status_text.set("正在监听 · 正在校准环境噪声，请暂时保持安静")
        self._append_message(
            "system",
            (
                f"语音指令监听已开始。前 {self.voice_vad_calibration_seconds:.1f} 秒用于"
                "校准环境噪声，请暂时保持安静；随后程序会在检测到约 "
                f"{self.voice_silence_seconds:.1f} 秒静音后自动切句、转写并发送；"
                "点击“停止监听”才会结束。"
            ),
        )
        self.root.after(200, self._update_listening_status)

    def _update_listening_status(self) -> None:
        listener = self.voice_listener
        if not self.listening_running or listener is None:
            return
        if self.listening_transcribing:
            activity = "正在转写并自动发送"
        elif getattr(listener, "is_calibrating", False):
            activity = "正在校准环境噪声，请保持安静"
        elif listener.is_speaking:
            activity = "检测到说话，等待句尾静音"
        else:
            activity = "等待语音指令"
        self.status_text.set(f"正在监听 · {activity} · 已识别 {self.voice_sentence_count} 句")
        self.root.after(200, self._update_listening_status)

    def _stop_listening(self) -> None:
        listener = self.voice_listener
        if listener is None:
            return
        self.listening_running = False
        self.voice_listener = None
        self.listen_button.configure(
            state="disabled",
            text="正在完成最后一句…",
            style="Action.TButton",
        )
        self.record_button.configure(state="disabled")
        self.status_text.set("监听已停止，正在完成剩余语音指令…")
        try:
            listener.stop(flush=True)
        except Exception as error:
            self._append_message("system", f"停止语音监听时发生错误：{error}")
        segment_queue = self.voice_segment_queue
        if segment_queue is not None:
            segment_queue.put(None)
        self._append_message("system", "语音监听已停止；已经切分的指令仍会完成转写和发送。")

    def _queue_voice_segment(self, path: Path) -> None:
        segment_queue = self.voice_segment_queue
        if segment_queue is None:
            path.unlink(missing_ok=True)
            return
        segment_queue.put(path)
        self._post_ui(self._voice_segment_detected)

    def _voice_segment_detected(self) -> None:
        if self.closing:
            return
        self.status_text.set("检测到完整语句，正在转写…")

    def _voice_transcription_loop(self, segment_queue: queue.Queue[Path | None]) -> None:
        transcriber = self.voice_transcriber
        if transcriber is None:
            self._post_ui(self._listening_finished)
            return
        while True:
            path = segment_queue.get()
            if path is None:
                break
            self._post_ui(self._voice_transcription_started)
            transcript = ""
            try:
                transcript = transcriber.transcribe(path)
                self._post_ui(self._voice_transcript_ready, transcript)
                if not self.server_ready:
                    raise RuntimeError("Commander 当前未连接，语音指令没有发送")
                response = self._request_json("/command", {"text": transcript}, timeout=3.0)
                self._post_ui(
                    self._voice_command_submitted,
                    transcript,
                    str(response.get("job_id", "")),
                    None,
                )
            except Exception as error:
                self._post_ui(self._voice_command_submitted, transcript, "", error)
            finally:
                path.unlink(missing_ok=True)
        self._post_ui(self._listening_finished)

    def _voice_transcription_started(self) -> None:
        if self.closing:
            return
        self.listening_transcribing = True
        self.status_text.set("语音切句完成，正在转写…")

    def _voice_transcript_ready(self, transcript: str) -> None:
        if self.closing:
            return
        self.status_text.set("语音已识别，正在自动发送…")
        self._append_message("system", f"语音切句已识别，正在自动发送：{transcript}")

    def _voice_command_submitted(
        self,
        transcript: str,
        job_id: str,
        error: Exception | None,
    ) -> None:
        if self.closing:
            return
        self.listening_transcribing = False
        if error is not None:
            self.status_text.set(
                "语音指令发送失败"
                if not self.listening_running
                else "正在监听 · 上一句发送失败"
            )
            prefix = f"“{transcript}”" if transcript else "该语音片段"
            self._append_message("system", f"{prefix}未能发送：{error}")
            return
        self.voice_sentence_count += 1
        if job_id:
            self._append_message(
                "system",
                f"语音指令 {job_id} 已进入顺序队列；后续继续监听。",
            )
        if self.listening_running:
            self.status_text.set(f"正在监听 · 等待下一条指令 · 已识别 {self.voice_sentence_count} 句")

    def _listening_finished(self) -> None:
        self.listening_transcribing = False
        self.voice_segment_queue = None
        self.voice_transcription_thread = None
        if self.closing:
            return
        self.listen_button.configure(
            state="normal",
            text="开始监听",
            style="Action.TButton",
        )
        if not self.recording_running and not self.recording_transcribing:
            self.record_button.configure(state="normal")
        if not self.listening_running:
            self.status_text.set("语音监听已停止")

    def _poll(self) -> None:
        if self.closing:
            return
        if self.poll_running:
            self.root.after(400, self._poll)
            return
        self.poll_running = True

        def work() -> None:
            try:
                health = self._request_json("/health", timeout=0.8)
                instance_id = str(health.get("instance_id", ""))
                after = 0 if instance_id and instance_id != self.server_instance_id else self.last_event_id
                payload = self._request_json(f"/events?after={after}", timeout=0.8)
                events = payload.get("events", [])
                state = self._request_json("/state", timeout=0.8)
                jobs_payload = self._request_json("/jobs", timeout=0.8)
                jobs = jobs_payload.get("jobs", [])
            except Exception as error:
                self._post_ui(self._poll_finished, None, [], None, [], error)
            else:
                self._post_ui(self._poll_finished, health, events, state, jobs, None)

        threading.Thread(target=work, name="gui-poll", daemon=True).start()

    def _poll_finished(
        self,
        health: dict[str, object] | None,
        events: list[dict[str, object]],
        state: dict[str, object] | None,
        jobs: list[dict[str, object]],
        error: Exception | None,
    ) -> None:
        self.poll_running = False
        if self.closing:
            return
        if error is not None or health is None:
            self.server_ready = False
            self.send_button.configure(state="disabled")
            self.map_button.configure(state="normal")
            if self.stop_running:
                def cleanup() -> None:
                    killed = self._terminate_tracked_processes()
                    self._post_ui(self._force_stop_finished, killed)

                threading.Thread(target=cleanup, name="gui-stop-cleanup", daemon=True).start()
                return
            if self.launch_started_at and time.monotonic() - self.launch_started_at < 60:
                self.status_text.set("项目正在启动…")
                self.stop_button.configure(state="normal")
            else:
                self.status_text.set("启动失败或项目已停止" if self.launch_started_at else "未连接")
                self.start_button.configure(state="normal")
                self.stop_button.configure(
                    state="normal"
                    if self.launch_process is not None and self.launch_process.poll() is None
                    else "disabled"
                )
            self.status_label.configure(foreground="#a33b20")
            self.root.after(900, self._poll)
            return

        instance_id = str(health.get("instance_id", ""))
        if instance_id and instance_id != self.server_instance_id:
            self.server_instance_id = instance_id
            self.last_event_id = 0
        self.server_ready = bool(health.get("ready"))
        if state is not None:
            self._received_game_state(state)
        self._received_jobs(jobs)
        self.commander_pid = int(health.get("commander_pid", 0) or 0)
        self.sc2_pid = int(health.get("sc2_pid", 0) or 0)
        provider = str(health.get("provider", ""))
        model = str(health.get("model", ""))
        self.stop_button.configure(state="disabled" if self.stop_running else "normal")
        if self.stop_running:
            self.status_text.set("正在停止游戏与项目…")
            self.status_label.configure(foreground="#a33b20")
            self.send_button.configure(state="disabled")
            self.root.after(500, self._poll)
            return
        if self.server_ready:
            self.launch_started_at = 0.0
            plan_state = self.game_state.get("command_plan", {})
            plan_suffix = ""
            if isinstance(plan_state, dict) and plan_state.get("active"):
                paused = " 已暂停" if plan_state.get("paused") else ""
                plan_suffix = (
                    f" · {plan_state.get('name', '计划')} "
                    f"{plan_state.get('step', 0)}/{plan_state.get('total', 0)}{paused}"
                )
            scheduled = self.game_state.get("scheduled_tasks", [])
            active_tasks = 0
            if isinstance(scheduled, list):
                active_tasks = sum(
                    1
                    for task in scheduled
                    if isinstance(task, dict) and not bool(task.get("terminal"))
                )
            task_suffix = f" · 持续任务 {active_tasks}" if active_tasks else ""
            self.status_text.set(f"已连接 · {provider} · {model}{plan_suffix}{task_suffix}")
            self.status_label.configure(foreground="#26734d")
            self.start_button.configure(state="disabled")
            if not self.send_running:
                self.send_button.configure(state="normal")
            self.map_button.configure(state="normal")
        else:
            if self.selected_multiplayer_mode == "host":
                self.status_text.set("主机已就绪，等待另一位玩家加入…")
            elif self.selected_multiplayer_mode == "join":
                self.status_text.set("已连接本地 Commander，等待与主机同步…")
            else:
                self.status_text.set("已连接，Agent 正在启动…")
            self.status_label.configure(foreground="#a66b00")
            self.send_button.configure(state="disabled")
            self.start_button.configure(state="disabled")
            self.map_button.configure(state="normal")
        for event in events:
            try:
                event_id = int(event["id"])
                role = str(event["role"])
                text = str(event["text"])
            except (KeyError, TypeError, ValueError):
                continue
            if event_id <= self.last_event_id:
                continue
            self.last_event_id = event_id
            self._append_message(role, text)
        self.root.after(500, self._poll)

    def _received_jobs(self, jobs: list[dict[str, object]]) -> None:
        self.jobs = jobs
        for item_id in self.job_tree.get_children():
            self.job_tree.delete(item_id)
        if not jobs:
            self.job_tree.insert(
                "",
                "end",
                iid="placeholder",
                values=("—", "空闲", "未选择", "尚无指令", ""),
            )
            return
        labels = {
            "queued": "排队中",
            "transcribing": "语音转写",
            "planning": "指令解析",
            "plan_ready": "计划就绪",
            "validating": "规则预检",
            "executing": "正在执行",
            "waiting": "持续运行",
            "completed": "已完成",
            "failed": "已终止",
        }
        for index, job in enumerate(jobs):
            job_id = str(job.get("id", ""))
            phase = str(job.get("phase", "queued"))
            message = str(job.get("message", ""))
            current = int(job.get("current", 0) or 0)
            total = int(job.get("total", 0) or 0)
            raw_selection = job.get("selection_tags", [])
            if isinstance(raw_selection, (list, tuple)):
                selection_tags = [str(tag) for tag in raw_selection]
            else:
                selection_tags = []
            if not selection_tags:
                selection_text = "未选择"
            elif len(selection_tags) <= 8:
                selection_text = ", ".join(selection_tags)
            else:
                selection_text = ", ".join(selection_tags[:8]) + f" …（共 {len(selection_tags)} 个）"
            if total > 0:
                message = f"{current}/{total} · {message}"
            self.job_tree.insert(
                "",
                "end",
                iid=f"job-{index}-{job_id}",
                values=(
                    job_id,
                    labels.get(phase, phase),
                    selection_text,
                    message,
                    str(job.get("text", "")),
                ),
                tags=(
                    "completed"
                    if phase == "completed"
                    else "failed"
                    if phase == "failed"
                    else "running",
                ),
            )

    def _request_json(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        timeout: float,
    ) -> dict[str, object]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(CONTROL_URL + path, data=data, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8")).get("error", str(error))
            except Exception:
                detail = str(error)
            raise RuntimeError(str(detail)) from error
        except URLError as error:
            raise ConnectionError("无法连接本地 Commander 控制接口") from error

    def _append_message(self, role: str, text: str) -> None:
        labels = {
            "player": ("玩家", "player_label"),
            "assistant": ("AI", "assistant_label"),
            "system": ("系统", "system_label"),
        }
        label, tag = labels.get(role, ("系统", "system_label"))
        timestamp = time.strftime("%H:%M:%S")
        self.messages.configure(state="normal")
        self.messages.insert("end", f"[{timestamp}] {label}\n", tag)
        self.messages.insert("end", text.strip() + "\n\n", "body")
        self.messages.configure(state="disabled")
        self.messages.see("end")

    def _close(self) -> None:
        self.closing = True
        self._clear_map_background()
        listener = self.voice_listener
        self.voice_listener = None
        self.listening_running = False
        if listener is not None:
            try:
                listener.cancel()
            except Exception:
                pass
        recorder = self.voice_recorder
        self.voice_recorder = None
        self.recording_running = False
        self.recording_transcribing = False
        if recorder is not None:
            try:
                recorder.cancel()
            except Exception:
                pass
        segment_queue = self.voice_segment_queue
        self.voice_segment_queue = None
        if segment_queue is not None:
            segment_queue.put(None)
        transcriber = self.voice_transcriber
        self.voice_transcriber = None
        close = getattr(transcriber, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass
        self.root.destroy()


def main() -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    root = tk.Tk()
    try:
        CommanderGUI(root)
    except Exception as error:
        messagebox.showerror("AI Commander GUI 启动失败", str(error))
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
