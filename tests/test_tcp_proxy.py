"""Tests for TCPProxyServer: upstream-failure log backoff + access control."""
from __future__ import annotations

import asyncio
import logging
import socket

import pytest

from shareadb.access import AccessPolicy
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


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


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

async def test_handleClient_deniedByPolicy_closedWithoutUpstream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange -- policy denies every non-loopback... but the test client IS
    # loopback, so deny loopback itself to exercise the rejection path.
    echo, echo_port = await _echo_server()
    proxy_port = _free_port()
    policy = AccessPolicy(deny=["127.0.0.0/8"])
    proxy = TCPProxyServer("127.0.0.1", proxy_port, "127.0.0.1", echo_port, name="echo", policy=policy)
    await proxy.start()
    caplog.set_level(logging.DEBUG)

    # Act -- connect, send bytes, expect the proxy to close us with no echo.
    # The abrupt close may surface as EOF *or* ConnectionResetError (the proxy
    # drops the socket while our sent bytes are still unread); both mean the
    # connection was terminated without being served.
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(b"should-not-echo")
    await writer.drain()
    try:
        data = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    except ConnectionResetError:
        data = b""

    # Assert -- EOF (b"") instead of an echo, rejection logged and counted.
    assert data == b""
    assert policy.rejected.get("127.0.0.1") == 1
    assert _count_warnings(caplog.records, "Rejected connection") == 1
    assert proxy.connections() == []

    await _close(writer)
    await proxy.stop()
    echo.close()
    await echo.wait_closed()


async def test_handleClient_deniedRetries_logOncePerBackoffWindow(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    echo, echo_port = await _echo_server()
    proxy_port = _free_port()
    policy = AccessPolicy(deny=["127.0.0.0/8"])
    proxy = TCPProxyServer("127.0.0.1", proxy_port, "127.0.0.1", echo_port, name="echo", policy=policy)
    await proxy.start()
    caplog.set_level(logging.DEBUG)

    # Act -- two rejected attempts, serialized, inside one backoff window
    for _ in range(2):
        _reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        await asyncio.wait_for(_reader.read(65536), timeout=2.0)
        await _close(writer)

    # Assert -- one WARNING, two counted rejections
    assert _count_warnings(caplog.records, "Rejected connection") == 1
    assert policy.rejected.get("127.0.0.1") == 2

    await proxy.stop()
    echo.close()
    await echo.wait_closed()


async def test_connections_trackActiveClientWithByteCounts() -> None:
    # Arrange
    echo, echo_port = await _echo_server()
    proxy_port = _free_port()
    policy = AccessPolicy(allow=["127.0.0.0/8"])
    proxy = TCPProxyServer("127.0.0.1", proxy_port, "127.0.0.1", echo_port, name="echo", policy=policy)
    await proxy.start()

    # Act -- push bytes through and read them back
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    payload = b"0123456789"
    writer.write(payload)
    await writer.drain()
    echoed = await asyncio.wait_for(reader.readexactly(len(payload)), timeout=2.0)
    assert echoed == payload
    await asyncio.sleep(0.1)  # let the pipe tasks account the drained bytes

    conns = proxy.connections()

    # Assert -- exactly one tracked connection with both byte directions
    assert len(conns) == 1
    conn = conns[0]
    assert conn.peer_host == "127.0.0.1"
    assert conn.peer_port > 0
    assert conn.proxy_name == "echo"
    assert conn.bytes_from_client == len(payload)
    assert conn.bytes_to_client == len(payload)

    # Once the client goes away, tracking empties again.
    await _close(writer)
    await asyncio.sleep(0.1)
    assert proxy.connections() == []

    await proxy.stop()
    echo.close()
    await echo.wait_closed()


async def test_kick_closesMatchingConnections() -> None:
    # Arrange
    echo, echo_port = await _echo_server()
    proxy_port = _free_port()
    proxy = TCPProxyServer("127.0.0.1", proxy_port, "127.0.0.1", echo_port, name="echo")
    await proxy.start()

    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(b"ping")
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(4), timeout=2.0) == b"ping"
    assert len(proxy.connections()) == 1

    # Act -- kick the (only) peer subnet
    kicked = proxy.kick("127.0.0.0/8")

    # Assert -- the client sees the connection close, tracking empties
    assert kicked == 1
    data = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    assert data == b""
    await asyncio.sleep(0.1)
    assert proxy.connections() == []
    assert proxy.kick("127.0.0.0/8") == 0  # nothing left to kick

    await _close(writer)
    await proxy.stop()
    echo.close()
    await echo.wait_closed()


async def test_stop_withActiveClientConnection_returnsPromptly() -> None:
    """``stop()`` must complete even while a client connection is established.

    On Python >= 3.12 ``Server.wait_closed()`` waits for the accepted connection
    handlers, which are blocked in ``read()`` until cancelled -- so ``stop()``
    has to cancel them first. Otherwise the first dead-forward heal
    (``mark_unavailable`` -> ``proxy.stop``) would deadlock the sync loop and
    leave the session stuck at "running" until the script is restarted.
    """

    # Arrange -- upstream echo server so the client connection stays live
    echo, echo_port = await _echo_server()
    proxy = TCPProxyServer(
        "127.0.0.1", _free_port(), "127.0.0.1", echo_port, name="stop-test"
    )
    await proxy.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.address[1])
    writer.write(b"ping")
    await writer.drain()
    assert await asyncio.wait_for(reader.read(65536), timeout=2.0) == b"ping"
    assert len(proxy.connections()) == 1

    # Act -- must not hang (bounded by wait_for, not by the handlers)
    await asyncio.wait_for(proxy.stop(), timeout=3.0)

    # Assert -- connection bookkeeping drained, listener gone
    await asyncio.sleep(0.05)  # let the handler's finally-blocks run
    assert proxy.connections() == []
    await _close(writer)
    echo.close()
    await echo.wait_closed()
