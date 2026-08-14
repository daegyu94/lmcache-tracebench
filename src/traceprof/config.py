"""Configuration for distributed storage-node profiling."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_BRACE_RE = re.compile(r"\{([^{}]*)\}")
_RANGE_RE = re.compile(r"^(-?\d+)\.\.(-?\d+)$")


def expand_brace_pattern(spec: str) -> list[str]:
    """Expand bash-brace-style {a..b} ranges and {a,b,c} lists in ``spec``.

    A string with no braces is returned unchanged as a single-item list, so
    plain device/interface entries pass through untouched. Multiple brace
    groups in one string are expanded as their cartesian product.
    """
    matches = list(_BRACE_RE.finditer(spec))
    if not matches:
        return [spec]

    options: list[list[str]] = []
    for match in matches:
        body = match.group(1)
        if ".." in body:
            range_match = _RANGE_RE.match(body)
            if not range_match:
                raise ValueError(
                    f"invalid range expression '{{{body}}}' in spec: {spec}"
                )
            start, end = int(range_match.group(1)), int(range_match.group(2))
            step = 1 if start <= end else -1
            options.append([str(value) for value in range(start, end + step, step)])
        else:
            items = [item.strip() for item in body.split(",")]
            if not body or any(not item for item in items):
                raise ValueError(f"empty brace expansion '{{{body}}}' in spec: {spec}")
            options.append(items)

    expanded: list[str] = []
    for combination in itertools.product(*options):
        rendered = spec
        for match, value in zip(reversed(matches), reversed(combination)):
            rendered = rendered[: match.start()] + value + rendered[match.end() :]
        expanded.append(rendered)
    return expanded


@dataclass(frozen=True)
class NodeConfig:
    hostname: str
    devices: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()
    role: str = "storage"
    user: str | None = None
    port: int | None = None

    def validate(self) -> None:
        if not self.hostname or _NAME_RE.fullmatch(self.hostname) is None:
            raise ValueError(
                "profile node hostname must contain only letters, numbers, "
                "underscores, dots, and hyphens"
            )
        if self.role not in {"storage", "replay"}:
            raise ValueError(
                f"profile node {self.hostname} role must be 'storage' or 'replay'"
            )
        if not self.devices and not self.interfaces:
            raise ValueError(
                f"profile node {self.hostname} must configure a device or interface"
            )
        if len(set(self.devices)) != len(self.devices):
            raise ValueError(f"profile node {self.hostname} has duplicate devices")
        if len(set(self.interfaces)) != len(self.interfaces):
            raise ValueError(f"profile node {self.hostname} has duplicate interfaces")
        for device in self.devices:
            if not device.startswith("/dev/"):
                raise ValueError(
                    f"profile node {self.hostname} device must be an absolute /dev "
                    f"path: {device}"
                )
        for interface in self.interfaces:
            if not interface or "/" in interface:
                raise ValueError(
                    f"profile node {self.hostname} interface must be a name: "
                    f"{interface}"
                )
        if self.port is not None and not 1 <= self.port <= 65535:
            raise ValueError(f"profile node {self.hostname} port is out of range")


@dataclass(frozen=True)
class ProfilerConfig:
    nodes: tuple[NodeConfig, ...]
    sample_interval_seconds: float = 5.0
    report_interval_seconds: float = 5.0
    remote_tmp_root: str = "/tmp/lmcache-tracebench-profile"
    ssh_user: str | None = None
    ssh_binary: str = "ssh"
    scp_binary: str = "scp"
    ssh_options: tuple[str, ...] = ()
    startup_timeout_seconds: float = 30.0
    stop_timeout_seconds: float = 30.0
    cleanup_on_success: bool = True

    def validate(self) -> None:
        if not self.nodes:
            raise ValueError("profile config must contain at least one node")
        hostnames = [node.hostname for node in self.nodes]
        if len(set(hostnames)) != len(hostnames):
            raise ValueError("profile node hostnames must be unique")
        for node in self.nodes:
            node.validate()
        if self.sample_interval_seconds <= 0:
            raise ValueError("sample_interval_seconds must be positive")
        if self.report_interval_seconds <= 0:
            raise ValueError("report_interval_seconds must be positive")
        if not self.remote_tmp_root.startswith("/"):
            raise ValueError("remote_tmp_root must be an absolute path")
        if Path(self.remote_tmp_root) in {Path("/"), Path("/tmp")}:
            raise ValueError("remote_tmp_root is too broad")
        if self.startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if self.stop_timeout_seconds <= 0:
            raise ValueError("stop_timeout_seconds must be positive")


def _tuple_strings(
    value: Any, field_name: str, *, expand_braces: bool = False
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    if not expand_braces:
        return tuple(value)
    expanded: list[str] = []
    for item in value:
        try:
            expanded.extend(expand_brace_pattern(item))
        except ValueError as exc:
            raise ValueError(f"{field_name}: {exc}") from exc
    return tuple(expanded)


def load_config(path: str | Path) -> ProfilerConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    if not isinstance(raw, dict):
        raise ValueError("profile config must contain a mapping")

    raw_nodes = raw.pop("nodes", None)
    if not isinstance(raw_nodes, list):
        raise ValueError("profile config nodes must be a list")
    raw_replay_node = raw.pop("replay_node", None)
    if raw_replay_node is not None:
        if not isinstance(raw_replay_node, dict):
            raise ValueError("replay_node must be a mapping or null")
        raw_nodes.append({**raw_replay_node, "role": "replay"})
    default_user = raw.pop("ssh_user", None)
    if default_user is not None and not isinstance(default_user, str):
        raise ValueError("ssh_user must be a string or null")

    nodes: list[NodeConfig] = []
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            raise ValueError(f"profile node at index {index} must be a mapping")
        node = NodeConfig(
            hostname=raw_node.get("hostname", ""),
            devices=_tuple_strings(
                raw_node.get("devices"), "node.devices", expand_braces=True
            ),
            interfaces=_tuple_strings(
                raw_node.get("interfaces"), "node.interfaces", expand_braces=True
            ),
            role=raw_node.get("role", "storage"),
            user=raw_node.get("user", default_user),
            port=raw_node.get("port"),
        )
        nodes.append(node)

    known = {
        "sample_interval_seconds",
        "report_interval_seconds",
        "remote_tmp_root",
        "ssh_binary",
        "scp_binary",
        "ssh_options",
        "startup_timeout_seconds",
        "stop_timeout_seconds",
        "cleanup_on_success",
    }
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown profile config fields: {', '.join(sorted(unknown))}")
    values = {key: raw[key] for key in known if key in raw}
    values["nodes"] = tuple(nodes)
    values["ssh_options"] = _tuple_strings(
        raw.get("ssh_options"), "ssh_options"
    )
    config = ProfilerConfig(**values)
    config.validate()
    return config
