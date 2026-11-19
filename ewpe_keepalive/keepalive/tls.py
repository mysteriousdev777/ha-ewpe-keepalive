"""TLS on the dis port: a self-signed certificate made at start, the settings the module
accepts, and TLS over an asyncio stream.

The module does not check the certificate on this port, but its TLS 1.2 offers only RSA
key exchange (AES-GCM and AES-CBC suites, RSA signatures, no SNI), so the key is RSA
and RSA+AESGCM must be allowed: Python's defaults allow only forward-secret suites.
The certificate and its key are on disk only while they are loaded."""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import ssl
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# The module picks AES256-GCM-SHA384 (RSA key exchange).
CIPHERS = "ECDHE+AESGCM:RSA+AESGCM"
NAME = "EWPE Keepalive"


def certificate() -> tuple[bytes, bytes]:
    """A new self-signed RSA 2048 certificate and its key, PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


def server_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers(CIPHERS)
    cert, key = certificate()
    with tempfile.TemporaryDirectory() as d:  # private to this user
        cert_path, key_path = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
        for path, data in ((cert_path, cert), (key_path, key)):
            with open(path, "wb") as f:
                f.write(data)
        ctx.load_cert_chain(cert_path, key_path)
    return ctx


class TLSConnection:
    """Server-side TLS over an accepted asyncio stream, through memory BIOs.

    The connection is accepted as plain TCP and the server feeds the bytes it reads to
    OpenSSL itself: asyncio's own upgrade (StreamWriter.start_tls) can miss a ClientHello
    that arrives before it starts, and a TLS server (start_server with ssl) never reports
    failed handshakes. Here a failure raises ssl.SSLError with OpenSSL's reason. Closing
    sends no close_notify: the connection is just closed, as the module is used to."""

    def __init__(
        self,
        ctx: ssl.SSLContext,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._incoming, self._outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.ssl = ctx.wrap_bio(self._incoming, self._outgoing, server_side=True)
        self._reader, self._writer = reader, writer

    async def _run(self, op):
        """op() until it no longer waits for bytes from the peer; sends what it produced."""
        while True:
            try:
                result = op()
                break
            except ssl.SSLWantReadError:
                await self._send()
                if self._incoming.eof:
                    raise ConnectionResetError("closed by the peer") from None
                data = await self._reader.read(4096)
                if data:
                    self._incoming.write(data)
                else:
                    self._incoming.write_eof()
        await self._send()
        return result

    async def _send(self) -> None:
        data = self._outgoing.read()
        if data:
            self._writer.write(data)
            await self._writer.drain()

    async def handshake(self) -> None:
        await self._run(self.ssl.do_handshake)

    async def read(self, n: int) -> bytes:
        """Up to n bytes; b"" when the peer has closed."""
        try:
            return await self._run(lambda: self.ssl.read(n))
        except (ssl.SSLZeroReturnError, ssl.SSLEOFError, ConnectionResetError):
            return b""

    async def write(self, data: bytes) -> None:
        await self._run(lambda: self.ssl.write(data))
