"""Access-control policy for proxy client connections.

Decides, per inbound client, whether the proxy may serve it. Entries are IPv4
or IPv6 addresses or CIDR networks (``192.168.1.5``, ``10.0.0.0/8``,
``fe80::/10``). Semantics:

* a peer matching any deny entry is rejected (deny wins over everything);
* otherwise, if the allow list is non-empty, only peers matching an allow
  entry (plus loopback, implicitly) are accepted;
* otherwise everyone is accepted.
"""
from __future__ import annotations

import ipaddress
import os
from typing import Dict, Iterable, List, Sequence, Tuple, Union

Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

_BLOCKLIST_HEADER = "# shareadb deny list - one IP or CIDR per line\n"


def normalize_peer_host(host: str) -> str:
    """Canonicalize a peer address for policy checks.

    Strips surrounding brackets (``[::1]``) and an IPv6 zone scope
    (``fe80::1%eth0``) so ``ipaddress`` can parse the result.
    """

    host = str(host).strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.split("%", 1)[0]


def parse_network(entry: str) -> Network:
    """Parse ``entry`` as a host address or CIDR network.

    A bare address becomes a /32 (or /128) network, so plain IPs and subnets
    can be stored uniformly. Raises ``ValueError`` for anything unparseable.
    """

    return ipaddress.ip_network(normalize_peer_host(entry), strict=False)


def entry_matches(entry: str, host: str) -> bool:
    """True if peer ``host`` falls inside network ``entry``.

    Unparseable input never matches (a kick pattern that cannot be parsed
    simply selects nothing) instead of raising, so management commands stay
    safe to issue against live traffic.
    """

    try:
        network = parse_network(entry)
        address = ipaddress.ip_address(normalize_peer_host(host))
    except ValueError:
        return False
    # Cross-family comparisons are False, not errors.
    return address in network


def read_blocklist(path: str) -> List[str]:
    """Load deny entries from a blocklist file (one per line, ``#`` comments)."""

    entries: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.split("#", 1)[0].strip()
            if stripped:
                entries.append(stripped)
    return entries


def write_blocklist(path: str, entries: Iterable[str]) -> None:
    """Persist deny entries to ``path`` (atomically via a same-dir temp file)."""

    lines = [_BLOCKLIST_HEADER]
    lines.extend(f"{entry}\n" for entry in entries)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    os.replace(tmp_path, path)


class AccessPolicy:
    """Mutable allow/deny rule set shared by all proxies of one server run."""

    def __init__(
        self,
        allow: Sequence[str] = (),
        deny: Sequence[str] = (),
    ) -> None:
        self._allow: List[Network] = [parse_network(e) for e in allow]
        self._deny: List[Network] = [parse_network(e) for e in deny]
        # Peer host -> number of connection attempts rejected so far. Lets the
        # operator see who keeps knocking on a closed door.
        self.rejected: Dict[str, int] = {}

    @property
    def allowlist_enabled(self) -> bool:
        """True when only explicitly allowed peers may connect."""

        return bool(self._allow)

    def allow_entries(self) -> List[str]:
        return [str(net) for net in self._allow]

    def deny_entries(self) -> List[str]:
        return [str(net) for net in self._deny]

    def check(self, host: str) -> Tuple[bool, str]:
        """Return ``(allowed, reason)`` for a peer address.

        Unparseable peer addresses are rejected: the peername from a TCP
        accept is always numeric, so anything else means something is off.
        """

        normalized = normalize_peer_host(host)
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            self._note_rejection(normalized)
            return False, f"unparseable peer address {normalized!r}"

        for net in self._deny:
            if address in net:
                self._note_rejection(normalized)
                return False, f"matched deny rule {net}"
        if self._allow:
            if address.is_loopback:
                return True, "loopback implicitly allowed"
            for net in self._allow:
                if address in net:
                    return True, f"matched allow rule {net}"
            self._note_rejection(normalized)
            return False, "not in allow list"
        return True, ""

    def add_deny(self, entry: str) -> bool:
        """Append a deny rule; returns False if an identical rule exists."""

        net = parse_network(entry)  # ValueError propagates to the caller
        if net in self._deny:
            return False
        self._deny.append(net)
        return True

    def remove_deny(self, entry: str) -> bool:
        """Remove a deny rule; returns False if it was not present."""

        net = parse_network(entry)
        try:
            self._deny.remove(net)
            return True
        except ValueError:
            return False

    def _note_rejection(self, host: str) -> None:
        self.rejected[host] = self.rejected.get(host, 0) + 1
