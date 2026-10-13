"""The offline socket guard (§1 refusal 3, §5 bootstrap sequence).

The running container initiates zero outbound connections other than DNS
resolution of the configured ``DATABASE_URL`` hostname — the single sanctioned
egress, exercised once at startup.  A runtime egress bug is a crash, not a
leak.

Bootstrap order (explicit, no circularity):

1. at import time the guard saves the original resolver;
2. ``bootstrap(dsn)`` parses the DSN — a literal IP is allowlisted and needs no
   resolution, otherwise the host is resolved **once** through the saved
   resolver and the returned IPs are cached;
3. ``install()`` patches ``socket.socket`` (connect/connect_ex/sendto/sendmsg)
   and ``socket.getaddrinfo`` with the allowlist;
4. only then does the application open its DB pool.

``bind()``/``listen()``/``accept()`` are untouched so uvicorn can serve.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import parse_qs, urlsplit

# Saved before any patching.  Module import order guarantees these are the
# originals: nothing else in the process patches sockets.
_ORIG_GETADDRINFO = socket.getaddrinfo
_ORIG_CONNECT = socket.socket.connect
_ORIG_CONNECT_EX = socket.socket.connect_ex
_ORIG_SENDTO = socket.socket.sendto
_ORIG_SENDMSG = socket.socket.sendmsg

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class OfflineViolation(RuntimeError):
    """Raised before a packet leaves the machine."""


class _State:
    installed = False
    host: str | None = None
    allowed_ips: set[str] = set()
    resolver = _ORIG_GETADDRINFO


def parse_database_host(dsn: str) -> str | None:
    """Extract the host from a Postgres DSN (URL or keyword form)."""
    if not dsn:
        return None
    if "://" in dsn:
        parts = urlsplit(dsn)
        if parts.hostname:
            return parts.hostname
        query = parse_qs(parts.query)
        if query.get("host"):
            return query["host"][0]
        return None
    # keyword/value form: host=db port=5432 dbname=...
    for chunk in dsn.split():
        if chunk.startswith("host="):
            return chunk.split("=", 1)[1]
    return None


def _is_literal_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _normalise_ip(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def bootstrap(dsn: str) -> dict:
    """Resolve the DB host through the saved resolver and cache the IPs."""
    host = parse_database_host(dsn)
    result = {"host": host, "ips": [], "resolved": False, "literal_ip": False,
              "unix_socket": False}
    _State.host = host
    if host is None:
        return result
    if host.startswith("/"):
        # a unix-socket DSN (e.g. host=/var/run/postgresql) is local by
        # construction — the same judgment `_address_allowed` makes for AF_UNIX
        # addresses; there is no hostname to resolve and nothing to allowlist
        result["unix_socket"] = True
        return result
    if _is_literal_ip(host):
        result["literal_ip"] = True
        result["ips"] = [_normalise_ip(host)]
        _State.allowed_ips = {_normalise_ip(host)}
        return result
    infos = _ORIG_GETADDRINFO(host, 5432, type=socket.SOCK_STREAM)
    ips = sorted({info[4][0] for info in infos})
    result["ips"] = ips
    result["resolved"] = True
    _State.allowed_ips = {_normalise_ip(ip) for ip in ips}
    return result


def _ip_allowed(host: str) -> bool:
    if host in LOCAL_HOSTS:
        return True
    if _State.host is not None and host == _State.host:
        return True
    return _normalise_ip(host) in _State.allowed_ips


def _address_allowed(address) -> bool:
    if address is None:
        return True  # connected socket: sendto(None) on a stream socket
    if isinstance(address, (str, bytes)):
        return True  # AF_UNIX path — local by construction
    if isinstance(address, tuple) and address:
        return _ip_allowed(str(address[0]))
    return False


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    if host in (None, ""):
        return _State.resolver(host, port, *args, **kwargs)
    if isinstance(host, (bytes, bytearray)):
        host = host.decode("utf-8", "replace")
    if _ip_allowed(str(host)):
        return _State.resolver(host, port, *args, **kwargs)
    raise OfflineViolation(
        f"DNS resolution of {host!r} is refused: only the configured database "
        f"host ({_State.host!r}) is a sanctioned egress"
    )


def _guarded_connect(self, address):
    if _address_allowed(address):
        return _ORIG_CONNECT(self, address)
    raise OfflineViolation(f"outbound connect to {address!r} is refused (offline guard)")


def _guarded_connect_ex(self, address):
    if _address_allowed(address):
        return _ORIG_CONNECT_EX(self, address)
    raise OfflineViolation(f"outbound connect_ex to {address!r} is refused (offline guard)")


def _guarded_sendto(self, data, *args):
    address = args[-1] if args else None
    if _address_allowed(address):
        return _ORIG_SENDTO(self, data, *args)
    raise OfflineViolation(f"outbound sendto to {address!r} is refused (offline guard)")


def _guarded_sendmsg(self, *args, **kwargs):
    address = kwargs.get("address")
    if address is None and len(args) >= 3:
        address = args[2]
    if _address_allowed(address):
        return _ORIG_SENDMSG(self, *args, **kwargs)
    raise OfflineViolation(f"outbound sendmsg to {address!r} is refused (offline guard)")


def install() -> dict:
    """Install the patch.  Idempotent."""
    if _State.installed:
        return {"installed": True, "host": _State.host, "ips": sorted(_State.allowed_ips)}
    socket.getaddrinfo = _guarded_getaddrinfo
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.socket.sendto = _guarded_sendto
    socket.socket.sendmsg = _guarded_sendmsg
    _State.installed = True
    return {"installed": True, "host": _State.host, "ips": sorted(_State.allowed_ips)}


def uninstall() -> None:
    """Restore the originals (tests and tooling only)."""
    socket.getaddrinfo = _ORIG_GETADDRINFO
    socket.socket.connect = _ORIG_CONNECT
    socket.socket.connect_ex = _ORIG_CONNECT_EX
    socket.socket.sendto = _ORIG_SENDTO
    socket.socket.sendmsg = _ORIG_SENDMSG
    _State.installed = False


def status() -> dict:
    return {
        "installed": _State.installed,
        "database_host": _State.host,
        "allowed_ips": sorted(_State.allowed_ips),
        "sanctioned_egress": (
            "DNS resolution of the configured DATABASE_URL host only"
            if _State.host else "none (no database host configured)"
        ),
    }


def bootstrap_and_install(dsn: str) -> dict:
    info = bootstrap(dsn)
    installed = install()
    return {**installed, "resolved_ips": info["ips"], "literal_ip": info["literal_ip"]}
