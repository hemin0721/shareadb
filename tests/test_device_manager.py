"""Tests for ADBDeviceSession startup / reconnect logic and DeviceManager healing."""
from __future__ import annotations

import asyncio

import pytest

from shareadb.access import AccessPolicy
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


async def test_start_directForwardSucceeds_neverRunsTcpip() -> None:
    # Arrange -- device already in adb TCP mode (persist.adb.tcp.port set):
    # forwarding succeeds on the first try, so the device must not be touched.
    session, adb, probe, sleeper, events = make_session(probe_results=(True,))

    # Act
    await session._start()

    # Assert -- no tcpip (no adbd restart, local scrcpy keeps its transport)
    seq = adb.adb_sequence()
    assert "tcpip" not in seq
    assert seq.index("reconnect offline") < seq.index("wait-for-device")
    assert seq.index("wait-for-device") < seq.index("forward")
    assert session._proxy.started is True


async def test_start_directForwardFails_fallsBackToTcpipThenForward() -> None:
    # Arrange -- first probe fails (device in USB mode), so the setup falls
    # back to `adb tcpip` exactly once, then forwards successfully.
    session, adb, probe, sleeper, events = make_session(probe_results=(False, True))

    # Act
    await session._start()

    # Assert -- adb command ordering: the failed direct attempt happens first,
    # then tcpip restarts adbd once, then the retry path brings the forward up.
    seq = adb.adb_sequence()
    assert seq.index("forward") < seq.index("tcpip")  # direct attempt tried first
    tcpip_pos = seq.index("tcpip")
    assert seq.count("tcpip") == 1
    # Everything after tcpip is the retry path: reconnect -> wait -> forward
    reconnect_pos = seq.index("reconnect offline", tcpip_pos)
    wait_pos = seq.index("wait-for-device", tcpip_pos)
    assert tcpip_pos < reconnect_pos < wait_pos
    assert wait_pos < seq.index("forward", tcpip_pos)
    assert session._proxy.started is True


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
    # Arrange -- direct forward fails, forcing the tcpip fallback
    session, adb, probe, sleeper, events = make_session(probe_results=(False, True))

    # Act
    await session._start()

    # Assert -- tcpip is followed by a stabilization sleep before wait-for-device
    # so the latter observes the adbd restart's offline window.
    tcpip_pos = _pos(events, ("adb", "tcpip"))
    wait_pos = next(
        i for i, e in enumerate(events)
        if i > tcpip_pos and e == ("adb", "wait-for-device")
    )
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
    # Arrange -- every probe fails: one direct attempt + START_MAX_ATTEMPTS
    # retries after the tcpip fallback.
    info = ADBDeviceInfo(serial="3696ade", state="device")
    session, adb, probe, sleeper, events = make_session(
        probe_results=tuple(False for _ in range(START_MAX_ATTEMPTS + 1))
    )

    # Act
    with pytest.raises(Exception):
        await session.ensure_running(info)

    # Assert
    assert session.state is SessionState.ERROR
    assert session.last_error is not None
    assert session._proxy.started is False
    assert probe.history == [False] * (START_MAX_ATTEMPTS + 1)


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
    # Arrange -- probe: setup(ok) -> healing(dead twice: original + recheck)
    # -> resetup(ok)
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, False, False, True), forward_base=16000, proxy_base=17000
    )
    adb.set_devices(("3696ade", "device"))

    # Act
    await manager._sync_devices()  # cycle 1: setup -> RUNNING
    assert manager._sessions["3696ade"].state is SessionState.RUNNING
    await manager._sync_devices()  # cycle 2: healing, double-check still dead -> WAITING
    assert manager._sessions["3696ade"].state is SessionState.WAITING
    await manager._sync_devices()  # cycle 3: resetup -> RUNNING
    assert manager._sessions["3696ade"].state is SessionState.RUNNING

    # Assert -- forward was established twice (initial + resetup)
    assert adb.adb_sequence().count("forward") == 2
    await manager.stop()


