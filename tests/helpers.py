"""A fake module and helpers for the tests. No real device data here: the MAC and the
addresses are made up."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import json
import os
import socket
import ssl
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ewpe_keepalive"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cryptography.hazmat.primitives import padding  # noqa: E402
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

from keepalive.config import Config  # noqa: E402
from keepalive.protocol import GENERIC_KEY  # noqa: E402
from keepalive.tls import server_context  # noqa: E402

MAC = "aabbccddeeff"
MODULE_CIPHERS = "AES256-GCM-SHA384:AES256-SHA256:AES256-SHA:AES128-GCM-SHA256:AES128-SHA256:AES128-SHA"


@functools.cache
def server_tls() -> ssl.SSLContext:
    """One server context for all tests: making the certificate takes a moment."""
    return server_context()


def module_tls() -> ssl.SSLContext:
    """TLS like the module's: TLS 1.2, RSA key exchange only, no certificate check."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers(MODULE_CIPHERS)
    return ctx


def dis(mac: str = MAC) -> bytes:
    return json.dumps(
        {"t": "dis", "mac": mac, "p": 1, "needMqtt": 1}, separators=(",", ":")
    ).encode()


def unpack(line: bytes) -> tuple[dict, dict]:
    """The envelope of a packed reply and the message inside."""
    envelope = json.loads(line)
    decryptor = Cipher(algorithms.AES(GENERIC_KEY), modes.ECB()).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    secret = base64.b64decode(envelope["pack"])
    plain = (
        unpadder.update(decryptor.update(secret) + decryptor.finalize())
        + unpadder.finalize()
    )
    return envelope, json.loads(plain)


class FakeModule:
    """Connects like the module (TLS without SNI), sends requests and reads reply lines."""

    async def connect(self, port: int, host: str = "127.0.0.1") -> FakeModule:
        self.reader, self.writer = await asyncio.open_connection(
            host, port, ssl=module_tls(), server_hostname=""
        )
        return self

    async def send(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def reply(self, timeout: float = 3.0) -> bytes:
        return await asyncio.wait_for(self.reader.readline(), timeout)

    async def closed_by_server(self, timeout: float = 3.0) -> bool:
        """True if the server closes the connection without sending anything."""
        try:
            return await asyncio.wait_for(self.reader.read(100), timeout) == b""
        except (ConnectionError, ssl.SSLError):
            return True  # closed with a reset: still closed by the server
        except TimeoutError:
            return False

    async def close(self) -> None:
        self.writer.close()
        with contextlib.suppress(TimeoutError, OSError, ssl.SSLError):
            await asyncio.wait_for(self.writer.wait_closed(), 3)

    async def ask(self, mac: str = MAC) -> dict:
        """One dis request: the svr message of the reply."""
        await self.send(dis(mac))
        return unpack(await self.reply())[1]


def dns_query(
    name: str, qtype: int = 1, ident: int = 0x1234, rd: bool = True, edns: bool = False
) -> bytes:
    """A DNS query like a stub resolver's: one question, recursion desired, EDNS if asked."""
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    head = struct.pack("!HHHHHH", ident, 0x0100 if rd else 0, 1, 0, 0, int(edns))
    opt = b"\0" + struct.pack("!HHIH", 41, 1232, 0, 0) if edns else b""
    return head + qname + struct.pack("!HH", qtype, 1) + opt


def dns_reply(data: bytes) -> dict:
    """The parts of a reply the tests look at."""
    ident, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", data)
    i = 12
    while data[i]:
        i += 1 + data[i]
    i += 5
    r = {
        "id": ident,
        "flags": flags & 0xFFF0,
        "rcode": flags & 0xF,
        "counts": (qd, an, ns, ar),
        "question": data[12:i],
        "address": None,
        "ttl": None,
        "size": len(data),
    }
    if an:
        _, rtype, _, ttl, size = struct.unpack_from("!HHHIH", data, i)
        r.update(
            address=socket.inet_ntoa(data[i + 12 : i + 12 + size]), ttl=ttl, type=rtype
        )
    return r


async def udp_ask(
    port: int, data: bytes, timeout: float = 2.0, host: str = "127.0.0.1"
) -> bytes | None:
    """Send one datagram, return the first reply, or None if none comes in time."""
    loop = asyncio.get_running_loop()
    got: asyncio.Future = loop.create_future()

    class Client(asyncio.DatagramProtocol):
        def datagram_received(self, reply, addr):
            if not got.done():
                got.set_result(reply)

        def error_received(self, exc):
            pass

    transport, _ = await loop.create_datagram_endpoint(Client, remote_addr=(host, port))
    try:
        transport.sendto(data)
        return await asyncio.wait_for(got, timeout)
    except TimeoutError:
        return None
    finally:
        transport.close()


def config(**kw) -> Config:
    base = dict(host="127.0.0.1", port=0, dns_port=0, status_port=0)
    base.update(kw)
    return Config(**base).validate()


async def http_get(port: int, path: str, method: str = "GET") -> tuple[int, bytes]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"{method} {path} HTTP/1.1\r\nHost: test\r\n\r\n".encode())
    await w.drain()
    data = await asyncio.wait_for(r.read(), 5)
    w.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body
