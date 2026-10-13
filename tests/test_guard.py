"""GUARD — the offline socket guard, bootstrap sequence included (§17.12).

The guard blocks ``connect``, ``connect_ex``, ``sendto``, ``sendmsg`` and
``asyncio.open_connection`` to non-allowlisted destinations; ``getaddrinfo``
for a foreign hostname raises before a DNS packet leaves; ``getaddrinfo`` for
the configured ``DATABASE_URL`` host succeeds through the saved original
resolver; ``bind``/``listen``/``accept`` remain untouched so uvicorn can serve.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from longwatch import offline_guard as guard

FOREIGN_IP = "93.184.216.34"


@pytest.fixture()
def guarded(dsn):
    info = guard.bootstrap_and_install(dsn)
    try:
        yield info
    finally:
        guard.uninstall()


def test_bootstrap_resolves_the_database_host(guarded, dsn):
    host = guard.parse_database_host(dsn)
    assert guarded["installed"] is True
    assert guarded["host"] == host
    if guard._is_literal_ip(host or ""):
        assert guarded["literal_ip"] is True
    else:
        assert guarded["resolved_ips"]


def test_sanctioned_host_resolves_and_the_database_stays_reachable(guarded, db):
    host = guard.parse_database_host(db.dsn)
    infos = socket.getaddrinfo(host, 5432, type=socket.SOCK_STREAM)
    assert infos
    db.ping()  # the allowlisted egress is genuinely usable


def test_foreign_hostname_resolution_raises(guarded):
    with pytest.raises(guard.OfflineViolation):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(guard.OfflineViolation):
        socket.create_connection(("example.com", 443), timeout=1)


def test_connect_connect_ex_and_sendto_are_refused(guarded):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(guard.OfflineViolation):
            sock.connect((FOREIGN_IP, 443))
        with pytest.raises(guard.OfflineViolation):
            sock.connect_ex((FOREIGN_IP, 443))
    finally:
        sock.close()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(guard.OfflineViolation):
            udp.sendto(b"payload", (FOREIGN_IP, 53))
    finally:
        udp.close()


def test_asyncio_open_connection_is_refused(guarded):
    async def attempt():
        return await asyncio.open_connection("example.com", 80)

    with pytest.raises(guard.OfflineViolation):
        asyncio.run(attempt())


def test_bind_listen_and_accept_still_work(guarded):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        async def connect_and_send():
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"ok")
            await writer.drain()
            server.settimeout(5)
            accepted, _address = server.accept()
            payload = accepted.recv(2)
            accepted.close()
            writer.close()
            return payload

        assert asyncio.run(connect_and_send()) == b"ok"
    finally:
        server.close()


def test_uninstall_restores_the_originals(dsn):
    guard.bootstrap_and_install(dsn)
    guard.uninstall()
    assert socket.getaddrinfo is guard._ORIG_GETADDRINFO
    assert socket.socket.connect is guard._ORIG_CONNECT
    assert socket.socket.sendto is guard._ORIG_SENDTO
    assert socket.socket.connect_ex is guard._ORIG_CONNECT_EX
    assert socket.socket.sendmsg is guard._ORIG_SENDMSG


def test_parse_database_host_handles_url_and_keyword_forms():
    assert guard.parse_database_host("postgresql://u:p@db.internal:5432/lw") == "db.internal"
    assert guard.parse_database_host("host=db.internal port=5432 dbname=lw") == "db.internal"
    assert guard.parse_database_host("postgresql://u:p@10.0.0.5:5432/lw") == "10.0.0.5"
    assert guard.parse_database_host("") is None


def test_a_unix_socket_dsn_is_local_by_construction():
    """A socket-path DSN carries no hostname: bootstrap must not resolve the
    path as a DNS name (a raw gaierror crash), because AF_UNIX egress is local
    by construction — the same judgment the guard makes for connected sockets."""
    for dsn in ("postgresql://postgres@/lwtest?host=/var/run/postgresql",
                "host=/var/run/postgresql dbname=lwtest"):
        host = guard.parse_database_host(dsn)
        assert host == "/var/run/postgresql"
        info = guard.bootstrap(dsn)  # must not touch the resolver at all
        assert info["unix_socket"] is True
        assert info["resolved"] is False and info["ips"] == []
    guard.uninstall()
