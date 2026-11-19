import asyncio
import contextlib
import json
import ssl
import time
import unittest
from unittest import mock

from helpers import (
    MAC,
    FakeModule,
    config,
    dis,
    dns_query,
    dns_reply,
    http_get,
    server_tls,
    udp_ask,
)
from keepalive import __version__, protocol
from keepalive.server import KeepaliveServer, log


class ServerTest(unittest.IsolatedAsyncioTestCase):
    async def start(self, **kw) -> KeepaliveServer:
        srv = KeepaliveServer(config(**kw), server_tls())
        await srv.start()
        self.addAsyncCleanup(srv.stop)
        return srv

    async def module(self, srv) -> FakeModule:
        m = await FakeModule().connect(srv.port)
        self.addAsyncCleanup(m.close)
        return m

    async def test_dis_gets_svr_without_broker(self):
        srv = await self.start()
        m = await self.module(srv)
        tls = m.writer.get_extra_info("ssl_object")
        self.assertEqual(
            (tls.version(), tls.cipher()[0]),
            ("TLSv1.2", "AES256-GCM-SHA384"),
            "the module's suite",
        )
        svr = await m.ask()
        self.assertEqual(svr["t"], "svr")
        self.assertNotIn("mqttInfo", svr)
        self.assertEqual(svr["ip"], "127.0.0.1", "the address the module reached")
        self.assertTrue(await m.closed_by_server(), "one request per connection")
        s = srv.status()
        self.assertEqual(
            (s["totals"]["dis"], s["modules"], s["connected"]), (1, 1, True)
        )

    async def test_request_in_pieces(self):
        srv = await self.start()
        m = await self.module(srv)
        data = dis()
        for i in range(0, len(data), 7):
            await m.send(data[i : i + 7])
            await asyncio.sleep(0.01)
        self.assertEqual(json.loads((await m.reply()).decode())["tcid"], MAC)

    async def test_each_request_its_connection(self):
        srv = await self.start()
        with self.assertLogs(log, "INFO") as logs:
            for _ in range(3):
                m = await FakeModule().connect(srv.port)
                self.assertEqual((await m.ask())["t"], "svr")
                await m.close()
        self.assertEqual(
            logs.output,
            ["INFO:keepalive:module connected"],
            "logged once, at the first",
        )
        self.assertEqual(srv.tracker.totals["connections"], 3)

    async def test_other_requests_not_answered(self):
        srv = await self.start()
        with self.assertLogs(log, "INFO") as logs:
            for _ in range(2):
                m = await FakeModule().connect(srv.port)
                await m.send(json.dumps({"t": "devLogin", "mac": MAC}).encode())
                self.assertTrue(await m.closed_by_server())
                await m.close()
        self.assertEqual(
            sum("does not answer: 'devLogin'" in line for line in logs.output), 1
        )
        self.assertEqual(srv.tracker.totals["unknown"], 2)
        self.assertEqual(srv.tracker.totals["dis"], 0)

    async def test_not_tls(self):
        srv = await self.start()
        with self.assertLogs(log, "INFO") as logs:
            for _ in range(2):
                r, w = await asyncio.open_connection("127.0.0.1", srv.port)
                w.write(dis())
                await w.drain()
                with contextlib.suppress(ConnectionError):
                    self.assertNotIn(b"svr", await asyncio.wait_for(r.read(), 5))
                w.close()
            await asyncio.sleep(0.1)
        self.assertEqual(
            sum("TLS handshake failed" in line for line in logs.output),
            1,
            "once per reason",
        )
        self.assertEqual(srv.tracker.totals["tls_failed"], 2)

    async def test_allowed_ips(self):
        srv = await self.start(allowed_ips=["192.0.2.10"])
        with self.assertLogs(log, "INFO") as logs:
            for _ in range(2):
                with self.assertRaises((ConnectionError, ssl.SSLError, OSError)):
                    m = await FakeModule().connect(srv.port)
                    await m.ask()
            await asyncio.sleep(0.1)
        self.assertEqual(sum("not in allowed_ips" in line for line in logs.output), 1)
        self.assertEqual(srv.status()["last_hour"]["rejected"], 2)

        srv2 = await self.start(allowed_ips=["127.0.0.1"])
        m = await self.module(srv2)
        self.assertEqual((await m.ask())["t"], "svr")

    async def test_request_deadline(self):
        with mock.patch("keepalive.server.REQUEST_TIMEOUT", 0.3):
            srv = await self.start()
            m = await self.module(srv)
            self.assertTrue(await m.closed_by_server(2))
        self.assertEqual(srv.tracker.totals["bad"], 1)

    async def test_request_too_long(self):
        srv = await self.start()
        m = await self.module(srv)
        await m.send(b"{" + b" " * 5000)
        self.assertTrue(await m.closed_by_server())
        self.assertEqual(srv.tracker.totals["bad"], 1)

    async def test_connection_limit(self):
        with mock.patch("keepalive.server.MAX_CONNECTIONS", 1):
            srv = await self.start()
            await self.module(srv)  # holds the only place
            await asyncio.sleep(0.05)
            with self.assertRaises((ConnectionError, ssl.SSLError, OSError)):
                m = await FakeModule().connect(srv.port)
                await m.ask()
            self.assertEqual(srv.tracker.totals["busy"], 1)

    async def test_status_endpoint(self):
        srv = await self.start()
        m = await self.module(srv)
        await m.ask()
        code, body = await http_get(srv.status_port, "/status")
        self.assertEqual(code, 200)
        s = json.loads(body)
        self.assertEqual(
            (s["version"], s["connected"], s["totals"]["dis"]), (__version__, True, 1)
        )
        self.assertLessEqual(s["last_dis_age_s"], 2)
        self.assertNotIn(MAC, body.decode(), "no MAC in the status")
        self.assertNotIn("127.0.0.1", body.decode(), "no addresses in the status")
        self.assertEqual(await http_get(srv.status_port, "/health"), (200, b"ok\n"))
        self.assertEqual((await http_get(srv.status_port, "/nope"))[0], 404)
        self.assertEqual((await http_get(srv.status_port, "/status", "POST"))[0], 405)

    async def test_status_deadline_and_limit(self):
        with (
            mock.patch("keepalive.status.DEADLINE", 0.5),
            mock.patch("keepalive.status.MAX_CONNECTIONS", 2),
        ):
            srv = await self.start()
            slow = []
            for _ in range(2):
                r, w = await asyncio.open_connection("127.0.0.1", srv.status_port)
                w.write(b"GET /status HTTP/1.1\r\n")
                await w.drain()
                slow.append((r, w))
            await asyncio.sleep(0.05)
            r3, w3 = await asyncio.open_connection("127.0.0.1", srv.status_port)
            self.assertEqual(
                await asyncio.wait_for(r3.read(), 1),
                b"",
                "over the limit: closed at once",
            )
            w3.close()
            start = time.monotonic()
            for r, _ in slow:
                with contextlib.suppress(ConnectionError):
                    await asyncio.wait_for(r.read(), 2)
            self.assertLess(
                time.monotonic() - start, 1.5, "a slow request is cut at the deadline"
            )
            for _, w in slow:
                w.close()
            self.assertEqual(
                await http_get(srv.status_port, "/health"), (200, b"ok\n"), "free again"
            )

    async def test_no_dns_server_by_default(self):
        srv = await self.start()
        self.assertIsNone(srv.dns_port)
        self.assertFalse(srv.status()["dns_server"])

    async def test_dns_server(self):
        srv = await self.start(dns_address="192.0.2.1")
        with self.assertLogs(log, "INFO") as logs:
            for name in (
                "eu.dis.gree.com",
                "eu.dis.gree.com",
                "example.com",
                "example.org",
            ):
                r = dns_reply(await udp_ask(srv.dns_port, dns_query(name)))
                self.assertEqual(
                    (r["rcode"], r["address"]),
                    (0, "192.0.2.1") if "gree" in name else (3, None),
                )
        self.assertEqual(
            logs.output,
            [
                "INFO:keepalive:DNS: first answer for dis.gree.com",
                "INFO:keepalive:DNS: answered NXDOMAIN to a name other than dis.gree.com "
                "(logged once; the names at debug)",
            ],
        )
        s = srv.status()
        self.assertEqual(
            (s["dns_server"], s["last_hour"]["dns"], s["totals"]["dns_other"]),
            (True, 2, 2),
        )
        m = await self.module(srv)
        self.assertEqual(
            (await m.ask())["ip"], "192.0.2.1", "svr names the address the DNS gave"
        )
        self.assertEqual(await http_get(srv.status_port, "/health"), (200, b"ok\n"))
        srv._dns.close()
        await asyncio.sleep(0)
        self.assertEqual(
            (await http_get(srv.status_port, "/health"))[0],
            503,
            "DNS server gone: unhealthy",
        )

    async def test_request_too_deep_to_parse(self):
        """The parser's own depth limit (it depends on the Python version): a bad request."""
        srv = await self.start()
        m = await self.module(srv)
        with mock.patch.object(
            protocol._decoder, "raw_decode", side_effect=RecursionError
        ):
            await m.send(dis())
            self.assertTrue(await m.closed_by_server())
        self.assertEqual(srv.tracker.totals["bad"], 1)

    async def test_request_too_deep_to_serialize(self):
        """A request parsed but nested too deep to serialize again (on Python 3.11 a real
        request just under the parser's limit was): unknown, and nothing escapes."""
        deep: list = []
        for _ in range(100_000):
            deep = [deep]
        errors = []
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, ctx: errors.append(ctx)
        )
        srv = await self.start()
        with mock.patch("keepalive.server.parse_request", return_value={"t": deep}):
            m = await self.module(srv)
            await m.send(dis())
            self.assertTrue(await m.closed_by_server())
        await asyncio.sleep(0.1)
        self.assertEqual(errors, [])
        self.assertEqual(srv.tracker.totals["unknown"], 1)

    async def test_dns_allowed_ips_and_junk(self):
        srv = await self.start(dns_address="192.0.2.1", allowed_ips=["192.0.2.10"])
        for _ in range(2):
            self.assertIsNone(
                await udp_ask(srv.dns_port, dns_query("eu.dis.gree.com"), 0.3)
            )
        self.assertEqual(srv.tracker.totals["dns_rejected"], 2)

        srv = await self.start(dns_address="192.0.2.1", allowed_ips=["127.0.0.1"])
        self.assertIsNone(await udp_ask(srv.dns_port, b"junk", 0.3))
        self.assertEqual(srv.tracker.totals["dns_bad"], 1)
        self.assertIsNotNone(
            await udp_ask(srv.dns_port, dns_query("eu.dis.gree.com")), "still answers"
        )

    async def test_stop_with_open_connection(self):
        srv = KeepaliveServer(config(), server_tls())
        await srv.start()
        m = await FakeModule().connect(srv.port)
        await asyncio.sleep(0.05)
        await asyncio.wait_for(srv.stop(), 3)
        self.assertTrue(await m.closed_by_server())
        self.assertFalse(srv.serving())
        await m.close()


if __name__ == "__main__":
    unittest.main()
