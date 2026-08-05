"""Download and validate a Mooncake FAST'25 workload trace."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .config import MooncakeWorkloadConfig
from .mooncake import prepare_mooncake_workload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace",
        default="all",
        choices=("all", "conversation", "toolagent"),
        help="official Mooncake trace to download (default: all)",
    )
    parser.add_argument(
        "--path",
        required=True,
        help="directory where <trace>_trace.jsonl files are stored",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="override the official source URL (requires one trace)",
    )
    parser.add_argument(
        "--num-requests",
        type=int,
        default=None,
        help="select this many requests while validating (default: all)",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="require an existing local file instead of downloading it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.url and args.trace == "all":
        _parser().error("--url requires --trace conversation or --trace toolagent")

    output_dir = Path(args.path).expanduser()
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"Mooncake trace path is not a directory: {output_dir}")

    traces = ("conversation", "toolagent") if args.trace == "all" else (args.trace,)
    summaries = []
    for trace in traces:
        config = MooncakeWorkloadConfig(
            trace=trace,
            path=str(output_dir / f"{trace}_trace.jsonl"),
            url=args.url,
            download_if_missing=not args.no_download,
            num_requests=args.num_requests,
        )
        plan = prepare_mooncake_workload(config)
        summary = asdict(plan)
        summary["path"] = str(Path(plan.path))
        summaries.append(summary)
    print(json.dumps({"traces": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
