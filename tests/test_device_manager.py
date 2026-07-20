"""Tests for ADBDeviceSession startup / reconnect logic and DeviceManager healing."""
from __future__ import annotations

import asyncio

import pytest

from shareadb.adb_client import ADBDeviceInfo
from shareadb.device_manager import (
    SETTLE_DELAY,
    START_MAX_ATTEMPTS,
    ADBDeviceSession,
    DeviceManager,
    DevicePorts,
    SessionState,
)

from tests.fakes import FakeADBClient, FakeProxy, RecordingSleeper, ScriptedProbe


def make_session(
    events=None,
    *,
    probe_results=(True,),
    adb=None,
    serial="3696ade",
    forward_port=6000,
    proxy_port=7000,
):
    """Build an ADBDeviceSession wired to fakes; returns (session, adb, probe, sleeper, events)."""

    events = [] if events is None else events
    adb = adb or FakeADBClient(events=events)
    probe = ScriptedProbe(probe_results, events=events)
    sleeper = RecordingSleeper(events=events)
    session = ADBDeviceSession(
        adb,
        serial,
        device_tcp_port=5555,
        forward_port=forward_port,
        proxy_port=proxy_port,
        listen_host="127.0.0.1",
        port_probe=probe,
        sleep_fn=sleeper,
    )
    session._proxy = FakeProxy()  # type: ignore[assignment]
    return session, adb, probe, sleeper, events


def _pos(events, needle):
    return events.index(needle)


async def test_start_ordersTcpipBeforeWaitForDeviceBeforeForward() -> None:
    # Arrange
    session, adb, probe, sleeper, events = make_session(probe_results=(True,))

    # Act
    await session._start()

    # Assert -- adb command ordering
    seq = adb.adb_sequence()
    assert seq.index("tcpip") < seq.index("reconnect offline")
    assert seq.index("reconnect offline") < seq.index("wait-for-device")
    assert seq.index("wait-for-device") < seq.index("forward")


async def test_start_settleSleepInvokedBetweenWaitForDeviceAndForward() -> None:
    # Arrange
    session, adb, probe, sleeper, events = make_session(probe_results=(True,))

    # Act
    await session._start()

    # Assert -- a SETTLE_DELAY sleep sits strictly between wait-for-device and
    # forward (there is also a post-tcpip sleep earlier, which is expected).
    wait_pos = _pos(events, ("adb", "wait-for-device"))
    forward_pos = _pos(events, ("adb", "forward"))
    between = [
        i for i, e in enumerate(events)
        if e[0] == "sleep" and wait_pos < i < forward_pos
    ]
    assert between, "expected a settle sleep between wait-for-device and forward"
    assert events[between[0]] == ("sleep", SETTLE_DELAY)


async def test_start_postTcpipSleepBeforeFirstWaitForDevice() -> None:
    # Arrange
    session, adb, probe, sleeper, events = make_session(probe_results=(True,))

    # Act
    await session._start()

    # Assert -- tcpip is followed by a stabilization sleep before wait-for-device
    # so the latter observes the adbd restart's offline window.
    tcpip_pos = _pos(events, ("adb", "tcpip"))
    wait_pos = _pos(events, ("adb", "wait-for-device"))
    post = [i for i, e in enumerate(events) if e[0] == "sleep" and tcpip_pos < i < wait_pos]
    assert post, "expected a post-tcpip stabilization sleep before wait-for-device"
    from shareadb.device_manager import POST_TCPIP_DELAY

    assert events[post[0]] == ("sleep", POST_TCPIP_DELAY)


async def test_start_coldBootReconnect_succeedsOnFirstAttempt_startsProxy() -> None:
    # Arrange
    session, adb, probe, sleeper, events = make_session(probe_results=(True,))

    # Act
    await session._start()

    # Assert
    assert session._proxy.started is True
    assert probe.history == [True]
    assert adb.adb_sequence().count("forward") == 1


async def test_start_probeVerifiedBeforeProxyStart() -> None:
    # Arrange -- probe fails on every attempt; proxy must never start
    session, adb, probe, sleeper, events = make_session(
        probe_results=tuple(False for _ in range(START_MAX_ATTEMPTS))
    )

    # Act
    with pytest.raises(Exception):
        await session._start()

    # Assert
    assert session._proxy.started is False


