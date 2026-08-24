from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse


LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ControlEvent:
    id: int
    role: str
    text: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class ControlJob:
    id: str
    text: str
    phase: str
    message: str
    current: int
    total: int
    created_at: str
    updated_at: str
    selection_tags: tuple[int, ...] = ()


class _LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class CommanderControlServer:
    """Loopback-only GUI bridge; all SC2 work remains on Commander's main loop."""

    def __init__(
        self,
        submit_command: Callable[[str], str | None],
        request_shutdown: Callable[[], None] | None = None,
        state_provider: Callable[[], dict[str, object]] | None = None,
        upsert_map_point: Callable[[str, float, float], dict[str, object]] | None = None,
        delete_map_point: Callable[[str], bool] | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        max_events: int = 300,
    ) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("Commander control server must bind to loopback")
        self.host = host
        self.port = port
        self._submit_command = submit_command
        self._request_shutdown = request_shutdown
        self._state_provider = state_provider
        self._upsert_map_point = upsert_map_point
        self._delete_map_point = delete_map_point
        self._events: deque[ControlEvent] = deque(maxlen=max_events)
        self._jobs: dict[str, ControlJob] = {}
        self._job_order: deque[str] = deque(maxlen=100)
        self._lock = threading.Lock()
        self._next_event_id = 1
        self.instance_id = uuid.uuid4().hex
        self._ready = False
        self._provider = ""
        self._model = ""
        self._commander_pid = os.getpid()
        self._sc2_pid = 0
        self._httpd: _LocalHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._httpd is None:
            return self.host, self.port
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        if self._httpd is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    owner._write_json(self, HTTPStatus.OK, owner.health())
                    return
                if parsed.path == "/events":
                    query = parse_qs(parsed.query)
                    try:
                        after = int(query.get("after", ["0"])[0])
                    except ValueError:
                        owner._write_json(self, HTTPStatus.BAD_REQUEST, {"error": "invalid after id"})
                        return
                    owner._write_json(
                        self,
                        HTTPStatus.OK,
                        {"events": [asdict(event) for event in owner.events_after(after)]},
                    )
                    return
                if parsed.path == "/state":
                    if owner._state_provider is None:
                        owner._write_json(self, HTTPStatus.NOT_IMPLEMENTED, {"error": "state is unavailable"})
                        return
                    try:
                        state = owner._state_provider()
                    except Exception as error:
                        LOG.exception("GUI state request failed")
                        owner._write_json(self, HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
                        return
                    owner._write_json(self, HTTPStatus.OK, state)
                    return
                if parsed.path == "/jobs":
                    owner._write_json(
                        self,
                        HTTPStatus.OK,
                        {"jobs": [asdict(job) for job in owner.jobs()]},
                    )
                    return
                owner._write_json(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                path = urlparse(self.path).path
                if path == "/shutdown":
                    if owner._request_shutdown is None:
                        owner._write_json(
                            self,
                            HTTPStatus.NOT_IMPLEMENTED,
                            {"error": "shutdown is unavailable"},
                        )
                        return
                    try:
                        owner._request_shutdown()
                    except Exception as error:
                        LOG.exception("GUI shutdown request failed")
                        owner._write_json(
                            self,
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {"error": str(error)},
                        )
                        return
                    owner._write_json(self, HTTPStatus.ACCEPTED, {"accepted": True})
                    return
                if path in {"/map-points", "/map-points/delete"}:
                    try:
                        payload = owner._read_json_body(self)
                        label = payload.get("label")
                        if not isinstance(label, str) or not label.strip():
                            raise ValueError("label is required")
                        if path == "/map-points":
                            if owner._upsert_map_point is None:
                                raise RuntimeError("map point editing is unavailable")
                            point = owner._upsert_map_point(
                                label,
                                float(payload["x"]),
                                float(payload["y"]),
                            )
                            owner._write_json(self, HTTPStatus.OK, {"point": point})
                        else:
                            if owner._delete_map_point is None:
                                raise RuntimeError("map point editing is unavailable")
                            deleted = owner._delete_map_point(label)
                            owner._write_json(self, HTTPStatus.OK, {"deleted": deleted})
                    except (KeyError, TypeError, ValueError) as error:
                        owner._write_json(self, HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    except Exception as error:
                        LOG.exception("GUI map-point request failed")
                        owner._write_json(self, HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
                    return
                if path != "/command":
                    owner._write_json(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                try:
                    payload = owner._read_json_body(self)
                except ValueError as error:
                    owner._write_json(self, HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    return
                text = payload.get("text") if isinstance(payload, dict) else None
                if not isinstance(text, str) or not text.strip():
                    owner._write_json(self, HTTPStatus.BAD_REQUEST, {"error": "text is required"})
                    return
                text = text.strip()
                if len(text) > 2000:
                    owner._write_json(self, HTTPStatus.BAD_REQUEST, {"error": "text is too long"})
                    return
                if not owner.health()["ready"]:
                    owner._write_json(self, HTTPStatus.CONFLICT, {"error": "Commander is not ready"})
                    return
                try:
                    job_id = owner._submit_command(text)
                except Exception as error:
                    LOG.exception("GUI command enqueue failed")
                    owner._write_json(self, HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})
                    return
                owner._write_json(
                    self,
                    HTTPStatus.ACCEPTED,
                    {"accepted": True, "job_id": job_id or ""},
                )

            def log_message(self, format: str, *args: object) -> None:
                LOG.debug("GUI HTTP: " + format, *args)

        self._httpd = _LocalHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="commander-gui-http",
            daemon=True,
        )
        self._thread.start()
        LOG.info("GUI control API listening at http://%s:%d", *self.address)

    def stop(self) -> None:
        server = self._httpd
        if server is None:
            return
        self._ready = False
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._httpd = None
        self._thread = None

    def set_agent(self, *, ready: bool, provider: str = "", model: str = "") -> None:
        with self._lock:
            self._ready = ready
            self._provider = provider
            self._model = model

    def set_sc2_pid(self, pid: int | None) -> None:
        with self._lock:
            self._sc2_pid = int(pid or 0)

    def health(self) -> dict[str, object]:
        with self._lock:
            return {
                "instance_id": self.instance_id,
                "status": "ready" if self._ready else "starting",
                "ready": self._ready,
                "provider": self._provider,
                "model": self._model,
                "commander_pid": self._commander_pid,
                "sc2_pid": self._sc2_pid,
            }

    def publish(self, role: str, text: str) -> ControlEvent:
        if role not in {"player", "assistant", "system"}:
            raise ValueError("event role must be player, assistant, or system")
        message = text.strip()
        if not message:
            raise ValueError("event text must not be empty")
        with self._lock:
            event = ControlEvent(
                id=self._next_event_id,
                role=role,
                text=message,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
            )
            self._next_event_id += 1
            self._events.append(event)
            return event

    def events_after(self, event_id: int) -> tuple[ControlEvent, ...]:
        with self._lock:
            return tuple(event for event in self._events if event.id > event_id)

    def create_job(
        self,
        job_id: str,
        text: str,
        *,
        selection_tags: tuple[int, ...] = (),
    ) -> ControlJob:
        now = _utc_timestamp()
        job = ControlJob(
            job_id,
            text.strip(),
            "queued",
            "等待处理",
            0,
            0,
            now,
            now,
            tuple(dict.fromkeys(int(tag) for tag in selection_tags)),
        )
        with self._lock:
            if job_id not in self._jobs:
                if len(self._job_order) == self._job_order.maxlen:
                    oldest = self._job_order.popleft()
                    self._jobs.pop(oldest, None)
                self._job_order.append(job_id)
            self._jobs[job_id] = job
        return job

    def update_job(
        self,
        job_id: str,
        *,
        phase: str,
        message: str,
        current: int = 0,
        total: int = 0,
    ) -> ControlJob | None:
        if not job_id:
            return None
        with self._lock:
            previous = self._jobs.get(job_id)
            if previous is None:
                return None
            job = ControlJob(
                id=previous.id,
                text=previous.text,
                phase=phase,
                message=message.strip(),
                current=max(0, int(current)),
                total=max(0, int(total)),
                created_at=previous.created_at,
                updated_at=_utc_timestamp(),
                selection_tags=previous.selection_tags,
            )
            self._jobs[job_id] = job
            return job

    def jobs(self) -> tuple[ControlJob, ...]:
        with self._lock:
            return tuple(self._jobs[job_id] for job_id in reversed(self._job_order))

    @staticmethod
    def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, object]:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid body size") from error
        if length < 1 or length > 64 * 1024:
            raise ValueError("invalid body size")
        try:
            payload = json.loads(handler.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    @staticmethod
    def _write_json(
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: dict[str, object],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        handler.send_response(int(status))
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
