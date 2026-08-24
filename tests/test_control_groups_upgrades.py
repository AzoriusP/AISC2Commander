from __future__ import annotations

from pathlib import Path

from s2clientprotocol import sc2api_pb2

from aisc2commander.catalog import GameCatalog, UpgradeInfo, UnitTypeInfo
from aisc2commander.observation import build_snapshot
from aisc2commander.sc2.session import SC2Session, SessionConfig


def test_observation_exposes_official_control_group_summary_and_completed_upgrades() -> None:
    catalog = GameCatalog()
    catalog.unit_types[48] = UnitTypeInfo(48, "Marine", False)
    catalog.upgrades[7] = UpgradeInfo(7, "Stimpack", 730)
    response = sc2api_pb2.ResponseObservation()
    response.observation.ui_data.groups.add(control_group_index=0, leader_unit_type=48, count=12)
    response.observation.raw_data.player.upgrade_ids.append(7)

    snapshot = build_snapshot(response, catalog)
    assert snapshot.control_groups[0].number == 1
    assert snapshot.control_groups[0].leader_type_name == "Marine"
    assert snapshot.control_groups[0].count == 12
    assert snapshot.completed_upgrades == ("Stimpack",)


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, sc2api_pb2.Request]] = []

    def request(self, populate, operation):
        request = sc2api_pb2.Request()
        populate(request)
        self.requests.append((operation, request))
        return sc2api_pb2.Response()


def test_official_control_group_recall_and_battlenet_map_create_requests() -> None:
    session = SC2Session(SessionConfig(battlenet_map_name="My Published Map", launch=False))
    transport = FakeTransport()
    session.transport = transport

    session._create_game()
    session.recall_control_group(1)

    create = transport.requests[0][1].create_game
    assert create.battlenet_map_name == "My Published Map"
    recall = transport.requests[1][1].action.actions[0].action_ui.control_group
    assert recall.control_group_index == 0
    assert recall.action == 1
