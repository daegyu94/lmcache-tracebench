"""Build the one-shot ``lmcache trace replay`` command."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from .config import ReplayerConfig


_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]\s+(?:OK|FAIL)\s+")


def _progress_from_log_line(line: str) -> tuple[int, int] | None:
    match = _PROGRESS_RE.search(line)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def build_command(config: ReplayerConfig, trace_path: str) -> list[str]:
    config.validate()
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
        json.dumps(config.l2_adapter, sort_keys=True, separators=(",", ":")),
        "--output-dir",
        config.output_dir,
        "--json",
        "--quiet",
    ]


def run_command(config: ReplayerConfig, trace_path: str) -> int:
    """Execute replay, persist LMCache logs, and return its exit code."""
    trace = Path(trace_path).expanduser()
    if not trace.is_file():
        raise FileNotFoundError(f"trace file not found: {trace}")
    output_dir = Path(config.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "lmcache-replay.log"
    started_at = time.monotonic()
    last_update_at = 0.0
    progress_seen = False

    with log_path.open("w", encoding="utf-8") as log_file:
        with subprocess.Popen(
            build_command(config, str(trace)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()
                progress = _progress_from_log_line(line)
                if progress is None:
                    continue
                completed, total = progress
                now = time.monotonic()
                if completed < total and now - last_update_at < 0.5:
                    continue
                elapsed = now - started_at
                percent = 100.0 * completed / total if total else 0.0
                print(
                    f"\r[progress] records={completed}/{total} "
                    f"({percent:.1f}%) elapsed={elapsed:.1f}s",
                    end="",
                    flush=True,
                )
                progress_seen = True
                last_update_at = now
            return_code = process.wait()

    if progress_seen:
        print()
    if return_code == 0:
        print(f"[INFO] Replay complete. LMCache log: {log_path}")
    else:
        print(
            f"[ERROR] Replay failed with exit code {return_code}. "
            f"LMCache log: {log_path}"
        )
    return return_code
