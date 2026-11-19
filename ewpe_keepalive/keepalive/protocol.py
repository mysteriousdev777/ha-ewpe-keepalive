"""The dis handshake: the module's request and the reply that names no MQTT broker.

After TLS the module sends one JSON object, in plain text:

  {"t":"dis","mac":"<12 hex digits>","p":1,"needMqtt":1}

The cloud answers with svr, packed: AES-128-ECB with the generic Gree key, PKCS#7,
base64, inside {"t":"pack","i":1,...}, one line. Without mqttInfo in svr the module does
not connect to MQTT; it asks dis again once its 5 s answer timeout is over (firmware V1.45).
No I/O here.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DIS_PORT = 1813  # fixed in the firmware: <region>dis.gree.com:1813
DIS_HOST = "eu.dis.gree.com"  # host in svr: a dis name, see svr()
GENERIC_KEY = b"a3K8Bx%2r8Y7#xDh"  # public: the same in every Gree module and app
MAX_REQUEST = 4096  # bytes; a dis request has about 60

_decoder = json.JSONDecoder()


class BadRequest(ValueError):
    pass


def parse_request(buf: bytes) -> dict | None:
    """The JSON object at the start of buf, or None while it is incomplete.

    Anything that cannot become a JSON object raises BadRequest. A broken object waits
    like an incomplete one: the request deadline and MAX_REQUEST end it."""
    text = buf.decode("utf-8", "replace").lstrip()
    if not text:
        return None
    if not text.startswith("{"):
        raise BadRequest("not a JSON object")
    try:
        request, _ = _decoder.raw_decode(text)
    except json.JSONDecodeError:
        return None
    except RecursionError:
        # [[[[...: deeper than the parser goes (about 1000 levels on 3.11)
        raise BadRequest("nested too deep") from None
    return request


def svr(address: str) -> dict:
    """The cloud's answer to dis, without mqttInfo. The addresses point at this server;
    host is a dis name, which any rewrite of dis.gree.com sends here too. Without mqttInfo
    firmware V1.45 reads none of them and asks dis again."""
    return {
        "t": "svr",
        "ip": address,
        "ip2": address,
        "Ip3": address,
        "host": DIS_HOST,
        "udpPort": DIS_PORT,
        "tcpPort": DIS_PORT,
        "protocol": "",
        "datHost": "",
        "datHostPort": 0,
    }


def pack(message: dict, tcid: str) -> bytes:
    """message encrypted with the generic key, in the envelope the module expects; one line."""
    padder = padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(GENERIC_KEY), modes.ECB()).encryptor()
    plain = json.dumps(message, separators=(",", ":")).encode()
    secret = (
        encryptor.update(padder.update(plain) + padder.finalize())
        + encryptor.finalize()
    )
    envelope = {
        "t": "pack",
        "i": 1,
        "uid": 0,
        "cid": "",
        "tcid": tcid,
        "pack": base64.b64encode(secret).decode(),
    }
    return json.dumps(envelope, separators=(",", ":")).encode() + b"\n"


def module_id(request: dict) -> str:
    """The module's MAC from a request ("" if it has none)."""
    mac = request.get("mac")
    return mac[:32] if isinstance(mac, str) else ""


def reply(request: dict, address: str) -> bytes | None:
    """The answer to a request: svr for dis, None for anything else."""
    if request.get("t") != "dis":
        return None
    return pack(svr(address), module_id(request))
