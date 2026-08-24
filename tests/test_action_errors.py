from __future__ import annotations

import logging

from s2clientprotocol import error_pb2, sc2api_pb2

from aisc2commander.sc2.session import SC2Session, SessionConfig


def test_immediate_action_error_is_named_and_logged(tmp_path, caplog) -> None:
    session = SC2Session(SessionConfig(map_path=tmp_path / "unused.SC2Map", launch=False))
    response = sc2api_pb2.ResponseAction()
    response.result.append(error_pb2.UnitCantMove)
    with caplog.at_level(logging.ERROR):
        errors = session._log_immediate_action_results(response, 16, (123,))
    assert errors == (
        "API Action Error (immediate): result=UnitCantMove ability=16 tags=[123]",
    )
    assert "API Action Error (immediate)" in caplog.text


def test_late_observation_action_error_is_named_and_logged(tmp_path, caplog) -> None:
    session = SC2Session(SessionConfig(map_path=tmp_path / "unused.SC2Map", launch=False))
    observation = sc2api_pb2.ResponseObservation()
    observation.action_errors.add(
        unit_tag=456,
        ability_id=16,
        result=error_pb2.UnitCantMove,
    )
    with caplog.at_level(logging.ERROR):
        session._log_observation_action_errors(observation)
    assert "API Action Error (late)" in caplog.text
    assert "UnitCantMove" in caplog.text
    assert "unit_tag=456" in caplog.text
