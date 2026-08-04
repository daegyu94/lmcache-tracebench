"""Configuration for one-shot LMCache trace replay."""

from __future__ import annotations

from dataclasses import dataclass
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
    l2_type: str = "fs_native"
    l2_base_path: str = "/mnt/ssd/lmcache-trace-replay"
    l2_num_workers: int | None = 8
    l2_extra: dict[str, Any] | None = None
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
        if self.l2_type != "fs_native":
            raise ValueError("the replayer currently requires l2_type=fs_native")
        if not self.l2_base_path:
            raise ValueError("l2_base_path must not be empty")
        if self.l1_align_bytes <= 0:
            raise ValueError("l1_align_bytes must be positive")


def load_config(path: str | Path | None = None) -> ReplayerConfig:
    raw = {}
    if path is not None:
        with Path(path).expanduser().open(encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
    if not isinstance(raw, dict):
        raise ValueError("replayer config must contain a mapping")
    config = ReplayerConfig(**raw)
    config.validate()
    return config
