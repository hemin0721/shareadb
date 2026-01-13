"""Async TCP proxy to bridge clients to forwarded adb ports."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

_LOG = logging.getLogger(__name__)


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
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._target_host = target_host
        self._target_port = target_port
        self._server: Optional[asyncio.AbstractServer] = None
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._name = name or f"proxy:{listen_port}->{target_port}"
        self._lock = asyncio.Lock()

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

    async def stop(self) -> None:
        async with self._lock:
            if not self._server:
                return
            server, self._server = self._server, None
            server.close()
            await server.wait_closed()
            for task in list(self._client_tasks):
                task.cancel()
            if self._client_tasks:
                await asyncio.gather(*self._client_tasks, return_exceptions=True)
            self._client_tasks.clear()
            _LOG.info("Stopped proxy %s", self._name)

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        peer = client_writer.get_extra_info("peername")
        _LOG.debug("Accepted connection %s -> %s", peer, self._name)
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                host=self._target_host,
                port=self._target_port,
            )
        except Exception as exc:  # pragma: no cover - network failures hard to deterministically test
            _LOG.warning(
                "Proxy %s failed to connect to target %s:%s: %s",
                self._name,
                self._target_host,
                self._target_port,
                exc,
            )
            client_writer.close()
            await client_writer.wait_closed()
            return

        async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
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

        task_down = asyncio.create_task(pipe(client_reader, upstream_writer))
        task_up = asyncio.create_task(pipe(upstream_reader, client_writer))
        grouped = asyncio.gather(task_down, task_up, return_exceptions=True)
        self._client_tasks.add(task_down)
        self._client_tasks.add(task_up)
        try:
            await grouped
        finally:
            self._client_tasks.discard(task_down)
            self._client_tasks.discard(task_up)
            _LOG.debug("Closed connection %s -> %s", peer, self._name)
