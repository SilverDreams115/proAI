"""Who the caller actually is, for throttling purposes.

Every per-client control in this app — the global rate limit and the
login failure counter — is only as good as the key it counts against.
Both used to read `X-Forwarded-For` unconditionally, which is correct
behind a reverse proxy and worthless without one: the header is
attacker-controlled, so rotating it per request gave every attempt a
fresh bucket and neither control ever fired. The login throttle
(5 failures / 300s) was measurably bypassable that way.

The header is still the right answer when a proxy really did set it,
so the rule is not "ignore it" but "trust it only from a peer allowed
to speak for someone else". `PROAI_TRUSTED_PROXY_IPS` lists those
peers (exact addresses or CIDR blocks); the deployment behind Caddy
sets it to the proxy network, and a directly-exposed instance leaves
it empty and falls back to the socket address, which cannot be forged.

Kept in one module because the two call sites must agree: a mismatch
would let a caller be throttled under one identity and counted under
another.
"""

from __future__ import annotations

from ipaddress import ip_address, ip_network
from typing import Iterable

UNKNOWN_CLIENT = "unknown"


def _networks(entries: Iterable[str]) -> list:
    networks = []
    for entry in entries:
        candidate = str(entry or "").strip()
        if not candidate:
            continue
        try:
            # strict=False so a host address ("10.0.0.7") and a block
            # ("10.0.0.0/24") can both be written in the same setting.
            networks.append(ip_network(candidate, strict=False))
        except ValueError:
            # A malformed entry must not widen trust. Drop it: the
            # effect is falling back to the socket address, which is
            # the safe direction to fail.
            continue
    return networks


def is_trusted_proxy(peer: str | None, trusted: Iterable[str]) -> bool:
    """True when `peer` is a peer allowed to assert a forwarded client."""
    if not peer:
        return False
    networks = _networks(trusted)
    if not networks:
        return False
    try:
        address = ip_address(peer.strip())
    except ValueError:
        return False
    return any(address in network for network in networks)


def resolve_client_key(
    *,
    peer: str | None,
    forwarded_for: str | None,
    trusted_proxies: Iterable[str],
) -> str:
    """Return the identity to throttle against.

    `X-Forwarded-For` is honoured only when `peer` is a trusted proxy.
    The leftmost entry is the original client per RFC 7239 conventions;
    it is only meaningful because every hop to its right was trusted to
    append honestly, which is precisely what the peer check establishes.
    """
    if forwarded_for and is_trusted_proxy(peer, trusted_proxies):
        candidate = forwarded_for.split(",", maxsplit=1)[0].strip()
        if candidate:
            return candidate
    if peer:
        return peer
    return UNKNOWN_CLIENT
