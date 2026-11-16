# EWPE Keepalive

## What it does

Devices with a Gree Wi-Fi module (air conditioners, dehumidifiers and more, sold as Gree,
Cooper&Hunter, Sinclair, Daitsu and other brands, set up with the EWPE Smart or Gree+ app)
can be controlled by Home Assistant over the local network, on UDP port 7000. But the
module answers there only while its cloud answers it: blocked from the internet, it goes
silent.

The module's first step towards the cloud is a question to the Gree dis server,
`<region>dis.gree.com` (for example `eu.dis.gree.com`), on TCP port 1813 over TLS: where
is my cloud? This app answers that question the way the cloud does, but names no cloud
server to go on to. The module then answers on the local network again and repeats the
question about every 5 seconds, without ever reaching the cloud.

- **Answers only.** The app answers the module's question and nothing else. It sends
  nothing of its own and never controls the device.
- **Nothing leaves your network**, and nothing is stored.
- **DNS has to send the module here.** The module finds the dis server by name, so
  `dis.gree.com` has to give the address of Home Assistant: a rewrite in the DNS your
  network uses, or the app's own small DNS server for the module (step 3).

## Setup

1. Install and start the app. The **Log** tab shows
   `EWPE Keepalive 0.1.0: dis port 1813, no DNS server, status on port 8080`.
