from __future__ import annotations

from s2clientprotocol import common_pb2, sc2api_pb2

from aisc2commander.catalog import GameCatalog
from aisc2commander.cli import _build_session, build_parser
from aisc2commander.sc2.session import (
    MultiplayerPortConfig,
    SC2Session,
    SessionConfig,
)


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, sc2api_pb2.Request]] = []
        self.connected = False

    def connect(self, timeout: float = 60.0) -> None:
        self.connected = True

    def request(self, populate, operation):
        request = sc2api_pb2.Request()
        populate(request)
        self.requests.append((operation, request))
        response = sc2api_pb2.Response()
        if operation == "join_game":
            response.join_game.player_id = 2
        return response

    @property
    def is_connected(self) -> bool:
        return self.connected

    def close(self) -> None:
        self.connected = False


def multiplayer_config(mode: str) -> SessionConfig:
    return SessionConfig(
        battlenet_map_name=None if mode == "join" else "Official Multiplayer Test",
        launch=False,
        player_race="protoss",
        multiplayer_mode=mode,
        multiplayer_host_ip="192.0.2.10",
        multiplayer_ports=MultiplayerPortConfig.from_start(5001),
    )


def test_official_multiplayer_port_block_uses_five_consecutive_ports() -> None:
    ports = MultiplayerPortConfig.from_start(5001)

    assert ports.ports == (5001, 5002, 5003, 5004, 5005)


def test_multiplayer_host_creates_two_participants_without_default_computer() -> None:
    session = SC2Session(multiplayer_config("host"))
    transport = FakeTransport()
    session.transport = transport

    session._create_game()

    players = transport.requests[0][1].create_game.player_setup
    assert [player.type for player in players] == [
        sc2api_pb2.Participant,
        sc2api_pb2.Participant,
    ]
    assert players[0].race == common_pb2.Protoss
    assert not players[1].HasField("race")


def test_both_peers_send_the_same_official_join_topology() -> None:
    for mode in ("host", "join"):
        session = SC2Session(multiplayer_config(mode))
        transport = FakeTransport()
        session.transport = transport

        session._join_game()

        join = transport.requests[0][1].join_game
        assert join.race == common_pb2.Protoss
        assert join.host_ip == "192.0.2.10"
        assert join.shared_port == 5001
        assert (join.server_ports.game_port, join.server_ports.base_port) == (5002, 5003)
        assert len(join.client_ports) == 1
        assert (join.client_ports[0].game_port, join.client_ports[0].base_port) == (
            5004,
            5005,
        )


def test_joiner_starts_with_join_game_and_never_creates_a_game(monkeypatch) -> None:
    session = SC2Session(multiplayer_config("join"))
    transport = FakeTransport()
    session.transport = transport
    response = sc2api_pb2.Response(status=sc2api_pb2.launched)
    response.ping.game_version = "test"
    calls: list[str] = []
    monkeypatch.setattr(session, "ping", lambda: response)
    monkeypatch.setattr(session, "_create_game", lambda: calls.append("create"))
    monkeypatch.setattr(session, "_join_game", lambda: calls.append("join"))
    monkeypatch.setattr(session, "_load_catalog", lambda: GameCatalog())

    session.start()

    assert calls == ["join"]


def test_multiplayer_close_leaves_before_quitting_official_client() -> None:
    session = SC2Session(multiplayer_config("host"))
    transport = FakeTransport()
    transport.connected = True
    session.transport = transport
    session._joined = True

    session.close(quit_game=True)

    assert [operation for operation, _request in transport.requests] == ["leave_game", "quit"]


def test_multiplayer_keep_game_still_leaves_official_match() -> None:
    session = SC2Session(multiplayer_config("join"))
    transport = FakeTransport()
    transport.connected = True
    session.transport = transport
    session._joined = True

    session.close(quit_game=False)

    assert [operation for operation, _request in transport.requests] == ["leave_game"]


def test_cli_join_mode_needs_no_map_and_builds_official_port_config() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "--multiplayer",
            "join",
            "--game-host",
            "192.0.2.10",
            "--network-port",
            "6100",
            "--race",
            "zerg",
        ]
    )

    session = _build_session(args)

    assert session.config.map_path is None
    assert session.config.battlenet_map_name is None
    assert session.config.multiplayer_mode == "join"
    assert session.config.multiplayer_ports is not None
    assert session.config.multiplayer_ports.ports == (6100, 6101, 6102, 6103, 6104)
    assert session.config.computer_players == ()
