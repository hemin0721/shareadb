"""Tests for the proxy access-control policy."""
from __future__ import annotations

import pytest

from shareadb.access import (
    AccessPolicy,
    entry_matches,
    normalize_peer_host,
    parse_network,
    read_blocklist,
    write_blocklist,
)


def test_check_emptyPolicy_allowsEveryone() -> None:
    policy = AccessPolicy()

    assert policy.check("192.168.1.50") == (True, "")
    assert policy.check("10.0.0.9") == (True, "")


def test_check_denyExactIp_rejectsOnlyThatIp() -> None:
    policy = AccessPolicy(deny=["10.0.0.9"])

    allowed, _ = policy.check("10.0.0.8")
    denied, reason = policy.check("10.0.0.9")

    assert allowed is True
    assert denied is False
    assert "10.0.0.9/32" in reason


def test_check_denyCidr_rejectsWholeSubnet() -> None:
    policy = AccessPolicy(deny=["172.16.0.0/16"])

    assert policy.check("172.16.5.5")[0] is False
    assert policy.check("172.17.0.1")[0] is True


def test_check_allowListMode_rejectsUnlistedPeers() -> None:
    policy = AccessPolicy(allow=["192.168.1.0/24"])

    assert policy.check("192.168.1.77")[0] is True
    assert policy.check("10.0.0.9")[0] is False


def test_check_allowListMode_implicitlyAllowsLoopback() -> None:
    policy = AccessPolicy(allow=["192.168.1.0/24"])

    assert policy.check("127.0.0.1")[0] is True
    assert policy.check("::1")[0] is True


def test_check_denyWinsOverAllow() -> None:
    policy = AccessPolicy(allow=["10.0.0.0/8"], deny=["10.0.0.9"])

    assert policy.check("10.0.0.10")[0] is True
    assert policy.check("10.0.0.9")[0] is False


def test_check_explicitLoopbackDeny_beatsImplicitAllow() -> None:
    policy = AccessPolicy(allow=["10.0.0.0/8"], deny=["127.0.0.0/8"])

    assert policy.check("127.0.0.1")[0] is False


def test_check_ipv6Supported() -> None:
    policy = AccessPolicy(allow=["fe80::/10"])

    assert policy.check("fe80::1234")[0] is True
    # Cross-family: an IPv6 allow rule does not admit IPv4 peers.
    assert policy.check("192.168.1.5")[0] is False


def test_check_ipv6DenyWithScopeStripped() -> None:
    policy = AccessPolicy(deny=["fe80::1"])

    assert policy.check("fe80::1%eth0")[0] is False


def test_check_unparseablePeer_rejectedAndCounted() -> None:
    policy = AccessPolicy()

    allowed, reason = policy.check("not-an-ip")

    assert allowed is False
    assert "unparseable" in reason
    assert policy.rejected == {"not-an-ip": 1}


def test_check_rejectionsCountedPerHost() -> None:
    policy = AccessPolicy(deny=["10.0.0.9"])

    policy.check("10.0.0.9")
    policy.check("10.0.0.9")
    policy.check("10.0.0.8")

    assert policy.rejected == {"10.0.0.9": 2}


def test_parseNetwork_rejectsGarbage() -> None:
    with pytest.raises(ValueError):
        parse_network("999.999.999.999")
    with pytest.raises(ValueError):
        AccessPolicy(deny=["hello"])


def test_normalizePeerHost_stripsBracketsAndScope() -> None:
    assert normalize_peer_host("[::1]") == "::1"
    assert normalize_peer_host("fe80::1%eth0") == "fe80::1"
    assert normalize_peer_host(" 10.0.0.9 ") == "10.0.0.9"


def test_entryMatches_ipAndCidr() -> None:
    assert entry_matches("10.0.0.9", "10.0.0.9") is True
    assert entry_matches("10.0.0.0/8", "10.9.9.9") is True
    assert entry_matches("10.0.0.9", "10.0.0.8") is False
    assert entry_matches("garbage", "10.0.0.9") is False
    assert entry_matches("10.0.0.9", "garbage") is False


def test_addRemoveDeny_idempotentFlags() -> None:
    policy = AccessPolicy()

    assert policy.add_deny("10.0.0.9") is True
    assert policy.add_deny("10.0.0.9") is False  # already present
    assert policy.deny_entries() == ["10.0.0.9/32"]

    assert policy.remove_deny("10.0.0.9") is True
    assert policy.remove_deny("10.0.0.9") is False
    assert policy.deny_entries() == []


def test_blocklistFile_roundTrip(tmp_path) -> None:
    path = str(tmp_path / "blocklist.txt")
    write_blocklist(path, ["10.0.0.9", "172.16.0.0/16"])

    entries = read_blocklist(path)

    assert entries == ["10.0.0.9", "172.16.0.0/16"]

    # Reload into a policy and verify the entries still bite.
    policy = AccessPolicy()
    for entry in read_blocklist(path):
        policy.add_deny(entry)
    assert policy.check("172.16.31.4")[0] is False


def test_readBlocklist_ignoresCommentsAndBlanks(tmp_path) -> None:
    path = tmp_path / "bl.txt"
    path.write_text("# comment line\n\n10.0.0.9  # trailing comment\n\n", encoding="utf-8")

    assert read_blocklist(str(path)) == ["10.0.0.9"]


def test_readBlocklist_missingFile_raisesOSError(tmp_path) -> None:
    with pytest.raises(OSError):
        read_blocklist(str(tmp_path / "missing.txt"))
