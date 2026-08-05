"""CLI for one-shot LMCache storage trace replay."""

from __future__ import annotations

import argparse

from .config import load_config
from .runner import build_command, run_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, help="path to a .lct trace")
    parser.add_argument("--config", required=True, help="replayer YAML config")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    command = build_command(config, args.trace)
    print(" ".join(command))
    if not args.dry_run:
        return run_command(config, args.trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
