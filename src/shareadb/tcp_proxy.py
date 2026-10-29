"""Async TCP proxy to bridge clients to forwarded adb ports."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .access import AccessPolicy, entry_matches, normalize_peer_host

_LOG = logging.getLogger(__name__)

# Minimum seconds between two WARNING logs for the same upstream-connect
# failure. Without this, a dead forward floods the log (one WARNING per inbound
# client retry, several per second). A successful connection resets the timer so
# the first failure after recovery is still reported at WARNING level.
LOG_BACKOFF_SECONDS = 5.0

# Cap on per-host rejection-log timestamps; beyond this the map is reset so a
# hostile scanner cannot grow it without bound.
REJECT_LOG_MAX_HOSTS = 1024

# Upper bound while waiting for the listening socket to finish closing in
# stop(). stop() must never block: it is called from the device-sync loop via
# ``ADBDeviceSession.mark_unavailable`` (dead-forward self-heal), and a stop()
# that hangs would freeze every session at its last known state.
STOP_WAIT_TIMEOUT = 5.0


@dataclass
class ConnectionInfo:
    """Immutable snapshot of one active proxied client connection."""

    peer_host: str
    peer_port: int
    proxy_name: str
    connected_at: float  # wall-clock time.time() of the accept
    bytes_from_client: int  # client -> device
    bytes_to_client: int  # device -> client
    device: str = "unknown"


class _ConnState:
    """Mutable per-connection bookkeeping used by connections()/kick()."""

    __slots__ = ("host", "port", "connected_at", "from_client", "to_client", "tasks")

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.connected_at = time.time()
        self.from_client = 0
        self.to_client = 0
        self.tasks: Tuple[asyncio.Task[None], ...] = ()

    def snapshot(self, proxy_name: str) -> ConnectionInfo:
        return ConnectionInfo(
            peer_host=self.host,
            peer_port=self.port,
            proxy_name=proxy_name,
            connected_at=self.connected_at,
            bytes_from_client=self.from_client,
            bytes_to_client=self.to_client,
        )


class TCPProxyServer:
    """Bidirectional TCP proxy implemented with asyncio streams."""

    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        target_host: str,
        target_port: int,
        *,
        name: Optional[str] = None,
        policy: Optional[AccessPolicy] = None,
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._target_host = target_host
        self._target_port = target_port
        self._server: Optional[asyncio.AbstractServer] = None
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._name = name or f"proxy:{listen_port}->{target_port}"
        self._lock = asyncio.Lock()
        self._last_failure_log = 0.0
        self._policy = policy
        self._conns: Dict[int, _ConnState] = {}
        self._next_conn_id = 0
        self._reject_log: Dict[str, float] = {}

    @property
    def address(self) -> tuple[str, int]:
        return self._listen_host, self._listen_port

    @property
    def name(self) -> str:
        return self._name

    async def start(self) -> None:
        async with self._lock:
            if self._server:
                return
            self._server = await asyncio.start_server(
                self._handle_client,
                host=self._listen_host,
                port=self._listen_port,
            )
            addr = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
            _LOG.info("Started proxy %s listening on %s", self._name, addr)

    async def _drain_client_tasks(self) -> None:
        """Cancel and await the pipe tasks of every in-flight client connection."""

        pending = list(self._client_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            self._client_tasks.difference_update(pending)

    async def stop(self) -> None:
        async with self._lock:
            if not self._server:
                return
            server, self._server = self._server, None
            server.close()
            # Cancel the connection handlers BEFORE awaiting wait_closed().
            # The semantics of Server.wait_closed() differ across Python
            # versions: on 3.12+ it waits for accepted connection handlers to
            # finish, and those handlers are blocked in read() until cancelled.
            # Awaiting it first would deadlock stop() — and with it the device
            # sync loop, because self-heal calls stop() via mark_unavailable,
            # leaving sessions stuck at "running" until the script restarts.
            await self._drain_client_tasks()
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=STOP_WAIT_TIMEOUT)
            except Exception as exc:  # bounded best-effort; the listener is already closed
                _LOG.debug("wait_closed for %s: %s", self._name, exc)
            # Handlers that were mid-accept when close() ran may have registered
            # their pipe tasks afterwards; sweep once more so nothing survives.
            await self._drain_client_tasks()
            _LOG.info("Stopped proxy %s", self._name)

    def connections(self) -> List[ConnectionInfo]:
        """Snapshot of currently active client connections."""

        return [state.snapshot(self._name) for state in self._conns.values()]

    def kick(self, pattern: str) -> int:
        """Close every active connection whose peer matches ``pattern``.

        ``pattern`` is an IP or CIDR. Returns the number of connections closed;
        the close completes asynchronously as the cancelled pipe tasks unwind.
        """

        count = 0
        for state in list(self._conns.values()):
            if entry_matches(pattern, state.host):
                for task in state.tasks:
                    task.cancel()
                count += 1
        if count:
            _LOG.info("Kicked %d connection(s) matching %s on %s", count, pattern, self._name)
        return count

    def _log_failure(self, exc: BaseException) -> None:
        """Log an upstream-connect failure, deduplicated to one WARNING per window.

        The first failure after ``LOG_BACKOFF_SECONDS`` (or after a recovery)
        is logged at WARNING; repeats within the window are downgraded to DEBUG
        so a persistently-dead forward cannot flood the log.
        """

        now = asyncio.get_running_loop().time()
        if now - self._last_failure_log >= LOG_BACKOFF_SECONDS:
            _LOG.warning(
                "Proxy %s failed to connect to target %s:%s: %s",
                self._name,
                self._target_host,
                self._target_port,
                exc,
            )
            self._last_failure_log = now
        else:
            _LOG.debug(
                "Proxy %s failed to connect to target %s:%s: %s",
                self._name,
                self._target_host,
                self._target_port,
                exc,
            )

    def _log_rejection(self, host: str, port: int, reason: str) -> None:
        """Log a policy rejection, deduplicated per host like _log_failure.

        A blocked client that keeps retrying (e.g. an adb auto-reconnect loop)
        would otherwise emit one WARNING per attempt.
        """

        now = asyncio.get_running_loop().time()
        last = self._reject_log.get(host)
        if last is None or now - last >= LOG_BACKOFF_SECONDS:
            _LOG.warning("Rejected connection from %s:%s to %s (%s)", host, port, self._name, reason)
        else:
            _LOG.debug("Rejected connection from %s:%s to %s (%s)", host, port, self._name, reason)
        if len(self._reject_log) >= REJECT_LOG_MAX_HOSTS:
            self._reject_log.clear()
        self._reject_log[host] = now

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        peer = client_writer.get_extra_info("peername")
        host, port = (peer[0], peer[1]) if peer else ("unknown", 0)
        host = normalize_peer_host(str(host))

        if self._policy is not None:
            allowed, reason = self._policy.check(host)
            if not allowed:
                self._log_rejection(host, port, reason)
                client_writer.close()
                await client_writer.wait_closed()
                return

        conn_id = self._next_conn_id
        self._next_conn_id += 1
        state = _ConnState(host, port)
        self._conns[conn_id] = state
        _LOG.debug("Accepted connection %s:%s -> %s", host, port, self._name)
        try:
            try:
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    host=self._target_host,
                    port=self._target_port,
                )
            except Exception as exc:  # pragma: no cover - network failures hard to deterministically test
                self._log_failure(exc)
                client_writer.close()
                await client_writer.wait_closed()
                return

            # A successful upstream connection resets the failure backoff timer so
            # the next failure (after recovery) is reported promptly.
            self._last_failure_log = 0.0

            async def pipe(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
                from_client: bool,
            ) -> None:
                try:
                    while True:
                        data = await reader.read(65536)
                        if not data:
                            break
                        writer.write(data)
                        await writer.drain()
                        if from_client:
                            state.from_client += len(data)
                        else:
                            state.to_client += len(data)
                except asyncio.CancelledError:  # Propagate cancellation cleanly
                    raise
                except Exception as exc:  # pragma: no cover - defensive logging
                    _LOG.debug("Proxy %s pipe error: %s", self._name, exc)
                finally:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

            task_down = asyncio.create_task(pipe(client_reader, upstream_writer, True))
            task_up = asyncio.create_task(pipe(upstream_reader, client_writer, False))
            state.tasks = (task_down, task_up)
            grouped = asyncio.gather(task_down, task_up, return_exceptions=True)
            self._client_tasks.add(task_down)
            self._client_tasks.add(task_up)
            try:
                await grouped
            finally:
                self._client_tasks.discard(task_down)
                self._client_tasks.discard(task_up)
                _LOG.debug("Closed connection %s:%s -> %s", host, port, self._name)
        finally:
            self._conns.pop(conn_id, None)
