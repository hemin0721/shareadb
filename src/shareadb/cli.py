"""Command line entry point for shareadb."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
from typing import Iterable, List, Optional

from .adb_client import ADBClient
from .device_manager import DeviceManager, DeviceStatus


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
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=30.0,
        help="Seconds between periodic status logs (0 to disable)",
    )
    return parser


async def main_async(args: argparse.Namespace) -> None:
    adb_path = ADBClient.detect(args.adb_path)
    adb_client = ADBClient(adb_path)

    manager = DeviceManager(
        adb_client,
        listen_host=args.listen_host,
        device_tcp_port=args.device_tcp_port,
        forward_base_port=args.forward_base_port,
        proxy_base_port=args.proxy_base_port,
        poll_interval=args.poll_interval,
        include_serials=args.include,
    )

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
        status_task = asyncio.create_task(_status_logger(manager, args.status_interval))

    # Monitor devices and print connection info when they become ready
    print_task = asyncio.create_task(_print_connection_on_ready(manager, args.listen_host, args.proxy_base_port))

    try:
        await stop_event.wait()
    finally:
        if status_task:
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)
        print_task.cancel()
        await asyncio.gather(print_task, return_exceptions=True)
        await manager.stop()


async def _status_logger(manager: DeviceManager, interval: float) -> None:
    try:
        while True:
            await asyncio.sleep(interval)
            statuses = manager.statuses()
            if not statuses:
                _LOG.info("No active adb devices detected")
            else:
                for status in statuses:
                    _LOG.info("%s", _format_status(status))
    except asyncio.CancelledError:
        return


async def _print_connection_on_ready(manager: DeviceManager, listen_host: str, proxy_base_port: int) -> None:
    """Monitor devices and print connection info when they become ready."""
    # Track running devices to avoid duplicate prints
    seen_running = set()
    
    while not manager._stop_event.is_set():
        statuses = manager.statuses()
        has_new = False
        
        for status in statuses:
            if status.state.value == "running" and status.serial not in seen_running:
                seen_running.add(status.serial)
                has_new = True
        
        # Print connection info if we have new running devices
        if has_new:
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
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        _LOG.info("Interrupted by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
