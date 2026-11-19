#!/usr/bin/env python3
"""Consistency checks of the app definition (CI): config.yaml, the option translations,
CHANGELOG.md, the Dockerfile base image, the pinned dependencies and the server version
must agree.

Needs PyYAML (requirements-dev.txt): a development tool, not part of the app.
"""

from __future__ import annotations

import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "ewpe_keepalive")
ARCHES = {"amd64", "aarch64"}
MAP_TYPES = {
    "app_config",
    "addon_config",
    "homeassistant_config",
    "share",
    "ssl",
    "media",
    "backup",
    "data",
    "all_app_configs",
    "all_addon_configs",
}
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==(\S+)", re.M)


def read(*parts: str) -> str:
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def check_app(cfg: dict, errors: list[str]) -> None:
    """config.yaml: the required keys, the version, the slug and the architectures."""
    for key in ("name", "version", "slug", "description", "arch"):
        if key not in cfg:
            errors.append(f"config.yaml: missing {key}")
    version = cfg.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        errors.append(
            f"config.yaml: version must be a quoted x.y.z string, not {version!r}"
        )
    slug = cfg.get("slug")
    if slug != os.path.basename(APP) or not re.fullmatch(r"[a-z0-9_.-]+", str(slug)):
        errors.append(
            "config.yaml: slug must match the folder name and use a-z 0-9 _ . -"
        )
    if not set(cfg.get("arch", [])) <= ARCHES or not cfg.get("arch"):
        errors.append(
            f"config.yaml: arch must be a non-empty subset of {sorted(ARCHES)}"
        )


def check_options(cfg: dict, errors: list[str]) -> None:
    """config.yaml: a schema for every option, a default for every required one; maps."""
    options, schema = cfg.get("options", {}), cfg.get("schema", {})
    for key in options:
        if key not in schema:
            errors.append(f"config.yaml: option {key} has no schema")
    for key, kind in schema.items():
        optional = isinstance(kind, str) and kind.endswith("?")
        if not optional and key not in options:
            errors.append(f"config.yaml: required option {key} has no default")

    for m in cfg.get("map", []):
        kind = m.get("type") if isinstance(m, dict) else str(m).split(":")[0]
        if kind not in MAP_TYPES:
            errors.append(f"config.yaml: unknown map type {kind}")


def check_ports(cfg: dict, errors: list[str]) -> None:
    """config.yaml: the dis port, the optional DNS server and the watchdog port."""
    ports = cfg.get("ports", {})
    if ports.get("1813/tcp") != 1813:
        errors.append(
            "config.yaml: the module always connects to 1813: map 1813/tcp to 1813"
        )
    if ports.get("53/udp") is not None:
        errors.append(
            "config.yaml: the DNS server is optional: 53/udp must be off (null) by default, "
            "or it clashes with a DNS app on the host"
        )
    if "dns_address" in cfg.get("options", {}):
        errors.append(
            "config.yaml: dns_address must have no default (the DNS server is off unless set)"
        )
    if wd := cfg.get("watchdog"):
        m = re.search(r"\[PORT:(\d+)\]", wd)
        if m and f"{m[1]}/tcp" not in ports:
            errors.append(f"config.yaml: watchdog port {m[1]} is not declared in ports")


def check_translations(cfg: dict, errors: list[str]) -> None:
    """translations/en.yaml: a name and a description for each option, each port."""
    schema, ports = cfg.get("schema", {}), cfg.get("ports", {})
    tr = yaml.safe_load(read(APP, "translations", "en.yaml"))
    conf, net = tr.get("configuration", {}), tr.get("network", {})
    for key in schema:
        entry = conf.get(key) or {}
        if not entry.get("name") or not entry.get("description"):
            errors.append(
                f"translations/en.yaml: option {key} needs a name and a description"
            )
    for key in conf:
        if key not in schema:
            errors.append(f"translations/en.yaml: {key} is not an option")
    for port in ports:
        if port not in net:
            errors.append(f"translations/en.yaml: port {port} needs a description")


def check_versions(cfg: dict, errors: list[str]) -> None:
    """CHANGELOG.md and the server carry the version of config.yaml."""
    version = cfg.get("version")
    m = re.search(r"^## (\S+)", read(APP, "CHANGELOG.md"), re.M)
    if not m or m[1] != version:
        errors.append(f"CHANGELOG.md: the first entry must be ## {version}")
    m = re.search(
        r'^__version__ = "([^"]+)"', read(APP, "keepalive", "__init__.py"), re.M
    )
    if not m or m[1] != version:
        errors.append(f"keepalive/__init__.py: __version__ must be {version}")


def check_dockerfile(errors: list[str]) -> None:
    """The Dockerfile sets its base image, pinned to a dated tag."""
    m = re.search(r"^ARG BUILD_FROM=(\S+)$", read(APP, "Dockerfile"), re.M)
    if not m:
        errors.append(
            "Dockerfile: needs ARG BUILD_FROM=<image>:<tag> (Supervisor no longer passes it)"
        )
    elif not re.search(r":[\w.-]*\d{4}\.\d{2}\.\d+$", m[1]):
        errors.append(f"Dockerfile: pin the base image to a dated tag, not {m[1]}")


def check_requirements(errors: list[str]) -> None:
    """The image installs pinned, hashed wheels; the tests use the same cryptography."""
    image = read(APP, "requirements.txt")
    pins = dict(PIN.findall(image))
    for line in image.replace("\\\n", " ").splitlines():
        if PIN.match(line) and "--hash=sha256:" not in line:
            errors.append(
                f"ewpe_keepalive/requirements.txt: {line.split()[0]} has no hash"
            )
    dev = dict(PIN.findall(read(ROOT, "requirements-dev.txt")))
    if "cryptography" not in pins:
        errors.append("ewpe_keepalive/requirements.txt: cryptography is not pinned")
    elif dev.get("cryptography") != pins["cryptography"]:
        errors.append(
            f"requirements-dev.txt: cryptography must be =={pins['cryptography']}, as in the image"
        )


def check_repository(errors: list[str]) -> None:
    """repository.yaml names the app repository."""
    repo = yaml.safe_load(read(ROOT, "repository.yaml"))
    if not (repo or {}).get("name"):
        errors.append("repository.yaml: missing name")


def main() -> int:
    errors: list[str] = []
    cfg = yaml.safe_load(read(APP, "config.yaml"))
    check_app(cfg, errors)
    check_options(cfg, errors)
    check_ports(cfg, errors)
    check_translations(cfg, errors)
    check_versions(cfg, errors)
    check_dockerfile(errors)
    check_requirements(errors)
    check_repository(errors)

    for e in errors:
        print("ERROR", e)
    print("config check:", "FAILED" if errors else "ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
