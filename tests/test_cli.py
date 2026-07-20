"""Tests for CLI helpers that do not require a running event loop / devices."""
from __future__ import annotations

import asyncio

import pytest

from shareadb.cli import (
    _format_status,
    _print_connection_info,
    _print_connection_on_ready,
    _status_logger,
    build_parser,
    get_local_ips,
    main_async,
)
from shareadb.device_manager import DeviceStatus, SessionState


def test_buildParser_defaults_setsExpectedDefaults() -> None:
    # Arrange / Act
    args = build_parser().parse_args([])

    # Assert
    assert args.listen_host == "0.0.0.0"
    assert args.device_tcp_port == 5555
    assert args.forward_base_port == 6000
    assert args.proxy_base_port == 7000
    assert args.poll_interval == 5.0
    assert args.status_interval == 30.0


def test_buildParser_parsesCustomPorts() -> None:
    # Arrange / Act
    args = build_parser().parse_args(
        ["--forward-base-port", "8000", "--proxy-base-port", "9000", "--include", "a", "b"]
    )

    # Assert
    assert args.forward_base_port == 8000
    assert args.proxy_base_port == 9000
    assert args.include == ["a", "b"]


def test_formatStatus_includesAllFields() -> None:
    # Arrange
    status = DeviceStatus(
        serial="3696ade",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
        model="Chery",
        product="Chery",
        last_error="boom",
    )

    # Act
    rendered = _format_status(status)

    # Assert
    assert "serial=3696ade" in rendered
    assert "state=running" in rendered
    assert "proxy=7000" in rendered
    assert "forward=6000" in rendered
    assert "tcp=5555" in rendered
    assert "model=Chery" in rendered
    assert "product=Chery" in rendered
    assert "error=boom" in rendered


def test_formatStatus_omitsOptionalWhenAbsent() -> None:
    # Arrange
    status = DeviceStatus(
        serial="x",
        state=SessionState.WAITING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
    )

    # Act
    rendered = _format_status(status)

    # Assert
    assert "model=" not in rendered
    assert "product=" not in rendered
    assert "error=" not in rendered


def test_getLocalIps_returnsListOfStrings() -> None:
    # Act
    ips = get_local_ips()

    # Assert -- always returns a non-empty list of IPv4 strings (falls back to 0.0.0.0)
    assert isinstance(ips, list)
    assert len(ips) >= 1
    assert all(isinstance(ip, str) for ip in ips)


class _FakeManager:
    """Minimal stand-in exposing statuses()/_stop_event for CLI render tests."""

    def __init__(self, statuses) -> None:
        self._statuses = statuses
        self._stop_event = asyncio.Event()

    def statuses(self):
        return list(self._statuses)


async def test_printConnectionInfo_rendersRunningDevices(caplog: pytest.LogCaptureFixture) -> None:
    # Arrange
    status = DeviceStatus(
        serial="3696ade",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
        model="Chery",
    )
    manager = _FakeManager([status])
    caplog.set_level("INFO")

    # Act
    _print_connection_info(manager, "0.0.0.0", 7000)

    # Assert
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "3696ade" in messages
    assert "Proxy port: 7000" in messages
    assert "ready for remote connection" in messages


async def test_printConnectionInfo_noDevices_logsWaitingMessage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    manager = _FakeManager([])
    caplog.set_level("INFO")

    # Act
    _print_connection_info(manager, "0.0.0.0", 7000)

    # Assert
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "No adb devices detected yet" in messages


async def test_statusLogger_emitsConnectionCommand(caplog: pytest.LogCaptureFixture) -> None:
    # Arrange
    status = DeviceStatus(
        serial="3696ade",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
    )
    manager = _FakeManager([status])
    caplog.set_level("INFO")

    # Act -- run one cycle then cancel
    task = asyncio.create_task(_status_logger(manager, 0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Assert
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "adb connect" in messages
    assert "3696ade" in messages


async def test_printConnectionOnReady_emitsWhenDeviceBecomesRunning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    status = DeviceStatus(
        serial="3696ade",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
    )
    manager = _FakeManager([status])
    caplog.set_level("INFO")

    # Act -- one iteration fires immediately, then the task is cancelled
    task = asyncio.create_task(_print_connection_on_ready(manager, "0.0.0.0", 7000))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Assert
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "ready for remote connection" in messages


async def test_mainAsync_startsManagerAndStopsCleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end lifecycle: manager.start -> await stop -> cancel tasks -> manager.stop."""

    # Arrange
    lifecycle = {"started": False, "stopped": False}

    class FakeManager:
        _stop_event = asyncio.Event()

        def __init__(self, *args, **kwargs) -> None:
            pass

        async def start(self) -> None:
            lifecycle["started"] = True

        async def stop(self) -> None:
            lifecycle["stopped"] = True

        def statuses(self):
            return []

    monkeypatch.setattr("shareadb.cli.DeviceManager", FakeManager)
    # Neutralize real signal-handler registration on this loop
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda *a, **kw: None)
    # Make the shutdown event resolve almost immediately so main_async returns
    class QuickEvent(asyncio.Event):
        async def wait(self) -> bool:
            await asyncio.sleep(0.01)
            return True

    monkeypatch.setattr("shareadb.cli.asyncio.Event", QuickEvent)
    args = build_parser().parse_args(["--status-interval", "0"])

    # Act
    await main_async(args)

    # Assert
    assert lifecycle["started"] is True
    assert lifecycle["stopped"] is True

