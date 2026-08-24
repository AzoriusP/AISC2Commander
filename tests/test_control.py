from __future__ import annotations

import json
import os
import queue
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from aisc2commander.control import CommanderControlServer
from aisc2commander.app import CommanderApp


def _json_request(url: str, *, text: str | None = None) -> tuple[int, dict[str, object]]:
    data = None
    headers: dict[str, str] = {}
    if text is not None:
        data = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_loopback_control_health_commands_and_role_events() -> None:
    commands: list[str] = []
    shutdowns: list[bool] = []
    server = CommanderControlServer(
        commands.append,
        request_shutdown=lambda: shutdowns.append(True),
        port=0,
    )
    server.start()
    try:
        host, port = server.address
        base = f"http://{host}:{port}"
        status, health = _json_request(base + "/health")
        assert status == 200
        assert health["ready"] is False
        assert health["commander_pid"] == os.getpid()
        assert health["sc2_pid"] == 0

        status, rejected = _json_request(base + "/command", text="生产一个枪兵")
        assert status == 409
        assert "not ready" in str(rejected["error"])

        server.set_agent(ready=True, provider="ollama", model="qwen3.6-q3:latest")
        server.publish("player", "生产一个枪兵")
        server.publish("assistant", "已提交正常生产：Marine x1")
        server.create_job("CMD-0001", "建造补给站", selection_tags=(7788, 9900))
        server.update_job(
            "CMD-0001",
            phase="validating",
            message="规则预检 1/1：build_structure",
            current=0,
            total=1,
        )
        status, accepted = _json_request(base + "/command", text="建造补给站")
        assert status == 202
        assert accepted["accepted"] is True
        assert accepted["job_id"] == ""
        assert commands == ["建造补给站"]

        status, jobs_payload = _json_request(base + "/jobs")
        assert status == 200
        job = jobs_payload["jobs"][0]
        assert job["id"] == "CMD-0001"
        assert job["phase"] == "validating"
        assert job["message"].startswith("规则预检")
        assert job["selection_tags"] == [7788, 9900]

        shutdown_request = Request(
            base + "/shutdown",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(shutdown_request, timeout=2) as response:
            assert response.status == 202
        assert shutdowns == [True]

        status, payload = _json_request(base + "/events?after=0")
        assert status == 200
        events = payload["events"]
        assert [event["role"] for event in events] == ["player", "assistant"]
        assert events[0]["text"] == "生产一个枪兵"
    finally:
        server.stop()


def test_control_server_rejects_non_loopback_binding() -> None:
    with pytest.raises(ValueError, match="loopback"):
        CommanderControlServer(lambda text: None, host="0.0.0.0")


def test_gui_enqueue_captures_selection_before_player_changes_it() -> None:
    class FakeControl:
        def __init__(self) -> None:
            self.created: list[tuple[str, str, tuple[int, ...]]] = []

        def create_job(self, job_id, text, *, selection_tags=()) -> None:
            self.created.append((job_id, text, selection_tags))

    app = CommanderApp.__new__(CommanderApp)
    app._latest = SimpleNamespace(selection=SimpleNamespace(unit_tags=(42, 73)))
    app._commands = queue.Queue()
    app._control = FakeControl()
    app._job_lock = threading.Lock()
    app._job_sequence = 0

    job_id = app._enqueue_control_command("让这个农民建造补给站")
    app._latest = SimpleNamespace(selection=SimpleNamespace(unit_tags=(9001,)))
    queued = app._commands.get_nowait()

    assert job_id == "CMD-0001"
    assert queued.selection_tags == (42, 73)
    assert app._control.created == [
        ("CMD-0001", "让这个农民建造补给站", (42, 73))
    ]