async def test_syncDevices_transientProbeFailure_flapGuardKeepsSessionRunning() -> None:
    # Arrange -- probe: setup(ok) -> healing: first probe dead, recheck alive.
    # A single failed probe must NOT tear the session down: re-running setup
    # used to restart adbd and kick local users like scrcpy off the device.
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, False, True, True), forward_base=16150, proxy_base=17150
    )
    adb.set_devices(("3696ade", "device"))

    # Act
    await manager._sync_devices()  # cycle 1: setup -> RUNNING
    await manager._sync_devices()  # cycle 2: dead probe, recheck alive -> keep

    # Assert -- session stayed RUNNING, no teardown/forward churn
    assert manager._sessions["3696ade"].state is SessionState.RUNNING
    assert adb.adb_sequence().count("forward") == 1
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
        events, probe_results=(True, True, True), forward_base=16500, proxy_base=17500,
        local_ips=["10.0.28.128"],
    )
    adb.set_devices(("3696ade", "device"))
    await manager._sync_devices()  # 3696ade -> proxy 17500

    # Act -- a client connected back to our own proxy: 10.0.28.128:17500
    adb.set_devices(("3696ade", "device"), ("10.0.28.128:17500", "device"))
    await manager._sync_devices()

    # Assert -- reflection never gets a session / forward; real device untouched
    assert "10.0.28.128:17500" not in manager._sessions
    assert "3696ade" in manager._sessions
    assert adb.adb_sequence().count("forward") == 1  # only 3696ade, never the reflection
    await manager.stop()


async def test_syncDevices_remoteDeviceOnServedPort_isSharedNotSkipped() -> None:
    # Arrange -- remote-IP device on a port that coincidentally equals a served
    # proxy port is a real device, not a reflection.
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, True, True), forward_base=16510, proxy_base=17510,
        local_ips=["10.0.28.128"],
    )
    adb.set_devices(("3696ade", "device"), ("10.0.99.99:17510", "device"))

    # Act
    await manager._sync_devices()

    # Assert -- both shared (3696ade->17510, remote->17511)
    assert "3696ade" in manager._sessions
    assert "10.0.99.99:17510" in manager._sessions
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





async def test_policyIsSharedWithSessionProxies_andAggregatesConnections() -> None:
    # Arrange -- one ready device; the manager gets an access policy
    events = []
    adb = FakeADBClient(events=events)
    adb.set_devices(("3696ade", "device"))
    policy = AccessPolicy(deny=["10.0.0.9"])
    manager = DeviceManager(
        adb,
        listen_host="127.0.0.1",
        device_tcp_port=5555,
        forward_base_port=16400,
        proxy_base_port=17400,
        poll_interval=5.0,
        port_probe=ScriptedProbe((True,), events=events),
        sleep_fn=RecordingSleeper(events=events),
        policy=policy,
    )

    # Act
    await manager._sync_devices()

    # Assert -- the policy reaches the live proxy of the one session
    session = manager._sessions["3696ade"]
    assert manager.policy is policy
    assert session._proxy._policy is policy
    assert session._proxy._policy.check("10.0.0.9")[0] is False

    # Connection management aggregates (no clients yet -> empty / zero).
    assert manager.connections() == []
    assert manager.kick("10.0.0.9") == 0

    await manager.stop()


async def test_dropReflections_disconnectsBogusEntryFromLocalAdbServer() -> None:
    # Arrange -- a stale `adb connect <our-ip>:<proxy-port>` entry on the local
    # server makes scrcpy see two devices and refuse to start; the reflection
    # must be disconnected, not merely skipped.
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, True, True), forward_base=16550, proxy_base=17550,
        local_ips=["10.0.28.128"],
    )
    adb.set_devices(("3696ade", "device"))
    await manager._sync_devices()
    adb.set_devices(("3696ade", "device"), ("10.0.28.128:17550", "device"))

    # Act
    await manager._sync_devices()

    # Assert -- the bogus entry was disconnected from the local adb server
    assert "disconnect" in adb.adb_sequence()
    await manager.stop()


