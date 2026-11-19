# EWPE Keepalive

[![CI](https://github.com/mysteriousdev777/ha-ewpe-keepalive/actions/workflows/ci.yml/badge.svg)](https://github.com/mysteriousdev777/ha-ewpe-keepalive/actions/workflows/ci.yml)

A Home Assistant app that stands in for the Gree cloud on your local network, so that a
device with a Gree Wi-Fi module keeps answering Home Assistant without internet access.

## Why

Air conditioners, dehumidifiers and other devices with a Gree Wi-Fi module (Gree,
Cooper&Hunter, Sinclair, Daitsu and other brands; set up with the EWPE Smart or Gree+ app)
can be controlled locally, on UDP port 7000: the `gree` integration of Home Assistant
(air conditioners) and [EWPE Local](https://github.com/mysteriousdev777/ha-ewpe-local)
(dehumidifiers) do that. But the module's local port and its cloud
connection share one thread: blocked from the internet, the module stops answering
locally, and the device stays `unavailable` in Home Assistant.

With this app as its cloud, the module answers locally as before, and does not restart.

## What it does, and what it does not

- Answers the module's first cloud request, `dis` ("where is my cloud?"), the way the
  Gree dis server does, but without the MQTT broker that the cloud would name. That is
  enough for the module: it answers locally and asks again every 5 seconds.
- Answers nothing else, sends nothing of its own, and never controls the device.
- Keeps nothing and forwards nothing.
- The module finds the dis server by name, `<region>dis.gree.com`, so DNS has to point
  that name at Home Assistant: one line in AdGuard Home, Pi-hole or a dnsmasq router, or
  the app's optional DNS server (off by default; it answers only that name). The app's
  documentation shows both ways.
- Reports the module's state as JSON for Home Assistant REST sensors.

Tested with a **Cooper&Hunter CH-D014WD6-30LD WF** dehumidifier and its Gree module
GRJWB04-J (U-WB05RT, Realtek RTL8720CF), firmware V1.45 (firmware code `362001065279`),
controlled by [EWPE Local](https://github.com/mysteriousdev777/ha-ewpe-local), with the
app's DNS server (way B of its documentation). In the app's first three days without
internet, the module asked `dis` about 710 times an hour while powered, with no TLS
failures and no requests the app does not answer. Home Assistant polled it every 30
seconds, and every command took effect within a second. Polls went unanswered only while
the module itself was gone (the device unplugged, a power cut, once a minute off Wi-Fi),
and each time it came back by itself. Other modules and firmware versions were not
tested. The app is built for amd64 and aarch64 Home Assistant hosts.

## Open question: the request every 5 seconds

Answered without an MQTT broker, firmware V1.45 asks `dis` again once its 5-second answer
timeout is over: about 720 short TLS connections an hour. Its firmware has no switch to
turn the cloud off, and no answer stops the loop (seen in the disassembled firmware).
In the tests and the first days of use the loop caused no problems; if you know a way to
stop it, sharing it in an issue would be great.

## Approaches compared

Measured with the PC prototype on the tested module, a few minutes each. Local answers:
`status` requests with a 5-second timeout, the one EWPE Local uses.

| Answer to the module | Its connections a minute | Local answers |
|---|---|---|
| None (internet blocked, no stand-in) | - | none |
| `svr` naming a local MQTT broker: the module rejects its certificate and reconnects at once | about 1000 | 4 of 4 |
| The same, the broker holding each connection 1 s before closing it | 55-57 | all, 1.0-2.1 s late |
| The same, holding 2 s | 29 | all, 2.0-4.2 s late |
| The same, holding 3 s | 19-20 | 9 of 16 within 5 s |
| `svr` without `mqttInfo` (this app) | 12, all `dis` | all, typically in 10-30 ms |

Holding the connection trades connections for delay: while the module waits for the
broker, the same thread does not answer locally. Without `mqttInfo` there is nothing to
wait for.

## Install

1. In Home Assistant, open the app store, choose **Repositories** in the top-right menu and
   add `https://github.com/mysteriousdev777/ha-ewpe-keepalive`.
2. Install **EWPE Keepalive**, start it, and follow its **Documentation** tab: set
   `allowed_ips`, send `dis.gree.com` to Home Assistant (a rewrite in your DNS, or the
   app's DNS server for the module), restart the module.

The app is built on your device from this repository; there is no prebuilt image. Updates
are not installed automatically unless you turn that on.

## How it works

After starting, the module resolves `<region>dis.gree.com` (`eu.dis.gree.com` here), opens
TLS 1.2 to port 1813 (RSA key exchange, no SNI; a self-signed certificate is fine) and
sends

```
{"t":"dis","mac":"<12 hex digits>","p":1,"needMqtt":1}
```

The cloud answers with `svr`, packed: AES-128-ECB with the generic Gree key, base64, in
`{"t":"pack","i":1,...,"pack":"..."}`. Its fields name the next server and, in `mqttInfo`,
the MQTT broker. This app sends the same reply without `mqttInfo`. The module then skips
MQTT and asks `dis` again after its 5-second answer timeout; it looks the name up again
when the DNS answer's TTL runs out. Local control works all along.

Things learned on the way, useful when you read the logs:

- Before its first `dis` answer after a restart the module answers locally only for 10-16
  seconds; with the answer, at once.
- Its MQTT connection cannot be faked: the module checks the broker's certificate. It
  rejected a self-signed one here, and others found Gree's own CA in firmware 1.31.
- More than 50 failed connections to the dis server since boot, or more than 50 failed DNS
  lookups in a row, and the module restarts itself (firmware V1.45, from its disassembly).
- The dis reply follows [ha-gree-local](https://github.com/csorbalazs306/ha-gree-local)
  by csorbalazs306, which answers more of the handshake and names a broker.

## Standalone

The server is a small Python package (Python 3.11 or newer) with one dependency,
`cryptography`. From the `ewpe_keepalive` folder:

```
pip install cryptography
python -m keepalive --allow <module address> [--dns <this machine's address>]
```

`--help` lists the options. Port 1813 must be reachable from the module, and
`dis.gree.com` must give the machine's address: a rewrite in your DNS, or `--dns` with the
module's DNS server set to the machine (UDP 53, a privileged port on Linux).

## Development

```
pip install -r requirements-dev.txt
python -m unittest discover -s tests       # tests
ruff format .                              # format
ruff check .                               # lint
python tools/check_config.py               # the app definition
python tools/privacy_check.py --history    # before every push, see below
```

The server is in `ewpe_keepalive/keepalive/`: `protocol.py` (the `dis` request and the
`svr` reply), `tls.py` (the certificate, the TLS settings, TLS over a connection),
`dns.py` (the optional DNS server), `config.py` (options), `tracker.py` (what the module
does: the counters), `server.py` (what the module asks: `dis` connections, DNS queries),
`status.py` (`/status` and `/health`), `__main__.py` (command line and logging).

CI runs the tests on Python 3.11 and 3.14, checks the formatting and lint (`ruff`) and the
app definition (`tools/check_config.py`), and builds the image for both architectures
(amd64, aarch64) on native runners and runs it against a fake module, DNS server on.

**Privacy.** Debug logs contain the module's MAC and addresses, so `*.log` is ignored.
`tools/privacy_check.py` fails on private addresses and MAC addresses in the files; list
your own MAC addresses, network names and e-mail addresses in `.privacy-local` (ignored by
git) and run it with `--history` before pushing.

**Base image and dependencies.** The Dockerfile pins `ghcr.io/home-assistant/base-python`
to a dated tag, and `ewpe_keepalive/requirements.txt` pins `cryptography` and its
dependencies by hash. Change them on purpose, with a CHANGELOG entry and a new version in
`config.yaml` and `keepalive/__init__.py`; the `cryptography` version in
`requirements-dev.txt` follows the image (`tools/check_config.py` checks it).

## License

[MIT](LICENSE). Not affiliated with Gree, EWPE or any device maker.
