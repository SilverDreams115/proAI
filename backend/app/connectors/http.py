from __future__ import annotations

from ipaddress import ip_address
import socket
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler
from urllib.request import Request
from urllib.request import build_opener


class UnsafeSourceUrlError(ValueError):
    pass


def validate_public_https_url(raw_url: str) -> None:
    parsed = urlparse(raw_url)
    if parsed.scheme.lower() != "https":
        raise UnsafeSourceUrlError("Only https source URLs are allowed.")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise UnsafeSourceUrlError("Source URL must include a hostname.")
    if hostname in {"localhost", "0.0.0.0"} or hostname.endswith(".local") or hostname.endswith(".internal"):
        raise UnsafeSourceUrlError("Local or internal source hosts are not allowed.")

    for resolved in _resolve_host_ips(hostname):
        if (
            resolved.is_private
            or resolved.is_loopback
            or resolved.is_link_local
            or resolved.is_multicast
            or resolved.is_reserved
            or resolved.is_unspecified
        ):
            raise UnsafeSourceUrlError("Private or non-routable source hosts are not allowed.")


def safe_urlopen(request: Request | str, timeout: int = 15):
    raw_url = request.full_url if isinstance(request, Request) else request
    validate_public_https_url(raw_url)
    opener = build_opener(_ValidatingRedirectHandler)
    return opener.open(request, timeout=timeout)


def _resolve_host_ips(hostname: str):
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeSourceUrlError(f"Source host could not be resolved: {hostname}.") from exc
    resolved = []
    for address in addresses:
        ip_value = address[4][0]
        try:
            resolved.append(ip_address(ip_value))
        except ValueError as exc:
            raise UnsafeSourceUrlError(f"Source host resolved to an invalid IP: {ip_value}.") from exc
    return resolved


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        validate_public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
