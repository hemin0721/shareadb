"""Device management and orchestration for adb sharing."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Sequence

from .access import AccessPolicy
from .adb_client import ADBClient, ADBCommandError, ADBDeviceInfo
from .tcp_proxy import ConnectionInfo, TCPProxyServer

_LOG = logging.getLogger(__name__)

# Setup timing / retry policy. All timeouts are bounded so that a wedged device
# cannot stall the event loop or block shutdown indefinitely (see _start).
TCPIP_TIMEOUT = 10.0  # seconds for ``adb tcpip``
POST_TCPIP_DELAY = 1.0  # let the adbd restart triggered by tcpip take effect
RECONNECT_TIMEOUT = 5.0  # seconds for ``adb reconnect offline``
WAIT_FOR_DEVICE_TIMEOUT = 6.0  # seconds to wait for the device to come back
SETTLE_DELAY = 0.75  # seconds to let the transport settle before forwarding
PROBE_TIMEOUT = 1.5  # seconds for the forward liveness probe
SHELL_TIMEOUT = 5.0  # seconds for the getprop TCP-mode verification
RETRY_BACKOFF = 0.5  # seconds between setup attempts
START_MAX_ATTEMPTS = 4  # wait/forward/probe retries after the single tcpip
FLAP_RECHECK_DELAY = 1.0  # re-probe wait before declaring a forward dead

# Properties through which adbd advertises a TCP listener. ``adb tcpip`` writes
# the runtime property before restarting adbd; runtime properties do not
# survive a reboot, which is exactly how a device silently drops back to
# USB-only adbd (e.g. after rooting) while the host-side forward still probes
# "healthy".
TCP_MODE_PROPS = ("service.adb.tcp.port", "persist.adb.tcp.port")

# Injectable seams (defaults point at real implementations) so the retry/probe
# logic is unit-testable without sockets or sleeps.
PortProbe = Callable[[str, int], Awaitable[bool]]
Sleeper = Callable[[float], Awaitable[None]]


async def _default_port_probe(host: str, port: int) -> bool:
    """Return True if a TCP connection to ``(host, port)`` is accepted.

    Used to verify an ``adb forward`` actually has a live listener, which the
    ``adb forward`` exit code alone cannot prove.
    """

    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # pragma: no cover - defensive; close errors are irrelevant
        pass
    return True


@dataclass
class DevicePorts:
    """Ports reserved for a specific device."""

    forward: int
    proxy: int


class SessionState(Enum):
    WAITING = "waiting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class DeviceStatus:
    """Public view of a device session."""

    serial: str
    state: SessionState
    forward_port: int
    proxy_port: int
    tcp_port: int
    model: Optional[str] = None
    product: Optional[str] = None
    last_error: Optional[str] = None


class ADBDeviceSession:
    """Lifecycle handler for a single adb device."""

    def __init__(
        self,
        adb_client: ADBClient,
        serial: str,
        *,
        device_tcp_port: int,
        forward_port: int,
        proxy_port: int,
        listen_host: str,
        port_probe: Optional[PortProbe] = None,
        sleep_fn: Optional[Sleeper] = None,
        policy: Optional[AccessPolicy] = None,
    ) -> None:
        self._adb = adb_client
        self._serial = serial
        self._device_tcp_port = device_tcp_port
        self._forward_port = forward_port
        self._proxy_port = proxy_port
        self._listen_host = listen_host
        self._proxy = TCPProxyServer(
            listen_host,
            proxy_port,
            "127.0.0.1",
            forward_port,
            name=f"{serial}:{proxy_port}->{forward_port}",
            policy=policy,
        )
        self._lock = asyncio.Lock()
        self._state = SessionState.WAITING
        self._last_error: Optional[str] = None
        self._last_info: Optional[ADBDeviceInfo] = None
        self._port_probe: PortProbe = port_probe or _default_port_probe
        self._sleep_fn: Sleeper = sleep_fn or asyncio.sleep

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def forward_port(self) -> int:
        return self._forward_port

    @property
    def proxy_port(self) -> int:
        return self._proxy_port

    @property
    def tcp_port(self) -> int:
        return self._device_tcp_port

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> DeviceStatus:
        info = self._last_info
        return DeviceStatus(
            serial=self._serial,
            state=self._state,
            forward_port=self._forward_port,
            proxy_port=self._proxy_port,
            tcp_port=self._device_tcp_port,
            model=info.model if info else None,
            product=info.product if info else None,
            last_error=self._last_error,
        )

    async def ensure_running(self, info: ADBDeviceInfo) -> bool:
        """Bring the session to RUNNING if it isn't already.

        Returns:
            True if the session was (re)started during this call (i.e. it
            transitioned into RUNNING from a non-running state), False if it was
            already RUNNING. Used by the manager to skip a redundant liveness
            probe on freshly started sessions.
        """

        async with self._lock:
            self._last_info = info
            if self._state == SessionState.RUNNING:
                return False
            try:
                await self._start()
                self._state = SessionState.RUNNING
                self._last_error = None
                return True
            except Exception as exc:  # pragma: no cover - relies on adb/hardware failures
                # asyncio.TimeoutError stringifies to ""; fall back to the type
                # name so the status log still shows something useful.
                self._last_error = str(exc) or type(exc).__name__
                self._state = SessionState.ERROR
                _LOG.error("Failed to enable adb sharing for %s: %s", self._serial, exc)
                await self._teardown_forward()
                raise

    async def mark_unavailable(self, reason: str) -> None:
        async with self._lock:
            if self._state == SessionState.WAITING:
                return
            _LOG.info("Device %s unavailable (%s); tearing down proxy", self._serial, reason)
            await self._stop()
            self._state = SessionState.WAITING

    async def shutdown(self) -> None:
        async with self._lock:
            await self._stop()
            self._state = SessionState.WAITING

    async def _start(self) -> None:
        """Bring up forwarding without disturbing local users of the device.

        Local adb clients (scrcpy, Android Studio, ...) share the device's USB
        transport, and ``adb tcpip`` RESTARTS adbd, briefly dropping every one
        of those connections. Therefore the setup is two-phase:

        * Fast path: the device may already be in adb-over-TCP mode (e.g.
          ``persist.adb.tcp.port`` is set, or a previous run switched it). Then
          forwarding succeeds as-is and we never touch the device — zero
          disruption to local clients, on every (re)setup.
        * Slow path (only if forwarding fails): run ``adb tcpip`` exactly once
          to switch adbd into TCP mode. This restarts adbd and is the only
          disruptive step; afterwards the loop waits for the device to come
          back and verifies the forward before starting the proxy. Two failure
          modes make the wait necessary:

            - ``adb forward`` issued during the offline window fails with
              ``device '...' not found`` and leaves no listener -> the proxy
              then sees a permanent ECONNREFUSED (the original reconnect bug).
            - Re-running ``tcpip`` on every retry *re*-restarts adbd each time,
              so a freshly-booted device never gets a chance to stabilize.

        All awaits are bounded so a wedged device cannot stall shutdown.
        """

        _LOG.info(
            "Configuring adb sharing for device %s (forward tcp:%s -> tcp:%s)",
            self._serial,
            self._forward_port,
            self._device_tcp_port,
        )

        # Fast path: forwarding works without any device-side changes when
        # adbd already listens on TCP. wait_for_device is bounded and returns
        # instantly against a healthy transport. The tunnel is only trusted
        # once adbd confirms TCP mode (getprop): after a root/reboot the
        # runtime property is gone, adbd is USB-only, yet `adb forward` still
        # binds its local listener — a probe would pass while the tunnel is
        # dead end-to-end. Failing here hands over to the slow path, which
        # re-runs `adb tcpip` and restores sharing automatically.
        try:
            await self._bring_up_forward(require_tcp_mode=True)
            _LOG.info(
                "Device %s already in adb TCP mode; shared without restarting adbd",
                self._serial,
            )
            return
        except Exception as exc:
            _LOG.debug(
                "Direct forward failed for %s (%s: %s); switching adbd to TCP mode",
                self._serial,
                type(exc).__name__,
                exc,
            )
            await self._teardown_forward()

        # Slow path: switch adbd into TCP mode (restarts adbd — local adb
        # clients will briefly drop), then wait for it to come back.
        _LOG.info(
            "Restarting adbd of %s in TCP mode (local adb clients will briefly drop)",
            self._serial,
        )
        await self._adb.run(
            ["tcpip", str(self._device_tcp_port)],
            device_serial=self._serial,
            timeout=TCPIP_TIMEOUT,
        )
        # Let the adbd restart triggered by tcpip actually take effect, so the
        # first wait-for-device below observes the offline->online transition
        # instead of returning instantly against a not-yet-restarted adbd.
        await self._sleep_fn(POST_TCPIP_DELAY)

        last_error: Optional[BaseException] = None
        for attempt in range(1, START_MAX_ATTEMPTS + 1):
            try:
                await self._bring_up_forward()
                _LOG.info(
                    "Device %s shared on %s:%s (forward tcp:%s -> tcp:%s, attempt %d)",
                    self._serial,
                    self._listen_host,
                    self._proxy_port,
                    self._forward_port,
                    self._device_tcp_port,
                    attempt,
                )
                return
            except Exception as exc:
                last_error = exc
                await self._teardown_forward()
                if attempt < START_MAX_ATTEMPTS:
                    _LOG.warning(
                        "Setup attempt %d/%d for %s failed (%s: %s); retrying",
                        attempt,
                        START_MAX_ATTEMPTS,
                        self._serial,
                        type(exc).__name__,
                        exc,
                    )
                    await self._sleep_fn(RETRY_BACKOFF)
        raise RuntimeError(
            f"Failed to establish adb sharing for {self._serial} "
            f"after {START_MAX_ATTEMPTS} attempts: {type(last_error).__name__}: {last_error}"
        )

    async def _bring_up_forward(self, *, require_tcp_mode: bool = False) -> None:
        """Nudge the transport, establish the forward and start the proxy.

        Never changes device state (no ``tcpip``); safe to re-run for retries
        and self-healing without kicking local adb clients off the device.

        Args:
            require_tcp_mode: Verify that adbd actually advertises the target
                TCP port before starting the proxy (used by the fast path).
                ``adb forward`` binds its local listener even when the device
                side is USB-only, so the host-port probe alone reports success
                for a tunnel that is dead end-to-end; without this check a
                device that lost TCP mode (root/reboot wipes the runtime
                property) would look healthy while sharing is broken.
        """

        await self._adb.reconnect_offline(
            device_serial=self._serial, timeout=RECONNECT_TIMEOUT
        )
        await self._adb.wait_for_device(self._serial, timeout=WAIT_FOR_DEVICE_TIMEOUT)
        await self._sleep_fn(SETTLE_DELAY)
        await self._adb.forward(self._serial, self._forward_port, self._device_tcp_port)
        if not await self._port_probe("127.0.0.1", self._forward_port):
            raise RuntimeError(
                f"forward tcp:{self._forward_port} did not become reachable"
            )
        if require_tcp_mode and not await self._device_in_tcp_mode():
            raise RuntimeError(
                f"device {self._serial} adbd is not listening on "
                f"tcp:{self._device_tcp_port} (adb TCP mode lost, e.g. after "
                "root/reboot); needs 'adb tcpip' to restore sharing"
            )
        await self._proxy.start()

    async def _device_in_tcp_mode(self) -> bool:
        """True if adbd advertises a TCP listener on the configured port.

        Reads ``service.adb.tcp.port`` (set by ``adb tcpip``) with a fallback
        to ``persist.adb.tcp.port``. Any shell failure propagates: when TCP
        mode cannot be confirmed the caller falls back to the slow path and
        re-establishes it with a single ``adb tcpip``.
        """

        for prop in TCP_MODE_PROPS:
            result = await self._adb.shell(
                self._serial, ["getprop", prop], timeout=SHELL_TIMEOUT
            )
            value = result.stdout.strip()
            if value.isdigit() and int(value) == self._device_tcp_port:
                return True
        return False

    async def is_forward_healthy(self) -> bool:
        """Return True if the forward port currently accepts connections.

        Read-only and lock-free (it probes an external port) so the manager can
        run it from the sync loop to detect and self-heal a silently dead
        forward on an otherwise RUNNING session.
        """

        return await self._port_probe("127.0.0.1", self._forward_port)

    def connections(self) -> List[ConnectionInfo]:
        """Active client connections on this device's proxy."""

        return self._proxy.connections()

    def kick(self, pattern: str) -> int:
        """Close this device's connections whose peer matches ``pattern``."""

        return self._proxy.kick(pattern)

    async def _stop(self) -> None:
        await self._proxy.stop()
        await self._teardown_forward()

    async def _teardown_forward(self) -> None:
        try:
            await self._adb.remove_forward(self._serial, self._forward_port)
        except ADBCommandError as exc:  # pragma: no cover - occurs on unexpected adb failures
            _LOG.debug("remove_forward failed for %s: %s", self._serial, exc)


