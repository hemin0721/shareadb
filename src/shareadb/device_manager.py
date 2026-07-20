"""Device management and orchestration for adb sharing."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Sequence

from .adb_client import ADBClient, ADBCommandError, ADBDeviceInfo
from .tcp_proxy import TCPProxyServer

_LOG = logging.getLogger(__name__)

# Setup timing / retry policy. All timeouts are bounded so that a wedged device
# cannot stall the event loop or block shutdown indefinitely (see _start).
TCPIP_TIMEOUT = 10.0  # seconds for ``adb tcpip``
POST_TCPIP_DELAY = 1.0  # let the adbd restart triggered by tcpip take effect
RECONNECT_TIMEOUT = 5.0  # seconds for ``adb reconnect offline``
WAIT_FOR_DEVICE_TIMEOUT = 6.0  # seconds to wait for the device to come back
SETTLE_DELAY = 0.75  # seconds to let the transport settle before forwarding
PROBE_TIMEOUT = 1.5  # seconds for the forward liveness probe
RETRY_BACKOFF = 0.5  # seconds between setup attempts
START_MAX_ATTEMPTS = 4  # wait/forward/probe retries after the single tcpip

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
                self._last_error = str(exc)
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
        """Enable adb-over-TCP sharing for the device, retrying on transient races.

        ``adb tcpip`` switches adbd into TCP mode (and persists the port), but it
        also restarts adbd *asynchronously*: right after it returns the device is
        still "device", then it drops offline for a moment as adbd restarts, then
        it comes back. Two failure modes follow if this is ignored:

          * ``adb forward`` issued during the offline window fails with
            ``device '...' not found`` and leaves no listener -> the proxy then
            sees a permanent ECONNREFUSED (the original reconnect bug).
          * Re-running ``tcpip`` on every retry *re*-restarts adbd each time, so a
            freshly-booted device never gets a chance to stabilize.

        Therefore ``tcpip`` runs exactly ONCE; the loop below waits for the device
        to come back from that single restart and verifies the forward before
        starting the proxy. All awaits are bounded so a wedged device cannot stall
        shutdown.
        """

        _LOG.info(
            "Configuring adb over TCP for device %s (forward tcp:%s -> tcp:%s)",
            self._serial,
            self._forward_port,
            self._device_tcp_port,
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
                await self._adb.reconnect_offline(timeout=RECONNECT_TIMEOUT)
                await self._adb.wait_for_device(
                    self._serial, timeout=WAIT_FOR_DEVICE_TIMEOUT
                )
                await self._sleep_fn(SETTLE_DELAY)
                await self._adb.forward(
                    self._serial, self._forward_port, self._device_tcp_port
                )
                if not await self._port_probe("127.0.0.1", self._forward_port):
                    raise RuntimeError(
                        f"forward tcp:{self._forward_port} did not become reachable"
                    )
                await self._proxy.start()
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

    async def is_forward_healthy(self) -> bool:
        """Return True if the forward port currently accepts connections.

        Read-only and lock-free (it probes an external port) so the manager can
        run it from the sync loop to detect and self-heal a silently dead
        forward on an otherwise RUNNING session.
        """

        return await self._port_probe("127.0.0.1", self._forward_port)

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
        port_probe: Optional[PortProbe] = None,
        sleep_fn: Optional[Sleeper] = None,
        local_ips: Optional[Sequence[str]] = None,
    ) -> None:
        self._adb = adb_client
        self._listen_host = listen_host
        self._device_tcp_port = device_tcp_port
        self._poll_interval = poll_interval
        self._include_serials = set(include_serials or [])
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
        except ADBCommandError as exc:
            _LOG.error("Failed to list devices: %s", exc)
            return

        if self._include_serials:
            devices = [d for d in devices if d.serial in self._include_serials]

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
        session/ports we may have already allocated for them."""

        served_proxy_ports = {ports.proxy for ports in self._port_state.values()}
        for serial in list(ready_devices):
            if not self._is_reflection(serial, served_proxy_ports):
                continue
            ready_devices.pop(serial, None)
            session = self._sessions.pop(serial, None)
            if session is not None:
                await session.shutdown()
            released = self._port_state.pop(serial, None)
            if serial not in self._reflection_logged:
                self._reflection_logged.add(serial)
                detail = (
                    f" (released ports forward={released.forward}/proxy={released.proxy})"
                    if released
                    else ""
                )
                _LOG.warning(
                    "Skipping self-referential device %s: it points at this "
                    "server's own proxy (someone ran `adb connect` to our proxy "
                    "port from this host). Not re-sharing it.%s",
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

    def statuses(self) -> List[DeviceStatus]:
        return [session.describe() for session in self._sessions.values()]


def devices_by_serial(devices: Iterable[ADBDeviceInfo]) -> List[str]:
    return [device.serial for device in devices]
