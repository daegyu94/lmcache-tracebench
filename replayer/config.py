"""Configuration for one-shot LMCache trace replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ReplayerConfig:
    lmcache_binary: str = "lmcache"
    output_dir: str = "outputs/replayer"
    l1_size_gb: float = 1.0
    l1_init_size_gb: int = 1
    eviction_policy: str = "noop"
    l2_store_policy: str = "skip_l1"
    l1_align_bytes: int = 4096
    l2_adapter: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "fs_native",
            "base_path": "/mnt/ssd/lmcache-trace-replay",
            "num_workers": 8,
            "use_odirect": True,
        }
    )
    verbose: bool = False

    def validate(self) -> None:
        if self.l1_size_gb <= 0:
            raise ValueError("l1_size_gb must be positive")
        if (
            not isinstance(self.l1_init_size_gb, int)
            or isinstance(self.l1_init_size_gb, bool)
            or self.l1_init_size_gb <= 0
        ):
            raise ValueError("l1_init_size_gb must be a positive integer")
        if self.l2_store_policy != "skip_l1":
            raise ValueError("replayer currently requires l2_store_policy=skip_l1")
        if not isinstance(self.l2_adapter, dict) or not self.l2_adapter.get("type"):
            raise ValueError("l2_adapter must be a mapping with a non-empty type")
        if self.l1_align_bytes <= 0:
            raise ValueError("l1_align_bytes must be positive")


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_raw_config(
    config_path: Path,
    *,
    ancestry: tuple[Path, ...] = (),
) -> dict[str, Any]:
    resolved_path = config_path.expanduser().resolve()
    if resolved_path in ancestry:
        chain = " -> ".join(str(item) for item in (*ancestry, resolved_path))
        raise ValueError(f"config extends cycle: {chain}")
    with resolved_path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream) or {}
    if not isinstance(loaded, dict):
        raise ValueError("replayer config must contain a mapping")

    extends = loaded.pop("extends", None)
    if extends is None:
        return loaded
    if not isinstance(extends, str) or not extends:
        raise ValueError("config extends must be a non-empty relative path")
    parent = _load_raw_config(
        resolved_path.parent / extends, ancestry=(*ancestry, resolved_path)
    )
    return _merge(parent, loaded)


def load_config(path: str | Path | None = None) -> ReplayerConfig:
    raw: dict[str, Any] = {}
    if path is not None:
        raw = _load_raw_config(Path(path))
    if not isinstance(raw, dict):
        raise ValueError("replayer config must contain a mapping")
    config = ReplayerConfig(**raw)
    config.validate()
    return config
