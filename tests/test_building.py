from __future__ import annotations

from pathlib import Path

from s2clientprotocol import data_pb2, error_pb2, sc2api_pb2

from aisc2commander.catalog import AbilityInfo, UnitTypeInfo
from aisc2commander.sc2.session import SC2Session, SessionConfig


class _FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, sc2api_pb2.Request]] = []

    def request(self, populate, operation: str) -> sc2api_pb2.Response:
        request = sc2api_pb2.Request()
        populate(request)
        self.requests.append((operation, request))
        response = sc2api_pb2.Response()
        if operation == "query.available_abilities":
            item = response.query.abilities.add(unit_tag=44)
            item.abilities.add(ability_id=319)
        elif operation == "query.building_placement":
            for _ in request.query.placements:
                response.query.placements.add(result=error_pb2.Success)
        elif operation == "action.build_structure":
            response.action.result.append(error_pb2.Success)
        return response


def test_official_building_query_and_action_use_request_data_ability() -> None:
    session = SC2Session(SessionConfig(map_path=Path("unused.SC2Map"), launch=False))
    transport = _FakeTransport()
    session.transport = transport
    session.catalog.unit_types[19] = UnitTypeInfo(
        type_id=19,
        name="SupplyDepot",
        is_structure=True,
        ability_id=319,
        mineral_cost=100,
    )

    assert session.building_placement_error(319, 20, 30, 44) is None
    assert session.build_structure(19, 44, target_position=(20, 30)) == ()

    placement = next(request for operation, request in transport.requests if operation == "query.building_placement")
    assert placement.query.placements[0].ability_id == 319
    assert placement.query.placements[0].placing_unit_tag == 44
    assert placement.query.placements[0].target_pos.x == 20
    assert placement.query.placements[0].target_pos.y == 30

    action = next(request for operation, request in transport.requests if operation == "action.build_structure")
    command = action.action.actions[0].action_raw.unit_command
    assert command.ability_id == 319
    assert tuple(command.unit_tags) == (44,)
    assert command.target_world_space_pos.x == 20
    assert command.target_world_space_pos.y == 30


def test_official_building_query_batches_nearby_candidates() -> None:
    session = SC2Session(SessionConfig(map_path=Path("unused.SC2Map"), launch=False))
    transport = _FakeTransport()
    session.transport = transport

    positions = ((10.0, 11.0), (12.0, 13.0), (14.0, 15.0))
    assert session.building_placement_errors(319, positions, 44) == (None, None, None)
    operation, request = transport.requests[-1]
    assert operation == "query.building_placement"
    assert len(request.query.placements) == 3
    assert [item.target_pos.x for item in request.query.placements] == [10, 12, 14]


class _WarpTrainingTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, sc2api_pb2.Request]] = []

    def request(self, populate, operation: str) -> sc2api_pb2.Response:
        request = sc2api_pb2.Request()
        populate(request)
        self.requests.append((operation, request))
        response = sc2api_pb2.Response()
        if operation == "query.available_abilities":
            item = response.query.abilities.add(unit_tag=77)
            item.abilities.add(ability_id=101)
        elif operation == "action.train":
            response.action.result.append(error_pb2.Success)
        return response


def test_warp_in_uses_official_remapped_ability_and_world_target() -> None:
    session = SC2Session(SessionConfig(map_path=Path("unused.SC2Map"), launch=False))
    transport = _WarpTrainingTransport()
    session.transport = transport
    session.catalog.unit_types[73] = UnitTypeInfo(
        type_id=73,
        name="Zealot",
        is_structure=False,
        ability_id=100,
        mineral_cost=100,
        food_required=2,
    )
    session.catalog.ability_details[100] = AbilityInfo(
        100,
        "Train Zealot",
        target=data_pb2.AbilityData.Target.Value("None"),
    )
    session.catalog.ability_details[101] = AbilityInfo(
        101,
        "Warp In Zealot",
        target=data_pb2.AbilityData.Point,
    )

    assert session.train_units(73, 1, (77,), target_position=(33, 44)) == ()
    action = next(request for operation, request in transport.requests if operation == "action.train")
    command = action.action.actions[0].action_raw.unit_command
    assert command.ability_id == 101
    assert tuple(command.unit_tags) == (77,)
    assert command.target_world_space_pos.x == 33
    assert command.target_world_space_pos.y == 44
