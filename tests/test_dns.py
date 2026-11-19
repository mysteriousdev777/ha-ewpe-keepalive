import struct
import unittest

from helpers import dns_query, dns_reply
from keepalive import dns

ADDRESS = bytes([192, 0, 2, 1])
QR_AA_RD_RA = 0x8580


class DNSTest(unittest.TestCase):
    def ask(self, *args, **kw) -> tuple[str, dict]:
        reply, name = dns.answer(dns_query(*args, **kw), ADDRESS)
        return name, dns_reply(reply)

    def test_dis_names(self):
        for name in (
            "eu.dis.gree.com",
            "dis.gree.com",
            "EU.Dis.GREE.com",
            "a.b.dis.gree.com",
        ):
            with self.subTest(name=name):
                asked, r = self.ask(name, ident=0xBEEF)
                self.assertEqual(asked, name.lower())
                self.assertTrue(dns.ours(asked))
                self.assertEqual(
                    (r["id"], r["flags"], r["rcode"], r["counts"]),
                    (0xBEEF, QR_AA_RD_RA, 0, (1, 1, 0, 0)),
                )
                self.assertEqual(
                    (r["address"], r["ttl"], r["type"]), ("192.0.2.1", 60, 1)
                )
                self.assertEqual(
                    r["question"], dns_query(name)[12:], "the question echoed as asked"
                )

    def test_the_modules_query(self):
        q = dns_query("eu.dis.gree.com")
        self.assertEqual(len(q), 33)
        self.assertEqual(
            len(dns.answer(q, ADDRESS)[0]), 33 + 16, "the question and one A record"
        )

    def test_recursion_desired_echoed(self):
        self.assertEqual(
            self.ask("eu.dis.gree.com", rd=False)[1]["flags"], QR_AA_RD_RA & ~0x0100
        )

    def test_other_types_get_no_record(self):
        for qtype in (28, 16, 255):  # AAAA, TXT, ANY
            with self.subTest(qtype=qtype):
                r = self.ask("eu.dis.gree.com", qtype=qtype)[1]
                self.assertEqual((r["rcode"], r["counts"]), (0, (1, 0, 0, 0)))

    def test_other_names_nxdomain(self):
        for name in (
            "gree.com",
            "eu.gree.com",
            "grih.gree.com",
            "notdis.gree.com",
            "dis.gree.com.example",
            "example.com",
        ):
            with self.subTest(name=name):
                asked, r = self.ask(name)
                self.assertFalse(dns.ours(asked))
                self.assertEqual(
                    (r["rcode"], r["counts"], r["flags"]),
                    (3, (1, 0, 0, 0), QR_AA_RD_RA),
                )

    def test_edns_left_out(self):
        r = self.ask("eu.dis.gree.com", edns=True)[1]
        self.assertEqual(
            (r["counts"], r["address"], r["size"]), ((1, 1, 0, 0), "192.0.2.1", 33 + 16)
        )

    def test_bad_queries(self):
        q = dns_query("eu.dis.gree.com")
        head = struct.pack("!HHHHHH", 1, 0x0100, 1, 0, 0, 0)
        bad = {
            "short": q[:11],
            "a reply": q[:2] + struct.pack("!H", 0x8100) + q[4:],
            "another opcode": q[:2] + struct.pack("!H", 0x0900) + q[4:],
            "no question": q[:4] + struct.pack("!H", 0) + q[6:],
            "two questions": q[:4] + struct.pack("!H", 2) + q[6:],
            "compressed name": head + b"\xc0\x0c" + struct.pack("!HH", 1, 1),
            "cut name": q[:16],
            "cut label": head + b"\x3fabc",
            "cut question": q[:-2],
            "too long": q + b"\0" * 500,
        }
        for what, query in bad.items():
            with self.subTest(what), self.assertRaises(dns.BadQuery):
                dns.answer(query, ADDRESS)


if __name__ == "__main__":
    unittest.main()
