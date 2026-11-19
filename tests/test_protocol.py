import json
import unittest
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa

from helpers import MAC, dis, unpack
from keepalive import protocol
from keepalive.protocol import (
    DIS_PORT,
    BadRequest,
    module_id,
    pack,
    parse_request,
    reply,
    svr,
)
from keepalive.tls import certificate


class ParseTest(unittest.TestCase):
    def test_whole_request(self):
        self.assertEqual(
            parse_request(dis()), {"t": "dis", "mac": MAC, "p": 1, "needMqtt": 1}
        )
        self.assertEqual(
            parse_request(b" \r\n" + dis() + b"\n"),
            parse_request(dis()),
            "whitespace around",
        )

    def test_incomplete(self):
        for part in (b"", b"  ", dis()[:1], dis()[:-1]):
            with self.subTest(part=part):
                self.assertIsNone(parse_request(part))

    def test_first_object_only(self):
        self.assertEqual(parse_request(dis() + b'{"t":"hb"}')["t"], "dis")

    def test_not_an_object(self):
        for bad in (b"junk" + dis(), b"[1, 2]", b"\x16\x03\x01\x00"):
            with self.subTest(bad=bad), self.assertRaises(BadRequest):
                parse_request(bad)

    def test_nested_too_deep(self):
        """Where the parser gives up on nesting depends on the Python version and the stack
        (about 1000 levels on 3.11; the request limit allows about 4000): wherever it does,
        the request is bad."""
        with (
            mock.patch.object(
                protocol._decoder, "raw_decode", side_effect=RecursionError
            ),
            self.assertRaises(BadRequest),
        ):
            parse_request(dis())


class ReplyTest(unittest.TestCase):
    def test_svr_names_no_broker(self):
        s = svr("192.0.2.5")
        self.assertEqual(s["t"], "svr")
        self.assertNotIn("mqttInfo", s)
        self.assertEqual(
            (s["ip"], s["host"], s["tcpPort"], s["protocol"]),
            ("192.0.2.5", "eu.dis.gree.com", DIS_PORT, ""),
            "the host the tested module works with",
        )

    def test_pack(self):
        line = pack({"t": "svr", "x": "é"}, MAC)
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(line.count(b"\n"), 1, "one line")
        envelope, message = unpack(line)
        self.assertEqual(
            (envelope["t"], envelope["i"], envelope["tcid"]), ("pack", 1, MAC)
        )
        self.assertEqual(message, {"t": "svr", "x": "é"})

    def test_reply_to_dis(self):
        envelope, message = unpack(reply(json.loads(dis()), "192.0.2.5"))
        self.assertEqual(envelope["tcid"], MAC)
        self.assertEqual(message, svr("192.0.2.5"))

    def test_no_reply_to_others(self):
        for request in (
            {"t": "devLogin", "mac": MAC},
            {"t": "hb"},
            {"t": "pack", "i": 1, "pack": ""},
            {},
        ):
            with self.subTest(request=request):
                self.assertIsNone(reply(request, "192.0.2.5"))

    def test_module_id(self):
        self.assertEqual(module_id({"mac": MAC}), MAC)
        self.assertEqual(module_id({"mac": 12}), "")
        self.assertEqual(module_id({}), "")
        self.assertEqual(len(module_id({"mac": "a" * 100})), 32)


class CertificateTest(unittest.TestCase):
    def test_self_signed_rsa(self):
        cert_pem, key_pem = certificate()
        cert = x509.load_pem_x509_certificate(cert_pem)
        self.assertIsInstance(
            cert.public_key(),
            rsa.RSAPublicKey,
            "the module offers RSA key exchange only",
        )
        self.assertEqual(cert.public_key().key_size, 2048)
        self.assertEqual(cert.issuer, cert.subject)
        self.assertIn(b"PRIVATE KEY", key_pem)


if __name__ == "__main__":
    unittest.main()
