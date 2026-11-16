"""Options: the Home Assistant app's options.json or the command line."""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field

from .dns import DNS_PORT
from .protocol import DIS_PORT

APP_OPTIONS = "/data/options.json"
LOG_LEVELS = ("debug", "info", "warning", "error")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    allowed_ips: list[str] = field(default_factory=list)
    log_level: str = "info"
    dns_address: str | None = None      # the DNS server's answer for dis.gree.com; None = no DNS server
    host: str = "0.0.0.0"
    port: int = DIS_PORT                # 0 = any free port (tests)
    dns_port: int = DNS_PORT            # 0 = any free port (tests)
    status_port: int | None = 8080      # None = off; 0 = any free port (tests)

    def validate(self) -> "Config":
        if self.log_level not in LOG_LEVELS:
            raise ConfigError(f"log_level must be one of {', '.join(LOG_LEVELS)}, not {self.log_level!r}")
        ips = []
        for ip in self.allowed_ips:
            try:
                ips.append(str(ipaddress.ip_address(str(ip).strip())))
            except ValueError as e:
                raise ConfigError(f"allowed_ips: {ip!r} is not an IP address") from e
        self.allowed_ips = ips
        if self.dns_address is not None:
            text = str(self.dns_address).strip()
            try:
                self.dns_address = str(ipaddress.IPv4Address(text)) if text else None
            except ValueError as e:
                raise ConfigError(f"dns_address: {self.dns_address!r} is not an IPv4 address") from e
        for name in ("port", "dns_port", "status_port"):
            value = getattr(self, name)
            if value is not None and not (isinstance(value, int) and 0 <= value <= 65535):
                raise ConfigError(f"{name} must be 0..65535, not {value!r}")
        return self


def load_options(path: str) -> Config:
    """Options of the Home Assistant app (Supervisor writes them to /data/options.json)."""
    try:
        with open(path, encoding="utf-8") as f:
            o = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read options {path}: {e}") from e
    if not isinstance(o, dict):
        raise ConfigError(f"options {path}: expected a JSON object")
    allowed = o.get("allowed_ips") or []
    if not isinstance(allowed, list):
        raise ConfigError("allowed_ips must be a list")
    return Config(allowed_ips=allowed, log_level=o.get("log_level", "info"),
                  dns_address=o.get("dns_address")).validate()