async def test_start_probeFailsOnce_retriesAndSucceeds() -> None:
    # Arrange
    session, adb, probe, sleeper, events = make_session(probe_results=(False, True))

    # Act
    await session._start()

    # Assert
    assert session._proxy.started is True
    assert probe.history == [False, True]
    assert adb.adb_sequence().count("forward") == 2
    # tcpip must NOT be re-run on retry -- re-running it would keep restarting
    # adbd and prevent a freshly-booted device from stabilizing.
    assert adb.adb_sequence().count("tcpip") == 1


async def test_start_allAttemptsFail_raisesAndEnsuresRunningSetsError() -> None:
    # Arrange
    info = ADBDeviceInfo(serial="3696ade", state="device")
    session, adb, probe, sleeper, events = make_session(
        probe_results=tuple(False for _ in range(START_MAX_ATTEMPTS))
    )

    # Act
    with pytest.raises(Exception):
        await session.ensure_running(info)

    # Assert
    assert session.state is SessionState.ERROR
    assert session.last_error is not None
    assert session._proxy.started is False
    assert probe.history == [False] * START_MAX_ATTEMPTS


async def test_start_waitForDeviceTimeoutOnce_retriesAndSucceeds() -> None:
    # Arrange -- first wait-for-device times out, second succeeds
    events = []
    adb = FakeADBClient(events=events)
    adb.fail("wait-for-device", asyncio.TimeoutError())
    session, _, probe, sleeper, _ = make_session(
        events=events, probe_results=(True,), adb=adb
    )

    # Act
    await session._start()

    # Assert
    assert session._proxy.started is True
    assert adb.adb_sequence().count("wait-for-device") == 2


# --------------------------------------------------------------------------- #
# Liveness self-healing (DeviceManager._sync_devices)
# --------------------------------------------------------------------------- #


def make_manager(events, *, probe_results, forward_base=6000, proxy_base=7000, local_ips=None):
    """Build a DeviceManager over a FakeADBClient with injected probe/sleep."""

    adb = FakeADBClient(events=events)
    probe = ScriptedProbe(probe_results, events=events)
    sleeper = RecordingSleeper(events=events)
    manager = DeviceManager(
        adb,
        listen_host="127.0.0.1",
        device_tcp_port=5555,
        forward_base_port=forward_base,
        proxy_base_port=proxy_base,
        poll_interval=5.0,
        port_probe=probe,
        sleep_fn=sleeper,
        local_ips=local_ips,
    )
    return manager, adb, probe, sleeper


async def test_isForwardHealthy_delegatesToInjectedProbe() -> None:
    # Arrange
    probe = ScriptedProbe((False,))
    session = ADBDeviceSession(
        FakeADBClient(),
        "3696ade",
        device_tcp_port=5555,
        forward_port=6000,
        proxy_port=7000,
        listen_host="127.0.0.1",
        port_probe=probe,
    )

    # Act
    result = await session.is_forward_healthy()

    # Assert
    assert result is False
    assert probe.history == [False]


async def test_syncDevices_runningSessionWithDeadForward_marksUnavailableThenResetsUp() -> None:
    # Arrange -- probe: setup(ok) -> healing(dead) -> resetup(ok)
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, False, True), forward_base=16000, proxy_base=17000
    )
    adb.set_devices(("3696ade", "device"))

    # Act
    await manager._sync_devices()  # cycle 1: setup -> RUNNING
    assert manager._sessions["3696ade"].state is SessionState.RUNNING
    await manager._sync_devices()  # cycle 2: healing detects dead forward -> WAITING
    assert manager._sessions["3696ade"].state is SessionState.WAITING
    await manager._sync_devices()  # cycle 3: resetup -> RUNNING
    assert manager._sessions["3696ade"].state is SessionState.RUNNING

    # Assert -- forward was established twice (initial + resetup)
    assert adb.adb_sequence().count("forward") == 2
    await manager.stop()


async def test_syncDevices_runningSessionWithHealthyForward_doesNotTouchSession() -> None:
    # Arrange -- probe always healthy after setup
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, True, True), forward_base=16100, proxy_base=17100
    )
    adb.set_devices(("3696ade", "device"))

    # Act
    await manager._sync_devices()
    await manager._sync_devices()
    await manager._sync_devices()

    # Assert -- no resetup; single forward; still RUNNING
    assert manager._sessions["3696ade"].state is SessionState.RUNNING
    assert adb.adb_sequence().count("forward") == 1
    await manager.stop()


