"""The optional DNS server: answers the module's question for <region>dis.gree.com with
the address of Home Assistant. For networks whose router cannot rewrite a name but can
give one device a DNS server of its own; with AdGuard Home, Pi-hole or a dnsmasq router
the rewrite belongs there, and this stays off.

Only what the module needs: UDP, one question per query, an A record for dis.gree.com
and the names under it. Any other name gets NXDOMAIN: no recursion, no forwarding, no
cache, so the module reaches nothing else by name. Firmware V1.45 asks eu.dis.gree.com
once a minute (the TTL) and nothing else, and works with these replies: the flags and the
TTL below.

No I/O here but the UDP endpoint at the end; what to answer whom is the server's call."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import struct

DNS_PORT = 53  # the module asks the DNS server its DHCP lease names, on 53
DOMAIN = "dis.gree.com"
TTL = 60  # s: the module asks again once a minute
MAX_QUERY = 512  # bytes: the classic UDP limit; the module's query has 33
TYPE_A = CLASS_IN = 1
NXDOMAIN = 3
QR, OPCODE, AA, RD, RA = 0x8000, 0x7800, 0x0400, 0x0100, 0x0080


class BadQuery(ValueError):
    pass


def question(query: bytes) -> tuple[str, int, int, int]:
    """The only question of a standard query: (name, type, class, where it ends).

    BadQuery for anything else: a reply, another opcode, not one question, a broken name."""
    if not 12 <= len(query) <= MAX_QUERY:
        raise BadQuery(f"{len(query)} bytes")
    flags, count = struct.unpack_from("!HH", query, 2)
    if flags & (QR | OPCODE) or count != 1:
        raise BadQuery("not a standard query with one question")
    labels, i = [], 12
    while True:
        if i >= len(query):
            raise BadQuery("the name is cut")
        n = query[i]
        if n == 0:
            break
        if n > 63 or i + 1 + n > len(query):
            # A question has nothing to compress.
            raise BadQuery("a cut or compressed label")
        labels.append(query[i + 1 : i + 1 + n])
        i += 1 + n
    end = i + 5
    if end > len(query):
        raise BadQuery("the question is cut")
    qtype, qclass = struct.unpack_from("!HH", query, i + 1)
    return b".".join(labels).decode("ascii", "replace").lower(), qtype, qclass, end


def ours(name: str) -> bool:
    return name == DOMAIN or name.endswith("." + DOMAIN)


def answer(query: bytes, address: bytes) -> tuple[bytes, str]:
    """The reply to a query, and the name asked. address: 4 bytes, the IPv4 address given
    for an A question about dis.gree.com; its other types get no record, any other name
    NXDOMAIN. Additional records of the query (EDNS) are left out. BadQuery: no reply."""
    name, qtype, qclass, end = question(query)
    mine = ours(name)
    flags = (
        QR
        | AA
        | RA
        | (struct.unpack_from("!H", query, 2)[0] & RD)
        | (0 if mine else NXDOMAIN)
    )
    record = mine and qtype == TYPE_A and qclass == CLASS_IN
    reply = (
        query[:2] + struct.pack("!HHHHH", flags, 1, int(record), 0, 0) + query[12:end]
    )
    if record:
        reply += (
            struct.pack("!HHHIH", 0xC00C, TYPE_A, CLASS_IN, TTL, len(address)) + address
        )
    return reply, name


class _Endpoint(asyncio.DatagramProtocol):
    def __init__(self, handle: Callable[[bytes, str], bytes | None]) -> None:
        self._handle = handle

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport: asyncio.DatagramTransport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        reply = self._handle(data, addr[0])
        if reply is not None:
            self._transport.sendto(reply, addr)

    def error_received(self, exc: Exception) -> None:
        pass  # an ICMP error for an earlier reply (Windows reports them): nothing to do


async def serve(
    handle: Callable[[bytes, str], bytes | None], host: str, port: int
) -> asyncio.DatagramTransport:
    """A UDP endpoint: handle(query, sender address) gives the reply, or None for none."""
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: _Endpoint(handle), local_addr=(host, port)
    )
    return transport
