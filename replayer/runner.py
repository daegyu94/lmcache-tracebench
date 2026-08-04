"""Build the one-shot ``lmcache trace replay`` command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import ReplayerConfig


def build_command(config: ReplayerConfig, trace_path: str) -> list[str]:
    config.validate()
    adapter: dict[str, object] = {
        "type": config.l2_type,
        "base_path": config.l2_base_path,
    }
    if config.l2_num_workers is not None:
        adapter["num_workers"] = config.l2_num_workers
    if config.l2_extra:
        adapter.update(config.l2_extra)
    return [
        config.lmcache_binary,
        "trace",
        "replay",
        trace_path,
        "--l1-size-gb",
        str(config.l1_size_gb),
        "--l1-init-size-gb",
        str(config.l1_init_size_gb),
        "--eviction-policy",
        config.eviction_policy,
        "--l2-store-policy",
        config.l2_store_policy,
        "--l1-align-bytes",
        str(config.l1_align_bytes),
        "--l2-adapter",
        json.dumps(adapter, sort_keys=True, separators=(",", ":")),
        "--output-dir",
        config.output_dir,
        "--json",
        "--quiet",
    ]


def run_command(config: ReplayerConfig, trace_path: str) -> int:
    """Execute a one-shot replay and return LMCache's exit code."""
    trace = Path(trace_path).expanduser()
    if not trace.is_file():
        raise FileNotFoundError(f"trace file not found: {trace}")
    Path(config.output_dir).expanduser().mkdir(parents=True, exist_ok=True)
    return subprocess.run(build_command(config, str(trace)), check=False).returncode
