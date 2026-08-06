"""CLI for planning and running vLLM + LMCache storage trace recording."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .config import load_config
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="recorder YAML config")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--base-path",
        help="override the fs_native L2 base path from the recorder config",
    )
    parser.add_argument(
        "--mooncake-trace",
        choices=("conversation", "toolagent"),
        help="Mooncake trace to record; requires --mooncake-path",
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
    if bool(args.mooncake_trace) != bool(args.mooncake_path):
        parser.error("--mooncake-trace and --mooncake-path must be used together")
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
    if args.base_path:
        config = replace(
            config,
            lmcache=replace(
                config.lmcache,
                l2=replace(config.lmcache.l2, base_path=args.base_path),
            ),
        )
    if config.workload.backend == "mooncake":
        l2 = config.lmcache.l2
        base_path = l2.base_path.replace(
            "{trace}", config.workload.mooncake.trace
        )
        config = replace(
            config,
            lmcache=replace(config.lmcache, l2=replace(l2, base_path=base_path)),
        )
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
