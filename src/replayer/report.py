# SPDX-License-Identifier: Apache-2.0

"""Human-readable reports derived from LMCache L2 replay statistics."""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_CSV_FIELDS = (
    "speedup",
    "status",
    "total_replay_seconds",
    "target_submission_window_seconds",
    "actual_submission_window_seconds",
    "total_bytes_submitted",
    "wall_throughput_gb_per_second",
    "mean_schedule_lag_seconds",
    "max_schedule_lag_seconds",
    "max_dependency_wait_seconds",
    "total_dependency_wait_seconds",
    "max_buffer_wait_seconds",
    "total_buffer_wait_seconds",
    "drain_seconds",
    "outcome_comparisons",
    "outcome_mismatch_count",
    "outcome_mismatch_rate",
    "read_adapter",
    "read_submitted",
    "read_completed",
    "read_total_bytes",
    "read_average_latency_ms",
    "read_p50_latency_ms",
    "read_p90_latency_ms",
    "read_p99_latency_ms",
    "read_bytes_over_sum_task_latency_gb_per_second",
    "write_adapter",
    "write_submitted",
    "write_completed",
    "write_total_bytes",
    "write_average_latency_ms",
    "write_p50_latency_ms",
    "write_p90_latency_ms",
    "write_p99_latency_ms",
    "write_bytes_over_sum_task_latency_gb_per_second",
    "output_dir",
)


def load_l2_stats(path: str | Path) -> dict[str, Any]:
    stats_path = Path(path)
    value = json.loads(stats_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"L2 replay stats must contain a JSON object: {stats_path}")
    return value


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _csv_number(value: Any) -> float:
    return round(_number(value), 10)


def _sum_counts(value: Any) -> int:
    if not isinstance(value, Mapping):
        return 0
    return sum(_integer(item) for item in value.values())


def _operation(stats: Mapping[str, Any], name: str) -> tuple[str, Mapping[str, Any]]:
    operations = stats.get("operations", {})
    if not isinstance(operations, Mapping):
        return "", {}
    adapters = operations.get(name, {})
    if not isinstance(adapters, Mapping) or not adapters:
        return "", {}
    adapter_name = min(str(item) for item in adapters)
    metrics = adapters.get(adapter_name, {})
    return adapter_name, metrics if isinstance(metrics, Mapping) else {}


def _format_seconds(value: Any) -> str:
    seconds = _number(value)
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.3f} us"
    if seconds < 1:
        return f"{seconds * 1_000:.3f} ms"
    return f"{seconds:.3f} s"


def _format_latency_us(value: Any) -> str:
    return f"{_number(value) / 1_000:.3f} ms"


def _format_bytes(value: Any) -> str:
    raw_bytes = _number(value)
    if raw_bytes >= 1_000_000_000:
        return f"{raw_bytes / 1_000_000_000:.3f} GB"
    if raw_bytes >= 1_000_000:
        return f"{raw_bytes / 1_000_000:.3f} MB"
    return f"{int(raw_bytes)} B"


def _format_rate(value: Any) -> str:
    return f"{_number(value) * 100:.4f}%"


def render_l2_summary(stats: Mapping[str, Any]) -> str:
    """Render one ``l2_replay_stats.json`` as a concise Markdown report."""
    comparisons = _sum_counts(stats.get("outcome_comparisons"))
    read_adapter, read = _operation(stats, "read")
    write_adapter, write = _operation(stats, "write")
    rows = []
    for operation, adapter, metrics in (
        ("Read", read_adapter, read),
        ("Write", write_adapter, write),
    ):
        if not adapter:
            continue
        rows.append(
            "| "
            + " | ".join(
                (
                    operation,
                    adapter,
                    str(_integer(metrics.get("submitted"))),
                    str(_integer(metrics.get("completed"))),
                    _format_bytes(metrics.get("total_bytes")),
                    _format_latency_us(metrics.get("average_latency_us")),
                    _format_latency_us(metrics.get("p50_latency_us")),
                    _format_latency_us(metrics.get("p90_latency_us")),
                    _format_latency_us(metrics.get("p99_latency_us")),
                    f"{_number(metrics.get('aggregate_throughput_gbps')):.3f} GB/s",
                )
            )
            + " |"
        )
    if not rows:
        rows.append("| - | - | 0 | 0 | 0 B | - | - | - | - | - |")

    return "\n".join(
        (
            "# L2 Replay Summary",
            "",
            (
                "Generated from `l2_replay_stats.json`. See "
                "`docs/l2-replay-metrics.md` for metric definitions and caveats."
            ),
            "",
            "## Overview",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Speedup | {_number(stats.get('speedup')):.3g}x |",
            f"| Selected operations | {_integer(stats.get('operations_selected')):,} |",
            f"| Replay elapsed | {_format_seconds(stats.get('total_replay_seconds'))} |",
            f"| Target submission window | {_format_seconds(stats.get('source_submission_window_seconds'))} |",
            f"| Actual submission window | {_format_seconds(stats.get('actual_submission_window_seconds'))} |",
            f"| Submitted data | {_format_bytes(stats.get('total_bytes_submitted'))} |",
            f"| Wall throughput | {_number(stats.get('throughput_bytes_per_second')) / 1_000_000_000:.3f} GB/s |",
            f"| Drain after final submission | {_format_seconds(stats.get('drain_seconds'))} |",
            "",
            "## Operation latency",
            "",
            "| Operation | Adapter | Submitted | Completed | Data | Average | P50 | P90 | P99 | Task-latency throughput |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            *rows,
            "",
            "## Scheduling and waits",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Mean schedule lag | {_format_seconds(stats.get('mean_schedule_lag_seconds'))} |",
            f"| Maximum schedule lag | {_format_seconds(stats.get('max_schedule_lag_seconds'))} |",
            f"| Maximum dependency wait | {_format_seconds(stats.get('max_dependency_wait_seconds'))} |",
            f"| Sum of dependency waits | {_format_seconds(stats.get('total_dependency_wait_seconds'))} |",
            f"| Maximum buffer wait | {_format_seconds(stats.get('max_buffer_wait_seconds'))} |",
            f"| Sum of buffer waits | {_format_seconds(stats.get('total_buffer_wait_seconds'))} |",
            "",
            "## Outcome comparison",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Compared operations | {comparisons:,} |",
            f"| Mismatches | {_integer(stats.get('outcome_mismatch_count')):,} |",
            f"| Mismatch rate | {_format_rate(stats.get('outcome_mismatch_rate'))} |",
            "",
            "> Outcome mismatch is a diagnostic metric, not a replay failure by itself.",
            "",
        )
    )


