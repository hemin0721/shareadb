"""Helpers for interacting with the adb command line tool."""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Union

_LOG = logging.getLogger(__name__)


@dataclass
class ADBCommandResult:
    """Result wrapper for adb command executions."""

    stdout: str
    stderr: str
    returncode: int


class ADBCommandError(RuntimeError):
    """Raised when an adb command exits with a non-zero code."""

    def __init__(self, command: Sequence[str], result: ADBCommandResult):
        message = (
            f"Command {' '.join(command)} failed with exit code {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        super().__init__(message)
        self.command = list(command)
        self.result = result


@dataclass
class ADBDeviceInfo:
    """Represents a single adb device entry."""

    serial: str
    state: str
    product: Optional[str] = None
    model: Optional[str] = None
    device: Optional[str] = None
    transport_id: Optional[str] = None
    extra: Optional[Dict[str, str]] = None

    @property
    def is_ready(self) -> bool:
        """Whether this device is in an operational state."""

        return self.state == "device"


class ADBClient:
    """Thin asynchronous wrapper around the adb executable."""

    def __init__(self, adb_path: Union[Path, str]):
        self._adb_path = Path(adb_path)

    @property
    def adb_path(self) -> Path:
        return self._adb_path

    @staticmethod
    def detect(default: Optional[str] = None) -> Path:
        """Resolve the adb executable location.

        Args:
            default: Optional path provided by the user.

        Returns:
            A ``Path`` pointing to the adb executable.

        Raises:
            FileNotFoundError: If adb cannot be located.
        """

        if default:
            candidate = Path(default).expanduser()
            if candidate.is_file():
                return candidate
            raise FileNotFoundError(f"Provided adb path does not exist: {candidate}")

        resolved = shutil.which("adb")
        if not resolved:
            raise FileNotFoundError(
                "Could not locate 'adb'. Install Android Platform Tools or provide --adb-path."
            )
        return Path(resolved)

    async def run(
        self,
        args: Sequence[str],
        *,
        device_serial: Optional[str] = None,
        timeout: Optional[float] = None,
        check: bool = True,
    ) -> ADBCommandResult:
        """Execute an adb sub-command asynchronously."""

        cmd: List[str] = [str(self._adb_path)]
        if device_serial:
            cmd += ["-s", device_serial]
        cmd.extend(args)

        _LOG.debug("Running adb command: %s", " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise

        stdout = stdout_raw.decode("utf-8", errors="replace").strip()
        stderr = stderr_raw.decode("utf-8", errors="replace").strip()
        result = ADBCommandResult(stdout=stdout, stderr=stderr, returncode=process.returncode)

        if check and process.returncode != 0:
            raise ADBCommandError(cmd, result)
        if result.stderr:
            _LOG.debug("adb stderr: %s", result.stderr)
        return result

    async def shell(
        self,
        device_serial: str,
        shell_args: Iterable[str],
        *,
        timeout: Optional[float] = None,
    ) -> ADBCommandResult:
        """Run an adb shell command on a specific device."""

        args = ["shell", *shell_args]
        return await self.run(args, device_serial=device_serial, timeout=timeout)

    async def forward(
        self,
        device_serial: str,
        local_port: int,
        remote_port: int,
        *,
        replace: bool = True,
    ) -> None:
        """Create or replace a port forward between host and device."""

        if replace:
            try:
                await self.run(
                    ["forward", "--remove", f"tcp:{local_port}"],
                    device_serial=device_serial,
                    check=False,
                )
            except Exception:  # pragma: no cover - defensive; run() already suppresses via check=False
                _LOG.debug("Ignoring failure removing existing forward for port %s", local_port)
        await self.run(
            ["forward", f"tcp:{local_port}", f"tcp:{remote_port}"],
            device_serial=device_serial,
        )

    async def remove_forward(self, device_serial: str, local_port: int) -> None:
        """Remove an adb forward if it exists."""

        await self.run(
            ["forward", "--remove", f"tcp:{local_port}"],
            device_serial=device_serial,
            check=False,
        )

    async def list_devices(self) -> List[ADBDeviceInfo]:
        """Return all devices reported by ``adb devices -l``."""

        result = await self.run(["devices", "-l"])
        return _parse_device_list(result.stdout)


def _parse_device_list(raw: str) -> List[ADBDeviceInfo]:
    devices: List[ADBDeviceInfo] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices attached"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state, *rest = parts
        fields: dict[str, str] = {}
        for item in rest:
            if ":" in item:
                key, value = item.split(":", 1)
                fields[key] = value
        devices.append(
            ADBDeviceInfo(
                serial=serial,
                state=state,
                product=fields.get("product"),
                model=fields.get("model"),
                device=fields.get("device"),
                transport_id=fields.get("transport_id"),
                extra={k: v for k, v in fields.items() if k not in {"product", "model", "device", "transport_id"}},
            )
        )
    return devices
