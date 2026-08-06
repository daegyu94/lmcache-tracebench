"""CLI for planning and running vLLM + LMCache storage trace recording."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

from .config import RecorderConfig, load_config
from .launcher import build_commands


def _mooncake_num_requests(value: str) -> int | None:
    """Parse a Mooncake request limit, with ``all`` meaning the full trace."""
    if value == "all":
        return None
    try:
        num_requests = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be a positive integer or 'all'"
        ) from exc
    if num_requests <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer or 'all'")
    return num_requests


def _positive_float(value: str) -> float:
    """Parse a finite positive floating-point CLI value."""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def apply_speedup(config: RecorderConfig, speedup: float) -> RecorderConfig:
    """Apply a workload speedup using the backend's timing control.

    Mooncake scales the timed-trace request interval, while Tensormesh scales
    recorded inter-turn gaps.  Tensormesh's max-pressure mode has no gap to
    scale and therefore cannot represent a separate speedup point.
    """
    if not math.isfinite(speedup) or speedup <= 0:
        raise ValueError("speedup must be a finite positive number")

    workload = config.workload
    if workload.backend == "mooncake":
        mooncake = replace(
            workload.mooncake,
            time_scale=1.0 / speedup,
        )
        return replace(config, workload=replace(workload, mooncake=mooncake))

    if workload.timing_mode != "respect-gaps":
        raise ValueError(
            "speedup requires workload.timing_mode: respect-gaps for Tensormesh"
        )
    return replace(
        config,
        workload=replace(workload, pre_gap_scale=1.0 / speedup),
    )


def resolve_mountpoint(
    config: RecorderConfig,
    mountpoint: str,
    *,
    l2_path: str | None = None,
) -> RecorderConfig:
    """Resolve relative storage and dataset paths below a mountpoint."""
    root = Path(mountpoint).expanduser()
    if not root.is_absolute():
        raise ValueError("mountpoint must be an absolute path")
    root = root.resolve(strict=False)
    trace = config.workload.mooncake.trace

    def resolve(value: str) -> str:
        value = value.replace("{trace}", trace)
        path = Path(value).expanduser()
        if path.is_absolute():
            return str(path.resolve(strict=False))
        resolved = (root / path).resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"relative path escapes mountpoint: {value}")
        return str(resolved)

    l2 = config.lmcache.l2
    mooncake = config.workload.mooncake
    workload = config.workload
    if l2_path is not None and not l2_path:
        raise ValueError("l2_path must not be empty")
    configured_l2_path = l2_path if l2_path is not None else l2.subpath
    resolved_l2 = replace(l2, resolved_path=resolve(configured_l2_path))
    resolved_mooncake = replace(mooncake, path=resolve(mooncake.path))
    if workload.hf_cache_dir is not None:
        workload = replace(
            workload,
            hf_cache_dir=resolve(workload.hf_cache_dir),
        )
    workload = replace(workload, mooncake=resolved_mooncake)
    return replace(
        config,
        lmcache=replace(config.lmcache, l2=resolved_l2),
        workload=workload,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="recorder YAML config")
    parser.add_argument(
        "--mountpoint",
        required=True,
        help="absolute storage mountpoint for relative config paths",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--l2-path",
        help=(
            "optional fs_native L2 path override; relative paths are rooted "
            "at --mountpoint"
        ),
    )
    parser.add_argument(
        "--mooncake-trace",
        choices=("conversation", "toolagent"),
        help="Mooncake trace to record; uses the config path unless overridden",
    )
    parser.add_argument(
        "--mooncake-path",
        help="local Mooncake JSONL path; requires --mooncake-trace",
    )
    parser.add_argument(
        "--mooncake-num-requests",
        type=_mooncake_num_requests,
        metavar="N|all",
        default=argparse.SUPPRESS,
        help="override the Mooncake request prefix; 'all' replays the full trace",
    )
    parser.add_argument(
        "--speedup",
        type=_positive_float,
        help=(
            "workload speedup relative to the source timeline; Mooncake uses "
            "time_scale=1/speedup and Tensormesh scales respect-gaps"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--load-workload",
        action="store_true",
        help="load the Tensormesh dataset while creating the dry-run plan",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.mooncake_path and not args.mooncake_trace:
        parser.error("--mooncake-path requires --mooncake-trace")
    mooncake_num_requests = getattr(args, "mooncake_num_requests", None)
    has_mooncake_num_requests = hasattr(args, "mooncake_num_requests")
    if args.mooncake_trace or has_mooncake_num_requests:
        if config.workload.backend != "mooncake":
            parser.error("Mooncake options require workload.backend: mooncake")
        mooncake = replace(
            config.workload.mooncake,
            trace=args.mooncake_trace or config.workload.mooncake.trace,
            path=args.mooncake_path or config.workload.mooncake.path,
            num_requests=(
                mooncake_num_requests
                if has_mooncake_num_requests
                else config.workload.mooncake.num_requests
            ),
        )
        config = replace(
            config,
            workload=replace(config.workload, mooncake=mooncake),
        )
    if args.speedup is not None:
        try:
            config = apply_speedup(config, args.speedup)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        config = resolve_mountpoint(
            config,
            args.mountpoint,
            l2_path=args.l2_path,
        )
    except ValueError as exc:
        parser.error(str(exc))
    config.validate()
    configured_output = Path(config.output.root)
    if config.output.run_id:
        configured_output /= config.output.run_id
    output_dir = Path(args.output_dir or configured_output).expanduser()
    trace_path = output_dir / "storage.lct"
    command_plan = build_commands(config, trace_path=str(trace_path))

    print("LMCache command:")
    print(" ".join(command_plan.lmcache))
    print("vLLM command:")
    print(" ".join(command_plan.vllm))
    print("Environment:")
    print(json.dumps(command_plan.env, sort_keys=True))
    if config.workload.backend == "mooncake":
        workload_summary = {
            "backend": "mooncake",
            "trace": config.workload.mooncake.trace,
            "path": config.workload.mooncake.path,
            "num_requests": config.workload.mooncake.num_requests,
            "time_scale": config.workload.mooncake.time_scale,
        }
    else:
        workload_summary = {
            "backend": "tensormesh",
            "source": config.workload.source,
            "timing_mode": config.workload.timing_mode,
            "pre_gap_scale": config.workload.pre_gap_scale,
        }
    print("Workload:")
    print(json.dumps(workload_summary, sort_keys=True))

    if args.load_workload:
        if config.workload.backend != "tensormesh":
            raise ValueError(
                "--load-workload supports Tensormesh only; use "
                "python -m recorder.mooncake_cli for Mooncake download and validation"
            )
        from .workload import load_workload

        plan = load_workload(config.workload)
        workload_plan = {
            "backend": "tensormesh",
            "source_counts": plan.source_counts,
            "session_ids": plan.session_ids,
        }
        print("Workload plan:")
        print(json.dumps(workload_plan, indent=2, default=str))

    if not args.dry_run:
        from .live import run_live

        run_live(config, output_dir=output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