def write_l2_summary(
    stats_path: str | Path, output_path: str | Path | None = None
) -> Path:
    stats_path = Path(stats_path)
    destination = (
        Path(output_path)
        if output_path is not None
        else stats_path.with_name("l2_replay_summary.md")
    )
    destination.write_text(
        render_l2_summary(load_l2_stats(stats_path)), encoding="utf-8"
    )
    return destination


def _csv_operation(stats: Mapping[str, Any], name: str) -> dict[str, Any]:
    adapter, metrics = _operation(stats, name)
    return {
        f"{name}_adapter": adapter,
        f"{name}_submitted": _integer(metrics.get("submitted")),
        f"{name}_completed": _integer(metrics.get("completed")),
        f"{name}_total_bytes": _integer(metrics.get("total_bytes")),
        f"{name}_average_latency_ms": _csv_number(
            _number(metrics.get("average_latency_us")) / 1_000
        ),
        f"{name}_p50_latency_ms": _csv_number(
            _number(metrics.get("p50_latency_us")) / 1_000
        ),
        f"{name}_p90_latency_ms": _csv_number(
            _number(metrics.get("p90_latency_us")) / 1_000
        ),
        f"{name}_p99_latency_ms": _csv_number(
            _number(metrics.get("p99_latency_us")) / 1_000
        ),
        f"{name}_bytes_over_sum_task_latency_gb_per_second": _csv_number(
            metrics.get("aggregate_throughput_gbps")
        ),
    }


def flatten_l2_stats(
    stats: Mapping[str, Any], *, status: str = "ok", output_dir: str = ""
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "speedup": _csv_number(stats.get("speedup")),
        "status": status,
        "total_replay_seconds": _csv_number(stats.get("total_replay_seconds")),
        "target_submission_window_seconds": _csv_number(
            stats.get("source_submission_window_seconds")
        ),
        "actual_submission_window_seconds": _csv_number(
            stats.get("actual_submission_window_seconds")
        ),
        "total_bytes_submitted": _integer(stats.get("total_bytes_submitted")),
        "wall_throughput_gb_per_second": _csv_number(
            _number(stats.get("throughput_bytes_per_second")) / 1_000_000_000
        ),
        "mean_schedule_lag_seconds": _csv_number(
            stats.get("mean_schedule_lag_seconds")
        ),
        "max_schedule_lag_seconds": _csv_number(stats.get("max_schedule_lag_seconds")),
        "max_dependency_wait_seconds": _csv_number(
            stats.get("max_dependency_wait_seconds")
        ),
        "total_dependency_wait_seconds": _csv_number(
            stats.get("total_dependency_wait_seconds")
        ),
        "max_buffer_wait_seconds": _csv_number(stats.get("max_buffer_wait_seconds")),
        "total_buffer_wait_seconds": _csv_number(
            stats.get("total_buffer_wait_seconds")
        ),
        "drain_seconds": _csv_number(stats.get("drain_seconds")),
        "outcome_comparisons": _sum_counts(stats.get("outcome_comparisons")),
        "outcome_mismatch_count": _integer(stats.get("outcome_mismatch_count")),
        "outcome_mismatch_rate": _csv_number(stats.get("outcome_mismatch_rate")),
        "output_dir": output_dir,
    }
    row.update(_csv_operation(stats, "read"))
    row.update(_csv_operation(stats, "write"))
    return row


def write_speed_sweep_csv(
    results_path: str | Path, output_path: str | Path | None = None
) -> Path:
    """Create a comparison CSV for successful cases in a speed sweep."""
    results_path = Path(results_path)
    destination = (
        Path(output_path)
        if output_path is not None
        else results_path.with_name("sweep-summary.csv")
    )
    rows = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        result = json.loads(line)
        if not isinstance(result, dict) or result.get("status") != "ok":
            continue
        output_dir = Path(str(result["output_dir"]))
        stats_path = output_dir / "l2_replay_stats.json"
        if not stats_path.is_file():
            continue
        rows.append(
            flatten_l2_stats(
                load_l2_stats(stats_path),
                status=str(result["status"]),
                output_dir=str(output_dir),
            )
        )
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    summary = subparsers.add_parser("summary", help="write one Markdown summary")
    summary.add_argument("--stats", required=True)
    summary.add_argument("--output")
    sweep = subparsers.add_parser("sweep-csv", help="write a speed sweep CSV")
    sweep.add_argument("--results", required=True)
    sweep.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "summary":
        output = write_l2_summary(args.stats, args.output)
    else:
        output = write_speed_sweep_csv(args.results, args.output)
    print(f"[INFO] L2 replay report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