class DeviceManager:
    """Coordinates adb devices and associated proxy sessions."""

    def __init__(
        self,
        adb_client: ADBClient,
        *,
        listen_host: str,
        device_tcp_port: int,
        forward_base_port: int,
        proxy_base_port: int,
        poll_interval: float,
        include_serials: Optional[Sequence[str]] = None,
        exclude_serials: Optional[Sequence[str]] = None,
        port_probe: Optional[PortProbe] = None,
        sleep_fn: Optional[Sleeper] = None,
        local_ips: Optional[Sequence[str]] = None,
        policy: Optional[AccessPolicy] = None,
    ) -> None:
        self._adb = adb_client
        self._listen_host = listen_host
        self._device_tcp_port = device_tcp_port
        self._poll_interval = poll_interval
        self._include_serials = set(include_serials or [])
        # Excluded serials are never touched at all: no tcpip, no forward, no
        # proxy — for devices a local tool (scrcpy etc.) owns exclusively.
        self._exclude_serials = set(exclude_serials or [])
        self._policy = policy
        self._sessions: Dict[str, ADBDeviceSession] = {}
        self._port_state: Dict[str, DevicePorts] = {}
        self._next_forward = forward_base_port
        self._next_proxy = proxy_base_port
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._port_probe: Optional[PortProbe] = port_probe
        self._sleep_fn: Optional[Sleeper] = sleep_fn
        # Local IPs (server's own addresses) used to detect self-referential
        # devices: a client that ``adb connect``s to one of our proxy ports from
        # this host would otherwise be reshared, recursing into our own proxy.
        self._local_ips: set = set(local_ips or [])
        self._local_ips.update({"127.0.0.1", "localhost", "0.0.0.0", "::1"})
        self._reflection_logged: set = set()
        # monotonic time of the last successful ``adb devices`` listing
        # (None until the first successful sync); see sync_age().
        self._last_sync_ok: Optional[float] = None

    async def start(self) -> None:
        if self._task:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task
            self._task = None
        await self._shutdown_sessions()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._sync_devices()
            except Exception as exc:  # pragma: no cover - ensures background loop resilience
                _LOG.exception("Unhandled error during device sync: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                continue

    async def _sync_devices(self) -> None:
        try:
            devices = await self._adb.list_devices()
        except FileNotFoundError:
            _LOG.error("adb executable not found during sync")
            return
        except asyncio.TimeoutError:
            # adb CLI exceeded its default bound: the adb server is wedged.
            # Return (do not raise) so _run_loop keeps cycling and retries; a
            # permanent block here would freeze every session at its last known
            # state and disable dead-forward healing until a script restart.
            _LOG.error(
                "Timed out listing adb devices (adb server wedged?); "
                "sessions keep their last state and will be re-checked next cycle"
            )
            return
        except ADBCommandError as exc:
            _LOG.error("Failed to list devices: %s", exc)
            return

        # A successful listing is what drives self-heal decisions; remember it
        # so the status logger can distinguish a live sync loop from a stalled
        # one (statuses() alone only serves cached session states).
        self._last_sync_ok = time.monotonic()

        if self._include_serials:
            devices = [d for d in devices if d.serial in self._include_serials]
        if self._exclude_serials:
            devices = [d for d in devices if d.serial not in self._exclude_serials]

        ready_devices = {device.serial: device for device in devices if device.is_ready}
        seen_serials = set(devices_by_serial(devices))

        # Drop self-referential "reflection" devices before doing anything else.
        # If someone runs ``adb connect <our-ip>:<our-proxy-port>`` on this host,
        # adb registers it as a network device; sharing it would recurse through
        # our own proxy (and running ``tcpip`` on it routes back to the real
        # device and disrupts it). Such a device's host is one of our local IPs
        # and its port is a proxy port we currently serve.
        await self._drop_reflections(ready_devices)

        # Start or refresh sessions for ready devices.
        started_this_cycle: set = set()
        for serial, device in ready_devices.items():
            session = self._sessions.get(serial)
            if not session:
                ports = self._ensure_ports(serial)
                session = ADBDeviceSession(
                    self._adb,
                    serial,
                    device_tcp_port=self._device_tcp_port,
                    forward_port=ports.forward,
                    proxy_port=ports.proxy,
                    listen_host=self._listen_host,
                    port_probe=self._port_probe,
                    sleep_fn=self._sleep_fn,
                    policy=self._policy,
                )
                self._sessions[serial] = session
            try:
                if await session.ensure_running(device):
                    started_this_cycle.add(serial)
            except Exception:
                # ``ensure_running`` already logged details; keep looping for retries.
                continue

        # Self-heal: a RUNNING session whose forward silently died (transport
        # race, USB glitch, adb server restart) would otherwise stay stuck. Skip
        # sessions just started this cycle and those whose device is gone (the
        # teardown pass below handles the latter).
        for serial, session in list(self._sessions.items()):
            if serial in started_this_cycle:
                continue
            if session.state != SessionState.RUNNING or serial not in ready_devices:
                continue
            try:
                healthy = await session.is_forward_healthy()
                if not healthy:
                    # The probe can fail transiently (adb server busy, transport
                    # hiccup); tearing down here would re-run setup and — before
                    # the fast path existed — restart adbd, kicking local users
                    # like scrcpy off the device. Re-probe once before deciding.
                    await self._sleep(FLAP_RECHECK_DELAY)
                    healthy = await session.is_forward_healthy()
            except Exception as exc:  # pragma: no cover - probe is best-effort
                _LOG.debug("Forward health check failed for %s: %s", serial, exc)
                continue
            if not healthy:
                _LOG.warning("Dead forward detected for %s; re-setting up", serial)
                await session.mark_unavailable("forward dead")

        # Tear down sessions for devices no longer available.
        for serial, session in list(self._sessions.items()):
            if serial not in ready_devices and session.state == SessionState.RUNNING:
                reason = "not listed" if serial not in seen_serials else "not ready"
                await session.mark_unavailable(reason)

    def _is_reflection(self, serial: str, served_proxy_ports: set) -> bool:
        """True if ``serial`` is a network device pointing at our own proxy.

        A reflection has the form ``<local-ip>:<port>`` where ``port`` is one of
        the proxy ports we currently serve. Real devices never satisfy both:
        their host is not one of our local addresses and (for adb devices) their
        port is not one of our proxy listeners.
        """

        host, sep, port_str = serial.rpartition(":")
        if not sep or not port_str.isdigit():
            return False
        if int(port_str) not in served_proxy_ports:
            return False
        return host in self._local_ips

    async def _drop_reflections(self, ready_devices: Dict[str, ADBDeviceInfo]) -> None:
        """Remove self-referential devices from consideration and tear down any
        session/ports we may have already allocated for them.

        The bogus ``<local-ip>:<proxy-port>`` entry is also disconnected from
        the local adb server: it is useless (it loops back into our own proxy)
        and while it lingers, local tools such as scrcpy see TWO devices and
        refuse to run without an explicit ``-s``.
        """

        served_proxy_ports = {ports.proxy for ports in self._port_state.values()}
        for serial in list(ready_devices):
            if not self._is_reflection(serial, served_proxy_ports):
                continue
            ready_devices.pop(serial, None)
            session = self._sessions.pop(serial, None)
            if session is not None:
                await session.shutdown()
            released = self._port_state.pop(serial, None)
            try:
                await self._adb.run(["disconnect", serial], check=False, timeout=RECONNECT_TIMEOUT)
            except Exception as exc:  # pragma: no cover - disconnect is best-effort
                _LOG.debug("disconnect %s failed: %s", serial, exc)
            if serial not in self._reflection_logged:
                self._reflection_logged.add(serial)
                detail = (
                    f" (released ports forward={released.forward}/proxy={released.proxy})"
                    if released
                    else ""
                )
                _LOG.warning(
                    "Disconnected self-referential device %s: it points at this "
                    "server's own proxy (someone ran `adb connect` to our proxy "
                    "port from this host). The stale entry would make local "
                    "tools such as scrcpy see two devices.%s",
                    serial,
                    detail,
                )

    async def _shutdown_sessions(self) -> None:
        for session in list(self._sessions.values()):
            await session.shutdown()

    def _ensure_ports(self, serial: str) -> DevicePorts:
        ports = self._port_state.get(serial)
        if ports:
            return ports
        ports = DevicePorts(forward=self._next_forward, proxy=self._next_proxy)
        self._next_forward += 1
        self._next_proxy += 1
        self._port_state[serial] = ports
        _LOG.debug("Allocated ports for %s: forward=%s proxy=%s", serial, ports.forward, ports.proxy)
        return ports

    async def _sleep(self, delay: float) -> None:
        """Bounded sleep honoring the injected sleeper (tests) when present."""

        sleeper = self._sleep_fn or asyncio.sleep
        await sleeper(delay)

    def statuses(self) -> List[DeviceStatus]:
        return [session.describe() for session in self._sessions.values()]

    def sync_age(self) -> Optional[float]:
        """Seconds since the last successful ``adb devices`` listing.

        ``None`` means no successful listing has happened yet. ``statuses()``
        only serves cached session states, so on its own it can keep printing
        "running" forever while the sync loop (and thus dead-forward healing)
        is stalled; the periodic status log uses this age to escalate to
        WARNING when the cache can no longer be trusted.
        """

        if self._last_sync_ok is None:
            return None
        return time.monotonic() - self._last_sync_ok

    @property
    def policy(self) -> Optional[AccessPolicy]:
        return self._policy

    def connections(self) -> List[ConnectionInfo]:
        """Active client connections across every device proxy."""

        infos: List[ConnectionInfo] = []
        for session in self._sessions.values():
            infos.extend(replace(info, device=session.serial) for info in session.connections())
        infos.sort(key=lambda info: info.connected_at)
        return infos

    def kick(self, pattern: str) -> int:
        """Close connections whose peer matches ``pattern`` on every device.

        Returns the total number of connections closed.
        """

        return sum(session.kick(pattern) for session in self._sessions.values())


def devices_by_serial(devices: Iterable[ADBDeviceInfo]) -> List[str]:
    return [device.serial for device in devices]
