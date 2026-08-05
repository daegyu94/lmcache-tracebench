"""Configuration for distributed storage-node profiling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class NodeConfig:
    name: str
    host: str
    devices: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()
    role: str = "storage"
    user: str | None = None
    port: int | None = None

    def validate(self) -> None:
        if not self.name or _NAME_RE.fullmatch(self.name) is None:
            raise ValueError(
                "profile node name must contain only letters, numbers, "
                "underscores, dots, and hyphens"
            )
        if not self.host:
            raise ValueError(f"profile node {self.name} host must not be empty")
        if self.role not in {"storage", "replay"}:
            raise ValueError(
                f"profile node {self.name} role must be 'storage' or 'replay'"
            )
        if not self.devices and not self.interfaces:
            raise ValueError(
                f"profile node {self.name} must configure a device or interface"
            )
        if len(set(self.devices)) != len(self.devices):
            raise ValueError(f"profile node {self.name} has duplicate devices")
        if len(set(self.interfaces)) != len(self.interfaces):
            raise ValueError(f"profile node {self.name} has duplicate interfaces")
        for device in self.devices:
            if not device.startswith("/dev/"):
                raise ValueError(
                    f"profile node {self.name} device must be an absolute /dev path: "
                    f"{device}"
                )
        for interface in self.interfaces:
            if not interface or "/" in interface:
                raise ValueError(
                    f"profile node {self.name} interface must be a name: {interface}"
                )
        if self.port is not None and not 1 <= self.port <= 65535:
            raise ValueError(f"profile node {self.name} port is out of range")


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
        names = [node.name for node in self.nodes]
        if len(set(names)) != len(names):
            raise ValueError("profile node names must be unique")
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


def _tuple_strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)


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
            name=raw_node.get("name", ""),
            host=raw_node.get("host", ""),
            devices=_tuple_strings(raw_node.get("devices"), "node.devices"),
            interfaces=_tuple_strings(
                raw_node.get("interfaces"), "node.interfaces"
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
