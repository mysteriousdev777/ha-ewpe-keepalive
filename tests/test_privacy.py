"""The normal (info) log may be shown to others: no MAC, no addresses. They appear only in
the debug log."""
import io
import logging
import unittest

from helpers import MAC, FakeModule, config, server_tls
from keepalive.__main__ import setup_logging
from keepalive.server import KeepaliveServer, log

PRIVATE = (MAC, "127.0.0.1")


class PrivacyTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        for h in list(log.handlers):
            log.removeHandler(h)
        log.setLevel(logging.NOTSET)

    async def session_log(self, level: str) -> str:
        setup_logging(level)
        for h in list(log.handlers):          # keep the test output quiet
            log.removeHandler(h)
        out = io.StringIO()
        log.addHandler(logging.StreamHandler(out))
        srv = KeepaliveServer(config(allowed_ips=["127.0.0.1"]), server_tls())
        await srv.start()
        for _ in range(2):
            m = await FakeModule().connect(srv.port)
            await m.ask()
            await m.close()
        await srv.stop()
        log.info(srv.tracker.summary(0))
        return out.getvalue()

    async def test_info_log_has_no_private_data(self):
        text = await self.session_log("info")
        self.assertIn("module connected", text)
        for s in PRIVATE:
            self.assertNotIn(s, text)

    async def test_debug_log_has_details(self):
        text = await self.session_log("debug")
        for s in PRIVATE:
            self.assertIn(s, text, "the check above would notice a leak")


if __name__ == "__main__":
    unittest.main()
