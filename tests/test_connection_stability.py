from __future__ import annotations

import logging

from aisc2commander.app import AppConfig, CommanderApp
from aisc2commander.sc2.protocol import SC2ProtocolClient, SC2ProtocolError


def test_sc2_websocket_disables_protocol_keepalive(monkeypatch) -> None:
    captured: dict[str, object] = {}
    fake_connection = object()

    def fake_connect(uri: str, **kwargs):
        captured["uri"] = uri
        captured.update(kwargs)
        return fake_connection

    monkeypatch.setattr("aisc2commander.sc2.protocol.connect", fake_connect)
    client = SC2ProtocolClient("127.0.0.1", 5000)
    client.connect(timeout=0.1)
    assert captured["uri"] == "ws://127.0.0.1:5000/sc2api"
    assert captured["ping_interval"] is None


def test_app_reports_disconnect_once_without_propagating_traceback(caplog) -> None:
    class FakeSession:
        process = None

        def __init__(self) -> None:
            self.closed = False

        def start(self) -> None:
            return None

        def observe(self):
            raise SC2ProtocolError("test disconnect")

        def close(self, quit_game: bool = True) -> None:
            self.closed = True

    session = FakeSession()
    app = CommanderApp(session, AppConfig(control_port=0))
    app._start_input_thread = lambda: None
    with caplog.at_level(logging.ERROR):
        result = app.run()
    assert result == 2
    assert session.closed
    assert caplog.text.count("SC2 API session disconnected") == 1
    assert "test disconnect" in caplog.text