async def test_syncDevices_excludedSerial_isNeverTouched() -> None:
    # Arrange -- the excluded device is the user's local scrcpy device; shareadb
    # must not run any adb command against it (no tcpip, no forward, no proxy).
    events = []
    adb = FakeADBClient(events=events)
    adb.set_devices(("scrcpy-dev", "device"), ("3696ade", "device"))
    manager = DeviceManager(
        adb,
        listen_host="127.0.0.1",
        device_tcp_port=5555,
        forward_base_port=16600,
        proxy_base_port=17600,
        poll_interval=5.0,
        exclude_serials=["scrcpy-dev"],
        port_probe=ScriptedProbe((True,), events=events),
        sleep_fn=RecordingSleeper(events=events),
    )

    # Act
    await manager._sync_devices()

    # Assert -- only the non-excluded device gets a session; scrcpy-dev was
    # never touched (it does not even appear in the adb command sequence beyond
    # the initial device listing).
    assert list(manager._sessions) == ["3696ade"]
    assert manager.statuses()[0].serial == "3696ade"
    await manager.stop()


async def test_sessionRestart_afterTeardown_reusesFastPathWithoutTcpip() -> None:
    # Arrange -- device unplugged and replugged: teardown puts the session into
    # WAITING; the next setup must succeed via the fast path (device stayed in
    # TCP mode) and NOT restart adbd again.
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True,), forward_base=16700, proxy_base=17700
    )
    adb.set_devices(("3696ade", "device"))
    await manager._sync_devices()
    adb.set_devices(("3696ade", "offline"))
    await manager._sync_devices()  # unplugged -> teardown
    assert manager._sessions["3696ade"].state is SessionState.WAITING

    # Act -- replug (device still in TCP mode from before)
    adb.set_devices(("3696ade", "device"))
    await manager._sync_devices()

    # Assert -- exactly one tcpip (zero, actually) across both setups
    assert adb.adb_sequence().count("tcpip") == 0
    assert manager._sessions["3696ade"].state is SessionState.RUNNING
    await manager.stop()


async def test_syncDevices_listDevicesTimeout_isContainedNotStalled() -> None:
    """A wedged adb server must not stall the sync loop.

    ``_run_loop`` relies on ``_sync_devices`` returning every cycle; if the
    listing blocked forever (or raised past the caller), sessions would keep
    their last cached state forever and healing would stop -- the observed
    "status says running, proxy says connection refused, restart fixes it".
    ``sync_age()`` must also stay unset so the status log can flag the stall.
    """

    # Arrange -- scripted adb-server wedge
    events = []
    manager, adb, probe, sleeper = make_manager(events, probe_results=(True,))
    adb.fail("devices", asyncio.TimeoutError())
    assert manager.sync_age() is None  # nothing listed yet

    # Act -- must return without raising
    await manager._sync_devices()

    # Assert -- no sessions created, no fresh sync recorded
    assert manager._sessions == {}
    assert manager.sync_age() is None


async def test_syncAge_afterSuccessfulListing_reportsFresh() -> None:
    """A successful listing refreshes sync_age(), which the status log uses to
    distinguish a live sync loop from a stalled one."""

    # Arrange
    events = []
    manager, adb, probe, sleeper = make_manager(events, probe_results=(True,))
    adb.set_devices()  # listing succeeds, no devices attached

    # Act
    await manager._sync_devices()

    # Assert
    age = manager.sync_age()
    assert age is not None
    assert 0.0 <= age < 60.0


# --------------------------------------------------------------------------- #
# TCP-mode verification (root/reboot wipes service.adb.tcp.port)
# --------------------------------------------------------------------------- #


