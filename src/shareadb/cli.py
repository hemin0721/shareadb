"""Command line entry point for shareadb."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
import sys
import time
from typing import Iterable, List, Optional

from .access import AccessPolicy, read_blocklist
from .adb_client import ADBClient
from .admin_console import AdminConsole
from .device_manager import DeviceManager, DeviceStatus
from .status_board import StatusBoard, StatusBoardHandler, render_block


def get_local_ips() -> List[str]:
    """Get all local IP addresses from network interfaces."""
    ips = []
    try:
        # Try to get IPs from all network interfaces
        hostname = socket.gethostname()
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        
        # Also try to get IPs directly from hostname
        try:
            hostname_ip = socket.gethostbyname(hostname)
            addr_info.extend([(socket.AF_INET, socket.SOCK_STREAM, 6, '', (hostname_ip, 0))])
        except:
            pass
        
        for info in addr_info:
            ip = info[4][0] if info[4] else None
            if ip:
                # Filter out IPv6 and loopback addresses
                if ':' not in ip and not ip.startswith('127.'):
                    if ip not in ips:
                        ips.append(ip)
                        
        # If still no IPs, try common interface names
        if not ips:
            interfaces = ['eth0', 'en0', 'wlan0', 'enp*', 'wlp*']
            for interface in interfaces:
                try:
                    # Try to get IP by creating a socket and binding to interface
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    # This won't actually connect, just get interface info
                    s.connect(('8.8.8.8', 80))
                    ip = s.getsockname()[0]
                    s.close()
                    if ip and not ip.startswith('127.') and ip not in ips:
                        ips.append(ip)
                        break
                except:
                    continue
    except Exception as exc:
        _LOG.debug("Error detecting local IPs: %s", exc)
    
    # Fallback to common local network IPs
    if not ips:
        _LOG.warning("Could not detect local IP address, using 0.0.0.0")
        ips = ['0.0.0.0']
    
    return sorted(ips)

_LOG = logging.getLogger(__name__)

# A device sync is considered stalled once no successful ``adb devices``
# listing happened for this many seconds (scaled up for slow poll intervals).
# Status lines are served from a cache, so without this signal a wedged sync
# loop keeps printing "state=running" while sharing is actually down.
SYNC_STALE_MIN_SECONDS = 90.0


def sync_stale_threshold(poll_interval: float) -> float:
    """Seconds of sync inactivity before the status log escalates to WARNING."""

    return max(SYNC_STALE_MIN_SECONDS, 3.0 * poll_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Share local adb devices with remote users")
    parser.add_argument("--adb-path", help="Path to adb executable; defaults to PATH lookup")
    parser.add_argument("--listen-host", default="0.0.0.0", help="Host/IP for proxy listeners")
    parser.add_argument("--device-tcp-port", type=int, default=5555, help="adb tcp port configured on devices")
    parser.add_argument(
        "--forward-base-port",
        type=int,
        default=6000,
        help="First local port for adb forward; increments per device",
    )
    parser.add_argument(
        "--proxy-base-port",
        type=int,
        default=7000,
        help="First proxy listening port; increments per device",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between adb device polling cycles",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        help="Optional list of device serials to manage (default: all detected devices)",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        help=(
            "Device serials to leave completely untouched (no tcpip, no "
            "forward, no proxy) - e.g. a device used locally by scrcpy"
        ),
    )
    parser.add_argument(
        "--allow",
        nargs="*",
        default=[],
        help=(
            "Only these client IPs/CIDRs may connect (allow-list mode; "
            "loopback stays allowed unless denied)"
        ),
    )
    parser.add_argument(
        "--deny",
        nargs="*",
        default=[],
        help="Client IPs/CIDRs to reject (deny wins over --allow)",
    )
    parser.add_argument(
        "--blocklist-file",
        default=None,
        help=(
            "Optional deny-list file (one IP/CIDR per line, '#' comments), "
            "loaded at startup and rewritten on runtime ban/unban"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=30.0,
        help=(
            "Seconds between status refreshes: the interactive status board is "
            "repainted in place, redirected logs get one line per interval (0 to disable)"
        ),
    )
    parser.add_argument(
        "--plain-status",
        action="store_true",
        help="Disable the in-place status board and always log one status line per interval",
    )
    return parser


async def main_async(
    args: argparse.Namespace,
    policy: Optional[AccessPolicy] = None,
    board: Optional[StatusBoard] = None,
) -> None:
    adb_path = ADBClient.detect(args.adb_path)
    adb_client = ADBClient(adb_path)

    if policy is None:
        policy = AccessPolicy(allow=args.allow, deny=args.deny)
    _log_access_control(policy, args.blocklist_file)

    manager = DeviceManager(
        adb_client,
        listen_host=args.listen_host,
        device_tcp_port=args.device_tcp_port,
        forward_base_port=args.forward_base_port,
        proxy_base_port=args.proxy_base_port,
        poll_interval=args.poll_interval,
        include_serials=args.include,
        exclude_serials=args.exclude,
        local_ips=get_local_ips(),
        policy=policy,
    )

    if board is not None and board.enabled:
        # Interactive terminal: paint an in-place status table (refreshed every
        # --status-interval seconds and repainted under every log line) instead
        # of appending a status row each cycle. The adb connect commands then
        # stay visible at the bottom instead of scrolling away.
        board.set_render_fn(
            _make_status_render_fn(manager, stale_after=sync_stale_threshold(args.poll_interval))
        )
        board.refresh()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_shutdown() -> None:
        _LOG.info("Shutdown requested via signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows event loop may not support signal handlers; fallback to default behavior.
            pass

    await manager.start()

    status_task: Optional[asyncio.Task[None]] = None
    if args.status_interval > 0:
        status_task = asyncio.create_task(
            _status_logger(
                manager,
                args.status_interval,
                sync_stale_threshold(args.poll_interval),
                board,
            )
        )

    # Monitor devices and print connection info when they become ready
    print_task = asyncio.create_task(
        _print_connection_on_ready(manager, args.listen_host, args.proxy_base_port, board)
    )

    console = AdminConsole(manager, policy, blocklist_path=args.blocklist_file)
    console_task = asyncio.create_task(console.run(stop_event))

    try:
        await stop_event.wait()
    finally:
        if status_task:
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)
        print_task.cancel()
        await asyncio.gather(print_task, return_exceptions=True)
        console_task.cancel()
        await asyncio.gather(console_task, return_exceptions=True)
        await manager.stop()


def _log_access_control(policy: AccessPolicy, blocklist_path: Optional[str]) -> None:
    if policy.allowlist_enabled:
        _LOG.info("Access control: allow-list mode, allow=%s", policy.allow_entries())
    else:
        _LOG.info("Access control: deny-list mode (everyone allowed except denied peers)")
    deny_entries = policy.deny_entries()
    if deny_entries:
        _LOG.info("Access control: deny=%s", deny_entries)
    if blocklist_path:
        _LOG.info("Access control: blocklist file %s (kept updated on ban/unban)", blocklist_path)


def _make_status_render_fn(
    manager: DeviceManager,
    *,
    stale_after: float,
    ips_ttl: float = 10.0,
):
    """Build the status-board render callback.

    The board repaints on every log record as well, so the callback must stay
    cheap: session/connection lookups are in-memory, and the local-IP lookup
    (getaddrinfo) is cached for a few seconds.
    """

    cache = {"stamp": 0.0, "ips": []}  # type: dict

    def render(width: int) -> str:
        now = time.monotonic()
        if now - cache["stamp"] >= ips_ttl:
            cache["ips"] = get_local_ips()
            cache["stamp"] = now
        client_counts = {}
        for info in manager.connections():
            client_counts[info.device] = client_counts.get(info.device, 0) + 1
        return render_block(
            statuses=manager.statuses(),
            local_ips=cache["ips"],
            sync_age=manager.sync_age(),
            stale_after=stale_after,
            now_str=time.strftime("%H:%M:%S"),
            width=width,
            client_counts=client_counts,
        )

    return render


async def _status_logger(
    manager: DeviceManager,
    interval: float,
    stale_after: float = SYNC_STALE_MIN_SECONDS,
    board: Optional[StatusBoard] = None,
) -> None:
    """Periodic status output with a staleness alarm for the device sync loop.

    Interactive terminals get an in-place board repaint (no new log line per
    cycle, so the ``adb connect`` commands stay on screen); redirected output
    keeps the one-line-per-cycle format, each line carrying ``sync_age``
    (seconds since the last successful ``adb devices`` listing). Once that age
    crosses ``stale_after`` a WARNING is logged in both modes, because the
    session states come from a cache that stops updating when the sync loop
    stalls while dead forwards are no longer re-established.
    """

    try:
        while True:
            await asyncio.sleep(interval)
            age = manager.sync_age()
            if board is not None and board.enabled:
                board.refresh()
            else:
                fresh = "" if age is None else f" sync_age={age:.0f}s"
                statuses = manager.statuses()
                if not statuses:
                    _LOG.info("No active adb devices detected%s", fresh)
                else:
                    local_ips = get_local_ips()
                    for status in statuses:
                        for ip in local_ips:
                            _LOG.info(
                                "command: adb connect %s:%d %s%s",
                                ip,
                                status.proxy_port,
                                _format_status(status),
                                fresh,
                            )
            if age is not None and age > stale_after:
                _LOG.warning(
                    "Device sync loop looks stalled: last successful `adb devices` "
                    "listing was %.0fs ago (threshold %.0fs). The status lines above "
                    "are cached; dead forwards will not be re-established until the "
                    "loop recovers - check the adb server.",
                    age,
                    stale_after,
                )
    except asyncio.CancelledError:
        return


async def _print_connection_on_ready(
    manager: DeviceManager,
    listen_host: str,
    proxy_base_port: int,
    board: Optional[StatusBoard] = None,
) -> None:
    """Monitor devices and show connection info when they become ready."""
    # Track running devices to avoid duplicate prints
    seen_running = set()

    while not manager._stop_event.is_set():
        statuses = manager.statuses()
        new_serials = [
            status.serial
            for status in statuses
            if status.state.value == "running" and status.serial not in seen_running
        ]
        for serial in new_serials:
            seen_running.add(serial)

        if new_serials:
            if board is not None and board.enabled:
                # The board already carries the adb connect commands; log a one-
                # line marker for history and paint the board immediately so the
                # user does not wait for the next refresh interval.
                for serial in new_serials:
                    _LOG.info("Device ready: %s (commands on the status board)", serial)
                board.refresh()
            else:
                _print_connection_info(manager, listen_host, proxy_base_port)

        # Wait a bit before checking again
        await asyncio.sleep(1.0)


def _print_connection_info(manager: DeviceManager, listen_host: str, proxy_base_port: int) -> None:
    """Print connection information for all active devices."""
    local_ips = get_local_ips()
    statuses = manager.statuses()
    
    if not statuses:
        _LOG.info("No adb devices detected yet. Waiting for devices...")
        return
    
    _LOG.info("=" * 60)
    _LOG.info("ADB devices are now ready for remote connection!")
    _LOG.info("Remote users can connect using:")
    _LOG.info("")
    
    for status in statuses:
        if status.state.value == "running":
            _LOG.info("Device: %s", status.serial)
            if status.model:
                _LOG.info("  Model: %s", status.model)
            _LOG.info("  Proxy port: %d", status.proxy_port)
            _LOG.info("  Connection commands:")
            for ip in local_ips:
                _LOG.info("    adb connect %s:%d", ip, status.proxy_port)
            _LOG.info("")
    
    _LOG.info("Note: Use the appropriate IP address that is accessible from the remote machine")
    _LOG.info("=" * 60)


def _format_status(status: DeviceStatus) -> str:
    parts: List[str] = [
        f"serial={status.serial}",
        f"state={status.state.value}",
        f"proxy={status.proxy_port}",
        f"forward={status.forward_port}",
        f"tcp={status.tcp_port}",
    ]
    if status.model:
        parts.append(f"model={status.model}")
    if status.product:
        parts.append(f"product={status.product}")
    if status.last_error:
        parts.append(f"error={status.last_error}")
    return " ".join(parts)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        policy = AccessPolicy(allow=args.allow, deny=args.deny)
        if args.blocklist_file:
            for entry in read_blocklist(args.blocklist_file):
                policy.add_deny(entry)
    except ValueError as exc:
        parser.error(f"invalid access-control entry: {exc}")
    except OSError as exc:
        parser.error(f"cannot read blocklist file {args.blocklist_file}: {exc}")
    # On an interactive terminal the status is a repainted table (see
    # StatusBoard); redirected output falls back to plain per-interval lines.
    # The handler erases the table, writes one log record, then repaints the
    # table underneath -- so the shared adb connect commands never scroll away.
    board = StatusBoard(sys.stderr)
    if args.plain_status:
        board.disable()
    handler: logging.Handler
    if board.enabled:
        handler = StatusBoardHandler(sys.stderr, board)
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        handlers=[handler],
    )
    try:
        asyncio.run(main_async(args, policy, board))
    except KeyboardInterrupt:
        _LOG.info("Interrupted by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
