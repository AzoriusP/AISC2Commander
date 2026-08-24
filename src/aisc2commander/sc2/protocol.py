from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from s2clientprotocol import sc2api_pb2
from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.sync.client import ClientConnection, connect


LOG = logging.getLogger(__name__)


class SC2ProtocolError(RuntimeError):
    pass


class SC2ProtocolClient:
    """Synchronous protobuf-over-WebSocket transport for the official SC2 API."""

    def __init__(self, host: str, port: int) -> None:
        self.uri = f"ws://{host}:{port}/sc2api"
        self._connection: ClientConnection | None = None
        self._request_id = 0
        self._lock = threading.Lock()

    def connect(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._connection = connect(
                    self.uri,
                    open_timeout=3,
                    # SC2's websocket endpoint closes the socket when the
                    # websockets library sends its default 20-second PING.
                    # Protobuf Observation traffic already provides liveness.
                    ping_interval=None,
                    close_timeout=2,
                    max_size=None,
                    compression=None,
                    proxy=None,
                )
                LOG.info("Connected to official SC2 API websocket: %s", self.uri)
                return
            except (OSError, TimeoutError, InvalidHandshake) as error:
                last_error = error
                time.sleep(0.25)
        raise TimeoutError(f"Timed out connecting to {self.uri}: {last_error}")

    def request(
        self,
        populate: Callable[[sc2api_pb2.Request], Any],
        operation: str,
    ) -> sc2api_pb2.Response:
        if self._connection is None:
            raise SC2ProtocolError("SC2 websocket is not connected")
        with self._lock:
            self._request_id += 1
            request = sc2api_pb2.Request(id=self._request_id)
            populate(request)
            request_type = request.WhichOneof("request")
            if request_type is None:
                raise ValueError(f"{operation} did not populate a request payload")
            payload = request.SerializeToString()
            started = time.perf_counter()
            LOG.debug(
                "SC2 request id=%d operation=%s type=%s bytes=%d",
                self._request_id,
                operation,
                request_type,
                len(payload),
            )
            try:
                self._connection.send(payload)
                received = self._connection.recv()
            except ConnectionClosed as error:
                self._connection = None
                raise SC2ProtocolError(f"SC2 connection closed during {operation}: {error}") from error
            if isinstance(received, str):
                raise SC2ProtocolError(f"SC2 returned a text frame during {operation}")
            response = sc2api_pb2.Response()
            response.ParseFromString(received)
            elapsed_ms = (time.perf_counter() - started) * 1000
            response_type = response.WhichOneof("response")
            LOG.debug(
                "SC2 response id=%s operation=%s type=%s status=%s bytes=%d elapsed_ms=%.2f",
                response.id if response.HasField("id") else "unset",
                operation,
                response_type,
                _status_name(response.status),
                len(received),
                elapsed_ms,
            )
            if response.HasField("id") and response.id != request.id:
                raise SC2ProtocolError(
                    f"Response id mismatch during {operation}: expected {request.id}, got {response.id}"
                )
            if response.error:
                detail = "; ".join(response.error)
                LOG.error("SC2 protocol error operation=%s: %s", operation, detail)
                raise SC2ProtocolError(f"SC2 protocol error during {operation}: {detail}")
            return response

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except (ConnectionClosed, OSError):
                LOG.debug("Websocket was already closed", exc_info=True)

    @property
    def is_connected(self) -> bool:
        return self._connection is not None


def _status_name(value: int) -> str:
    try:
        return sc2api_pb2.Status.Name(value)
    except ValueError:
        return f"unknown({value})"
