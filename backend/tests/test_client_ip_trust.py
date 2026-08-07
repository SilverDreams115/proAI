"""X-Forwarded-For is only an identity when a trusted peer said so.

Locks the fix for the throttle bypass: before this, both the global rate
limit and the login failure counter keyed off the raw header, so rotating
it handed every request a fresh bucket and neither control ever fired.
"""

from __future__ import annotations

from app.core.clientip import UNKNOWN_CLIENT, is_trusted_proxy, resolve_client_key


class TestResolveClientKey:
    def test_untrusted_peer_cannot_forge_an_identity(self) -> None:
        # The attack: a direct caller sets the header itself.
        key = resolve_client_key(
            peer="203.0.113.7",
            forwarded_for="10.9.9.9",
            trusted_proxies=[],
        )
        assert key == "203.0.113.7"

    def test_rotating_the_header_yields_one_stable_bucket(self) -> None:
        keys = {
            resolve_client_key(
                peer="203.0.113.7",
                forwarded_for=f"198.51.100.{n}",
                trusted_proxies=[],
            )
            for n in range(20)
        }
        assert keys == {"203.0.113.7"}

    def test_trusted_proxy_may_assert_the_original_client(self) -> None:
        key = resolve_client_key(
            peer="172.18.0.2",
            forwarded_for="198.51.100.4",
            trusted_proxies=["172.18.0.0/16"],
        )
        assert key == "198.51.100.4"

    def test_leftmost_entry_wins_through_a_trusted_chain(self) -> None:
        key = resolve_client_key(
            peer="172.18.0.2",
            forwarded_for="198.51.100.4, 172.18.0.9",
            trusted_proxies=["172.18.0.2"],
        )
        assert key == "198.51.100.4"

    def test_trusted_peer_with_blank_header_falls_back_to_socket(self) -> None:
        key = resolve_client_key(
            peer="172.18.0.2",
            forwarded_for="   ",
            trusted_proxies=["172.18.0.0/16"],
        )
        assert key == "172.18.0.2"

    def test_missing_peer_is_named_rather_than_empty(self) -> None:
        key = resolve_client_key(peer=None, forwarded_for=None, trusted_proxies=[])
        assert key == UNKNOWN_CLIENT

    def test_malformed_trust_entry_does_not_widen_trust(self) -> None:
        # A typo in the setting must fail closed, not open.
        key = resolve_client_key(
            peer="203.0.113.7",
            forwarded_for="10.9.9.9",
            trusted_proxies=["not-an-ip", ""],
        )
        assert key == "203.0.113.7"


class TestIsTrustedProxy:
    def test_exact_address_matches(self) -> None:
        assert is_trusted_proxy("10.0.0.7", ["10.0.0.7"]) is True

    def test_cidr_block_matches(self) -> None:
        assert is_trusted_proxy("10.0.0.7", ["10.0.0.0/24"]) is True

    def test_address_outside_the_block_is_untrusted(self) -> None:
        assert is_trusted_proxy("10.0.1.7", ["10.0.0.0/24"]) is False

    def test_empty_trust_list_trusts_nobody(self) -> None:
        assert is_trusted_proxy("10.0.0.7", []) is False

    def test_non_ip_peer_is_untrusted(self) -> None:
        assert is_trusted_proxy("nonsense", ["10.0.0.0/8"]) is False
