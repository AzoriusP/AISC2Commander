from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path


POINT_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,15}$")
DEFAULT_PRESET_NAME = "默认配置"
STORE_VERSION = 2
IMAGE_STORE_VERSION = 1
SUPPORTED_MAP_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True, slots=True)
class MapPoint:
    label: str
    x: float
    y: float

    def as_dict(self) -> dict[str, str | float]:
        return asdict(self)


def map_profile_key(kind: str, value: str) -> str:
    """Return the same stable point-profile key in the GUI and Commander."""

    if kind == "local":
        path = Path(value).expanduser().resolve()
        return "local:" + path.as_posix().casefold()
    if kind == "battlenet":
        name = value.strip()
        if not name:
            raise ValueError("Battle.net 地图名称不可为空")
        return "battlenet:" + name.casefold()
    raise ValueError(f"未知地图来源：{kind}")


def _preset_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("预设名称不可为空")
    if len(name) > 64 or any(ord(character) < 32 for character in name):
        raise ValueError("预设名称不能超过 64 个字符或包含控制字符")
    return name


def _valid_points(raw: object) -> dict[str, dict[str, float]]:
    if not isinstance(raw, dict):
        return {}
    valid: dict[str, dict[str, float]] = {}
    for label, value in raw.items():
        if not isinstance(label, str) or not isinstance(value, dict):
            continue
        try:
            x, y = float(value["x"]), float(value["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if POINT_LABEL.fullmatch(label) and math.isfinite(x) and math.isfinite(y):
            valid[label.upper()] = {"x": x, "y": y}
    return valid


class MapPointStore:
    """Thread- and process-aware map/preset point storage used by GUI and Agent."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._maps: dict[str, dict[str, object]] = {}
        self._mtime_ns = -1
        self._reload()

    def has_map(self, map_name: str) -> bool:
        with self._lock:
            self._refresh_if_changed()
            return map_name.strip() in self._maps

    def map_names(self) -> tuple[str, ...]:
        with self._lock:
            self._refresh_if_changed()
            return tuple(sorted(self._maps, key=str.casefold))

    def preset_names(self, map_name: str) -> tuple[str, ...]:
        with self._lock:
            self._refresh_if_changed()
            entry = self._maps.get(map_name.strip())
            presets = entry.get("presets") if isinstance(entry, dict) else None
            return tuple(sorted(presets, key=str.casefold)) if isinstance(presets, dict) else ()

    def active_preset(self, map_name: str) -> str:
        with self._lock:
            self._refresh_if_changed()
            entry = self._maps.get(map_name.strip())
            return str(entry.get("active_preset", "")) if isinstance(entry, dict) else ""

    def copy_map_if_missing(self, source_map: str, target_map: str) -> bool:
        """Copy a legacy map-name profile to its stable map-source key once."""

        source, target = source_map.strip(), target_map.strip()
        if not source or not target or source == target:
            return False
        with self._lock:
            self._refresh_if_changed()
            if target in self._maps or source not in self._maps:
                return False
            self._maps[target] = deepcopy(self._maps[source])
            self._write()
            return True

    def ensure_preset(self, map_name: str, preset_name: str = DEFAULT_PRESET_NAME) -> str:
        map_key = map_name.strip()
        name = _preset_name(preset_name)
        if not map_key:
            raise ValueError("地图名称不可为空")
        with self._lock:
            self._refresh_if_changed()
            entry = self._maps.setdefault(map_key, {"active_preset": name, "presets": {}})
            presets = entry.setdefault("presets", {})
            assert isinstance(presets, dict)
            existing = next((item for item in presets if item.casefold() == name.casefold()), None)
            changed = False
            if existing is None:
                presets[name] = {}
                existing = name
                changed = True
            if not entry.get("active_preset"):
                entry["active_preset"] = existing
                changed = True
            if changed:
                self._write()
            return existing

    def create_preset(self, map_name: str, preset_name: str) -> str:
        map_key = map_name.strip()
        name = _preset_name(preset_name)
        if not map_key:
            raise ValueError("地图名称不可为空")
        with self._lock:
            self._refresh_if_changed()
            entry = self._maps.setdefault(map_key, {"active_preset": "", "presets": {}})
            presets = entry.setdefault("presets", {})
            assert isinstance(presets, dict)
            if any(item.casefold() == name.casefold() for item in presets):
                raise ValueError(f"点位预设“{name}”已经存在")
            presets[name] = {}
            entry["active_preset"] = name
            self._write()
            return name

    def rename_preset(self, map_name: str, old_name: str, new_name: str) -> str:
        map_key = map_name.strip()
        old = old_name.strip()
        new = _preset_name(new_name)
        with self._lock:
            self._refresh_if_changed()
            entry = self._maps.get(map_key)
            presets = entry.get("presets") if isinstance(entry, dict) else None
            if not isinstance(presets, dict):
                raise ValueError("地图点位配置不存在")
            actual_old = next((item for item in presets if item.casefold() == old.casefold()), None)
            if actual_old is None:
                raise ValueError(f"点位预设“{old}”不存在")
            collision = next(
                (item for item in presets if item.casefold() == new.casefold() and item != actual_old),
                None,
            )
            if collision is not None:
                raise ValueError(f"点位预设“{new}”已经存在")
            points = presets.pop(actual_old)
            presets[new] = points
            if str(entry.get("active_preset", "")).casefold() == actual_old.casefold():
                entry["active_preset"] = new
            self._write()
            return new

    def set_active_preset(self, map_name: str, preset_name: str) -> str:
        map_key = map_name.strip()
        wanted = preset_name.strip()
        with self._lock:
            self._refresh_if_changed()
            entry = self._maps.get(map_key)
            presets = entry.get("presets") if isinstance(entry, dict) else None
            if not isinstance(presets, dict):
                raise ValueError("地图点位配置不存在")
            actual = next((item for item in presets if item.casefold() == wanted.casefold()), None)
            if actual is None:
                raise ValueError(f"点位预设“{wanted}”不存在")
            if entry.get("active_preset") != actual:
                entry["active_preset"] = actual
                self._write()
            return actual

    def points(self, map_name: str, preset_name: str | None = None) -> tuple[MapPoint, ...]:
        with self._lock:
            self._refresh_if_changed()
            raw = self._point_values(map_name, preset_name)
            return tuple(
                MapPoint(label, float(value["x"]), float(value["y"]))
                for label, value in sorted(raw.items())
            )

    def lookup(self, map_name: str, label: str, preset_name: str | None = None) -> MapPoint | None:
        wanted = label.strip().casefold()
        return next(
            (point for point in self.points(map_name, preset_name) if point.label.casefold() == wanted),
            None,
        )

    def upsert(
        self,
        map_name: str,
        label: str,
        x: float,
        y: float,
        *,
        bounds: tuple[float, float, float, float] | None = None,
        preset_name: str | None = None,
    ) -> MapPoint:
        map_key = map_name.strip()
        normalized = label.strip().upper()
        if not map_key:
            raise ValueError("地图名称不可为空")
        if not POINT_LABEL.fullmatch(normalized):
            raise ValueError("点位名必须以字母开头，可使用字母、数字、_ 或 -（例如 A1）")
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("点位坐标必须是有限数值")
        if bounds is not None:
            min_x, min_y, max_x, max_y = bounds
            if not (min_x <= x <= max_x and min_y <= y <= max_y):
                raise ValueError("点位超出地图可玩区域")
        point = MapPoint(normalized, round(float(x), 3), round(float(y), 3))
        with self._lock:
            self._refresh_if_changed()
            if map_key not in self._maps:
                self._maps[map_key] = {
                    "active_preset": DEFAULT_PRESET_NAME,
                    "presets": {DEFAULT_PRESET_NAME: {}},
                }
            entry = self._maps[map_key]
            presets = entry["presets"]
            assert isinstance(presets, dict)
            wanted = preset_name or str(entry.get("active_preset", "")) or DEFAULT_PRESET_NAME
            actual = next((item for item in presets if item.casefold() == wanted.casefold()), None)
            if actual is None:
                actual = _preset_name(wanted)
                presets[actual] = {}
            entry["active_preset"] = actual
            values = presets[actual]
            assert isinstance(values, dict)
            values[normalized] = {"x": point.x, "y": point.y}
            self._write()
        return point

    def delete(self, map_name: str, label: str, preset_name: str | None = None) -> bool:
        normalized = label.strip().upper()
        with self._lock:
            self._refresh_if_changed()
            values = self._point_values(map_name, preset_name)
            if normalized not in values:
                return False
            del values[normalized]
            self._write()
            return True

    def _point_values(
        self,
        map_name: str,
        preset_name: str | None,
    ) -> dict[str, dict[str, float]]:
        entry = self._maps.get(map_name.strip())
        if not isinstance(entry, dict):
            return {}
        presets = entry.get("presets")
        if not isinstance(presets, dict):
            return {}
        wanted = preset_name or str(entry.get("active_preset", ""))
        actual = next((item for item in presets if item.casefold() == wanted.casefold()), None)
        values = presets.get(actual, {}) if actual is not None else {}
        return values if isinstance(values, dict) else {}

    def _refresh_if_changed(self) -> None:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            mtime_ns = -1
        if mtime_ns != self._mtime_ns:
            self._reload()

    def _reload(self) -> None:
        self._maps = self._read()
        try:
            self._mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime_ns = -1

    def _read(self) -> dict[str, dict[str, object]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}

        if raw.get("version") == STORE_VERSION and isinstance(raw.get("maps"), dict):
            source = raw["maps"]
            result: dict[str, dict[str, object]] = {}
            for map_name, entry in source.items():
                if not isinstance(map_name, str) or not isinstance(entry, dict):
                    continue
                raw_presets = entry.get("presets")
                if not isinstance(raw_presets, dict):
                    continue
                presets: dict[str, dict[str, dict[str, float]]] = {}
                for name, points in raw_presets.items():
                    if not isinstance(name, str):
                        continue
                    try:
                        valid_name = _preset_name(name)
                    except ValueError:
                        continue
                    presets[valid_name] = _valid_points(points)
                if not presets:
                    presets[DEFAULT_PRESET_NAME] = {}
                active = str(entry.get("active_preset", ""))
                actual = next((name for name in presets if name.casefold() == active.casefold()), None)
                result[map_name] = {
                    "active_preset": actual or next(iter(presets)),
                    "presets": presets,
                }
            return result

        # Version 1 stored points directly under each map. Preserve them as one preset.
        legacy: dict[str, dict[str, object]] = {}
        for map_name, points in raw.items():
            if not isinstance(map_name, str) or not isinstance(points, dict):
                continue
            valid = _valid_points(points)
            if valid:
                legacy[map_name] = {
                    "active_preset": DEFAULT_PRESET_NAME,
                    "presets": {DEFAULT_PRESET_NAME: valid},
                }
        return legacy

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(
                    {"version": STORE_VERSION, "maps": self._maps},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
            self._mtime_ns = self.path.stat().st_mtime_ns
        finally:
            temporary.unlink(missing_ok=True)


class MapPreviewStore:
    """Caches only official API map bounds/pathing so the GUI can reopen offline."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def get(self, profile_key: str) -> dict[str, object] | None:
        with self._lock:
            values = self._read()
            state = values.get(profile_key)
            return dict(state) if isinstance(state, dict) else None

    def update(self, profile_key: str, state: dict[str, object]) -> bool:
        bounds = state.get("bounds")
        if not profile_key or not isinstance(bounds, dict):
            return False
        try:
            normalized_bounds = {
                "min_x": float(bounds["min_x"]),
                "min_y": float(bounds["min_y"]),
                "max_x": float(bounds["max_x"]),
                "max_y": float(bounds["max_y"]),
            }
        except (KeyError, TypeError, ValueError):
            return False
        if not all(math.isfinite(value) for value in normalized_bounds.values()):
            return False
        preview: dict[str, object] = {
            "map_name": str(state.get("map_name", "")),
            "bounds": normalized_bounds,
            "pathing_grid": state.get("pathing_grid") if isinstance(state.get("pathing_grid"), dict) else None,
        }
        with self._lock:
            values = self._read()
            if values.get(profile_key) == preview:
                return False
            values[profile_key] = preview
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return True

    def _read(self) -> dict[str, dict[str, object]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}


class MapImageStore:
    """Copies player-provided map artwork into a managed per-map asset store."""

    def __init__(self, path: Path, asset_directory: Path) -> None:
        self.path = path
        self.asset_directory = asset_directory
        self._lock = threading.RLock()

    def get(self, profile_key: str) -> Path | None:
        with self._lock:
            entry = self._read().get(profile_key)
            filename = str(entry.get("file", "")) if isinstance(entry, dict) else ""
            if not filename or Path(filename).name != filename:
                return None
            candidate = self.asset_directory / filename
            return candidate if candidate.is_file() else None

    def associate(
        self,
        profile_key: str,
        source: Path,
        *,
        width: int,
        height: int,
    ) -> Path:
        if not profile_key:
            raise ValueError("地图标识不能为空")
        source = source.expanduser().resolve()
        suffix = source.suffix.casefold()
        if suffix not in SUPPORTED_MAP_IMAGE_SUFFIXES or not source.is_file():
            raise ValueError("请选择 PNG、JPG、JPEG 或 WebP 地图图片")
        if width <= 0 or height <= 0:
            raise ValueError("地图图片尺寸无效")
        digest = hashlib.sha256(profile_key.encode("utf-8")).hexdigest()[:24]
        filename = digest + suffix
        target = self.asset_directory / filename
        with self._lock:
            self.asset_directory.mkdir(parents=True, exist_ok=True)
            if source != target.resolve():
                temporary = target.with_name(
                    f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
                )
                try:
                    shutil.copyfile(source, temporary)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            values = self._read()
            values[profile_key] = {
                "file": filename,
                "width": int(width),
                "height": int(height),
            }
            self._write(values)
        return target

    def _read(self) -> dict[str, dict[str, object]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(raw, dict) and isinstance(raw.get("maps"), dict):
            raw = raw["maps"]
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): dict(value)
            for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def _write(self, values: dict[str, dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(
                    {"version": IMAGE_STORE_VERSION, "maps": values},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
