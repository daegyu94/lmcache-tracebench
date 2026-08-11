# SPDX-License-Identifier: Apache-2.0

"""Configuration for one-shot LMCache trace replay."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
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
    speedup: float = 1.0
    trace_percent: float = 100.0
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
        if not math.isfinite(self.speedup) or self.speedup <= 0:
            raise ValueError("speedup must be finite and positive")
        if (
            not math.isfinite(self.trace_percent)
            or self.trace_percent <= 0
            or self.trace_percent > 100
        ):
            raise ValueError("trace_percent must be finite and in (0, 100]")


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


def apply_overrides(
    config: ReplayerConfig,
    *,
    l2_path: str | None = None,
    output_dir: str | None = None,
    speedup: float | None = None,
    trace_percent: float | None = None,
    l1_size_gb: float | None = None,
    l1_init_size_gb: int | None = None,
) -> ReplayerConfig:
    """Return config with CLI overrides applied."""
    if l2_path is not None:
        if not l2_path:
            raise ValueError("l2_path must not be empty")
        adapter = dict(config.l2_adapter)
        adapter_type = adapter.get("type")
        if adapter_type == "fs_native":
            adapter["base_path"] = l2_path
        elif adapter_type == "nixl_store_dynamic":
            backend_params = adapter.get("backend_params")
            if not isinstance(backend_params, dict):
                raise ValueError(
                    "nixl_store_dynamic adapter requires backend_params mapping"
                )
            adapter["backend_params"] = {**backend_params, "file_path": l2_path}
        else:
            raise ValueError(
                "--l2-path is supported for fs_native and nixl_store_dynamic adapters"
            )
        config = replace(config, l2_adapter=adapter)
    if output_dir is not None:
        if not output_dir:
            raise ValueError("output_dir must not be empty")
        config = replace(config, output_dir=output_dir)
    if speedup is not None:
        config = replace(config, speedup=speedup)
    if trace_percent is not None:
        config = replace(config, trace_percent=trace_percent)
    if l1_size_gb is not None:
        config = replace(config, l1_size_gb=l1_size_gb)
    if l1_init_size_gb is not None:
        config = replace(config, l1_init_size_gb=l1_init_size_gb)
    config.validate()
    return config
