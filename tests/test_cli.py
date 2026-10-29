"""Tests for CLI helpers that do not require a running event loop / devices."""
from __future__ import annotations

import asyncio

import pytest

from shareadb.cli import (
    _format_status,
    _make_status_render_fn,
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

    def __init__(self, statuses, sync_age_value=None) -> None:
        self._statuses = statuses
        self._stop_event = asyncio.Event()
        self._sync_age_value = sync_age_value

    def statuses(self):
        return list(self._statuses)

    def connections(self):
        return []

    def sync_age(self):
        return self._sync_age_value


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


def test_buildParser_accessControlDefaults() -> None:
    args = build_parser().parse_args([])

    assert args.allow == []
    assert args.deny == []
    assert args.blocklist_file is None


def test_buildParser_parsesAccessControlEntries() -> None:
    args = build_parser().parse_args(
        ["--allow", "192.168.1.0/24", "10.0.0.5", "--deny", "172.16.0.0/16"]
    )

    assert args.allow == ["192.168.1.0/24", "10.0.0.5"]
    assert args.deny == ["172.16.0.0/16"]


def test_main_invalidDenyEntry_exitsWithUsageError(capsys) -> None:
    from shareadb.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--deny", "not-an-ip"])

    assert excinfo.value.code == 2
    assert "invalid access-control entry" in capsys.readouterr().err


def test_main_invalidBlocklistEntry_exitsWithUsageError(tmp_path, capsys) -> None:
    from shareadb.cli import main

    blocklist = tmp_path / "bl.txt"
    blocklist.write_text("garbage-entry\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(["--blocklist-file", str(blocklist)])

    assert excinfo.value.code == 2


def test_main_missingBlocklistFile_exitsWithUsageError(tmp_path, capsys) -> None:
    from shareadb.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--blocklist-file", str(tmp_path / "missing.txt")])

    assert excinfo.value.code == 2

def test_buildParser_parsesExcludeSerials() -> None:
    args = build_parser().parse_args(["--exclude", "scrcpy-dev", "abc123"])

    assert args.exclude == ["scrcpy-dev", "abc123"]


async def test_statusLogger_staleSyncAge_escalatesToWarning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stalled sync loop must be visible in the status log.

    Session states are served from a cache, so "state=running" alone can keep
    being printed while sharing is down; sync_age beyond the threshold turns
    the report into a WARNING so the log cannot look healthy indefinitely.
    """

    # Arrange
    status = DeviceStatus(
        serial="3696ade",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
    )
    manager = _FakeManager([status], sync_age_value=123.0)
    caplog.set_level("INFO")

    # Act -- threshold of 1s makes the 123s age stale
    task = asyncio.create_task(_status_logger(manager, 0.01, stale_after=1.0))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Assert
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "sync_age=123s" in messages
    assert any(
        r.levelname == "WARNING" and "stalled" in r.getMessage() for r in caplog.records
    )


async def test_statusLogger_freshSyncAge_staysQuiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fresh sync age: lines carry sync_age=... but no stale WARNING."""

    # Arrange
    status = DeviceStatus(
        serial="3696ade",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
    )
    manager = _FakeManager([status], sync_age_value=2.0)
    caplog.set_level("INFO")

    # Act -- default threshold (SYNC_STALE_MIN_SECONDS) far above 2s
    task = asyncio.create_task(_status_logger(manager, 0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Assert
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "sync_age=2s" in messages
    assert not any(
        r.levelname == "WARNING" and "stalled" in r.getMessage() for r in caplog.records
    )


# --------------------------------------------------------------------------- #
# In-place status board integration (TTY mode)
# --------------------------------------------------------------------------- #


class _FakeBoard:
    """Stands in for StatusBoard: records refresh() calls, exposes .enabled."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.refreshes = 0

    def refresh(self) -> None:
        self.refreshes += 1


def _running_status() -> DeviceStatus:
    return DeviceStatus(
        serial="3696ade",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
        model="Chery",
    )


async def test_statusLogger_boardMode_refreshesInPlaceWithoutStatusLines(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TTY mode: repaint the board, do NOT append a status line per cycle."""

    # Arrange
    manager = _FakeManager([_running_status()], sync_age_value=123.0)
    board = _FakeBoard(enabled=True)
    caplog.set_level("INFO")

    # Act
    task = asyncio.create_task(_status_logger(manager, 0.01, stale_after=1.0, board=board))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Assert -- repaints happened, no appended status rows, stale WARN kept
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert board.refreshes >= 1
    assert "command: adb connect" not in messages
    assert "No active adb devices detected" not in messages
    assert any(
        r.levelname == "WARNING" and "stalled" in r.getMessage() for r in caplog.records
    )


async def test_statusLogger_disabledBoard_fallsBackToLines(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Redirected/plain mode: one status line per cycle, board untouched."""

    # Arrange
    manager = _FakeManager([_running_status()], sync_age_value=2.0)
    board = _FakeBoard(enabled=False)
    caplog.set_level("INFO")

    # Act
    task = asyncio.create_task(_status_logger(manager, 0.01, board=board))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Assert
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert board.refreshes == 0
    assert "command: adb connect" in messages


async def test_printConnectionOnReady_boardMode_showsBoardNotBanner(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With the board active, readiness triggers an immediate repaint plus a
    one-line marker instead of the multi-line banner."""

    # Arrange
    manager = _FakeManager([_running_status()])
    board = _FakeBoard(enabled=True)
    caplog.set_level("INFO")

    # Act
    task = asyncio.create_task(_print_connection_on_ready(manager, "0.0.0.0", 7000, board))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Assert
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert board.refreshes >= 1
    assert "Device ready: 3696ade" in messages
    assert "ready for remote connection" not in messages


def test_makeStatusRenderFn_rendersTableWithConnectCommands() -> None:
    """The board render callback produces the framed table with commands."""

    # Arrange
    manager = _FakeManager([_running_status()], sync_age_value=2.0)

    # Act
    render = _make_status_render_fn(manager, stale_after=90.0, ips_ttl=0.0)
    text = render(72)

    # Assert
    assert "adb connect " in text
    assert "3696ade" in text
    assert "运行中" in text
    assert text.startswith("┌")


def test_buildParser_plainStatusFlag() -> None:
    assert build_parser().parse_args([]).plain_status is False
    assert build_parser().parse_args(["--plain-status"]).plain_status is True
