"""Run the same LMCache storage trace in parallel replay instances."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class InstancePlan:
    """Paths and identity for one replay instance."""

    instance_id: int
    l2_path: Path
    output_dir: Path


@dataclass
class RunningInstance:
    """A launched replay process and its launcher log."""

    plan: InstancePlan
    process: subprocess.Popen[str]
    log_file: TextIO


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_instance_plans(
    instances: int,
    *,
    l2_root: str | Path,
    output_root: str | Path,
) -> list[InstancePlan]:
    """Build isolated L2 and output paths for each replay instance."""
    if instances <= 0:
        raise ValueError("instances must be positive")
    resolved_l2_root = Path(l2_root).expanduser()
    if not resolved_l2_root.is_absolute():
        raise ValueError("l2_root must be an absolute path")
    resolved_l2_root = resolved_l2_root.resolve(strict=False)
    resolved_output_root = Path(output_root).expanduser()
    return [
        InstancePlan(
            instance_id=instance_id,
            l2_path=resolved_l2_root / f"instance-{instance_id}",
            output_dir=resolved_output_root / f"instance-{instance_id}",
        )
        for instance_id in range(instances)
    ]


def build_instance_command(
    trace: str | Path,
    config: str | Path,
    plan: InstancePlan,
    *,
    python_executable: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Build the single-instance command launched by the orchestrator."""
    command = [
        python_executable or sys.executable,
        "-m",
        "replayer.main",
        "--trace",
        str(trace),
        "--config",
        str(config),
        "--l2-path",
        str(plan.l2_path),
        "--output-dir",
        str(plan.output_dir),
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def _terminate_instances(running: list[RunningInstance]) -> None:
    for item in running:
        if item.process.poll() is None:
            item.process.terminate()
    active = list(running)
    deadline = time.monotonic() + 5.0
    while active and time.monotonic() < deadline:
        active = [item for item in active if item.process.poll() is None]
        if active:
            time.sleep(0.05)
    for item in active:
        if item.process.poll() is None:
            item.process.kill()
    for item in active:
        item.process.wait()


def _write_summary(
    output_root: Path,
    *,
    trace: Path,
    results: list[dict[str, object]],
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    summary_path = output_root / "instances-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "trace": str(trace),
                "instances": len(results),
                "results": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary_path


def _ensure_l2_path_available(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"L2 case path is a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"L2 case path is not a directory: {path}")
    if path == Path("/"):
        raise ValueError("L2 case path must not be the filesystem root")
    if path.is_dir():
        try:
            next(path.iterdir())
        except StopIteration:
            return
        raise ValueError(f"L2 case path is not empty: {path}")


def _reset_l2_path(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"L2 case path is a symlink; refusing to reset it: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"L2 case path is not a directory: {path}")
    if path == Path("/"):
        raise ValueError("L2 case path must not be the filesystem root")
    if path.is_dir():
        print(f"[INFO] Resetting L2 case path: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def run_instances(
    *,
    trace: str | Path,
    config: str | Path,
    plans: list[InstancePlan],
    output_root: str | Path,
    dry_run: bool = False,
    keep_l2: bool = False,
) -> int:
    """Run all plans concurrently and return a process-style exit code."""
    trace_path = Path(trace).expanduser()
    config_path = Path(config).expanduser()
    if not dry_run:
        if not trace_path.is_file():
            raise FileNotFoundError(f"trace file not found: {trace_path}")
        if not config_path.is_file():
            raise FileNotFoundError(f"config file not found: {config_path}")

    commands = [
        build_instance_command(trace_path, config_path, plan, dry_run=dry_run)
        for plan in plans
    ]
    if dry_run:
        for plan, command in zip(plans, commands, strict=True):
            print(f"Instance {plan.instance_id}: {shlex.join(command)}")
        return 0
    for plan in plans:
        if keep_l2:
            _ensure_l2_path_available(plan.l2_path)
        else:
            _reset_l2_path(plan.l2_path)

    output_root_path = Path(output_root).expanduser()
    running: list[RunningInstance] = []
    launched: list[RunningInstance] = []
    results: dict[int, int] = {}
    try:
        for plan, command in zip(plans, commands, strict=True):
            plan.l2_path.mkdir(parents=True, exist_ok=True)
            plan.output_dir.mkdir(parents=True, exist_ok=True)
            log_file = (plan.output_dir / "launcher.log").open(
                "w", encoding="utf-8"
            )
            try:
                process = subprocess.Popen(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except BaseException:
                log_file.close()
                raise
            item = RunningInstance(plan, process, log_file)
            running.append(item)
            launched.append(item)
            print(
                f"[INFO] Started replay instance {plan.instance_id}: "
                f"{plan.output_dir}"
            )

        while running:
            finished: list[RunningInstance] = []
            for item in running:
                return_code = item.process.poll()
                if return_code is not None:
                    results[item.plan.instance_id] = return_code
                    finished.append(item)
            for item in finished:
                running.remove(item)
            if any(results[item.plan.instance_id] != 0 for item in finished):
                print("[ERROR] A replay instance failed; stopping remaining instances.")
                _terminate_instances(running)
                for item in running:
                    results[item.plan.instance_id] = item.process.returncode or 1
                running.clear()
                break
            if running:
                time.sleep(0.1)
    except BaseException:
        _terminate_instances(running)
        raise
    finally:
        for item in launched:
            item.log_file.close()

    ordered_results = [
        {
            "instance": plan.instance_id,
            "l2_path": str(plan.l2_path),
            "output_dir": str(plan.output_dir),
            "returncode": results.get(plan.instance_id, 1),
        }
        for plan in plans
    ]
    summary_path = _write_summary(
        output_root_path,
        trace=trace_path,
        results=ordered_results,
    )
    failed = [item for item in ordered_results if item["returncode"] != 0]
    if failed:
        print(f"[ERROR] Replay instances failed: {len(failed)}")
        print(f"[INFO] Summary: {summary_path}")
        return 1
    print(f"[INFO] Replay instances complete: {len(ordered_results)}")
    print(f"[INFO] Summary: {summary_path}")
    return 0


def _default_output_root(trace: str | Path) -> Path:
    trace_path = Path(trace).expanduser()
    label = trace_path.parent.name or trace_path.stem
    label = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in label
    )
    label = label or "replay"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = Path("outputs") / "replay-l2" / f"{label}-{timestamp}"
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = Path(f"{base}-{suffix}")
        suffix += 1
    return candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", required=True, type=_positive_int)
    parser.add_argument("--trace", required=True, help="shared .lct trace path")
    parser.add_argument("--config", required=True, help="replayer YAML config")
    parser.add_argument(
        "--l2-root",
        required=True,
        help="absolute root; each instance uses root/instance-N",
    )
    parser.add_argument(
        "--keep-l2",
        action="store_true",
        help="preserve existing L2 instance paths; require them to be empty",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="root for per-instance output and summary (default: outputs/replay-l2/<trace-name>-<UTC timestamp>)",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_root = args.output_root or _default_output_root(args.trace)
    try:
        plans = build_instance_plans(
            args.instances,
            l2_root=args.l2_root,
            output_root=output_root,
        )
        return run_instances(
            trace=args.trace,
            config=args.config,
            plans=plans,
            output_root=output_root,
            dry_run=args.dry_run,
            keep_l2=args.keep_l2,
        )
    except (FileNotFoundError, ValueError) as exc:
        _parser().error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
