"""Test doubles for shareadb.

These fakes let the device-manager / proxy logic be exercised deterministically
without a real ``adb`` binary or any Android hardware. The shared ``events``
list lets a test assert a single, interleaved timeline across adb calls, sleeps
and probes (e.g. that a settle sleep happens strictly between ``wait-for-device``
and ``forward``).
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Sequence

from shareadb.adb_client import ADBCommandResult, ADBDeviceInfo

# A timeline event is a (category, value) pair, e.g. ("adb", "forward").
Event = Any


class FakeADBClient:
    """Scriptable stand-in for :class:`shareadb.adb_client.ADBClient`.

    Records high-level adb operations in call order and lets any tagged command
    be scripted to raise on a given call via :meth:`fail`.
    """

    def __init__(self, events: Optional[List[Event]] = None) -> None:
        self.events: List[Event] = events if events is not None else []
        self.devices: List[ADBDeviceInfo] = []
        self._behaviors: Dict[str, List[BaseException]] = {}

    def set_devices(self, *serials_states: tuple) -> None:
        """Populate ``list_devices`` output from ``(serial, state)`` pairs."""

        self.devices = [
            ADBDeviceInfo(serial=serial, state=state) for serial, state in serials_states
        ]

    def fail(self, tag: str, exc: BaseException) -> "FakeADBClient":
        """Make the next call tagged ``tag`` raise ``exc``."""

        self._behaviors.setdefault(tag, []).append(exc)
        return self

    def _record(self, tag: str) -> None:
        self.events.append(("adb", tag))

    def _apply(self, tag: str) -> None:
        outcomes = self._behaviors.get(tag)
        if outcomes:
            raise outcomes.pop(0)

    def adb_sequence(self) -> List[str]:
        """Just the adb command tags, in order, ignoring sleeps/probes."""

        return [value for category, value in self.events if category == "adb"]

    # --- adb operations (signatures mirror ADBClient) ---
    async def list_devices(self) -> List[ADBDeviceInfo]:
        return list(self.devices)

    async def run(
        self,
        args: Sequence[str],
        *,
        device_serial: Optional[str] = None,
        timeout: Optional[float] = None,
        check: bool = True,
    ) -> ADBCommandResult:
        tag = args[0] if args else "unknown"
        self._record(tag)
        self._apply(tag)
        return ADBCommandResult(stdout="", stderr="", returncode=0)

    async def shell(
        self,
        device_serial: str,
        shell_args: Sequence[str],
        *,
        timeout: Optional[float] = None,
    ) -> ADBCommandResult:
        self._record("shell")
        return ADBCommandResult(stdout="", stderr="", returncode=0)

    async def wait_for_device(self, device_serial: str, *, timeout: float) -> ADBCommandResult:
        self._record("wait-for-device")
        self._apply("wait-for-device")
        return ADBCommandResult(stdout="", stderr="", returncode=0)

    async def reconnect_offline(self, *, timeout: float = 5.0) -> ADBCommandResult:
        self._record("reconnect offline")
        self._apply("reconnect offline")
        return ADBCommandResult(stdout="", stderr="", returncode=0)

    async def forward(
        self,
        device_serial: str,
        local_port: int,
        remote_port: int,
        *,
        replace: bool = True,
    ) -> None:
        if replace:
            self._record("remove_forward")
        self._record("forward")
        self._apply("forward")

    async def remove_forward(self, device_serial: str, local_port: int) -> None:
        self._record("remove_forward")
        self._apply("remove_forward")


class RecordingSleeper:
    """Async sleeper that records durations and returns immediately.

    Appends ``("sleep", delay)`` to the shared ``events`` timeline so a test can
    assert *where* in the sequence a settle/backoff sleep occurred.
    """

    def __init__(self, events: Optional[List[Event]] = None) -> None:
        self.events: List[Event] = events if events is not None else []
        self.delays: List[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.events.append(("sleep", delay))


class ScriptedProbe:
    """Async port probe returning scripted results in order.

    Once the scripted results are exhausted, the last value repeats (handy for
    multi-cycle sync tests).
    """

    def __init__(self, results: Sequence[bool], events: Optional[List[Event]] = None) -> None:
        self._results: List[bool] = list(results)
        self._last: bool = results[-1] if results else False
        self.events: List[Event] = events if events is not None else []
        self.history: List[bool] = []

    async def __call__(self, host: str, port: int) -> bool:
        if self._results:
            self._last = self._results.pop(0)
        self.history.append(self._last)
        self.events.append(("probe", self._last))
        return self._last


class FakeProxy:
    """Stand-in for :class:`shareadb.tcp_proxy.TCPProxyServer`.

    Records ``start``/``stop`` so tests can assert lifecycle without binding a
    real listening socket.
    """

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True
        self.started = False


class RunRecorder:
    """Replaces :meth:`ADBClient.run` so real ADBClient methods can be unit-tested.

    Records every invocation and returns queued :class:`ADBCommandResult` values
    (or raises queued exceptions) in FIFO order; defaults to a successful result.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._results: "deque[Any]" = deque()

    async def __call__(
        self,
        args: Sequence[str],
        *,
        device_serial: Optional[str] = None,
        timeout: Optional[float] = None,
        check: bool = True,
    ) -> ADBCommandResult:
        self.calls.append(
            {
                "args": list(args),
                "device_serial": device_serial,
                "timeout": timeout,
                "check": check,
            }
        )
        if self._results:
            item = self._results.popleft()
            if isinstance(item, BaseException):
                raise item
            return item
        return ADBCommandResult(stdout="", stderr="", returncode=0)

    def queue(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> "RunRecorder":
        self._results.append(ADBCommandResult(stdout=stdout, stderr=stderr, returncode=returncode))
        return self

    def queue_exc(self, exc: BaseException) -> "RunRecorder":
        self._results.append(exc)
        return self