async def test_start_tcpModeConfirmed_fastPathSkipsTcpipAndVerifiesAfterForward() -> None:
    """Fast path: adbd confirms TCP mode via getprop before trusting the tunnel.

    The check runs on the established forward (after the host-port probe, before
    the proxy starts) and must not trigger `adb tcpip` for a healthy device.
    """

    # Arrange -- fake defaults to service.adb.tcp.port=5555 (TCP mode active)
    session, adb, probe, sleeper, events = make_session(probe_results=(True,))

    # Act
    await session._start()

    # Assert -- verified then shared, zero adbd restarts
    seq = adb.adb_sequence()
    assert "tcpip" not in seq
    assert seq.count("shell") == 1
    assert seq.index("forward") < seq.index("shell")
    assert session._proxy.started is True


async def test_start_tcpModeLostAfterRoot_fallsBackToTcpipOnce() -> None:
    """root/reboot scenario: host forward probes OK but the tunnel is dead.

    `adb forward` binds its local listener even when the device side fell back
    to USB-only adbd (runtime prop wiped), so the host-port probe passes. The
    getprop verification must fail the fast path and hand over to exactly one
    `adb tcpip`, restoring sharing without a script restart.
    """

    # Arrange -- service.* and persist.* both empty: TCP mode lost
    session, adb, probe, sleeper, events = make_session(probe_results=(True, True))
    adb.props["service.adb.tcp.port"] = ""
    adb.props["persist.adb.tcp.port"] = ""

    # Act
    await session._start()

    # Assert -- fast path tried (forward before tcpip) then one tcpip restore
    seq = adb.adb_sequence()
    assert seq.index("forward") < seq.index("tcpip")
    assert seq.count("tcpip") == 1
    # both TCP-mode props consulted (neither set); the slow path skips the check
    assert seq.count("shell") == 2
    assert session._proxy.started is True


async def test_start_tcpModeCheckShellFails_fallsBackToTcpipNotWedge() -> None:
    """A transport hiccup while verifying TCP mode must not wedge the fast
    path: confirmation failed -> re-establish via the slow path (one tcpip)."""

    # Arrange
    session, adb, probe, sleeper, events = make_session(probe_results=(True, True))
    adb.fail("shell", asyncio.TimeoutError())

    # Act
    await session._start()

    # Assert
    assert adb.adb_sequence().count("tcpip") == 1
    assert session._proxy.started is True


async def test_syncDevices_rootWipedTcpMode_healsWithSingleTcpip() -> None:
    """End-to-end incident: working session -> device rooted -> link lost.

    Rooting restarts the transport (host forward dies) and wipes the runtime
    TCP-mode property (adbd back to USB-only). The manager must detect the dead
    forward, find the tunnel untrustworthy via getprop, and restore sharing
    with a single `adb tcpip` -- ending RUNNING again.
    """

    # Arrange -- probes: setup(ok) | heal: dead + recheck dead |
    # resetup: fast-path host probe(ok, but TCP check fails) + slow-path probe(ok)
    events = []
    manager, adb, probe, sleeper = make_manager(
        events, probe_results=(True, False, False, True, True),
        forward_base=16300, proxy_base=17300,
    )
    adb.set_devices(("3696ade", "device"))

    # Act -- cycle 1: healthy setup (device still in TCP mode)
    await manager._sync_devices()
    assert manager._sessions["3696ade"].state is SessionState.RUNNING
    assert adb.adb_sequence().count("tcpip") == 0

    # root happens: transport drops (forward dies) and runtime prop is wiped
    adb.props["service.adb.tcp.port"] = ""
    adb.props["persist.adb.tcp.port"] = ""

    await manager._sync_devices()  # cycle 2: dead forward detected -> WAITING
    assert manager._sessions["3696ade"].state is SessionState.WAITING

    await manager._sync_devices()  # cycle 3: fast path rejects -> tcpip -> RUNNING
    assert manager._sessions["3696ade"].state is SessionState.RUNNING

    # Assert -- exactly one adbd restart across the whole incident
    assert adb.adb_sequence().count("tcpip") == 1
    await manager.stop()
