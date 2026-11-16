"""EWPE Keepalive: a local stand-in for the Gree cloud's dis server.

A Gree Wi-Fi module (devices paired with the EWPE Smart or Gree+ app) answers on the
local network (UDP 7000) only while its cloud thread gets answers: blocked from the
internet, it goes silent. Its first
step is a question to the dis server, <region>dis.gree.com on TCP 1813 over TLS: where is
my cloud? With that name sent here by your DNS (or by this app's optional DNS server),
this server answers, the module answers locally again, and asks again about every
5 seconds.

The server only answers dis, with the cloud's reply minus the MQTT broker, so the module
never connects anywhere else. It sends nothing of its own.

Home Assistant app: python -m keepalive --options /data/options.json
Standalone, from the ewpe_keepalive folder:
             python -m keepalive --allow <module-ip> [--dns <this machine's ip>]

Modules:
  protocol   the dis request and the svr reply (no I/O)
  tls        the self-signed certificate, the TLS settings the module accepts, TLS over
             an accepted connection
  dns        the optional DNS server: dis.gree.com only (no I/O but the UDP endpoint)
  config     options
  tracker    what the module does: counters for the log and the status (no I/O)
  server     what the module asks: dis connections, DNS queries
  status     the HTTP status endpoint
  __main__   command line, logging, the main loop

Python 3.11 or newer, with the cryptography package (AES for the reply, the certificate).
"""

__version__ = "0.1.0"
