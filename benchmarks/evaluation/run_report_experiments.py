#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run report experiments on a staged remote replay node.

The runner creates one staged-remote replay run per matrix cell.  A completed
cell is recorded in case.json and is skipped on the next invocation, so an
interrupted matrix can resume without repeating completed work.  The actual
LMCache replay remains in benchmarks/replayer/replay_speed_sweep.sh; this file
only builds the graph-specific matrix and calls staged_remote_replay.sh.
"""

from __future__ import annotations

import argparse
import datetime as datetime_module
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGED_REMOTE_SCRIPT = PROJECT_ROOT / "benchmarks/replayer/staged_remote_replay.sh"
RUNNER_VERSION = 3
PRESETS_PATH = Path(__file__).with_name("workload-presets.json")
_TEMPLATE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

ALL_WORKLOADS = (
    "tensormesh-gaia",
    "tensormesh-wildclaw",
    "tensormesh-swebench",
    "mooncake-toolagent",
    "mooncake-conversation",
)
TRACE_DIRS = {
    "tensormesh-gaia": "tensormesh/gaia",
    "tensormesh-wildclaw": "tensormesh/wildclaw",
    "tensormesh-swebench": "tensormesh/swebench",
    "mooncake-toolagent": "mooncake/toolagent",
    "mooncake-conversation": "mooncake/conversation",
}


@dataclass(frozen=True)
class GraphSpec:
    workloads: tuple[str, ...]
    speedups: tuple[float, ...]
    scaling: bool = False


GRAPH_SPECS: dict[str, GraphSpec] = {
    "throughput": GraphSpec(ALL_WORKLOADS, (1.0,)),
    "speedup": GraphSpec(ALL_WORKLOADS, (1.0, 1.25, 1.5, 2.0)),
    "latency": GraphSpec(
        ("tensormesh-swebench", "mooncake-conversation"),
        (1.0, 2.0),
    ),
    "resource": GraphSpec(ALL_WORKLOADS, (1.0,)),
    "nodewise": GraphSpec(("tensormesh-swebench",), (1.0, 2.0)),
    "scaling": GraphSpec(("tensormesh-swebench",), (2.0,), scaling=True),
}
GRAPH_ORDER = tuple(GRAPH_SPECS)


@dataclass(frozen=True)
class BackendSpec:
    name: str
    config_template: str
    l2_template: str
    raw: str

    @property
    def uses_nodes(self) -> bool:
        return "{nodes}" in self.config_template or "{nodes}" in self.l2_template


@dataclass(frozen=True)
class Case:
    case_id: str
    graph: str
    workload: str
    backend: str
    node_count: str
    speedup: float
    repeat: int
    trace_percent: float
    source_submission_window_seconds: float | None
    trace: str
    config: str
    l2_path: str
    profile: str | None
    run_name: str
    state_dir: str
    result_dir: str
    trace_metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "graph": self.graph,
            "workload": self.workload,
            "backend": self.backend,
            "node_count": self.node_count,
            "speedup": self.speedup,
            "repeat": self.repeat,
            "trace_percent": self.trace_percent,
            "source_submission_window_seconds": self.source_submission_window_seconds,
            "duration_estimate": (
                {
                    "basis": "source first-to-last submission window / speedup",
                    "scope": "minimum scheduled replay interval; excludes preparation, backend, SSH, drain and schedule lag",
                    "source_submission_window_seconds": self.source_submission_window_seconds,
                    "speedup": self.speedup,
                    "schedule_seconds": (
                        self.source_submission_window_seconds / self.speedup
                    ),
                }
                if self.source_submission_window_seconds is not None
                else None
            ),
            "trace": self.trace,
            "config": self.config,
            "l2_path": self.l2_path,
            "profile": self.profile,
            "run_name": self.run_name,
            "state_dir": self.state_dir,
            "result_dir": self.result_dir,
            "trace_metadata": self.trace_metadata,
        }


class RunnerError(RuntimeError):
    """Invalid runner input or an unsafe output state."""


def utc_now() -> str:
    return datetime_module.datetime.now(datetime_module.timezone.utc).isoformat()


def format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return format(value, ".12g")


def format_duration(seconds: float) -> str:
    """Format a schedule lower bound for human-readable dry-run output."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {int(remaining_seconds)}s"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{int(hours)}h {int(remaining_minutes)}m"
    days, remaining_hours = divmod(hours, 24)
    return f"{int(days)}d {int(remaining_hours)}h {int(remaining_minutes)}m"


def safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return label or "unnamed"


def parse_csv(raw: str, option: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise RunnerError(f"{option} must contain at least one value")
    return values


def parse_float_list(raw: str, option: str) -> tuple[float, ...]:
    values: list[float] = []
    for item in parse_csv(raw, option):
        try:
            value = float(item)
        except ValueError as exc:
            raise RunnerError(f"{option} contains a non-number: {item}") from exc
        if not math.isfinite(value) or value <= 0:
            raise RunnerError(f"{option} values must be finite and positive: {item}")
        values.append(value)
    return tuple(values)


def parse_positive_int_list(raw: str, option: str) -> tuple[int, ...]:
    values: list[int] = []
    for item in parse_csv(raw, option):
        try:
            value = int(item)
        except ValueError as exc:
            raise RunnerError(f"{option} contains a non-integer: {item}") from exc
        if value <= 0:
            raise RunnerError(f"{option} values must be positive: {item}")
        values.append(value)
    return tuple(values)


def parse_backend_spec(raw: str) -> BackendSpec:
    if "=" not in raw:
        raise RunnerError(f"backend spec must be NAME=CONFIG|L2_ROOT (received: {raw})")
    name, value = raw.split("=", 1)
    if not name or not _NAME_RE.fullmatch(name):
        raise RunnerError(f"backend name contains unsupported characters: {name}")
    if "|" in value:
        config, l2_path = value.split("|", 1)
    elif "@" in value:
        # Keep compatibility with replay_backend_sweep.sh.  Staged paths that
        # contain @PLACEHOLDER@ should use the unambiguous | separator.
        config, l2_path = value.rsplit("@", 1)
    else:
        raise RunnerError(f"backend spec must be NAME=CONFIG|L2_ROOT (received: {raw})")
    if not config or not l2_path:
        raise RunnerError(f"backend spec has an empty config or L2 path: {raw}")
    return BackendSpec(name, config, l2_path, raw)


def render_template(value: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise RunnerError(f"unknown backend template variable: {{{key}}}")
        return context[key]

    return _TEMPLATE_RE.sub(replace, value)


def trace_relative_dir(workload: str) -> str:
    return TRACE_DIRS.get(workload, workload)


def load_topology(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def read_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_trace_metadata(
    local_trace_root: Path | None,
    workload: str,
    trace_name: str,
    trace_percent: float,
    source_submission_window_seconds: float | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "remote_trace": f"@TRACE_ROOT@/{trace_relative_dir(workload)}/{trace_name}",
        "trace_percent": trace_percent,
    }
    if source_submission_window_seconds is not None:
        metadata["source_submission_window_seconds"] = (
            source_submission_window_seconds
        )
    if local_trace_root is None:
        return metadata
    local_path = local_trace_root / trace_relative_dir(workload) / trace_name
    if not local_path.is_file():
        raise RunnerError(f"local trace not found: {local_path}")
    stat = local_path.stat()
    metadata.update(
        {
            "local_trace": str(local_path),
            "size_bytes": stat.st_size,
            "sha256": read_sha256(local_path),
        }
    )
    return metadata


def load_workload_presets(path: Path = PRESETS_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise RunnerError(
            f"workload preset file not found: {path}; run "
            "benchmarks/evaluation/generate_preflight_estimates.py first"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise RunnerError(f"invalid workload preset file: {path}") from exc
    presets = payload.get("presets")
    if not isinstance(presets, dict):
        raise RunnerError(f"workload preset file has no presets object: {path}")
    return payload


def resolve_trace_percents(
    args: argparse.Namespace,
    workloads: Iterable[str],
) -> tuple[dict[str, float], dict[str, float | None], str | None]:
    if args.workload_preset:
        payload = load_workload_presets()
        presets = payload["presets"]
        preset = presets.get(args.workload_preset)
        if not isinstance(preset, dict):
            choices = ", ".join(sorted(presets))
            raise RunnerError(
                f"unknown --workload-preset {args.workload_preset!r}; choose from {choices}"
            )
        entries = preset.get("workloads")
        if not isinstance(entries, dict):
            raise RunnerError(
                f"preset {args.workload_preset!r} has no workload mapping"
            )
        values: dict[str, float] = {}
        windows: dict[str, float | None] = {}
        for workload in workloads:
            item = entries.get(workload)
            if not isinstance(item, dict) or "trace_percent" not in item:
                raise RunnerError(
                    f"preset {args.workload_preset!r} has no entry for {workload}"
                )
            percent = float(item["trace_percent"])
            if not math.isfinite(percent) or not 0 < percent <= 100:
                raise RunnerError(
                    f"preset {args.workload_preset!r} has invalid trace_percent "
                    f"for {workload}: {percent}"
                )
            raw_window = item.get("source_submission_window_seconds")
            if raw_window is None:
                raise RunnerError(
                    f"preset {args.workload_preset!r} has no source timestamp window "
                    f"for {workload}; regenerate workload-presets.json"
                )
            window = float(raw_window)
            if not math.isfinite(window) or window < 0:
                raise RunnerError(
                    f"preset {args.workload_preset!r} has invalid source timestamp "
                    f"window for {workload}: {window}"
                )
            values[workload] = percent
            windows[workload] = window
        source = payload.get("source_revision")
        return values, windows, str(source) if source else None

    raw_percent = 100.0 if args.trace_percent is None else float(args.trace_percent)
    if not math.isfinite(raw_percent) or not 0 < raw_percent <= 100:
        raise RunnerError("--trace-percent must be finite and in (0, 100]")
    return (
        {workload: raw_percent for workload in workloads},
        {workload: None for workload in workloads},
        None,
    )


def expand_graphs(requested: Iterable[str]) -> tuple[str, ...]:
    expanded: list[str] = []
    for graph in requested:
        if graph == "all":
            candidates = GRAPH_ORDER
        else:
            candidates = (graph,)
        for candidate in candidates:
            if candidate not in GRAPH_SPECS:
                raise RunnerError(f"unknown graph: {candidate}")
            if candidate not in expanded:
                expanded.append(candidate)
    if not expanded:
        raise RunnerError("at least one --graph is required")
    return tuple(expanded)


def make_run_name(
    graph: str,
    workload: str,
    backend: str,
    node_count: str,
    speedup: float,
    repeat: int,
) -> str:
    node_label = "base" if node_count == "baseline" else f"n{node_count}"
    raw = (
        f"report-{graph}-{workload}-{backend}-{node_label}"
        f"-s{format_number(speedup)}-r{repeat}"
    )
    raw = safe_label(raw)
    if len(raw) <= 180:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{raw[:167]}-{digest}"


def build_cases(
    args: argparse.Namespace,
    topology: dict[str, str],
) -> list[Case]:
    graphs = expand_graphs(args.graph)
    backends = tuple(parse_backend_spec(raw) for raw in args.backend_spec)
    if not backends:
        raise RunnerError("at least one --backend-spec is required")
    if "/" in args.trace_name or args.trace_name in {".", "..", ""}:
        raise RunnerError("--trace-name must be a file name without '/'")
    if args.repeats <= 0:
        raise RunnerError("--repeats must be positive")
    if "controller_output_root" not in topology:
        raise RunnerError("topology is missing controller_output_root")
    controller_output_root = topology["controller_output_root"]
    if not controller_output_root.startswith("/"):
        raise RunnerError("topology controller_output_root must be absolute")
    workloads_override = (
        parse_csv(args.workloads, "--workloads") if args.workloads else None
    )
    selected_workloads = tuple(
        dict.fromkeys(
            workload
            for graph in graphs
            for workload in (workloads_override or GRAPH_SPECS[graph].workloads)
        )
    )
    trace_percents, source_windows, preset_source = resolve_trace_percents(
        args,
        selected_workloads,
    )
    args.resolved_trace_percents = trace_percents
    args.resolved_trace_windows = source_windows
    args.preset_source = preset_source
    speedups_override = (
        parse_float_list(args.speedups, "--speedups") if args.speedups else None
    )
    node_counts = parse_positive_int_list(args.node_counts, "--node-counts")
    local_trace_root = (
        Path(args.local_trace_root).expanduser().resolve()
        if args.local_trace_root
        else None
    )
    trace_metadata = {
        workload: local_trace_metadata(
            local_trace_root,
            workload,
            args.trace_name,
            trace_percents[workload],
            source_windows[workload],
        )
        for graph in graphs
        for workload in (workloads_override or GRAPH_SPECS[graph].workloads)
    }
    cases: list[Case] = []
    case_ids: set[str] = set()
    for graph in graphs:
        graph_spec = GRAPH_SPECS[graph]
        workloads = workloads_override or graph_spec.workloads
        speedups = speedups_override or graph_spec.speedups
        for workload in workloads:
            if "/" in workload or workload in {".", "..", ""}:
                raise RunnerError(f"workload must not contain '/': {workload}")
            for backend in backends:
                if graph_spec.scaling:
                    backend_nodes: tuple[int | None, ...] = (
                        node_counts if backend.uses_nodes else (None,)
                    )
                else:
                    if backend.uses_nodes:
                        raise RunnerError(
                            f"{backend.name} uses {{nodes}} outside the scaling graph"
                        )
                    backend_nodes = (None,)
                for node_count in backend_nodes:
                    node_label = (
                        str(node_count) if node_count is not None else "baseline"
                    )
                    for speedup in speedups:
                        speedup_label = format_number(speedup)
                        context = {
                            "backend": backend.name,
                            "graph": graph,
                            "workload": workload,
                            "nodes": node_label,
                            "repeat": "1",
                            "speedup": speedup_label,
                        }
                        config = render_template(
                            backend.config_template,
                            context,
                        )
                        l2_path = render_template(backend.l2_template, context)
                        if not l2_path.startswith(("@", "/")):
                            raise RunnerError(
                                "staged L2 paths must be absolute or use a "
                                f"topology placeholder: {l2_path}"
                            )
                        profile = (
                            render_template(args.profile, context)
                            if args.profile
                            else None
                        )
                        for repeat in range(1, args.repeats + 1):
                            context["repeat"] = str(repeat)
                            config = render_template(
                                backend.config_template,
                                context,
                            )
                            l2_path = render_template(
                                backend.l2_template,
                                context,
                            )
                            if not l2_path.startswith(("@", "/")):
                                raise RunnerError(
                                    "staged L2 paths must be absolute or use a "
                                    f"topology placeholder: {l2_path}"
                                )
                            profile = (
                                render_template(args.profile, context)
                                if args.profile
                                else None
                            )
                            run_name = make_run_name(
                                graph,
                                workload,
                                backend.name,
                                node_label,
                                speedup,
                                repeat,
                            )
                            case_id = "/".join(
                                (
                                    graph,
                                    workload,
                                    backend.name,
                                    node_label,
                                    speedup_label,
                                    f"r{repeat}",
                                )
                            )
                            if case_id in case_ids:
                                raise RunnerError(f"duplicate case id: {case_id}")
                            case_ids.add(case_id)
                            state_dir = (
                                Path(args.state_root)
                                / "cases"
                                / safe_label(graph)
                                / safe_label(workload)
                                / safe_label(backend.name)
                                / f"n{safe_label(node_label)}"
                                / f"s{safe_label(speedup_label)}"
                                / f"r{repeat}"
                            )
                            result_dir = str(Path(controller_output_root) / run_name)
                            trace = (
                                f"{args.trace_root.rstrip('/')}/"
                                f"{trace_relative_dir(workload)}/{args.trace_name}"
                            )
                            cases.append(
                                Case(
                                    case_id=case_id,
                                    graph=graph,
                                    workload=workload,
                                    backend=backend.name,
                                    node_count=node_label,
                                    speedup=speedup,
                                    repeat=repeat,
                                    trace_percent=trace_percents[workload],
                                    source_submission_window_seconds=source_windows[workload],
                                    trace=trace,
                                    config=config,
                                    l2_path=l2_path,
                                    profile=profile,
                                    run_name=run_name,
                                    state_dir=str(state_dir),
                                    result_dir=result_dir,
                                    trace_metadata=trace_metadata[workload],
                                )
                            )
    return cases


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_results(
    state_root: Path,
    cases: list[Case],
    *,
    skipped_ids: set[str] | None = None,
) -> None:
    skipped_ids = skipped_ids or set()
    records: list[dict[str, Any]] = []
    for case in cases:
        marker_path = Path(case.state_dir) / "case.json"
        if marker_path.is_file():
            try:
                record = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                record = {"case_id": case.case_id, "status": "invalid"}
        else:
            record = {**case.as_dict(), "status": "pending"}
        if case.case_id in skipped_ids:
            record["resume_skipped"] = True
        records.append(record)
    records.sort(key=lambda item: item["case_id"])
    results_path = state_root / "matrix-results.jsonl"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = results_path.with_name(f".{results_path.name}.tmp")
    temporary.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
    )
    temporary.replace(results_path)
    statuses = [item.get("status", "pending") for item in records]
    summary = {
        "runner_version": RUNNER_VERSION,
        "updated_at_utc": utc_now(),
        "planned": len(records),
        "completed": sum(status in {"ok", "dry_run"} for status in statuses),
        "failed": sum(status == "failed" for status in statuses),
        "interrupted": sum(status == "interrupted" for status in statuses),
        "running": sum(status == "running" for status in statuses),
        "pending": sum(status == "pending" for status in statuses),
        "resume_skipped": sum(bool(item.get("resume_skipped")) for item in records),
        "results": str(results_path),
    }
    write_json(state_root / "matrix-summary.json", summary)


def build_duration_estimate(cases: list[Case]) -> dict[str, Any] | None:
    """Build a schedule lower bound from preset source timestamp windows."""
    groups: dict[tuple[str, float], dict[str, Any]] = {}
    total_seconds = 0.0
    estimated_case_count = 0
    for case in cases:
        if case.source_submission_window_seconds is None:
            continue
        schedule_seconds = case.source_submission_window_seconds / case.speedup
        key = (case.workload, case.speedup)
        group = groups.setdefault(
            key,
            {
                "workload": case.workload,
                "speedup": case.speedup,
                "one_case_schedule_seconds": schedule_seconds,
                "case_count": 0,
                "aggregate_schedule_seconds": 0.0,
            },
        )
        group["case_count"] += 1
        group["aggregate_schedule_seconds"] += schedule_seconds
        total_seconds += schedule_seconds
        estimated_case_count += 1
    if estimated_case_count == 0:
        return None
    return {
        "basis": "raw source first-to-last submission timestamp window / replay speedup",
        "lower_bound": True,
        "case_count": len(cases),
        "estimated_case_count": estimated_case_count,
        "minimum_sequential_schedule_seconds": total_seconds,
        "groups": sorted(
            groups.values(),
            key=lambda item: (str(item["workload"]), float(item["speedup"])),
        ),
        "excluded_overhead": [
            "L2 preparation and cleanup",
            "backend startup, mount and filesystem metadata work",
            "SSH, trace staging and result transfer",
            "async drain time and schedule lag",
        ],
    }


def print_duration_estimate(summary: dict[str, Any]) -> None:
    """Print the per-case and sequential matrix schedule lower bounds."""
    print(
        "[INFO] Replay schedule estimate (preset source window / speedup; "
        "minimum lower bound):",
        flush=True,
    )
    for group in summary["groups"]:
        print(
            "[INFO]   "
            f"workload={group['workload']} "
            f"speedup=x{format_number(float(group['speedup']))} "
            f"one_case_min={format_duration(float(group['one_case_schedule_seconds']))} "
            f"cases={int(group['case_count'])} "
            f"aggregate={format_duration(float(group['aggregate_schedule_seconds']))}",
            flush=True,
        )
    total = float(summary["minimum_sequential_schedule_seconds"])
    print(
        "[INFO] Minimum sequential replay schedule: "
        f"{format_duration(total)} ({total:.1f}s)",
        flush=True,
    )
    print(
        "[INFO] This is a lower bound; preparation, backend startup/mount, "
        "SSH/transfer, async drain and schedule lag are excluded.",
        flush=True,
    )


def marker_fingerprint(case: Case, args: argparse.Namespace) -> str:
    payload = {
        "runner_version": RUNNER_VERSION,
        "case": case.as_dict(),
        "workload_preset": args.workload_preset,
        "preset_source": getattr(args, "preset_source", None),
        "profile": args.profile,
        "dry_run": bool(args.dry_run),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_marker(case: Case) -> dict[str, Any] | None:
    path = Path(case.state_dir) / "case.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise RunnerError(f"invalid case marker: {path}") from exc
    if not isinstance(loaded, dict):
        raise RunnerError(f"case marker must be an object: {path}")
    return loaded


def prepare_case(
    case: Case,
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, bool, bool]:
    state_dir = Path(case.state_dir)
    if state_dir.is_symlink():
        raise RunnerError(f"case state directory is a symlink: {state_dir}")
    marker = read_marker(case)
    had_marker = marker is not None
    fingerprint = marker_fingerprint(case, args)
    if marker is not None:
        successful = (
            marker.get("status") in {"ok", "dry_run"}
            and marker.get("returncode") == 0
            and marker.get("fingerprint") == fingerprint
        )
        if args.resume and successful:
            return marker, True, had_marker
        if (
            args.resume
            and not args.retry_incomplete
            and marker.get("status") not in {"ok", "dry_run"}
        ):
            return marker, True, had_marker
        if state_dir.is_symlink():
            raise RunnerError(f"case state directory is a symlink: {state_dir}")
        shutil.rmtree(state_dir)
    elif state_dir.exists():
        if state_dir.is_symlink():
            raise RunnerError(f"case state directory is a symlink: {state_dir}")
        if any(state_dir.iterdir()):
            raise RunnerError(
                "case state directory exists without case.json; refusing to "
                f"remove it: {state_dir}"
            )
    state_dir.mkdir(parents=True, exist_ok=True)
    return {"fingerprint": fingerprint}, False, had_marker


def build_replay_command(case: Case, args: argparse.Namespace) -> list[str]:
    command = [
        "bash",
        "benchmarks/replayer/replay_speed_sweep.sh",
        "--trace",
        case.trace,
        "--config",
        case.config,
        "--l2-root",
        case.l2_path,
        "--output-root-exact",
        "@OUTPUT_ROOT@",
        "--speedups",
        format_number(case.speedup),
        "--trace-percent",
        format_number(case.trace_percent),
    ]
    if case.profile:
        command.extend(["--io-profile", case.profile])
    return command


def execute_case(
    case: Case,
    args: argparse.Namespace,
    *,
    replace_existing: bool,
) -> dict[str, Any]:
    fingerprint = marker_fingerprint(case, args)
    started_at = utc_now()
    marker = {
        **case.as_dict(),
        "runner_version": RUNNER_VERSION,
        "fingerprint": fingerprint,
        "status": "running",
        "returncode": None,
        "started_at_utc": started_at,
        "command": build_replay_command(case, args),
    }
    write_json(Path(case.state_dir) / "case.json", marker)
    command = [
        "bash",
        str(STAGED_REMOTE_SCRIPT),
        "replay",
        "--topology",
        str(Path(args.topology).resolve()),
        "--run-name",
        case.run_name,
    ]
    if replace_existing:
        command.append("--replace-existing")
    if args.dry_run:
        command.append("--dry-run")
    command.extend(["--", *build_replay_command(case, args)])
    print(f"[INFO] Case {case.case_id}")
    print("[INFO] Command:", " ".join(command))
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(command, cwd=PROJECT_ROOT)
        returncode = process.wait()
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        marker.update(
            {
                "status": "interrupted",
                "returncode": 130,
                "ended_at_utc": utc_now(),
            }
        )
        write_json(Path(case.state_dir) / "case.json", marker)
        raise
    except OSError as exc:
        marker.update(
            {
                "status": "failed",
                "returncode": 127,
                "ended_at_utc": utc_now(),
                "error": str(exc),
            }
        )
        write_json(Path(case.state_dir) / "case.json", marker)
        return marker
    status = (
        "dry_run"
        if args.dry_run and returncode == 0
        else ("ok" if returncode == 0 else "failed")
    )
    marker.update(
        {
            "status": status,
            "returncode": returncode,
            "ended_at_utc": utc_now(),
        }
    )
    write_json(Path(case.state_dir) / "case.json", marker)
    return marker


def run_stage_command(
    phase: str,
    args: argparse.Namespace,
) -> int:
    command = [
        "bash",
        str(STAGED_REMOTE_SCRIPT),
        phase,
        "--topology",
        str(Path(args.topology).resolve()),
    ]
    for asset in args.asset:
        command.extend(["--asset", asset])
    if args.dry_run:
        command.append("--dry-run")
    print("[INFO] Preparation command:", " ".join(command))
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


def prepare_staged_remote(args: argparse.Namespace, state_root: Path) -> None:
    if args.skip_prepare:
        print("[INFO] Skipping staged remote preparation")
        return
    marker_path = state_root / "preparation.json"
    previous: dict[str, Any] = {}
    if marker_path.is_file():
        try:
            previous = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            previous = {}
    if args.asset and previous.get("trace_status") != "ok":
        status = run_stage_command("prepare-trace", args)
        previous["trace_status"] = (
            "dry_run"
            if args.dry_run and status == 0
            else ("ok" if status == 0 else "failed")
        )
        previous["trace_assets"] = list(args.asset)
        write_json(marker_path, previous)
        if status != 0:
            raise RunnerError("staged remote trace preparation failed")
    if previous.get("replay_status") == "ok" and not args.dry_run:
        return
    status = run_stage_command("prepare-replay", args)
    previous["replay_status"] = (
        "dry_run"
        if args.dry_run and status == 0
        else ("ok" if status == 0 else "failed")
    )
    previous["updated_at_utc"] = utc_now()
    write_json(marker_path, previous)
    if status != 0:
        raise RunnerError("staged remote runtime preparation failed")


def run_matrix(args: argparse.Namespace) -> int:
    topology_path = Path(args.topology).expanduser().resolve()
    if not topology_path.is_file():
        raise RunnerError(f"topology not found: {topology_path}")
    topology = load_topology(topology_path)
    state_root = Path(args.state_root).expanduser()
    if not state_root.is_absolute():
        state_root = PROJECT_ROOT / state_root
    state_root = state_root.resolve()
    args.state_root = str(state_root)
    cases = build_cases(args, topology)
    duration_estimate = build_duration_estimate(cases)
    if duration_estimate is not None:
        print_duration_estimate(duration_estimate)
    elif args.dry_run:
        print(
            "[INFO] Replay schedule estimate unavailable: direct "
            "--trace-percent has no preset source timestamp metadata.",
            flush=True,
        )
    state_root.mkdir(parents=True, exist_ok=True)
    invocation = {
        "runner_version": RUNNER_VERSION,
        "created_at_utc": utc_now(),
        "topology": str(topology_path),
        "state_root": str(state_root),
        "arguments": vars(args),
        "cases": len(cases),
        "duration_estimate": duration_estimate,
    }
    write_json(state_root / "run-config.json", invocation)
    write_json(
        state_root / "matrix-plan.json",
        {
            "runner_version": RUNNER_VERSION,
            "generated_at_utc": utc_now(),
            "cases": [case.as_dict() for case in cases],
        },
    )
    prepare_staged_remote(args, state_root)
    write_results(state_root, cases)
    interrupted = False
    resumed_ids: set[str] = set()
    for case in cases:
        _marker, skipped, had_marker = prepare_case(case, args)
        if skipped:
            resumed_ids.add(case.case_id)
            write_results(state_root, cases, skipped_ids=resumed_ids)
            continue
        replace_existing = had_marker
        try:
            execute_case(
                case,
                args,
                replace_existing=replace_existing,
            )
        except KeyboardInterrupt:
            interrupted = True
            write_results(state_root, cases, skipped_ids=resumed_ids)
            break
        write_results(state_root, cases, skipped_ids=resumed_ids)
        current = read_marker(case) or {}
        if args.fail_fast and current.get("status") == "failed":
            break
    write_results(state_root, cases, skipped_ids=resumed_ids)
    if interrupted:
        return 130
    statuses = [(read_marker(case) or {}).get("status", "pending") for case in cases]
    return 1 if any(status in {"failed", "interrupted"} for status in statuses) else 0


def build_parser() -> argparse.ArgumentParser:
    graph_help = (
        "throughput (figures 1-2), speedup (figure 3), latency (figure 4), "
        "resource (figure 5), nodewise (figure 6), scaling (figure 7), all"
    )
    epilog = """
Examples:
  bash benchmarks/evaluation/run_report_experiments.sh \\
    --topology configs/replayer/staged-remote/topology.yaml \\
    --graph speedup \\
    --asset tensormesh/wildclaw.tar.gz \\
    --backend-spec 'fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native' \\
    --backend-spec '3FS=@REPO_ROOT@/configs/replayer/nixl-hf3fs.yaml|@L2_ROOT@/3fs' \\
    --backend-spec 'pNFS=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/pnfs' \\
    --trace-percent 10 \\
    --speedups 1,1.25,1.5,2 \\
    --repeats 3

For scaling, put {nodes} in distributed backend paths:
  --backend-spec '3FS=@REPO_ROOT@/configs/replayer/nixl-hf3fs-{nodes}.yaml|@L2_ROOT@/3fs-{nodes}'
  --backend-spec 'pNFS=@REPO_ROOT@/configs/replayer/fs-native-{nodes}.yaml|@L2_ROOT@/pnfs-{nodes}'

The topology must already describe staged remote storage. Use --skip-prepare
when the trace and replay repository are already staged. State files live under
outputs/report-experiments-staged by default; rerunning the same command resumes
successful cases and retries incomplete cases.
"""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    parser.add_argument(
        "--topology",
        required=True,
        help="staged-remote topology YAML",
    )
    parser.add_argument(
        "--graph",
        action="append",
        required=True,
        choices=(*GRAPH_ORDER, "all"),
        help=graph_help,
    )
    parser.add_argument(
        "--backend-spec",
        action="append",
        default=[],
        metavar="NAME=CONFIG|L2_ROOT",
        help="repeatable backend mapping; | avoids @PLACEHOLDER@ ambiguity",
    )
    parser.add_argument(
        "--asset",
        action="append",
        default=[],
        help="optional HF .tar.gz asset to stage before the matrix",
    )
    parser.add_argument(
        "--trace-root",
        default="@TRACE_ROOT@",
        help="remote trace root or @TRACE_ROOT@ placeholder",
    )
    parser.add_argument(
        "--trace-name",
        default="l2.lct",
        help="trace file name under each workload directory",
    )
    parser.add_argument(
        "--local-trace-root",
        help="optional local mirror used only for size/checksum metadata",
    )
    parser.add_argument(
        "--profile",
        "--io-profile",
        dest="profile",
        help="remote profiler YAML passed to every replay case",
    )
    parser.add_argument(
        "--workloads",
        help="comma-separated workload directory names; graph presets otherwise apply",
    )
    parser.add_argument(
        "--speedups",
        help="comma-separated positive replay speedups; graph presets otherwise apply",
    )
    trace_selection = parser.add_mutually_exclusive_group()
    trace_selection.add_argument(
        "--trace-percent",
        type=float,
        default=None,
        help="replay the first N percent for every workload (default: 100)",
    )
    trace_selection.add_argument(
        "--workload-preset",
        metavar="NAME",
        help=(
            "use per-workload trace-percent from workload-presets.json; available "
            "names include full, 0.5tb, 1tb, 2tb and 4tb"
        ),
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="repetitions per matrix cell (default: 3)",
    )
    parser.add_argument(
        "--node-counts",
        default="1,2,3,4,5,6",
        help="storage node counts for the scaling graph (default: 1..6)",
    )
    parser.add_argument(
        "--state-root",
        "--output-root",
        dest="state_root",
        default="outputs/report-experiments-staged",
        help="controller-side runner state directory (stable for resume)",
    )
    parser.add_argument(
        "--skip-prepare",
        action="store_true",
        help="do not run staged_remote_replay prepare-trace/prepare-replay",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="skip successful case markers (default: true)",
    )
    parser.add_argument(
        "--retry-incomplete",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="retry failed/interrupted cases by replacing their run directory",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after the first failed case",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print staged commands without executing remote work",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_matrix(args)
    except KeyboardInterrupt:
        return 130
    except (RunnerError, OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
