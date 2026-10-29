"""Tests for the runtime admin console command handling."""
from __future__ import annotations

import asyncio
import logging

import pytest

from shareadb.access import AccessPolicy, read_blocklist
from shareadb.admin_console import AdminConsole, _fmt_bytes
from shareadb.device_manager import DeviceStatus, SessionState
from shareadb.tcp_proxy import ConnectionInfo


class FakeManager:
    """Duck-typed DeviceManager for console tests."""

    def __init__(self, conns=None) -> None:
        self._conns = list(conns or [])
        self.kick_patterns = []

    def statuses(self):
        return [
            DeviceStatus(
                serial="3696ade",
                state=SessionState.RUNNING,
                forward_port=6000,
                proxy_port=7000,
                tcp_port=5555,
            )
        ]

    def connections(self):
        return list(self._conns)

    def kick(self, pattern: str) -> int:
        self.kick_patterns.append(pattern)
        # Report the connections that matched (simplification: all of them).
        return len(self._conns)


def _conn(host="10.0.0.9", port=51234) -> ConnectionInfo:
    return ConnectionInfo(
        peer_host=host,
        peer_port=port,
        proxy_name="3696ade:7000->6000",
        connected_at=1_700_000_000.0,
        bytes_from_client=2048,
        bytes_to_client=512,
        device="3696ade",
    )


def test_handleLine_helpLogsCommandList(caplog: pytest.LogCaptureFixture) -> None:
    console = AdminConsole(FakeManager(), AccessPolicy())
    caplog.set_level(logging.INFO)

    console.handle_line("help")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "ban <ip|cidr>" in messages
    assert "clients" in messages


def test_handleLine_clientsListsConnections(caplog: pytest.LogCaptureFixture) -> None:
    console = AdminConsole(FakeManager([_conn()]), AccessPolicy())
    caplog.set_level(logging.INFO)

    console.handle_line("clients")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "10.0.0.9:51234" in messages
    assert "device=3696ade" in messages
    assert "up=2.0KB" in messages
    assert "down=512.0B" in messages


def test_handleLine_clientsEmptyReportsNone(caplog: pytest.LogCaptureFixture) -> None:
    console = AdminConsole(FakeManager(), AccessPolicy())
    caplog.set_level(logging.INFO)

    console.handle_line("clients")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "none connected" in messages


def test_handleLine_kickForwardsPattern(caplog: pytest.LogCaptureFixture) -> None:
    manager = FakeManager([_conn()])
    console = AdminConsole(manager, AccessPolicy())
    caplog.set_level(logging.INFO)

    console.handle_line("kick 10.0.0.9")

    assert manager.kick_patterns == ["10.0.0.9"]
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "closed 1 connection(s)" in messages


def test_handleLine_kickWithoutArg_logsError(caplog: pytest.LogCaptureFixture) -> None:
    manager = FakeManager()
    console = AdminConsole(manager, AccessPolicy())
    caplog.set_level(logging.INFO)

    console.handle_line("kick")

    assert manager.kick_patterns == []
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "missing" in messages


