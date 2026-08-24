from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from s2clientprotocol import common_pb2, sc2api_pb2

from .map_points import map_profile_key
from .sc2.process import SC2Process, choose_free_port, discover_sc2_executable
from .sc2.protocol import SC2ProtocolClient, SC2ProtocolError


MAX_SC2_PLAYERS = 16


class MapCapacityCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def get(self, kind: str, value: str) -> int | None:
        key = map_profile_key(kind, value)
        signature = self._signature(kind, value)
        with self._lock:
            values = self._read()
            entry = values.get(key)
            if not isinstance(entry, dict) or entry.get("signature") != signature:
                return None
            try:
                capacity = int(entry["max_players"])
            except (KeyError, TypeError, ValueError):
                return None
            return capacity if 1 <= capacity <= MAX_SC2_PLAYERS else None

    def put(self, kind: str, value: str, max_players: int) -> None:
        if not 1 <= max_players <= MAX_SC2_PLAYERS:
            raise ValueError("地图玩家容量必须在 1 到 16 之间")
        key = map_profile_key(kind, value)
        with self._lock:
            values = self._read()
            values[key] = {
                "max_players": int(max_players),
                "signature": self._signature(kind, value),
            }
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

    def _signature(self, kind: str, value: str) -> str:
        if kind != "local":
            return value.strip().casefold()
        path = Path(value).expanduser().resolve(strict=True)
        stat = path.stat()
        return f"{path.as_posix().casefold()}:{stat.st_size}:{stat.st_mtime_ns}"

    def _read(self) -> dict[str, dict[str, object]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}


def probe_map_capacity(
    kind: str,
    value: str,
    *,
    executable: str | Path | None = None,
    connect_timeout: float = 60.0,
) -> int:
    """Read melee start-slot capacity through official Create/Join/GameInfo calls.

    RequestCreateGame accepts excess Computer rows on some maps, so an
    InvalidPlayerSetup search isn't a reliable capacity signal. After joining a
    temporary game with one participant and one computer,
    start_raw.start_locations contains every possible enemy start position;
    adding the participant's own start gives the map's usable melee player
    count.
    """

    if kind == "local":
        map_path = Path(value).expanduser().resolve(strict=True)
        battlenet_name = ""
    elif kind == "battlenet":
        map_path = None
        battlenet_name = value.strip()
        if not battlenet_name:
            raise ValueError("Battle.net 地图名称不可为空")
    else:
        raise ValueError(f"未知地图来源：{kind}")

    host = "127.0.0.1"
    port = choose_free_port(host)
    process = SC2Process(discover_sc2_executable(executable), host, port, 960, 600)
    transport = SC2ProtocolClient(host, port)
    quit_was_sent = False
    process.launch()
    try:
        transport.connect(timeout=connect_timeout)
        transport.request(lambda request: request.ping.SetInParent(), "map_capacity.ping")
        def populate_create(request: sc2api_pb2.Request) -> None:
            create = request.create_game
            if map_path is not None:
                create.local_map.map_path = str(map_path)
            else:
                create.battlenet_map_name = battlenet_name
            create.realtime = True
            participant = create.player_setup.add()
            participant.type = sc2api_pb2.Participant
            participant.race = common_pb2.Terran
            computer = create.player_setup.add()
            computer.type = sc2api_pb2.Computer
            computer.race = common_pb2.Zerg
            computer.difficulty = sc2api_pb2.Easy
            computer.ai_build = sc2api_pb2.RandomBuild

        response = transport.request(populate_create, "map_capacity.create_game")
        if response.create_game.HasField("error"):
            error = response.create_game.error
            name = sc2api_pb2.ResponseCreateGame.Error.Name(error)
            raise SC2ProtocolError(
                f"Map capacity CreateGame failed: {name}: "
                f"{response.create_game.error_details}"
            )

        def populate_join(request: sc2api_pb2.Request) -> None:
            request.join_game.race = common_pb2.Terran
            request.join_game.player_name = "AI Commander Map Probe"
            request.join_game.options.raw = True

        response = transport.request(populate_join, "map_capacity.join_game")
        if response.join_game.HasField("error"):
            error = response.join_game.error
            name = sc2api_pb2.ResponseJoinGame.Error.Name(error)
            raise SC2ProtocolError(
                f"Map capacity JoinGame failed: {name}: {response.join_game.error_details}"
            )

        response = transport.request(
            lambda request: request.game_info.SetInParent(),
            "map_capacity.game_info",
        )
        max_players = len(response.game_info.start_raw.start_locations) + 1
        if not 1 <= max_players <= MAX_SC2_PLAYERS:
            raise SC2ProtocolError(
                f"地图返回了无效的起始位置数量：{max_players}"
            )
        return max_players
    finally:
        if transport.is_connected:
            try:
                transport.request(lambda request: request.quit.SetInParent(), "map_capacity.quit")
                quit_was_sent = True
            except (SC2ProtocolError, OSError):
                pass
        transport.close()
        if quit_was_sent:
            deadline = time.monotonic() + 5.0
            while process.handle is not None and process.handle.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
        process.terminate_if_running("地图容量探测结束后 SC2 未正常退出")
