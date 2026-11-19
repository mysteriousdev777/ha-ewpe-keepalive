"""Smoke test of the built app image (run by CI against a started container).

The container runs like under the Supervisor, with options from tests/fixtures/options-ci.json
(the DNS server on), the dis port published on localhost:--port, the status port on
localhost:--status-port and the DNS port on localhost:--dns-port. A fake module asks DNS,
then dis with the module's TLS settings; the reply must be svr without an MQTT broker.

Usage: python tests/smoke_container.py
"""

import argparse
import asyncio
import json
import sys
import time
import urllib.request

from helpers import FakeModule, dns_query, dns_reply, udp_ask

DNS_ADDRESS = "192.0.2.1"  # dns_address in options-ci.json


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


async def main(a) -> None:
    base = f"http://127.0.0.1:{a.status_port}"
    deadline = time.time() + 60
    while True:
        try:
            if get(base + "/health")[0] == 200:
                break
        except OSError:
            pass
        if time.time() > deadline:
            sys.exit("the health check never answered")
        await asyncio.sleep(1)

    r = dns_reply(await udp_ask(a.dns_port, dns_query("eu.dis.gree.com"), 5))
    assert (r["rcode"], r["address"]) == (0, DNS_ADDRESS), r
    r = dns_reply(await udp_ask(a.dns_port, dns_query("example.com"), 5))
    assert (r["rcode"], r["counts"][1]) == (3, 0), r

    for _ in range(2):
        m = await FakeModule().connect(a.port)
        tls = m.writer.get_extra_info("ssl_object")
        assert (tls.version(), tls.cipher()[0]) == ("TLSv1.2", "AES256-GCM-SHA384"), (
            tls.cipher()
        )
        svr = await m.ask()
        assert (
            svr["t"] == "svr" and "mqttInfo" not in svr and svr["ip"] == DNS_ADDRESS
        ), svr
        await m.close()

    status = json.loads(get(base + "/status")[1])
    assert (
        status["connected"] and status["modules"] == 1 and status["totals"]["dis"] == 2
    ), status
    assert (
        status["dns_server"]
        and status["last_hour"]["dns"] == 1
        and status["totals"]["dns_other"] == 1
    ), status
    print("smoke test ok:", json.dumps(status["totals"]))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=1813)
    p.add_argument("--status-port", type=int, default=8080)
    p.add_argument("--dns-port", type=int, default=10053)
    asyncio.run(main(p.parse_args()))
