import unittest
from unittest import mock

from helpers import MAC  # noqa: F401  (puts the app on sys.path)
from keepalive.tracker import CONNECTED_WINDOW, GAP, HOUR, Tracker, iso


class TrackerTest(unittest.TestCase):
    def tracker(self) -> Tracker:
        return Tracker(clock=lambda: 0.0)

    def test_first_dis_connects(self):
        t = self.tracker()
        self.assertFalse(t.connected(1))
        self.assertEqual(t.dis(MAC, 10), ["module connected"])
        self.assertTrue(t.connected(10 + CONNECTED_WINDOW))
        self.assertFalse(t.connected(10 + CONNECTED_WINDOW + 1))

    def test_regular_requests_are_quiet(self):
        t = self.tracker()
        t.dis(MAC, 0)
        for i in range(1, 100):
            self.assertEqual(t.dis(MAC, i * 5.5), [])
        self.assertEqual((t.totals["dis"], t.totals["gaps"]), (100, 0))

    def test_pause(self):
        t = self.tracker()
        t.dis(MAC, 0)
        msgs = t.dis(MAC, GAP + 0.5)
        self.assertEqual(len(msgs), 1)
        self.assertIn(f"back after {int(GAP + 0.5)} s", msgs[0])
        self.assertEqual(t.last_hour(GAP + 1)["gaps"], 1)

    def test_modules_followed_apart(self):
        t = self.tracker()
        t.dis("aabbccddee01", 0)
        self.assertEqual(t.dis("aabbccddee02", 3), ["another module connected (2 modules)"])
        for i in range(1, 10):          # interleaved, each every 5 s: no pauses
            t.dis("aabbccddee01", i * 5)
            t.dis("aabbccddee02", i * 5 + 3)
        self.assertEqual(t.totals["gaps"], 0)
        self.assertEqual(t.snapshot(50, 1000)["modules"], 2)

    def test_module_limit(self):
        with mock.patch("keepalive.tracker.MAX_MODULES", 2):
            t = self.tracker()
            for i in range(4):
                t.dis(f"aabbccddee0{i}", i)
            self.assertEqual(len(t.modules), 2)
            self.assertEqual(t.totals["dis"], 4, "still counted")

    def test_last_hour(self):
        t = self.tracker()
        t.count("rejected", 0)
        t.count("tls_failed", 10)
        t.count("bad", 10)                  # totals only
        t.dis(MAC, 50)
        t.count("dns", 20)
        t.count("dns_other", 20)            # totals only
        t.dis(MAC, 61)                      # the next minute, no pause
        self.assertEqual(t.last_hour(80), {"dis": 2, "gaps": 0, "tls_failed": 1, "rejected": 1, "dns": 1})
        self.assertEqual(t.last_hour(HOUR - 1)["rejected"], 1, "minute 0 is still in the hour")
        self.assertEqual(t.last_hour(HOUR), {"dis": 1, "gaps": 0, "tls_failed": 0, "rejected": 0, "dns": 0})
        self.assertEqual((t.totals["bad"], t.totals["dns_other"]), (1, 1))

    def test_last_hour_memory_is_bounded(self):
        t = self.tracker()
        end = 2 * HOUR + 30
        for s in range(end):                # a flood: 5 events a second for two hours
            for _ in range(5):
                t.count("rejected", s)
        self.assertLessEqual(len(t.minutes), HOUR // 60)
        self.assertEqual(t.last_hour(end - 1)["rejected"], 5 * (end - 61 * 60), "minutes 61..120")

    def test_snapshot(self):
        t = self.tracker()
        t.dis(MAC, 100)
        wall = 1_800_000_000
        s = t.snapshot(110, wall=wall)
        self.assertEqual(s["last_dis"], iso(wall - 10))
        self.assertEqual(s["started"], iso(wall - 110))
        self.assertTrue(s["last_dis"].endswith("+00:00"))
        self.assertEqual((s["last_dis_age_s"], s["connected"], s["modules"]), (10, True, 1))
        self.assertEqual(s["totals"]["dis"], 1)
        self.assertNotIn(MAC, str(s), "no MAC in the status")

    def test_summary(self):
        t = self.tracker()
        self.assertIn("module NOT connected; 0 dis request(s)", t.summary(5))
        t.dis(MAC, 10)
        self.assertIn("module connected; 1 dis request(s), 0 pause(s)", t.summary(15))
        self.assertNotIn("DNS", t.summary(15), "no DNS server, no word about it")
        t.count("dns", 12)
        self.assertTrue(t.summary(15, dns=True).endswith(", 1 DNS answer(s) for dis.gree.com"))


if __name__ == "__main__":
    unittest.main()
