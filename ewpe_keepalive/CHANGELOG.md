# Changelog

## 0.1.0

First version.

- Answers the module's dis request on TCP 1813 (TLS) with the cloud's svr reply without an
  MQTT broker, then closes the connection; answers nothing else and sends nothing of its
  own.
- A new self-signed RSA certificate at each start; TLS 1.2 with the RSA key exchange the
  module offers. A failed TLS handshake is logged with OpenSSL's reason.
- An optional DNS server for a module given Home Assistant as its DNS server: off by
  default (option `dns_address`, port `53/udp` closed); when on, UDP only, an A record
  for `dis.gree.com` and the names under it, NXDOMAIN for any other name, nothing
  forwarded. With it on, `svr` names that address, and `/health` checks the DNS server
  too.
- `allowed_ips` for connections and DNS queries (each rejected address logged once, the
  rest counted); deadlines for the TLS handshake and the request, a request size limit,
  up to 8 connections at a time.
- Follows each module by its MAC: connected, pauses between dis requests (restarts,
  Wi-Fi), counters per hour (kept per minute: the same memory under any load) and in
  total.
- Status JSON (`/status`) for Home Assistant REST sensors and `/health` for the watchdog,
  on the internal network only; up to 16 status connections, 5 s for a whole request.
- Event log with an hourly summary; MAC and addresses only at the `debug` level.
- Runs on amd64 and aarch64.
