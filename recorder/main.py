"""CLI for planning and running vLLM + LMCache storage trace recording."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .launcher import build_commands


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="recorder YAML config")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--load-workload",
        action="store_true",
        help="load the Tensormesh dataset while creating the dry-run plan",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)
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
        if config.workload.backend == "tensormesh":
            from .workload import load_workload

            plan = load_workload(config.workload)
            workload_plan = {
                "backend": "tensormesh",
                "source_counts": plan.source_counts,
                "session_ids": plan.session_ids,
            }
        else:
            from dataclasses import asdict

            from .mooncake import (
                build_mooncake_benchmark_command,
                prepare_mooncake_workload,
            )

            plan = prepare_mooncake_workload(config.workload.mooncake)
            workload_plan = {"backend": "mooncake", **asdict(plan)}
            workload_command = build_mooncake_benchmark_command(
                config,
                plan,
                result_path=output_dir / "vllm_benchmark.json",
            )
            print("Workload command:")
            print(" ".join(workload_command))
        print("Workload plan:")
        print(json.dumps(workload_plan, indent=2, default=str))

    if not args.dry_run:
        from .live import run_live

        run_live(config, output_dir=output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
