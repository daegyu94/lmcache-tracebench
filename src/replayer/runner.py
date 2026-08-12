# SPDX-License-Identifier: Apache-2.0

"""Build the one-shot ``lmcache trace replay`` command."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import ReplayerConfig

if TYPE_CHECKING:
    from traceprof.config import ProfilerConfig


_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]\s+(?:OK|FAIL)\s+")
_L2_PROGRESS_RE = re.compile(
    r"L2 replay progress: elapsed=([0-9.]+)s dispatched=(\d+)/(\d+) "
    r"completed=(\d+) pending=(\d+) "
    r"in_flight\(store=(\d+) lookup=(\d+) load=(\d+)\) "
    r"bytes_submitted=(\d+)"
)


def _progress_from_log_line(line: str) -> tuple[int, int] | None:
    match = _PROGRESS_RE.search(line)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _l2_progress_from_log_line(
    line: str,
) -> tuple[float, int, int, int, int, int, int, int, int] | None:
    match = _L2_PROGRESS_RE.search(line)
    if match is None:
        return None
    (
        elapsed,
        dispatched,
        total,
        completed,
        pending,
        stores,
        lookups,
        loads,
        raw_bytes,
    ) = match.groups()
    return (
        float(elapsed),
        int(dispatched),
        int(total),
        int(completed),
        int(pending),
        int(stores),
        int(lookups),
        int(loads),
        int(raw_bytes),
    )


def build_command(config: ReplayerConfig, trace_path: str) -> list[str]:
    config.validate()
    return [
        config.lmcache_binary,
        "trace",
        "replay",
        trace_path,
        "--speedup",
        str(config.speedup),
        "--trace-percent",
        str(config.trace_percent),
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


def build_prepare_command(config: ReplayerConfig, trace_path: str) -> list[str]:
    """Build the L2 target preparation command for one replay case."""
    return [*build_command(config, trace_path), "--prepare-l2", "--prepare-only"]


def _read_trace_level(trace_path: Path) -> str:
    """Read the trace header level through the installed LMCache runtime."""
    from lmcache.v1.mp_observability.trace.reader import TraceReader

    with TraceReader(str(trace_path)) as reader:
        return reader.header.level


def _run_prepare(config: ReplayerConfig, trace: Path, output_dir: Path) -> int:
    log_path = output_dir / "lmcache-prepare.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        return subprocess.run(
            build_prepare_command(config, str(trace)),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        ).returncode


def run_command(
    config: ReplayerConfig,
    trace_path: str,
    *,
    profiler_config: ProfilerConfig | None = None,
) -> int:
    """Execute replay, persist LMCache logs, and return its exit code."""
    trace = Path(trace_path).expanduser()
    if not trace.is_file():
        raise FileNotFoundError(f"trace file not found: {trace}")
    output_dir = Path(config.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "lmcache-replay.log"
    trace_level = _read_trace_level(trace)
    if trace_level == "l2":
        print("[INFO] Preparing L2 replay target", flush=True)
        prepare_code = _run_prepare(config, trace, output_dir)
        if prepare_code != 0:
            print(
                "[ERROR] L2 prepare failed with exit code "
                f"{prepare_code}. Log: {output_dir / 'lmcache-prepare.log'}"
            )
            return prepare_code
        print(
            f"[INFO] L2 prepare complete. Log: {output_dir / 'lmcache-prepare.log'}",
            flush=True,
        )
    started_at = time.monotonic()
    last_update_at = 0.0
    progress_seen = False
    profiler = None
    profiler_started = False
    profiler_error: BaseException | None = None

    if profiler_config is not None:
        from traceprof.controller import RemoteProfiler

        profiler = RemoteProfiler(profiler_config, output_dir=output_dir)

    return_code: int | None = None
    try:
        if profiler is not None:
            profiler.preflight()
            profiler.start()
            profiler_started = True

        if trace_level == "l2":
            print(
                f"[INFO] Starting L2 replay. LMCache log: {log_path}",
                flush=True,
            )

        with (
            log_path.open("w", encoding="utf-8") as log_file,
            subprocess.Popen(
                build_command(config, str(trace)),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            ) as process,
        ):
            assert process.stdout is not None
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()
                if trace_level == "l2":
                    l2_progress = _l2_progress_from_log_line(line)
                    if l2_progress is None:
                        continue
                    (
                        elapsed,
                        dispatched,
                        total,
                        completed,
                        pending,
                        stores,
                        lookups,
                        loads,
                        submitted_bytes,
                    ) = l2_progress
                    percent = 100.0 * dispatched / total if total else 100.0
                    in_flight = stores + lookups + loads
                    print(
                        f"\r[progress] L2 submitted={dispatched}/{total} "
                        f"({percent:.1f}%) completed={completed} "
                        f"pending={pending} in_flight={in_flight} "
                        f"submitted_GiB={submitted_bytes / 1024**3:.2f} "
                        f"elapsed={elapsed:.1f}s",
                        end="",
                        flush=True,
                    )
                    progress_seen = True
                    continue
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
    finally:
        if profiler is not None:
            try:
                if profiler_started:
                    profiler.stop()
                    profiler.collect()
                    profiler.aggregate()
                    profiler.cleanup()
                    print(
                        "[INFO] Profile summary: "
                        f"{output_dir / 'profile_summary.json'}",
                        flush=True,
                    )
            except BaseException as exc:  # noqa: BLE001
                profiler_error = exc
                print(f"[ERROR] Profiler finalization failed: {exc}", flush=True)

    if progress_seen:
        print()
    if profiler_error is not None:
        print(
            f"[ERROR] Replay exit code {return_code}; profiler finalization failed. "
            f"LMCache log: {log_path}"
        )
    elif return_code == 0:
        print(f"[INFO] Replay complete. LMCache log: {log_path}")
    else:
        print(
            f"[ERROR] Replay failed with exit code {return_code}. "
            f"LMCache log: {log_path}"
        )
    if return_code is None:
        return 1
    if profiler_error is not None and return_code == 0:
        return 1
    return return_code
