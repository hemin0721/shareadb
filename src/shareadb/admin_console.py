"""Interactive runtime management for a running shareadb server.

Reads management commands from stdin (in a daemon thread, so it works on
Windows too) and executes them on the event loop:

    help              show commands
    status            device sharing status
    clients           list active client connections
    kick <ip|cidr>    close matching connections (peer may reconnect)
    ban <ip|cidr>     add to deny list AND kick matching connections
    unban <ip|cidr>   remove from deny list
    policy            show access-control rules and rejected attempts

Commands are no-ops when stdin is unavailable (daemon/service runs); the
startup access-control flags still apply in that case.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from typing import Optional

from .access import AccessPolicy, write_blocklist

_LOG = logging.getLogger(__name__)

HELP_TEXT = """shareadb management commands:
  help              show this help
  status            show device sharing status
  clients           list active client connections
  kick <ip|cidr>    close matching connections (peer may reconnect)
  ban <ip|cidr>     deny this peer AND close its connections
  unban <ip|cidr>   remove from deny list
  policy            show access-control rules and rejected attempts"""


def _fmt_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}TB"  # pragma: no cover - unreachable


def _fmt_age(connected_at: float) -> str:
    seconds = max(0, int(time.time() - connected_at))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


class AdminConsole:
    """Executes management commands against a live DeviceManager."""

    def __init__(
        self,
        manager,
        policy: Optional[AccessPolicy] = None,
        *,
        blocklist_path: Optional[str] = None,
    ) -> None:
        self._manager = manager
        self._policy = policy
        self._blocklist_path = blocklist_path

    async def run(self, stop_event: asyncio.Event) -> None:
        """Consume stdin lines until cancelled (stdin thread is a daemon)."""

        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[str]" = asyncio.Queue()
        thread = threading.Thread(
            target=self._read_stdin,
            args=(loop, queue),
            name="shareadb-admin-stdin",
            daemon=True,
        )
        thread.start()
        _LOG.info("Admin console ready - type 'help' for management commands")
        while not stop_event.is_set():
            line = await queue.get()
            try:
                self.handle_line(line)
            except Exception as exc:  # pragma: no cover - defensive: console must never die
                _LOG.error("Management command failed: %s", exc)

    @staticmethod
    def _read_stdin(loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[str]") -> None:
        if sys.stdin is None:
            return
        try:
            while True:
                line = sys.stdin.readline()
                if not line:  # EOF (piped input finished, no controlling tty)
                    return
                loop.call_soon_threadsafe(queue.put_nowait, line)
        except Exception:
            # stdin unavailable (daemon run, pytest capture, GUI pythonw) - console off.
            return

    def handle_line(self, line: str) -> None:
        parts = line.split()
        if not parts:
            return
        cmd, args = parts[0].lower(), parts[1:]
        handlers = {
            "help": self._cmd_help,
            "h": self._cmd_help,
            "?": self._cmd_help,
            "status": self._cmd_status,
            "clients": self._cmd_clients,
            "list": self._cmd_clients,
            "kick": self._cmd_kick,
            "ban": self._cmd_ban,
            "unban": self._cmd_unban,
            "policy": self._cmd_policy,
        }
        handler = handlers.get(cmd)
        if handler is None:
            _LOG.warning("Unknown management command %r - type 'help'", cmd)
            return
        handler(args)

    # --- command handlers (all sync; they only touch loop-confined state) ---

    def _cmd_help(self, args) -> None:
        for line in HELP_TEXT.splitlines():
            _LOG.info("%s", line)

    def _cmd_status(self, args) -> None:
        statuses = self._manager.statuses()
        if not statuses:
            _LOG.info("status: no adb devices detected")
            return
        for status in statuses:
            _LOG.info(
                "status: serial=%s state=%s proxy=%d forward=%d",
                status.serial,
                status.state.value,
                status.proxy_port,
                status.forward_port,
            )

    def _cmd_clients(self, args) -> None:
        conns = self._manager.connections()
        if not conns:
            _LOG.info("clients: none connected")
            return
        _LOG.info("clients: %d active connection(s)", len(conns))
        for info in conns:
            _LOG.info(
                "client %s:%d -> device=%s proxy=%s since=%s (%s) up=%s down=%s",
                info.peer_host,
                info.peer_port,
                info.device,
                info.proxy_name,
                time.strftime("%H:%M:%S", time.localtime(info.connected_at)),
                _fmt_age(info.connected_at),
                _fmt_bytes(info.bytes_from_client),
                _fmt_bytes(info.bytes_to_client),
            )

    def _cmd_kick(self, args) -> None:
        if not self._require_arg("kick", args):
            return
        count = self._manager.kick(args[0])
        _LOG.info("kick %s: closed %d connection(s)", args[0], count)

    def _cmd_ban(self, args) -> None:
        if not self._require_arg("ban", args):
            return
        if self._policy is None:  # pragma: no cover - CLI always provides a policy
            _LOG.error("ban: access control is not configured")
            return
        entry = args[0]
        try:
            added = self._policy.add_deny(entry)
        except ValueError as exc:
            _LOG.error("ban %s: invalid address or network (%s)", entry, exc)
            return
        kicked = self._manager.kick(entry)
        if not added:
            _LOG.info("ban %s: already denied; kicked %d connection(s)", entry, kicked)
        else:
            _LOG.warning("ban %s: peer denied from now on; kicked %d connection(s)", entry, kicked)
        self._persist_blocklist()

    def _cmd_unban(self, args) -> None:
        if not self._require_arg("unban", args):
            return
        if self._policy is None:  # pragma: no cover - CLI always provides a policy
            _LOG.error("unban: access control is not configured")
            return
        entry = args[0]
        try:
            removed = self._policy.remove_deny(entry)
        except ValueError as exc:
            _LOG.error("unban %s: invalid address or network (%s)", entry, exc)
            return
        if removed:
            _LOG.info("unban %s: removed from deny list", entry)
            self._persist_blocklist()
        else:
            _LOG.info("unban %s: not in deny list", entry)

    def _cmd_policy(self, args) -> None:
        if self._policy is None:  # pragma: no cover - CLI always provides a policy
            _LOG.info("policy: access control is not configured")
            return
        mode = "allow-list (only allowed peers may connect)" if self._policy.allowlist_enabled \
            else "deny-list (everyone allowed except denied peers)"
        _LOG.info("policy: mode=%s", mode)
        _LOG.info("policy: allow=%s", ", ".join(self._policy.allow_entries()) or "(none)")
        _LOG.info("policy: deny=%s", ", ".join(self._policy.deny_entries()) or "(none)")
        if self._policy.rejected:
            for host, count in sorted(self._policy.rejected.items()):
                _LOG.info("policy: rejected attempts from %s: %d", host, count)
        else:
            _LOG.info("policy: no rejected attempts so far")

    def _persist_blocklist(self) -> None:
        if not self._blocklist_path or self._policy is None:
            return
        try:
            write_blocklist(self._blocklist_path, self._policy.deny_entries())
            _LOG.info("blocklist saved to %s", self._blocklist_path)
        except OSError as exc:
            _LOG.error("Failed to save blocklist %s: %s", self._blocklist_path, exc)

    @staticmethod
    def _require_arg(cmd: str, args) -> bool:
        if args:
            return True
        _LOG.error("%s: missing <ip|cidr> argument - try 'help'", cmd)
        return False
