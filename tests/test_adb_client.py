"""Tests for the ADBClient wrapper and device-list parsing."""
from __future__ import annotations

import asyncio

import pytest

from shareadb.adb_client import ADBClient, _parse_device_list

from tests.fakes import RunRecorder


async def test_waitForDevice_passesSerialAndTimeout_invokesRunWithCheck() -> None:
    # Arrange
    client = ADBClient("adb")
    rec = RunRecorder()
    client.run = rec  # type: ignore[method-assign]

    # Act
    await client.wait_for_device("3696ade", timeout=12.0)

    # Assert
    assert rec.calls[0]["args"] == ["wait-for-device"]
    assert rec.calls[0]["device_serial"] == "3696ade"
    assert rec.calls[0]["timeout"] == 12.0
    assert rec.calls[0]["check"] is True


async def test_reconnectOffline_nonZeroExit_checkFalse_doesNotRaise() -> None:
    # Arrange
    client = ADBClient("adb")
    rec = RunRecorder()
    client.run = rec  # type: ignore[method-assign]
    rec.queue(stderr="some error", returncode=1)  # would raise if check=True

    # Act -- must not raise even though adb reported a failure
    await client.reconnect_offline(timeout=5.0)

    # Assert
    assert rec.calls[0]["args"] == ["reconnect", "offline"]
    assert rec.calls[0]["check"] is False
    assert rec.calls[0]["timeout"] == 5.0


def test_parseDeviceList_readyDevice_returnsParsedEntry() -> None:
    # Arrange
    raw = (
        "List of devices attached\n"
        "3696ade device product:Chery model:Chery device:chery transport_id:1\n"
    )

    # Act
    devices = _parse_device_list(raw)

    # Assert
    assert len(devices) == 1
    device = devices[0]
    assert device.serial == "3696ade"
    assert device.state == "device"
    assert device.is_ready is True
    assert device.model == "Chery"
    assert device.product == "Chery"
    assert device.transport_id == "1"


def test_parseDeviceList_offlineDevice_isReadyFalse() -> None:
    # Arrange
    raw = "List of devices attached\n3696ade offline\n"

    # Act
    devices = _parse_device_list(raw)

    # Assert
    assert devices[0].serial == "3696ade"
    assert devices[0].state == "offline"
    assert devices[0].is_ready is False


def test_parseDeviceList_emptyInput_returnsEmpty() -> None:
    # Arrange / Act / Assert
    assert _parse_device_list("") == []
    assert _parse_device_list("List of devices attached\n") == []
    assert _parse_device_list("List of devices attached\n\n") == []


async def test_run_commandExceedsTimeout_raisesTimeoutErrorAndKillsProcess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bounded-timeout contract that keeps _start (and shutdown) responsive.

    ``run`` wraps ``communicate`` in ``asyncio.wait_for``; on timeout it must
    raise ``TimeoutError`` and kill the subprocess so a wedged adb invocation
    can never block the event loop or shutdown indefinitely.
    """

    # Arrange
    client = ADBClient("adb")

    class FakeProc:
        def __init__(self) -> None:
            self.killed = False

        @property
        def returncode(self) -> int:
            return 0

        async def communicate(self):
            await asyncio.sleep(100)  # never completes within the timeout

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            return 0

    proc = FakeProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("shareadb.adb_client.asyncio.create_subprocess_exec", fake_exec)

    # Act / Assert
    with pytest.raises(asyncio.TimeoutError):
        await client.run(["devices"], timeout=0.1)
    assert proc.killed is True


async def test_run_nonZeroExit_checkTrue_raisesADBCommandError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    client = ADBClient("adb")

    class FakeProc:
        returncode = 1

        async def communicate(self):
            return b"out", b"err"

        def kill(self) -> None:  # pragma: no cover - not reached
            ...

        async def wait(self) -> int:  # pragma: no cover - not reached
            return 1

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr("shareadb.adb_client.asyncio.create_subprocess_exec", fake_exec)

    # Act / Assert
    from shareadb.adb_client import ADBCommandError

    with pytest.raises(ADBCommandError):
        await client.run(["frobnicate"], check=True)


async def test_forward_replacesExistingBeforeAdding() -> None:
    # Arrange
    client = ADBClient("adb")
    rec = RunRecorder()
    client.run = rec  # type: ignore[method-assign]

    # Act
    await client.forward("3696ade", 6000, 5555)

    # Assert -- remove is issued first (check-agnostic), then the new forward
    assert rec.calls[0]["args"] == ["forward", "--remove", "tcp:6000"]
    assert rec.calls[0]["check"] is False
    assert rec.calls[1]["args"] == ["forward", "tcp:6000", "tcp:5555"]
    assert rec.calls[1]["device_serial"] == "3696ade"


async def test_listDevices_parsesViaRun() -> None:
    # Arrange
    client = ADBClient("adb")
    rec = RunRecorder()
    client.run = rec  # type: ignore[method-assign]
    rec.queue(
        stdout="List of devices attached\n3696ade device model:Chery\n",
        returncode=0,
    )

    # Act
    devices = await client.list_devices()

    # Assert
    assert rec.calls[0]["args"] == ["devices", "-l"]
    assert len(devices) == 1
    assert devices[0].serial == "3696ade"
    assert devices[0].is_ready is True


def test_detect_usesProvidedPathWhenItExists(tmp_path) -> None:
    # Arrange
    fake_adb = tmp_path / "adb"
    fake_adb.write_text("#!/bin/sh\n")

    # Act
    resolved = ADBClient.detect(str(fake_adb))

    # Assert
    assert resolved == fake_adb


def test_detect_providedPathMissing_raisesFileNotFoundError() -> None:
    # Act / Assert
    with pytest.raises(FileNotFoundError):
        ADBClient.detect("/nonexistent/adb-binary")