async def test_syncDevices_deadForwardButDeviceAbsent_handledByTeardownNotHealing() -> None:
    # Arrange
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True,), forward_base=16200, proxy_base=17200
    )
    adb.set_devices(("3696ade", "device"))

    # Act
    await manager._sync_devices()  # setup -> RUNNING, probe consumed once
    assert manager._sessions["3696ade"].state is SessionState.RUNNING
    adb.set_devices()  # device disappears
    await manager._sync_devices()  # teardown path fires; healing must not

    # Assert -- torn down (WAITING) by the not-listed branch, forward only once,
    # and the probe was never consulted by the healing pass.
    assert manager._sessions["3696ade"].state is SessionState.WAITING
    assert adb.adb_sequence().count("forward") == 1
    assert probe.history == [True]
    await manager.stop()


# --------------------------------------------------------------------------- #
# Self-referential (reflection) device filtering
# --------------------------------------------------------------------------- #


def _manager_for_reflection(local_ips) -> DeviceManager:
    return DeviceManager(
        FakeADBClient(),
        listen_host="0.0.0.0",
        device_tcp_port=5555,
        forward_base_port=6000,
        proxy_base_port=7000,
        poll_interval=5.0,
        local_ips=local_ips,
    )


def test_isReflection_localIpAndServedPort_returnsTrue() -> None:
    # Arrange
    manager = _manager_for_reflection(["10.0.28.128"])

    # Act / Assert
    assert manager._is_reflection("10.0.28.128:7000", {7000, 7001}) is True


def test_isReflection_remoteIpOnServedPort_returnsFalse() -> None:
    # Arrange
    manager = _manager_for_reflection(["10.0.28.128"])

    # Act / Assert -- host not local => a real remote device, not a reflection
    assert manager._is_reflection("10.0.99.99:7000", {7000}) is False


def test_isReflection_localIpNonProxyPort_returnsFalse() -> None:
    # Arrange
    manager = _manager_for_reflection(["10.0.28.128"])

    # Act / Assert -- port 5555 is a real device port, not our proxy
    assert manager._is_reflection("10.0.28.128:5555", {7000}) is False


def test_isReflection_usbSerial_returnsFalse() -> None:
    # Arrange
    manager = _manager_for_reflection(["10.0.28.128"])

    # Act / Assert -- USB serials have no host:port form
    assert manager._is_reflection("3696ade", {7000}) is False


async def test_syncDevices_selfReferentialProxyDevice_isSkippedNotShared() -> None:
    # Arrange
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, True, True), forward_base=6000, proxy_base=7000,
        local_ips=["10.0.28.128"],
    )
    adb.set_devices(("3696ade", "device"))
    await manager._sync_devices()  # 3696ade -> proxy 7000

    # Act -- a client connected back to our own proxy: 10.0.28.128:7000
    adb.set_devices(("3696ade", "device"), ("10.0.28.128:7000", "device"))
    await manager._sync_devices()

    # Assert -- reflection never gets a session / forward; real device untouched
    assert "10.0.28.128:7000" not in manager._sessions
    assert "3696ade" in manager._sessions
    assert adb.adb_sequence().count("forward") == 1  # only 3696ade, never the reflection
    await manager.stop()


async def test_syncDevices_remoteDeviceOnServedPort_isSharedNotSkipped() -> None:
    # Arrange -- remote-IP device on a port that coincidentally equals a served
    # proxy port is a real device, not a reflection.
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, True, True), forward_base=6000, proxy_base=7000,
        local_ips=["10.0.28.128"],
    )
    adb.set_devices(("3696ade", "device"), ("10.0.99.99:7000", "device"))

    # Act
    await manager._sync_devices()

    # Assert -- both shared (3696ade->7000, remote->7001)
    assert "3696ade" in manager._sessions
    assert "10.0.99.99:7000" in manager._sessions
    await manager.stop()


async def test_dropReflections_releasesPreAllocatedPortsForUpgradeScenario() -> None:
    # Arrange -- simulate a phantom that a pre-fix run had already allocated
    # ports for; on upgrade it must be torn down and its ports released.
    manager = _manager_for_reflection(["10.0.28.128"])
    phantom = "10.0.28.128:7000"
    manager._port_state["3696ade"] = DevicePorts(forward=6000, proxy=7000)
    manager._port_state[phantom] = DevicePorts(forward=6002, proxy=7002)
    ready = {phantom: ADBDeviceInfo(serial=phantom, state="device")}

    # Act
    await manager._drop_reflections(ready)

    # Assert
    assert phantom not in ready
    assert phantom not in manager._port_state  # ports released
    assert "3696ade" in manager._port_state  # legit session untouched



