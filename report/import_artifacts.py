# SPDX-License-Identifier: Apache-2.0
"""Normalize staged report experiment artifacts into the report dataset schema."""

from __future__ import annotations

import argparse
import csv
import datetime as datetime_module
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from report.report_data import (
    WORKLOAD_LABELS,
    DatasetError,
    MetricRecord,
    metric_record,
    write_dataset,
)


class ImportError(DatasetError):
    """Raised when a completed report case has invalid or missing artifacts."""


def _format_number(value: float) -> str:
    return f"{value:g}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise ImportError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ImportError(f"expected JSON object: {path}")
    return value


def _read_matrix(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ImportError(f"matrix result file not found: {path}")
    records = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise ImportError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ImportError(f"{path}:{line_number}: expected JSON object")
        records.append(value)
    return records


def _parse_backend_rates(values: list[str]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for value in values:
        name, separator, rate_text = value.partition("=")
        if not separator or not name or not rate_text:
            raise ImportError(
                f"--network-link-gbps must use BACKEND=RATE, got {value!r}"
            )
        try:
            rate = float(rate_text)
        except ValueError as exc:
            raise ImportError(f"invalid network link rate: {value!r}") from exc
        if not math.isfinite(rate) or rate <= 0:
            raise ImportError(f"network link rate must be positive: {value!r}")
        parsed[name] = rate
    return parsed


def _operation_metrics(
    stats: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    operations = stats.get("operations", {})
    if not isinstance(operations, dict):
        raise ImportError("l2_replay_stats.json operations must be an object")
    adapter_metrics = operations.get(operation, {})
    if not isinstance(adapter_metrics, dict):
        raise ImportError(
            f"l2_replay_stats.json operations.{operation} must be an object"
        )
    candidates = [
        value for value in adapter_metrics.values() if isinstance(value, dict)
    ]
    if not candidates:
        return {}
    if len(candidates) != 1:
        raise ImportError(
            f"expected one target adapter under operations.{operation}, "
            f"found {len(candidates)}"
        )
    return candidates[0]


def _required_number(
    source: dict[str, Any],
    key: str,
    *,
    context: str,
) -> float:
    value = source.get(key)
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ImportError(f"{context}: missing finite numeric field {key!r}")
    return float(value)


def _case_common(case: dict[str, Any]) -> tuple[str, str, float, int, int | None]:
    workload_key = str(case.get("workload", ""))
    if workload_key not in WORKLOAD_LABELS:
        raise ImportError(f"unknown report workload: {workload_key!r}")
    backend = str(case.get("backend", ""))
    if not backend:
        raise ImportError("case backend is missing")
    speedup = _required_number(case, "speedup", context="case")
    repeat_raw = case.get("repeat")
    if not isinstance(repeat_raw, int) or repeat_raw <= 0:
        raise ImportError("case repeat must be a positive integer")
    node_raw = case.get("node_count")
    node_count = None
    if node_raw not in {None, "", "baseline"}:
        try:
            node_count = int(node_raw)
        except (TypeError, ValueError) as exc:
            raise ImportError(f"invalid node_count: {node_raw!r}") from exc
        if node_count <= 0:
            raise ImportError("node_count must be positive")
    return WORKLOAD_LABELS[workload_key], backend, speedup, repeat_raw, node_count


def _stats_records(
    case: dict[str, Any],
    stats: dict[str, Any],
) -> list[MetricRecord]:
    graph = str(case.get("graph", ""))
    workload, backend, speedup, repeat, node_count = _case_common(case)
    read = _operation_metrics(stats, "read")
    write = _operation_metrics(stats, "write")
    records: list[MetricRecord] = []

    if graph in {"speedup", "scaling"}:
        values = (
            (
                "wall_throughput_gb_per_second",
                _required_number(
                    stats,
                    "throughput_bytes_per_second",
                    context="l2_replay_stats.json",
                )
                / 1e9,
            ),
            (
                "read_p99_latency_ms",
                _required_number(
                    read,
                    "p99_latency_us",
                    context="operations.read",
                )
                / 1000.0,
            ),
        )
        if graph == "speedup":
            values += (
                (
                    "max_schedule_lag_seconds",
                    _required_number(
                        stats,
                        "max_schedule_lag_seconds",
                        context="l2_replay_stats.json",
                    ),
                ),
            )
        for metric, value in values:
            records.append(
                metric_record(
                    graph=graph,
                    workload=workload,
                    backend=backend,
                    speedup=speedup,
                    repeat=repeat,
                    node_count=node_count,
                    metric=metric,
                    node="aggregate" if graph == "scaling" else None,
                    value=value,
                )
            )

    if graph == "latency":
        mapping = (
            ("read_p50_latency_ms", read, "p50_latency_us", 0.001),
            ("read_p90_latency_ms", read, "p90_latency_us", 0.001),
            ("read_p99_latency_ms", read, "p99_latency_us", 0.001),
            ("write_p50_latency_ms", write, "p50_latency_us", 0.001),
            ("write_p90_latency_ms", write, "p90_latency_us", 0.001),
            ("write_p99_latency_ms", write, "p99_latency_us", 0.001),
            (
                "max_dependency_wait_ms",
                stats,
                "max_dependency_wait_seconds",
                1000.0,
            ),
            ("max_buffer_wait_ms", stats, "max_buffer_wait_seconds", 1000.0),
            ("max_schedule_lag_ms", stats, "max_schedule_lag_seconds", 1000.0),
        )
        for metric, source, key, factor in mapping:
            records.append(
                metric_record(
                    graph=graph,
                    workload=workload,
                    backend=backend,
                    speedup=speedup,
                    repeat=repeat,
                    metric=metric,
                    value=_required_number(source, key, context=metric) * factor,
                )
            )
    return records


def _throughput_records(
    case: dict[str, Any],
    interval_path: Path,
) -> list[MetricRecord]:
    workload, backend, speedup, repeat, _node_count = _case_common(case)
    records: list[MetricRecord] = []
    with interval_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        for line_number, row in enumerate(reader, start=2):
            try:
                elapsed = float(
                    row.get("interval_end_seconds") or row.get("elapsed_seconds") or ""
                )
                interval = float(row.get("interval_seconds") or 0)
                if interval > 0 and row.get("read_bytes") not in {None, ""}:
                    read_value = float(row["read_bytes"]) / interval / 1e9
                    write_value = float(row["write_bytes"]) / interval / 1e9
                elif row.get("read_gb_per_second") not in {None, ""}:
                    read_value = float(row["read_gb_per_second"])
                    write_value = float(row["write_gb_per_second"])
                else:
                    read_value = float(row["read_gib_per_second"]) * 1024**3 / 1e9
                    write_value = float(row["write_gib_per_second"]) * 1024**3 / 1e9
            except (KeyError, TypeError, ValueError) as exc:
                raise ImportError(
                    f"{interval_path}:{line_number}: invalid interval row"
                ) from exc
            for metric, value in (
                ("l2_read_gb_per_second", read_value),
                ("l2_write_gb_per_second", write_value),
            ):
                records.append(
                    metric_record(
                        graph="throughput",
                        workload=workload,
                        backend=backend,
                        speedup=speedup,
                        repeat=repeat,
                        elapsed_seconds=elapsed,
                        metric=metric,
                        value=value,
                    )
                )
    if not records:
        raise ImportError(f"interval file contains no samples: {interval_path}")
    return records


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ImportError("cannot calculate a percentile from no samples")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _grouped_peak(
    path: Path,
    *,
    value_columns: tuple[str, ...],
    combine: str,
) -> float:
    samples: dict[float, list[tuple[float, ...]]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        for line_number, row in enumerate(reader, start=2):
            try:
                elapsed = float(row["elapsed_s"])
                values = tuple(float(row[column]) for column in value_columns)
            except (KeyError, TypeError, ValueError) as exc:
                raise ImportError(f"{path}:{line_number}: invalid profile row") from exc
            samples[elapsed].append(values)
    if not samples:
        raise ImportError(f"profile file contains no samples: {path}")
    interval_values = []
    for rows in samples.values():
        if combine == "max":
            interval_values.append(max(value for row in rows for value in row))
        elif combine == "directional_sum":
            directions = [
                sum(row[index] for row in rows) for index in range(len(value_columns))
            ]
            interval_values.append(max(directions))
        else:
            raise AssertionError(f"unknown profile aggregation: {combine}")
    return _percentile(interval_values, 95.0)


def _profile_records(
    case: dict[str, Any],
    result_path: Path,
    network_link_gbps: float | None,
) -> tuple[list[MetricRecord], list[str]]:
    graph = str(case.get("graph", ""))
    if graph not in {"resource", "nodewise", "scaling"}:
        return [], []
    workload, backend, speedup, repeat, node_count = _case_common(case)
    profile_root = result_path / "profile"
    if not profile_root.is_dir():
        raise ImportError(f"profile directory not found: {profile_root}")

    node_values: dict[str, dict[str, float]] = {}
    warnings = []
    node_roles: dict[str, str] = {}
    for node_root in sorted(path for path in profile_root.iterdir() if path.is_dir()):
        summary_path = node_root / "summary.json"
        summary = _read_json(summary_path) if summary_path.is_file() else {}
        node = str(summary.get("node") or node_root.name)
        role = str(summary.get("role") or "storage")
        metrics: dict[str, float] = {}
        disk_path = node_root / "disk.tsv"
        if disk_path.is_file():
            metrics["disk_utilization_p95_percent"] = _grouped_peak(
                disk_path,
                value_columns=("io_util_percent",),
                combine="max",
            )
        network_path = node_root / "network.tsv"
        if network_path.is_file() and network_link_gbps is not None:
            peak_mibps = _grouped_peak(
                network_path,
                value_columns=("rx_mibps", "tx_mibps"),
                combine="directional_sum",
            )
            metrics["network_utilization_p95_percent"] = min(
                peak_mibps * 1024**2 * 8 / (network_link_gbps * 1e9) * 100,
                100.0,
            )
        elif network_path.is_file():
            warnings.append(
                f"{case.get('case_id')}: network metric omitted; "
                f"set --network-link-gbps {backend}=RATE"
            )
        if metrics:
            node_values[node] = metrics
            node_roles[node] = role

    if not node_values:
        raise ImportError(f"profile contains no usable samples: {profile_root}")

    records: list[MetricRecord] = []
    for node, values in node_values.items():
        if graph == "resource" or backend == "xfs":
            continue
        for metric, value in values.items():
            records.append(
                metric_record(
                    graph=graph,
                    workload=workload,
                    backend=backend,
                    speedup=speedup,
                    repeat=repeat,
                    node_count=node_count,
                    node=node,
                    metric=metric,
                    value=value,
                )
            )

    preferred_role = "replay" if backend == "xfs" else "storage"
    aggregate_values = {
        node: values
        for node, values in node_values.items()
        if node_roles.get(node) == preferred_role
    }
    if not aggregate_values:
        aggregate_values = node_values
    metric_names = {metric for values in aggregate_values.values() for metric in values}
    for metric in metric_names:
        available = [
            values[metric] for values in aggregate_values.values() if metric in values
        ]
        aggregate = sum(available) / len(available)
        records.append(
            metric_record(
                graph=graph,
                workload=workload,
                backend=backend,
                speedup=speedup,
                repeat=repeat,
                node_count=node_count,
                node="aggregate",
                metric=metric,
                value=aggregate,
            )
        )
    return records, warnings


def import_artifacts(
    matrix_results: Path,
    *,
    output_dir: Path,
    network_rates: dict[str, float],
    allow_incomplete: bool,
) -> tuple[int, list[str]]:
    records: list[MetricRecord] = []
    warnings: list[str] = []
    imported_cases = 0
    for case in _read_matrix(matrix_results):
        if case.get("status") != "ok":
            continue
        graph = str(case.get("graph", ""))
        _workload, backend, speedup, _repeat, _node_count = _case_common(case)
        result_root = Path(str(case.get("result_dir", ""))).expanduser()
        result_path = result_root / f"x{_format_number(speedup)}"
        try:
            stats_path = result_path / "l2_replay_stats.json"
            stats = _read_json(stats_path)
            if graph in {"speedup", "latency", "scaling"}:
                records.extend(_stats_records(case, stats))
            if graph == "throughput":
                records.extend(
                    _throughput_records(case, result_path / "l2_io_interval.tsv")
                )
            profile_records, profile_warnings = _profile_records(
                case,
                result_path,
                network_rates.get(backend),
            )
            records.extend(profile_records)
            warnings.extend(profile_warnings)
            imported_cases += 1
        except (ImportError, OSError) as exc:
            message = f"{case.get('case_id', '<unknown>')}: {exc}"
            if not allow_incomplete:
                raise ImportError(message) from exc
            warnings.append(message)

    if not records:
        raise ImportError("no measured metric records were imported")
    write_dataset(
        output_dir,
        kind="measured",
        source={
            "matrix_results": str(matrix_results.resolve()),
            "generated_at_utc": datetime_module.datetime.now(
                datetime_module.UTC
            ).isoformat(),
            "imported_cases": imported_cases,
            "warnings": warnings,
            "network_link_gbps": network_rates,
            "profile_aggregation": (
                "node p95; aggregate is the equal-weight mean of preferred-role nodes"
            ),
        },
        records=records,
    )
    return imported_cases, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert staged report artifacts to report/data metrics.csv."
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        required=True,
        help="Report runner state root containing matrix-results.jsonl.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "measured",
        help="Normalized dataset directory (default: report/data/measured).",
    )
    parser.add_argument(
        "--network-link-gbps",
        action="append",
        default=[],
        metavar="BACKEND=RATE",
        help="Directional link capacity used for network utilization.",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Skip completed cases with missing/invalid artifacts and record warnings.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rates = _parse_backend_rates(args.network_link_gbps)
        cases, warnings = import_artifacts(
            args.state_root / "matrix-results.jsonl",
            output_dir=args.output_dir,
            network_rates=rates,
            allow_incomplete=args.allow_incomplete,
        )
    except (ImportError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"[WARN] {warning}", file=sys.stderr)
    print(
        f"Imported {cases} completed cases into {args.output_dir} "
        f"({len(warnings)} warnings)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
