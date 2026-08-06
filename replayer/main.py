"""CLI for one-shot LMCache storage trace replay."""

from __future__ import annotations

import argparse

from .config import apply_overrides, load_config
from .runner import build_command, run_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, help="path to a .lct trace")
    parser.add_argument("--config", required=True, help="replayer YAML config")
    parser.add_argument(
        "--l2-path",
        help="override the configured fs_native or NIXL L2 path",
    )
    parser.add_argument(
        "--output-dir",
        help="override the directory for replay output and logs",
    )
    parser.add_argument(
        "--profile",
        "--profile-config",
        dest="profile_config",
        help="profile storage nodes with the supplied YAML config",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    config = apply_overrides(
        config, l2_path=args.l2_path, output_dir=args.output_dir
    )
    profiler_config = None
    if args.profile_config:
        from traceprof.config import load_config as load_profile_config

        profiler_config = load_profile_config(args.profile_config)
    command = build_command(config, args.trace)
    print(" ".join(command))
    if profiler_config is not None:
        print(
            "Profiler: "
            f"nodes={len(profiler_config.nodes)} "
            f"sample={profiler_config.sample_interval_seconds}s "
            f"report={profiler_config.report_interval_seconds}s"
        )
    if not args.dry_run:
        return run_command(config, args.trace, profiler_config=profiler_config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