def test_handleLine_banAddsRuleKicksAndPersists(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    manager = FakeManager([_conn()])
    policy = AccessPolicy()
    blocklist = str(tmp_path / "blocklist.txt")
    console = AdminConsole(manager, policy, blocklist_path=blocklist)
    caplog.set_level(logging.INFO)

    console.handle_line("ban 10.0.0.9")

    assert policy.deny_entries() == ["10.0.0.9/32"]
    assert manager.kick_patterns == ["10.0.0.9"]
    assert read_blocklist(blocklist) == ["10.0.0.9/32"]
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "peer denied from now on" in messages
    assert "blocklist saved" in messages


def test_handleLine_banExistingEntry_reportsAlreadyDenied(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    manager = FakeManager()
    policy = AccessPolicy(deny=["10.0.0.9"])
    console = AdminConsole(manager, policy)
    caplog.set_level(logging.INFO)

    console.handle_line("ban 10.0.0.9")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "already denied" in messages


def test_handleLine_banInvalidEntry_rejectedWithoutSideEffects(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    manager = FakeManager([_conn()])
    policy = AccessPolicy()
    blocklist = str(tmp_path / "blocklist.txt")
    console = AdminConsole(manager, policy, blocklist_path=blocklist)
    caplog.set_level(logging.INFO)

    console.handle_line("ban not-an-ip")

    assert policy.deny_entries() == []
    assert manager.kick_patterns == []
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "invalid address" in messages


def test_handleLine_unbanRemovesRuleAndPersists(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    manager = FakeManager()
    policy = AccessPolicy(deny=["10.0.0.9", "172.16.0.0/16"])
    blocklist = str(tmp_path / "blocklist.txt")
    console = AdminConsole(manager, policy, blocklist_path=blocklist)
    caplog.set_level(logging.INFO)

    console.handle_line("unban 10.0.0.9")

    assert policy.deny_entries() == ["172.16.0.0/16"]
    assert read_blocklist(blocklist) == ["172.16.0.0/16"]
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "removed from deny list" in messages


def test_handleLine_unbanMissingEntry_reportsNotPresent(caplog: pytest.LogCaptureFixture) -> None:
    console = AdminConsole(FakeManager(), AccessPolicy())
    caplog.set_level(logging.INFO)

    console.handle_line("unban 10.0.0.9")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "not in deny list" in messages


def test_handleLine_policyShowsRulesAndRejections(caplog: pytest.LogCaptureFixture) -> None:
    policy = AccessPolicy(allow=["192.168.0.0/16"], deny=["10.0.0.9"])
    console = AdminConsole(FakeManager(), policy)
    caplog.set_level(logging.INFO)

    console.handle_line("policy")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "allow-list" in messages
    assert "192.168.0.0/16" in messages
    assert "10.0.0.9" in messages
    assert "no rejected attempts" in messages


def test_handleLine_policyShowsRejectionCounts(caplog: pytest.LogCaptureFixture) -> None:
    policy = AccessPolicy(deny=["10.0.0.9"])
    policy.check("10.0.0.9")
    policy.check("10.0.0.9")
    console = AdminConsole(FakeManager(), policy)
    caplog.set_level(logging.INFO)

    console.handle_line("policy")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "rejected attempts from 10.0.0.9: 2" in messages


def test_handleLine_statusListsDevices(caplog: pytest.LogCaptureFixture) -> None:
    console = AdminConsole(FakeManager(), AccessPolicy())
    caplog.set_level(logging.INFO)

    console.handle_line("status")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "serial=3696ade" in messages
    assert "state=running" in messages


def test_handleLine_unknownCommand_warns(caplog: pytest.LogCaptureFixture) -> None:
    console = AdminConsole(FakeManager(), AccessPolicy())
    caplog.set_level(logging.INFO)

    console.handle_line("explode")

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "Unknown management command" in messages


def test_handleLine_blankLineIsNoop(caplog: pytest.LogCaptureFixture) -> None:
    console = AdminConsole(FakeManager(), AccessPolicy())
    caplog.set_level(logging.DEBUG)

    console.handle_line("   \n")

    assert caplog.records == []


async def test_run_isCancellable() -> None:
    # run() blocks on the stdin queue (stdin is unusable under pytest), so it
    # must simply stay alive until cancelled - like the other CLI tasks.
    console = AdminConsole(FakeManager(), AccessPolicy())
    task = asyncio.create_task(console.run(asyncio.Event()))
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_fmtBytes_units() -> None:
    assert _fmt_bytes(0) == "0.0B"
    assert _fmt_bytes(512) == "512.0B"
    assert _fmt_bytes(2048) == "2.0KB"
    assert _fmt_bytes(5 * 1024 * 1024) == "5.0MB"
