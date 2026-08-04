"""Live vLLM + LMCache MP trace-recording lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import RecorderConfig
from .launcher import build_commands
from .mooncake import (
    MooncakePlan,
    build_mooncake_benchmark_command,
    load_mooncake_result,
    prepare_mooncake_workload,
    write_mooncake_request_stats,
)
from .process import (
    ManagedProcess,
    start_process,
    stop_process,
    wait_for_http,
    wait_for_tcp,
)
from .workload import WorkloadPlan, load_workload


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _status(level: str, message: str) -> None:
    print(f"[{level}] {message}", flush=True)


def reset_l2_storage(
    base_path: str | Path,
    *,
    protected_paths: tuple[Path, ...] = (),
) -> Path:
    """Remove and recreate an explicitly configured L2 storage directory."""
    requested = Path(base_path).expanduser()
    if not requested.is_absolute():
        raise ValueError("LMCache L2 reset requires an absolute base_path")

    for candidate in (requested, *requested.parents):
        if candidate.exists() and candidate.is_symlink():
            raise ValueError(
                f"LMCache L2 reset refuses paths containing a symlink: {candidate}"
            )

    target = requested.resolve(strict=False)
    protected = (Path.cwd().resolve(), Path.home().resolve(), *protected_paths)
    if target == Path("/") or len(target.parts) < 4:
        raise ValueError(f"LMCache L2 reset target is too broad: {target}")
    for path in protected:
        resolved = path.resolve(strict=False)
        if target == resolved or target in resolved.parents:
            raise ValueError(
                f"LMCache L2 reset target contains a protected path: {target}"
            )

    if target.exists():
        if not target.is_dir():
            raise ValueError(f"LMCache L2 base_path is not a directory: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=False)
    return target


def _sender_classes(root: str | Path):
    """Import TensorMesh's V3 sender without copying its implementation."""
    import sys

    src = str(Path(root).expanduser().resolve() / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from streaming_utils import build_base_url
        from v3_request_sender import V3RequestSender
    except ImportError as exc:
        raise RuntimeError(
            "Tensormesh-Benchmark V3 runtime dependencies are unavailable; "
            "install third_party/Tensormesh-Benchmark/src/requirements.txt"
        ) from exc
    return build_base_url, V3RequestSender


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _run_v3_replay(
    config: RecorderConfig,
    workload: WorkloadPlan,
    *,
    stats_path: Path,
    sessions_path: Path,
    log_path: Path,
) -> tuple[list[Any], list[Any]]:
    build_base_url, sender_type = _sender_classes(config.workload.tensormesh_root)
    if workload.v3_config is None:
        raise ValueError("workload plan is missing its V3Config")

    stats_stream = stats_path.open("w", encoding="utf-8", buffering=1)
    sessions_stream = sessions_path.open("w", encoding="utf-8", buffering=1)
    log_stream = log_path.open("w", encoding="utf-8", buffering=1)
    progress = {
        "sessions": 0,
        "turns": 0,
        "successful": 0,
        "failed": 0,
    }
    total_sessions = len(workload.sessions)

    def write_stat(stat: Any) -> None:
        progress["turns"] += 1
        if bool(stat.successful):
            progress["successful"] += 1
        else:
            progress["failed"] += 1
        stats_stream.write(
            json.dumps(asdict(stat), sort_keys=True, default=str) + "\n"
        )

    def write_session(outcome: Any) -> None:
        progress["sessions"] += 1
        sessions_stream.write(
            json.dumps(asdict(outcome), sort_keys=True, default=str) + "\n"
        )

    def write_log(line: str) -> None:
        log_stream.write(line + "\n")

    async def print_progress(started: float) -> None:
        interval = config.workload.progress_interval_seconds
        while True:
            await asyncio.sleep(interval)
            elapsed = time.monotonic() - started
            completed = progress["sessions"]
            percent = 100.0 * completed / total_sessions if total_sessions else 100.0
            print(
                f"\r[progress] sessions={completed}/{total_sessions} "
                f"({percent:.1f}%) turns={progress['turns']} "
                f"success={progress['successful']} failed={progress['failed']} "
                f"elapsed={_format_duration(elapsed)}",
                end="",
                flush=True,
            )

    async def run_replay(sender: Any) -> tuple[list[Any], list[Any]]:
        started = time.monotonic()
        progress_task = asyncio.create_task(print_progress(started))
        try:
            result = await sender.run_replay(
                workload.sessions,
                workload.v3_config,
                stats_callback=write_stat,
                log_callback=write_log,
                session_done_callback=write_session,
            )
        finally:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
            print(flush=True)

        elapsed = time.monotonic() - started
        print(
            f"[progress] sessions={progress['sessions']}/{total_sessions} "
            f"(100.0%) turns={progress['turns']} "
            f"success={progress['successful']} failed={progress['failed']} "
            f"elapsed={_format_duration(elapsed)}",
            flush=True,
        )
        return result

    try:
        sender = sender_type(
            base_url=build_base_url(
                host=config.runtime.vllm_host,
                port=config.runtime.vllm_port,
            ),
            model=config.model.id,
            verbose=True,
        )
        return asyncio.run(run_replay(sender))
    finally:
        stats_stream.close()
        sessions_stream.close()
        log_stream.close()


def run_live(
    config: RecorderConfig,
    *,
    output_dir: str | Path | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run V3 against vLLM while LMCache records an MP storage trace.

    The function always stops both child process groups and writes a manifest,
    including when the workload or server fails. It raises the original error
    after cleanup so a non-zero CLI exit still signals an incomplete run.
    """
    config.validate()
    configured_output = Path(config.output.root)
    if config.output.run_id:
        configured_output /= config.output.run_id
    run_dir = Path(output_dir or configured_output).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "storage.lct"
    commands = build_commands(config, trace_path=str(trace_path))
    mooncake_plan: MooncakePlan | None = None
    workload_command: list[str] | None = None
    if config.workload.backend == "mooncake":
        if verbose:
            _status("INFO", "Preparing Mooncake FAST'25 workload")
        mooncake_plan = prepare_mooncake_workload(config.workload.mooncake)
        if (
            config.model.max_model_len is not None
            and mooncake_plan.max_total_tokens > config.model.max_model_len
        ):
            raise ValueError(
                "Mooncake request exceeds model.max_model_len: "
                f"{mooncake_plan.max_total_tokens} > {config.model.max_model_len}"
            )
        workload_command = build_mooncake_benchmark_command(
            config,
            mooncake_plan,
            result_path=run_dir / "vllm_benchmark.json",
        )
    _write_json(
        run_dir / "commands.json",
        {
            "lmcache": commands.lmcache,
            "vllm": commands.vllm,
            "workload": workload_command,
            "env": commands.env,
        },
    )
    if verbose:
        _status("INFO", f"Run output directory: {run_dir}")

    env = os.environ.copy()
    env.update(commands.env)
    env["PYTHONUNBUFFERED"] = "1"
    lmcache_process: ManagedProcess | None = None
    vllm_process: ManagedProcess | None = None
    workload_process: ManagedProcess | None = None
    workload: WorkloadPlan | None = None
    stats: list[Any] = []
    outcomes: list[Any] = []
    request_summary = {"turns": 0, "successful": 0, "failed": 0}
    error_message: str | None = None
    return_codes: dict[str, int | None] = {}

    try:
        if config.lmcache.l2.reset_on_start:
            if verbose:
                _status(
                    "INFO",
                    f"Resetting LMCache L2 storage: {config.lmcache.l2.base_path}",
                )
            reset_path = reset_l2_storage(
                config.lmcache.l2.base_path,
                protected_paths=(run_dir,),
            )
            if verbose:
                _status("INFO", f"LMCache L2 storage is ready: {reset_path}")

        if verbose:
            _status("INFO", "Starting LMCache MP server")
        lmcache_process = start_process(
            "lmcache-mp",
            commands.lmcache,
            env=env,
            log_path=run_dir / "lmcache.log",
        )
        wait_for_tcp(
            lmcache_process,
            config.lmcache.host,
            config.lmcache.port,
            timeout_seconds=config.runtime.startup_timeout_seconds,
        )
        if verbose:
            _status(
                "INFO",
                f"LMCache MP server is ready at {config.lmcache.host}:{config.lmcache.port}",
            )

        if verbose:
            _status(
                "INFO",
                "Starting vLLM; model download, loading, and compilation can take time",
            )
        vllm_process = start_process(
            "vllm",
            commands.vllm,
            env=env,
            log_path=run_dir / "vllm.log",
        )
        wait_for_http(
            vllm_process,
            f"http://{config.runtime.vllm_host}:{config.runtime.vllm_port}/health",
            timeout_seconds=config.runtime.startup_timeout_seconds,
        )
        if verbose:
            _status("INFO", "vLLM API server is ready")

        if config.workload.backend == "tensormesh":
            if verbose:
                _status("INFO", "Loading Tensormesh V3 workload")
            workload = load_workload(config.workload, verbose=verbose)
            _write_json(
                run_dir / "workload.json",
                {
                    "backend": "tensormesh",
                    "source_counts": workload.source_counts,
                    "session_ids": workload.session_ids,
                    "num_sessions": len(workload.sessions),
                },
            )
            if verbose:
                _status(
                    "INFO",
                    "Workload loaded: "
                    f"sessions={len(workload.sessions)} "
                    f"sources={workload.source_counts}",
                )
                _status("INFO", "Replaying workload")
            stats, outcomes = _run_v3_replay(
                config,
                workload,
                stats_path=run_dir / "request_stats.jsonl",
                sessions_path=run_dir / "session_outcomes.jsonl",
                log_path=run_dir / "workload.log",
            )
            request_summary = {
                "turns": len(stats),
                "successful": sum(bool(item.successful) for item in stats),
                "failed": sum(not bool(item.successful) for item in stats),
            }
        else:
            assert mooncake_plan is not None
            assert workload_command is not None
            _write_json(
                run_dir / "workload.json",
                {"backend": "mooncake", **asdict(mooncake_plan)},
            )
            result_path = run_dir / "vllm_benchmark.json"
            if verbose:
                duration_seconds = (
                    mooncake_plan.last_timestamp_ms
                    - mooncake_plan.first_timestamp_ms
                ) * 0.001 * config.workload.mooncake.time_scale
                _status(
                    "INFO",
                    "Mooncake workload ready: "
                    f"trace={mooncake_plan.trace} "
                    f"requests={mooncake_plan.selected_requests} "
                    f"scheduled_duration={_format_duration(duration_seconds)}",
                )
                _status("INFO", "Replaying Mooncake timed trace with vLLM bench")
            workload_process = start_process(
                "vllm-bench",
                workload_command,
                env=env,
                log_path=run_dir / "workload.log",
            )
            workload_return_code = workload_process.process.wait()
            return_codes["workload"] = workload_return_code
            workload_process.close_log()
            workload_process = None
            if workload_return_code != 0:
                raise RuntimeError(
                    "vLLM Mooncake benchmark exited with code "
                    f"{workload_return_code}; see {run_dir / 'workload.log'}"
                )
            mooncake_result = load_mooncake_result(result_path)
            write_mooncake_request_stats(
                mooncake_result,
                run_dir / "request_stats.jsonl",
            )
            (run_dir / "session_outcomes.jsonl").write_text("", encoding="utf-8")
            request_summary = {
                "turns": int(mooncake_result["num_prompts"]),
                "successful": int(mooncake_result["completed"]),
                "failed": int(mooncake_result["failed"]),
            }
    except BaseException as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        if verbose:
            _status("ERROR", error_message)
        raise
    finally:
        if workload_process is not None:
            if verbose:
                _status("INFO", "Stopping workload client")
            return_codes["workload"] = stop_process(
                workload_process,
                timeout_seconds=config.runtime.process_stop_timeout_seconds,
            )
        # Stop the model first so no more storage operations arrive while the
        # MP server is flushing its recorder.
        if vllm_process is not None:
            if verbose:
                _status("INFO", "Stopping vLLM API server")
            return_codes["vllm"] = stop_process(
                vllm_process,
                timeout_seconds=config.runtime.process_stop_timeout_seconds,
            )
        if lmcache_process is not None:
            if verbose:
                _status("INFO", "Stopping LMCache MP server and flushing trace")
            return_codes["lmcache"] = stop_process(
                lmcache_process,
                timeout_seconds=config.runtime.process_stop_timeout_seconds,
            )

        if workload is not None:
            workload_manifest = {
                "backend": "tensormesh",
                "num_sessions": len(workload.sessions),
                "source_counts": workload.source_counts,
            }
        elif mooncake_plan is not None:
            workload_manifest = {
                "backend": "mooncake",
                "trace": mooncake_plan.trace,
                "trace_path": str(mooncake_plan.path),
                "num_requests": mooncake_plan.selected_requests,
                "source_counts": mooncake_plan.source_counts,
            }
        else:
            workload_manifest = {
                "backend": config.workload.backend,
                "num_sessions": 0,
                "source_counts": {},
            }

        manifest = {
            "trace_path": str(trace_path),
            "trace_exists": trace_path.exists(),
            "trace_bytes": trace_path.stat().st_size if trace_path.exists() else 0,
            "workload": workload_manifest,
            "request_stats": request_summary,
            "session_outcomes": len(outcomes),
            "process_return_codes": return_codes,
            "error": error_message,
        }
        _write_json(run_dir / "manifest.json", manifest)

    if verbose:
        _status("INFO", f"Trace output: {trace_path}")
    return manifest
