# SPDX-License-Identifier: Apache-2.0

"""Build the one-shot ``lmcache trace replay`` command."""

from __future__ import annotations

import csv
import json
import re
import subprocess
import time
from datetime import datetime, timezone
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
_L2_IO_INTERVAL_RE = re.compile(
    r"FS native I/O interval: elapsed=([0-9.]+)s interval=([0-9.]+)s "
    r"total_ops=(\d+) total_bytes=(\d+) total_GiB/s=([0-9.]+) "
    r"read_ops=(\d+) read_bytes=(\d+) read_GiB/s=([0-9.]+) "
    r"write_ops=(\d+) write_bytes=(\d+) write_GiB/s=([0-9.]+)"
)
_L2_IO_INTERVAL_COLUMNS = (
    "elapsed_seconds",
    "total_ops",
    "total_gb_bytes",
    "total_gb_per_second",
    "read_ops",
    "read_gb_bytes",
    "read_gb_per_second",
    "write_ops",
    "write_gb_bytes",
    "write_gb_per_second",
)
_GB_BYTES = 1_000_000_000
_GIB_TO_GB = 1024**3 / _GB_BYTES
_L2_USAGE_TIMEOUT_SECONDS = 300


def _l2_namespace_path(config: ReplayerConfig) -> Path | None:
    """Return the client-visible directory used by a filesystem L2 adapter."""
    adapter = config.l2_adapter
    adapter_type = adapter.get("type")
    raw_path: object | None = None
    if adapter_type in {"fs", "fs_native"}:
        raw_path = adapter.get("base_path")
    elif adapter_type in {"nixl_store", "nixl_store_dynamic"}:
        backend_params = adapter.get("backend_params")
        if isinstance(backend_params, dict):
            raw_path = backend_params.get("file_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    return Path(raw_path).expanduser()


def _measure_l2_namespace_usage(path: Path | None) -> dict[str, object]:
    """Measure apparent bytes in a client-visible L2 namespace with GNU du."""
    measured_at = datetime.now(timezone.utc).isoformat()
    if path is None:
        return {
            "bytes": None,
            "measurement_status": "unsupported_adapter",
            "measured_at_utc": measured_at,
        }
    if not path.is_dir():
        return {
            "bytes": None,
            "measurement_status": "missing",
            "measured_at_utc": measured_at,
        }
    try:
        result = subprocess.run(
            ["du", "-sb", "--", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=_L2_USAGE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "bytes": None,
            "measurement_status": "measurement_failed",
            "error": str(exc),
            "measured_at_utc": measured_at,
        }
    if result.returncode != 0:
        return {
            "bytes": None,
            "measurement_status": "measurement_failed",
            "error": result.stderr.strip() or f"du exited with {result.returncode}",
            "measured_at_utc": measured_at,
        }
    try:
        bytes_used = int(result.stdout.split(maxsplit=1)[0])
    except (IndexError, ValueError):
        return {
            "bytes": None,
            "measurement_status": "measurement_failed",
            "error": f"unexpected du output: {result.stdout.strip()!r}",
            "measured_at_utc": measured_at,
        }
    return {
        "bytes": bytes_used,
        "gb": round(bytes_used / _GB_BYTES, 3),
        "gib": round(bytes_used / (1024**3), 3),
        "measurement_status": "ok",
        "measured_at_utc": measured_at,
    }


def _record_l2_namespace_usage(
    output_dir: Path,
    payload: dict[str, object],
    stage: str,
    *,
    command_exit_code: int | None,
) -> None:
    path_value = payload.get("path")
    namespace_path = Path(path_value) if isinstance(path_value, str) else None
    snapshot = _measure_l2_namespace_usage(namespace_path)
    snapshot["command_exit_code"] = command_exit_code
    payload[stage] = snapshot
    usage_path = output_dir / "l2_usage.json"
    temporary_path = usage_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(usage_path)
    if snapshot["measurement_status"] == "ok":
        print(
            f"[INFO] L2 namespace usage {stage}: "
            f"{snapshot['gb']} GB ({snapshot['bytes']} bytes). "
            f"Artifact: {usage_path}",
            flush=True,
        )
    else:
        print(
            f"[WARN] L2 namespace usage {stage}: "
            f"{snapshot['measurement_status']}. Artifact: {usage_path}",
            flush=True,
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


def _l2_io_interval_from_log_line(
    line: str,
) -> tuple[float, float, int, int, float, int, int, float, int, int, float] | None:
    match = _L2_IO_INTERVAL_RE.search(line)
    if match is None:
        return None
    (
        elapsed,
        interval,
        total_ops,
        total_bytes,
        total_gib_per_second,
        read_ops,
        read_bytes,
        read_gib_per_second,
        write_ops,
        write_bytes,
        write_gib_per_second,
    ) = match.groups()
    return (
        float(elapsed),
        float(interval),
        int(total_ops),
        int(total_bytes),
        float(total_gib_per_second),
        int(read_ops),
        int(read_bytes),
        float(read_gib_per_second),
        int(write_ops),
        int(write_bytes),
        float(write_gib_per_second),
    )


def _initialize_l2_io_interval_tsv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerow(
            _L2_IO_INTERVAL_COLUMNS
        )


def _append_l2_io_interval(
    path: Path,
    interval: tuple[float, float, int, int, float, int, int, float, int, int, float],
) -> None:
    (
        elapsed,
        _interval_seconds,
        total_ops,
        total_bytes,
        total_gib_per_second,
        read_ops,
        read_bytes,
        read_gib_per_second,
        write_ops,
        write_bytes,
        write_gib_per_second,
    ) = interval
    row = (
        round(elapsed),
        total_ops,
        f"{total_bytes / _GB_BYTES:.3f}",
        f"{total_gib_per_second * _GIB_TO_GB:.3f}",
        read_ops,
        f"{read_bytes / _GB_BYTES:.3f}",
        f"{read_gib_per_second * _GIB_TO_GB:.3f}",
        write_ops,
        f"{write_bytes / _GB_BYTES:.3f}",
        f"{write_gib_per_second * _GIB_TO_GB:.3f}",
    )
    with path.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerow(row)


def _render_progress(message: str, previous_width: int) -> int:
    width = max(len(message), previous_width)
    print(f"\r{message:<{width}}", end="", flush=True)
    return width


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
    l2_usage: dict[str, object] | None = None
    io_interval_path = (
        output_dir / "l2_io_interval.tsv" if trace_level == "l2" else None
    )
    if trace_level == "l2":
        namespace_path = _l2_namespace_path(config)
        l2_usage = {
            "schema_version": 1,
            "scope": "client_visible_namespace",
            "adapter_type": config.l2_adapter.get("type"),
            "path": str(namespace_path) if namespace_path is not None else None,
            "measurement_method": "du -sb",
        }
        print("[INFO] Preparing L2 replay target", flush=True)
        prepare_code = _run_prepare(config, trace, output_dir)
        _record_l2_namespace_usage(
            output_dir,
            l2_usage,
            "after_prepare",
            command_exit_code=prepare_code,
        )
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
    progress_width = 0
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
            assert io_interval_path is not None
            _initialize_l2_io_interval_tsv(io_interval_path)
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
                if trace_level == "l2":
                    io_interval = _l2_io_interval_from_log_line(line)
                    if io_interval is not None:
                        assert io_interval_path is not None
                        _append_l2_io_interval(io_interval_path, io_interval)
                        continue
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
                        _submitted_bytes,
                    ) = l2_progress
                    percent = 100.0 * dispatched / total if total else 100.0
                    in_flight = stores + lookups + loads
                    progress_width = _render_progress(
                        f"[progress] L2 submitted={dispatched}/{total} "
                        f"({percent:.1f}%) completed={completed} "
                        f"pending={pending} in_flight={in_flight} "
                        f"elapsed={elapsed:.1f}s",
                        progress_width,
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
                progress_width = _render_progress(
                    f"[progress] records={completed}/{total} "
                    f"({percent:.1f}%) elapsed={elapsed:.1f}s",
                    progress_width,
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
    if trace_level == "l2":
        assert l2_usage is not None
        _record_l2_namespace_usage(
            output_dir,
            l2_usage,
            "after_replay",
            command_exit_code=return_code,
        )
    if return_code == 0 and trace_level == "l2":
        stats_path = output_dir / "l2_replay_stats.json"
        if stats_path.is_file():
            from .report import write_l2_summary

            try:
                summary_path = write_l2_summary(stats_path)
                print(f"[INFO] L2 replay summary: {summary_path}", flush=True)
            except (OSError, TypeError, ValueError) as exc:
                print(f"[WARN] Failed to write L2 replay summary: {exc}", flush=True)
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
