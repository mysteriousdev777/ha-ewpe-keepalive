"""What the module does, from its connections and dis requests: counters for the log and
the status endpoint. No I/O here: the server feeds events in and logs what comes out.

Answered without an MQTT broker, the module asks dis about every 5 s. A longer pause
means it restarted, lost Wi-Fi, or could not reach this server. Several modules may use
one app: each is followed by its MAC, the counters add them up.

All times here are time.monotonic() seconds, so a jump of the system clock does not
distort intervals; snapshot() turns them into wall-clock timestamps. The last hour is
counted per minute: the same memory under any number of events."""
from __future__ import annotations

import collections
import datetime as dt
import time

CONNECTED_WINDOW = 30   # s: a dis within this = connected
GAP = 15                # s between two dis of a module = a pause (restart, Wi-Fi, app not reachable)
HOUR = 3600
MINUTE = 60
HOURLY = ("dis", "gaps", "tls_failed", "rejected", "dns")
TOTALS = ("connections", "dis", "gaps", "tls_failed", "rejected", "unknown", "bad", "busy",
          "dns", "dns_other", "dns_rejected", "dns_bad")
MAX_MODULES = 64


def iso(t: float | None) -> str | None:
    return None if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


class Tracker:
    def __init__(self, clock=time.monotonic) -> None:
        self.started = clock()
        self.totals: collections.Counter[str] = collections.Counter()
        self.minutes: collections.deque[tuple[int, collections.Counter[str]]] = collections.deque()
        self.modules: dict[str, float] = {}     # MAC -> time of its last dis
        self.last_dis: float | None = None

    def count(self, key: str, t: float) -> None:
        """One event: connections, tls_failed, rejected, unknown, bad or busy on the dis
        port; dns, dns_other, dns_rejected or dns_bad from the DNS server."""
        self.totals[key] += 1
        if key in HOURLY:
            minute = int(t // MINUTE)
            if not self.minutes or self.minutes[-1][0] != minute:
                self._drop_old(minute)
                self.minutes.append((minute, collections.Counter()))
            self.minutes[-1][1][key] += 1

    def _drop_old(self, minute: int) -> None:
        while self.minutes and self.minutes[0][0] <= minute - HOUR // MINUTE:
            self.minutes.popleft()

    def dis(self, mac: str, t: float) -> list[str]:
        """One answered dis; returns events for the log."""
        msgs = []
        self.count("dis", t)
        last = self.modules.get(mac)
        if last is None:
            if len(self.modules) < MAX_MODULES:
                self.modules[mac] = t
                n = len(self.modules)
                msgs.append("module connected" if n == 1 else f"another module connected ({n} modules)")
        else:
            if t - last > GAP:
                self.count("gaps", t)
                msgs.append(f"module back after {int(t - last)} s without dis requests "
                            "(it restarted, lost Wi-Fi, or could not reach the app)")
            self.modules[mac] = t
        self.last_dis = t
        return msgs

    def connected(self, t: float) -> bool:
        return self.last_dis is not None and t - self.last_dis <= CONNECTED_WINDOW

    def last_hour(self, t: float) -> dict:
        """Counts of this minute and the 59 before it."""
        self._drop_old(int(t // MINUTE))
        return {k: sum(counts[k] for _, counts in self.minutes) for k in HOURLY}

    def snapshot(self, t: float, wall: float | None = None) -> dict:
        """t: monotonic now; wall: the system clock now, for the timestamps."""
        wall = time.time() if wall is None else wall

        def at(m: float | None) -> str | None:
            return None if m is None else iso(wall - (t - m))

        return {
            "started": at(self.started),
            "connected": self.connected(t),
            "modules": len(self.modules),
            "last_dis": at(self.last_dis),
            "last_dis_age_s": None if self.last_dis is None else int(t - self.last_dis),
            "last_hour": self.last_hour(t),
            "totals": {k: self.totals[k] for k in TOTALS},
        }

    def summary(self, t: float, dns: bool = False) -> str:
        """dns: the DNS server is on, so its answers are worth a word."""
        h = self.last_hour(t)
        state = "connected" if self.connected(t) else "NOT connected"
        return (f"last hour: module {state}; {h['dis']} dis request(s), {h['gaps']} pause(s), "
                f"{h['tls_failed']} TLS failure(s), {h['rejected']} connection(s) rejected"
                + (f", {h['dns']} DNS answer(s) for dis.gree.com" if dns else ""))
