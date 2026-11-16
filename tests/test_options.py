import contextlib
import io
import json
import os
import tempfile
import unittest

import helpers  # noqa: F401  (puts the app on sys.path)
from keepalive.__main__ import config_from_args, main, parse_args
from keepalive.config import Config, ConfigError, load_options
from keepalive.protocol import DIS_PORT
from keepalive.server import log


class OptionsTest(unittest.TestCase):
    def load(self, options) -> Config:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "options.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(options if isinstance(options, str) else json.dumps(options))
            return load_options(path)

    def test_defaults(self):
        c = self.load({})
        self.assertEqual((c.allowed_ips, c.log_level, c.dns_address, c.host, c.port, c.dns_port, c.status_port),
                         ([], "info", None, "0.0.0.0", DIS_PORT, 53, 8080))
        self.assertEqual(DIS_PORT, 1813)

    def test_app_options(self):
        c = self.load({"allowed_ips": ["192.0.2.10 "], "log_level": "debug", "dns_address": " 192.0.2.1"})
        self.assertEqual((c.allowed_ips, c.log_level, c.dns_address), (["192.0.2.10"], "debug", "192.0.2.1"))
        self.assertIsNone(self.load({"dns_address": ""}).dns_address, "empty: no DNS server")

    def test_invalid(self):
        for bad in ({"log_level": "loud"}, {"allowed_ips": ["module"]}, {"allowed_ips": "192.0.2.10"},
                    {"dns_address": "homeassistant.local"}, {"dns_address": "2001:db8::1"}, {"dns_address": 5},
                    "[1, 2]", "not json"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.load(bad)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_options(os.path.join(tempfile.gettempdir(), "no-such-dir-ewpe", "options.json"))

    def test_bad_options_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "options.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"allowed_ips": ["module"]}, f)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--options", path]), 2)
        for h in list(log.handlers):
            log.removeHandler(h)
        self.assertIn("ERROR allowed_ips: 'module' is not an IP address", out.getvalue())

    def test_command_line(self):
        c = config_from_args(parse_args(["--allow", "192.0.2.10", "--no-status", "--host", "127.0.0.1",
                                         "--port", "11813"]))
        self.assertEqual((c.allowed_ips, c.status_port, c.host, c.port), (["192.0.2.10"], None, "127.0.0.1", 11813))
        c = config_from_args(parse_args([]))
        self.assertEqual((c.port, c.dns_address), (DIS_PORT, None))
        self.assertEqual(config_from_args(parse_args(["--dns", "192.0.2.1"])).dns_address, "192.0.2.1")


if __name__ == "__main__":
    unittest.main()
