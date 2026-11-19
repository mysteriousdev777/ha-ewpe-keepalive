"""Command line, logging and the main loop: python -m keepalive."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys

from . import __version__
from .config import APP_OPTIONS, LOG_LEVELS, Config, ConfigError, load_options
from .dns import DNS_PORT
from .protocol import DIS_PORT
from .server import KeepaliveServer, log


def setup_logging(level: str) -> None:
    for h in list(log.handlers):
        log.removeHandler(h)
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    log.addHandler(out)
    log.setLevel(getattr(logging, level.upper()))
    log.propagate = False


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m keepalive",
        description="EWPE Keepalive: a local stand-in for the Gree cloud's dis server",
    )
    p.add_argument(
        "--options",
        metavar="FILE",
        help=f"Home Assistant app options ({APP_OPTIONS}); "
        "other options are then ignored",
    )
    p.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="IP",
        help="accept connections only from this address (repeatable; default: any)",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="address to listen on (default 0.0.0.0, all; 127.0.0.1 for local tries)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=DIS_PORT,
        help=f"dis port (default {DIS_PORT}; the module always connects to {DIS_PORT})",
    )
    p.add_argument(
        "--dns",
        metavar="ADDRESS",
        help=f"also answer DNS queries for dis.gree.com on UDP {DNS_PORT}, with this address "
        "(this machine's); for a module given this machine as its DNS server. "
        "Default: no DNS server",
    )
    p.add_argument(
        "--status-port",
        type=int,
        default=8080,
        help="port of the status endpoint (default 8080)",
    )
    p.add_argument("--no-status", action="store_true", help="no status endpoint")
    p.add_argument("--log-level", choices=LOG_LEVELS, default="info")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def config_from_args(a: argparse.Namespace) -> Config:
    if a.options:
        return load_options(a.options)
    return Config(
        allowed_ips=a.allow,
        log_level=a.log_level,
        dns_address=a.dns,
        host=a.host,
        port=a.port,
        status_port=None if a.no_status else a.status_port,
    ).validate()


async def run(cfg: Config) -> None:
    server = KeepaliveServer(cfg)
    await server.start()
    dns = (
        f"DNS server on port {server.dns_port}"
        if server.dns_port is not None
        else "no DNS server"
    )
    status = (
        f"status on port {server.status_port}"
        if server.status_port is not None
        else "no status endpoint"
    )
    log.info(
        "EWPE Keepalive %s: dis port %d, %s, %s", __version__, server.port, dns, status
    )
    log.info(
        "allowed addresses: %s",
        f"{len(cfg.allowed_ips)} configured" if cfg.allowed_ips else "any",
    )
    log.debug("allowed_ips: %s", ", ".join(cfg.allowed_ips) or "any")
    log.debug("dns_address: %s", cfg.dns_address or "none")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await server.stop()
        log.info("stopped")


def main(argv: list[str] | None = None) -> int:
    setup_logging("info")
    try:
        cfg = config_from_args(parse_args(argv))
        setup_logging(cfg.log_level)
    except ConfigError as e:
        log.error("%s", e)
        return 2
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        log.error("cannot listen: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
