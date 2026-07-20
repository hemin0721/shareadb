"""Tests for TCPProxyServer, focusing on upstream-failure log backoff."""
from __future__ import annotations

import asyncio
import logging
import socket

import pytest

from shareadb.tcp_proxy import LOG_BACKOFF_SECONDS, TCPProxyServer


def _free_port() -> int:
    """Reserve and release an ephemeral port so it (briefly) refuses connections."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


async def _echo_server() -> tuple:
    """Start an echo TCP server; return (server, actual_port)."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def _trigger_failure(host: str, port: int) -> None:
    """Connect to the proxy and wait until it closes us (upstream failure path)."""

    reader, writer = await asyncio.open_connection(host, port)
    try:
        await asyncio.wait_for(reader.read(65536), timeout=2.0)
    except (asyncio.TimeoutError, ConnectionError):
        pass
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


def _count_warnings(records, fragment: str) -> int:
    return sum(
        1
        for r in records
        if r.levelno == logging.WARNING and fragment in r.getMessage()
    )


async def test_handleClient_upstreamFailure_logsWarningOncePerBackoffWindow(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange -- proxy targets a closed port so every upstream connect fails
    target = _free_port()
    proxy_port = _free_port()
    proxy = TCPProxyServer("127.0.0.1", proxy_port, "127.0.0.1", target, name="ut")
    await proxy.start()
    caplog.set_level(logging.DEBUG)
    assert LOG_BACKOFF_SECONDS > 0

    # Act -- two failures, serialized, well inside one backoff window
    await _trigger_failure("127.0.0.1", proxy_port)
    await _trigger_failure("127.0.0.1", proxy_port)

    # Assert -- exactly one WARNING despite two connection attempts (no flood)
    assert _count_warnings(caplog.records, "failed to connect to target") == 1

    await proxy.stop()


async def test_handleClient_upstreamSuccess_resetsFailureBackoffTimer() -> None:
    # Arrange
    echo, echo_port = await _echo_server()
    proxy_port = _free_port()
    proxy = TCPProxyServer("127.0.0.1", proxy_port, "127.0.0.1", echo_port, name="echo")
    await proxy.start()
    proxy._last_failure_log = 999999.0  # simulate a recently-logged failure

    # Act -- one successful proxied connection
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(b"x")
    await writer.drain()
    await asyncio.wait_for(reader.readexactly(1), timeout=2.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    # Assert -- the backoff timer was reset so the next failure will warn again
    assert proxy._last_failure_log == 0.0

    await proxy.stop()
    echo.close()
    await echo.wait_closed()


async def test_handleClient_upstreamSuccess_proxiesBytesBidirectionally() -> None:
    # Arrange
    echo, echo_port = await _echo_server()
    proxy_port = _free_port()
    proxy = TCPProxyServer("127.0.0.1", proxy_port, "127.0.0.1", echo_port, name="echo")
    await proxy.start()

    # Act -- send bytes through the proxy and read the echo back
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    payload = b"hello-shareadb"
    writer.write(payload)
    await writer.drain()
    echoed = await asyncio.wait_for(reader.readexactly(len(payload)), timeout=2.0)

    # Assert -- data flowed end to end (covers the pipe happy path)
    assert echoed == payload

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    await proxy.stop()
    echo.close()
    await echo.wait_closed()
