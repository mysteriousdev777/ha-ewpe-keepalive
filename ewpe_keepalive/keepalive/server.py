"""What the module asks: connections on the dis port (allowed_ips, TLS, one request, one
reply, close) and, with the DNS server on, its DNS queries. Runs the status endpoint next
to them."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import ssl
import time

from . import __version__, dns
from .config import Config
from .protocol import MAX_REQUEST, BadRequest, module_id, parse_request, reply
from .status import StatusServer
from .tls import TLSConnection, server_context
from .tracker import HOUR, Tracker

log = logging.getLogger("keepalive")

MAX_CONNECTIONS = 8  # the module holds one for about a second, every 5 s
TLS_TIMEOUT = 10  # s for the TLS handshake
REQUEST_TIMEOUT = 10  # s for the request after it
CLOSE_TIMEOUT = 5  # s to wait for a closed connection to finish
MAX_NOTED = 64  # things logged once: rejected addresses, TLS errors, request types


def _peer_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(host.split("%", maxsplit=1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _reason(e: OSError) -> str:
    if isinstance(e, TimeoutError):
        return "timeout"
    if isinstance(e, ssl.SSLError) and e.reason:
        return e.reason  # NO_SHARED_CIPHER, WRONG_VERSION_NUMBER, ...
    return type(e).__name__


async def _read_request(conn: TLSConnection) -> tuple[dict, bytes]:
    """The request and its bytes as received (for the debug log: a parsed request can nest
    too deep to be serialized again)."""
    buf = b""
    while True:
        chunk = await conn.read(1024)
        if not chunk:
            raise BadRequest("closed before a whole request")
        buf += chunk
        if len(buf) > MAX_REQUEST:
            raise BadRequest(f"more than {MAX_REQUEST} bytes")
        request = parse_request(buf)
        if request is not None:
            return request, buf


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), CLOSE_TIMEOUT)


class KeepaliveServer:
    def __init__(self, cfg: Config, tls: ssl.SSLContext | None = None) -> None:
        self.cfg = cfg
        self.tls = tls or server_context()
        self.allowed = {ipaddress.ip_address(ip) for ip in cfg.allowed_ips}
        self.dns_answer = (
            ipaddress.IPv4Address(cfg.dns_address).packed if cfg.dns_address else None
        )
        self.tracker = Tracker()
        self.port: int | None = None
        self.dns_port: int | None = None
        self.status_port: int | None = None
        self._server: asyncio.base_events.Server | None = None
        self._dns: asyncio.DatagramTransport | None = None
        self._status: StatusServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._noted: set[str] = set()
        self._summary_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._conn, self.cfg.host, self.cfg.port
        )
        self.port = self._server.sockets[0].getsockname()[1]
        if self.dns_answer:
            self._dns = await dns.serve(
                self._dns_query, self.cfg.host, self.cfg.dns_port
            )
            self.dns_port = self._dns.get_extra_info("sockname")[1]
        if self.cfg.status_port is not None:
            self._status = StatusServer(self.status, self.serving)
            await self._status.start(self.cfg.host, self.cfg.status_port)
            self.status_port = self._status.port
        self._summary_task = asyncio.create_task(self._summaries())

    async def stop(self) -> None:
        if self._summary_task:
            self._summary_task.cancel()
        if self._dns:
            self._dns.close()
        for server in (self._server, self._status):
            if server:
                server.close()
        for writer in list(self._writers):
            writer.transport.abort()
        for server in (self._server, self._status):
            if server:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(server.wait_closed(), CLOSE_TIMEOUT)

    def serving(self) -> bool:
        """For /health: the dis port and, if on, the DNS server are open."""
        dns_ok = self.dns_answer is None or (
            self._dns is not None and not self._dns.is_closing()
        )
        return self._server is not None and self._server.is_serving() and dns_ok

    def status(self) -> dict:
        return {
            "version": __version__,
            "dns_server": self.dns_answer is not None,
            **self.tracker.snapshot(time.monotonic()),
        }

    async def _summaries(self) -> None:
        while True:
            await asyncio.sleep(HOUR)
            log.info(
                self.tracker.summary(time.monotonic(), dns=self.dns_answer is not None)
            )

    def _note(self, key: str, msg: str, *args) -> None:
        """Log at info once per key; the hourly summary and the status count the rest."""
        if key in self._noted or len(self._noted) >= MAX_NOTED:
            return
        self._noted.add(key)
        log.info(msg, *args)

    async def _conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        where = f"{peer[0]}:{peer[1]}"
        ip = _peer_ip(str(peer[0]))
        if self.allowed and ip not in self.allowed:
            self.tracker.count("rejected", time.monotonic())
            log.debug("%s rejected: not in allowed_ips", where)
            self._note(
                f"rejected {ip}",
                "rejected a connection from an address not in allowed_ips "
                "(logged once per address)",
            )
            await _close(writer)
            return
        if len(self._writers) >= MAX_CONNECTIONS:
            self.tracker.count("busy", time.monotonic())
            log.debug("%s closed: %d connections open", where, len(self._writers))
            await _close(writer)
            return
        self.tracker.count("connections", time.monotonic())
        self._writers.add(writer)
        try:
            await self._serve(reader, writer, where)
        finally:
            self._writers.discard(writer)
            await _close(writer)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, where: str
    ) -> None:
        conn = TLSConnection(self.tls, reader, writer)
        try:
            await asyncio.wait_for(conn.handshake(), TLS_TIMEOUT)
        except OSError as e:  # ssl.SSLError, a reset, the timeout
            self.tracker.count("tls_failed", time.monotonic())
            log.debug("%s TLS failed: %s", where, _reason(e))
            self._note(
                f"tls {_reason(e)}",
                "TLS handshake failed: %s (logged once per reason)",
                _reason(e),
            )
            return
        log.debug("%s TLS %s %s", where, conn.ssl.version(), conn.ssl.cipher()[0])

        try:
            request, raw = await asyncio.wait_for(_read_request(conn), REQUEST_TIMEOUT)
        except (OSError, BadRequest) as e:  # the timeout is an OSError too
            self.tracker.count("bad", time.monotonic())
            log.debug(
                "%s no request: %s",
                where,
                "timeout" if isinstance(e, TimeoutError) else e,
            )
            return
        log.debug("%s -> %.300r", where, raw)

        # The address the module was sent to: from the DNS server if it is on; else the one
        # this connection came in on (inside a container, the container's own).
        answer = reply(
            request, self.cfg.dns_address or writer.get_extra_info("sockname")[0]
        )
        if answer is None:
            t = request.get("t")
            kind = t[:32] if isinstance(t, str) else type(t).__name__
            self.tracker.count("unknown", time.monotonic())
            self._note(
                f"type {kind}",
                "the module sent a request this app does not answer: %r "
                "(logged once per type)",
                kind,
            )
            return
        try:
            await conn.write(answer)
        except OSError as e:
            self.tracker.count("bad", time.monotonic())
            log.debug("%s reply not sent: %s", where, _reason(e))
            return
        log.debug("%s <- svr", where)
        for msg in self.tracker.dis(module_id(request), time.monotonic()):
            log.info(msg)

    def _dns_query(self, query: bytes, host: str) -> bytes | None:
        """One DNS query: the reply, or None to send none. Other names are logged at debug
        only: a device given this server by mistake would put its browsing in the log."""
        t = time.monotonic()
        ip = _peer_ip(host)
        if self.allowed and ip not in self.allowed:
            self.tracker.count("dns_rejected", t)
            log.debug("DNS %s rejected: not in allowed_ips", host)
            self._note(
                f"dns rejected {ip}",
                "rejected a DNS query from an address not in allowed_ips "
                "(logged once per address)",
            )
            return None
        try:
            answer, name = dns.answer(query, self.dns_answer)
        except dns.BadQuery as e:
            self.tracker.count("dns_bad", t)
            log.debug("DNS %s not answered: %s", host, e)
            return None
        mine = dns.ours(name)
        if mine:
            self.tracker.count("dns", t)
            self._note("dns", "DNS: first answer for %s", dns.DOMAIN)
        else:
            self.tracker.count("dns_other", t)
            self._note(
                "dns other",
                "DNS: answered NXDOMAIN to a name other than %s "
                "(logged once; the names at debug)",
                dns.DOMAIN,
            )
        log.debug(
            "DNS %s %r -> %s", host, name, self.cfg.dns_address if mine else "NXDOMAIN"
        )
        return answer
