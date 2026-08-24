from __future__ import annotations

import pytest
from s2clientprotocol import common_pb2, sc2api_pb2

from aisc2commander.cli import _build_session, build_parser
from aisc2commander.sc2.session import ComputerPlayerSetup, SC2Session, SessionConfig


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, sc2api_pb2.Request]] = []

    def request(self, populate, operation):
        request = sc2api_pb2.Request()
        populate(request)
        self.requests.append((operation, request))
        return sc2api_pb2.Response()


@pytest.mark.parametrize(
    ("race_name", "race_id"),
    (
        ("terran", common_pb2.Terran),
        ("zerg", common_pb2.Zerg),
        ("protoss", common_pb2.Protoss),
        ("random", common_pb2.Random),
    ),
)
def test_selected_race_reaches_official_create_and_join_requests(race_name, race_id) -> None:
    session = SC2Session(
        SessionConfig(battlenet_map_name="Race Test", launch=False, player_race=race_name)
    )
    transport = FakeTransport()
    session.transport = transport

    session._create_game()
    session._join_game()

    create = transport.requests[0][1].create_game
    join = transport.requests[1][1].join_game
    assert create.player_setup[0].type == sc2api_pb2.Participant
    assert create.player_setup[0].race == race_id
    assert join.race == race_id


def test_cli_accepts_player_race_and_defaults_to_terran() -> None:
    selected = build_parser().parse_args(["run", "--battlenet-map", "Race Test", "--race", "protoss"])
    defaulted = build_parser().parse_args(["run", "--battlenet-map", "Race Test"])
    assert selected.race == "protoss"
    assert defaulted.race == "terran"


def test_session_rejects_unknown_player_race() -> None:
    with pytest.raises(ValueError, match="Unsupported SC2 player race"):
        SC2Session(SessionConfig(battlenet_map_name="Race Test", player_race="invalid"))


def test_multiple_computers_reach_official_player_setup() -> None:
    session = SC2Session(
        SessionConfig(
            battlenet_map_name="Computer Test",
            launch=False,
            computer_players=(
                ComputerPlayerSetup("zerg", "easy", "rush"),
                ComputerPlayerSetup("protoss", "very_hard", "air"),
                ComputerPlayerSetup("random", "cheat_money", "macro"),
            ),
        )
    )
    transport = FakeTransport()
    session.transport = transport

    session._create_game()

    players = transport.requests[0][1].create_game.player_setup
    assert len(players) == 4
    assert players[1].type == sc2api_pb2.Computer
    assert players[1].race == common_pb2.Zerg
    assert players[1].difficulty == sc2api_pb2.Easy
    assert players[1].ai_build == sc2api_pb2.Rush
    assert players[2].race == common_pb2.Protoss
    assert players[2].difficulty == sc2api_pb2.VeryHard
    assert players[2].ai_build == sc2api_pb2.Air
    assert players[3].race == common_pb2.Random
    assert players[3].difficulty == sc2api_pb2.CheatMoney
    assert players[3].ai_build == sc2api_pb2.Macro


def test_cli_accepts_repeatable_official_computer_configuration() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "--battlenet-map",
            "Computer Test",
            "--computer",
            "zerg,easy,rush",
            "--computer",
            "protoss,hard,macro",
        ]
    )
    session = _build_session(args)
    assert session.config.computer_players == (
        ComputerPlayerSetup("zerg", "easy", "rush"),
        ComputerPlayerSetup("protoss", "hard", "macro"),
    )
    assert session.config.opponent


def test_cli_rejects_fake_team_or_color_fields() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["run", "--battlenet-map", "Computer Test", "--computer", "zerg,easy,rush,red"]
        )
