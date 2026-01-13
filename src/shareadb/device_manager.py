"""Device management and orchestration for adb sharing."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence

from .adb_client import ADBClient, ADBCommandError, ADBDeviceInfo
from .tcp_proxy import TCPProxyServer

_LOG = logging.getLogger(__name__)


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

    async def ensure_running(self, info: ADBDeviceInfo) -> None:
        async with self._lock:
            self._last_info = info
            if self._state == SessionState.RUNNING:
                return
            try:
                await self._start()
                self._state = SessionState.RUNNING
                self._last_error = None
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
        _LOG.info(
            "Configuring adb over TCP for device %s (forward tcp:%s -> tcp:%s)",
            self._serial,
            self._forward_port,
            self._device_tcp_port,
        )
        await self._adb.shell(
            self._serial,
            ["setprop", "persist.adb.tcp.port", str(self._device_tcp_port)],
        )
        await self._adb.forward(self._serial, self._forward_port, self._device_tcp_port)
        await self._proxy.start()
        _LOG.info(
            "Device %s shared on %s:%s (forward tcp:%s -> tcp:%s)",
            self._serial,
            self._listen_host,
            self._proxy_port,
            self._forward_port,
            self._device_tcp_port,
        )

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

        # Start or refresh sessions for ready devices.
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
                )
                self._sessions[serial] = session
            try:
                await session.ensure_running(device)
            except Exception:
                # ``ensure_running`` already logged details; keep looping for retries.
                continue

        # Tear down sessions for devices no longer available.
        for serial, session in list(self._sessions.items()):
            if serial not in ready_devices and session.state == SessionState.RUNNING:
                reason = "not listed" if serial not in seen_serials else "not ready"
                await session.mark_unavailable(reason)

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
