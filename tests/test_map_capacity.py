from __future__ import annotations

from s2clientprotocol import sc2api_pb2

from aisc2commander.map_capacity import MapCapacityCache, probe_map_capacity


def test_map_capacity_cache_invalidates_when_local_map_changes(tmp_path) -> None:
    map_path = tmp_path / "Capacity.SC2Map"
    map_path.write_bytes(b"first")
    cache = MapCapacityCache(tmp_path / "capacity.json")

    cache.put("local", str(map_path), 6)
    assert cache.get("local", str(map_path)) == 6

    map_path.write_bytes(b"different-size")
    assert cache.get("local", str(map_path)) is None


def test_map_capacity_cache_normalizes_battlenet_names(tmp_path) -> None:
    cache = MapCapacityCache(tmp_path / "capacity.json")
    cache.put("battlenet", "  My Published Map  ", 8)
    assert cache.get("battlenet", "my published map") == 8


def test_probe_uses_official_game_info_start_locations_for_capacity(monkeypatch, tmp_path) -> None:
    map_path = tmp_path / "Probe.SC2Map"
    map_path.write_bytes(b"map")
    operations: list[str] = []
    requests: list[sc2api_pb2.Request] = []

    class FakeProcess:
        handle = None

        def __init__(self, *_args) -> None:
            self.launched = False
            self.terminated = False

        def launch(self) -> None:
            self.launched = True

        def terminate_if_running(self, _reason: str) -> None:
            self.terminated = True

    class FakeProtocol:
        def __init__(self, _host: str, _port: int) -> None:
            self.is_connected = False

        def connect(self, timeout: float) -> None:
            assert timeout == 3.0
            self.is_connected = True

        def request(self, populate, operation: str):
            request = sc2api_pb2.Request()
            populate(request)
            operations.append(operation)
            requests.append(request)
            response = sc2api_pb2.Response()
            if operation == "map_capacity.game_info":
                for index in range(3):
                    location = response.game_info.start_raw.start_locations.add()
                    location.x = float(index)
                    location.y = float(index)
            return response

        def close(self) -> None:
            self.is_connected = False

    monkeypatch.setattr("aisc2commander.map_capacity.choose_free_port", lambda _host: 5000)
    monkeypatch.setattr("aisc2commander.map_capacity.discover_sc2_executable", lambda _path: "SC2.exe")
    monkeypatch.setattr("aisc2commander.map_capacity.SC2Process", FakeProcess)
    monkeypatch.setattr("aisc2commander.map_capacity.SC2ProtocolClient", FakeProtocol)

    assert probe_map_capacity("local", str(map_path), connect_timeout=3.0) == 4
    create = requests[1].create_game
    assert len(create.player_setup) == 2
    assert create.player_setup[0].type == sc2api_pb2.Participant
    assert create.player_setup[1].type == sc2api_pb2.Computer
    assert requests[2].join_game.options.raw
    assert operations[-2] == "map_capacity.game_info"
    assert operations[-1] == "map_capacity.quit"