2. Recommended: set `allowed_ips` to your module's address. In the **Configuration** tab,
   add the address as an item of the list, or in YAML mode:

   ```yaml
   allowed_ips:
     - 192.0.2.10
   ```

   (your module's address instead of `192.0.2.10`; your router shows it, and so does the
   device's page in Home Assistant).
3. Send `dis.gree.com` to Home Assistant, one of two ways.

   **A. A rewrite in your DNS**, if your network has AdGuard Home, Pi-hole or a router with
   dnsmasq. The app's DNS server stays off. In the DNS server your router hands out,
   rewrite `dis.gree.com` and the names under it to the address of Home Assistant:

   - dnsmasq (Asuswrt-Merlin: `/jffs/configs/dnsmasq.conf.add`, with JFFS custom scripts
     enabled; Pi-hole v6: `misc.dnsmasq_lines`):
     `address=/dis.gree.com/<Home Assistant address>`
   - AdGuard Home, custom filtering rules:
     `||dis.gree.com^$dnsrewrite=<Home Assistant address>`

   Both cover `eu.dis.gree.com` and the other regions, for every device of the network:
   any other Gree module in it comes to the app too, and loses its cloud. AdGuard Home can
   limit the rule to your module with the `$client` modifier. Other Gree names (the app's
   account server, for example) stay as they are. To check, `nslookup eu.dis.gree.com` on
   a computer in the same network must give the address of Home Assistant.

   **B. The app's DNS server**, if your router can give one device a DNS server of its own
   but cannot rewrite names.

   1. In the **Configuration** tab, turn on **Show unused optional configuration
      options**, set `dns_address` to the address of Home Assistant and save.
   2. In the **Network** section, turn on **Show disabled ports**, set `53/udp` to `53` and
      save. Restart the app: the log shows `DNS server on port 53`.
   3. In your router's DHCP settings, give the module the address of Home Assistant as its
      DNS server (Asuswrt-Merlin: **LAN → DHCP Server**, the module's line in the list of
      manually assigned addresses, **DNS Server**; in other routers, a DNS server for one
      device, DHCP option 6).

   The app answers only `dis.gree.com` and the names under it, and "no such name"
   (NXDOMAIN) for any other: it forwards nothing, so give it to no other device. Port 53
   must be free on the host: with AdGuard Home, Pi-hole or Dnsmasq on this Home Assistant,
   the app does not start with `53/udp` set, so use way A there. To check from a computer:
   `nslookup eu.dis.gree.com <Home Assistant address>` (while `allowed_ips` is empty or
   lists the computer).
4. Recommended: block the module's internet access in your router. Either way the module
   does not reach the cloud by name, but the block makes sure.
5. Restart the module: unplug the device for 10 seconds. Within a minute the log shows
   `module connected` (with way B, after `DNS: first answer for dis.gree.com`), and the
   device answers in Home Assistant.

## Options

| Option | Default | Meaning |
|---|---|---|
| `allowed_ips` | empty | Accept connections and DNS queries only from these addresses: put your module's address here. Empty: any address. |
| `dns_address` | not set | The app's DNS server (way B): the address of Home Assistant, given for `dis.gree.com`. Not set or empty: no DNS server. Needs `53/udp` set to `53` in **Network**. |
| `log_level` | `info` | `info`: events and an hourly summary. `debug`: also every request, with the module's MAC and address. |

## Status in Home Assistant

The app serves `GET /status` (JSON) and `GET /health` on port 8080 of the internal network.
Home Assistant reaches it by the app's host name: the app ID with `_` replaced by `-`, for
example `1a2b3c4d-ewpe-keepalive` (the app ID is in the address bar of the app's page).
The port does not need to be opened on the host. `/health` answers `ok` while the dis port
and, if it is on, the DNS server are open; otherwise the Supervisor's watchdog restarts
the app.

```yaml
rest:
  - resource: http://1a2b3c4d-ewpe-keepalive:8080/status
    scan_interval: 60
    binary_sensor:
      - name: EWPE Module Connected
        device_class: connectivity
        value_template: "{{ value_json.connected }}"
    sensor:
      - name: EWPE Module Last Request
        device_class: timestamp
        value_template: "{{ value_json.last_dis }}"
      - name: EWPE Module Pauses Last Hour
        value_template: "{{ value_json.last_hour.gaps }}"
```

| Field | Meaning |
|---|---|
| `connected` | A dis request came within the last 30 seconds (the module asks every 5). |
| `modules` | How many different modules asked since the app started. |
| `last_dis`, `last_dis_age_s` | Time of the last dis request (UTC) and its age in seconds. |
| `dns_server` | The app's DNS server is on (`dns_address` set). |
| `last_hour` | dis requests, pauses (more than 15 s between two requests of a module: a restart, Wi-Fi), TLS failures, rejected connections and DNS answers for `dis.gree.com` (`dns`; about 60 an hour per module) in the last hour. |
| `totals` | The same and more since the app started: connections, requests the app does not answer (`unknown`), connections without a valid request (`bad`), connections closed because 8 were open (`busy`); DNS: NXDOMAIN answers (`dns_other`), queries from addresses not in `allowed_ips` (`dns_rejected`), packets that are no DNS query (`dns_bad`). |

The status contains no MAC addresses and no IP addresses.

## Logs

- **Log tab** (`info`): the module connecting, its pauses, TLS errors, rejected
  connections and DNS queries, requests the app does not answer, the first DNS answer
  and the first NXDOMAIN, and a summary every hour. No MAC or IP addresses, and no names
  other than `dis.gree.com`.
- **`debug`**: also every connection, request and DNS query, with the module's MAC and
  address.

## Good to know

- **About 720 short connections an hour.** The module asks every 5 seconds, each time with
  a new TLS connection: firmware V1.45 does this when the answer names no cloud server.
- **No time from the cloud.** The module's clock starts at 1970-01-01 at each boot. Control
  from Home Assistant worked without it in the test.
- **If the app is stopped** (an update, a restart of Home Assistant or the host), the
  module stops answering within 5 to 20 seconds, and the device is `unavailable` in Home
  Assistant meanwhile. Once the app is back, the module asks within seconds and Home
  Assistant sees the device again within a minute. A stop of 15 minutes made the module
  restart itself after about 14; the device keeps running through a module restart. This
  was measured with way B (the app's prototype was the module's DNS server too); with
  way A it was not measured.
- **Several modules** can use one app: each gets its answer, the status counts them in
  `modules` and adds their requests up.
- **The certificate** is self-signed and new at each start. The module does not check it
  on this port.
- **Control through the cloud stops**, the EWPE Smart or Gree+ app away from home
  included: that is the point.

## Troubleshooting

- No `module connected`, way A: check the DNS (`nslookup eu.dis.gree.com`), that the
  module uses that DNS server (restart it after changing the router), that port 1813 is
  enabled in the app's **Network** section, and `allowed_ips`.
- No `module connected`, way B: no `DNS: first answer for dis.gree.com` in the log means
  the queries do not reach the app: check that `53/udp` is set to `53` in **Network**, the
  module's DNS server in the router (restart the module after changing it), and
  `allowed_ips`. With the DNS answer logged, check port 1813 and `allowed_ips`.
- The app does not start once `53/udp` is set: another app on the host uses port 53
  (AdGuard Home, Pi-hole, Dnsmasq). Clear `53/udp` and use way A, in that DNS server.
- `rejected a connection from an address not in allowed_ips` (or `a DNS query`): another
  device, or the module got a new address. It is logged once per address; the hourly
  summary and the status count the rest.
- `TLS handshake failed: <reason>`: the module's TLS does not match the app's (the tested
  module's does). Please open an issue with the reason.
- `the module sent a request this app does not answer: '<type>'`: the module asked for
  more than the dis answer. Please open an issue with the type.
- To go back: remove the DNS rewrite (or the module's own DNS server in the router) and the
  internet block, and restart the module; it connects to the Gree cloud again. If it does
  not, pair it again in the EWPE Smart app.
