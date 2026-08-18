# SPDX-License-Identifier: Apache-2.0
"""Generate the versioned synthetic dataset used by report placeholder figures."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from report.report_data import (
    BACKENDS,
    WORKLOADS,
    metric_record,
    write_dataset,
)

SPEEDUPS = np.array([1.0, 1.25, 1.5, 2.0])
REFERENCE_SPEEDUP = 2.0
REPEATS = (1, 2, 3)
WORKLOAD_BASE = {
    "GAIA": 0.72,
    "WildClaw": 0.98,
    "SWE-bench": 1.24,
    "ToolAgent": 1.08,
    "Conversation": 0.82,
}
BACKEND_FACTOR = {"fs-native": 0.82, "3FS": 1.18, "pNFS": 1.0}


def _seed(*labels: str) -> int:
    return sum(
        (index + 1) * ord(char) for index, text in enumerate(labels) for char in text
    )


def _repeat_factor(repeat: int) -> float:
    return (0.975, 1.0, 1.025)[repeat - 1]


def _throughput(
    workload: str,
    backend: str,
    repeat: int,
    elapsed_seconds: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    normalized = elapsed_seconds / elapsed_seconds.max()
    phase = (_seed(workload) % 23) / 11.0
    base = WORKLOAD_BASE[workload] * BACKEND_FACTOR[backend]
    burst_center = 0.28 + 0.08 * (list(WORKLOADS).index(workload) % 3)
    burst = np.exp(-(((normalized - burst_center) / 0.12) ** 2))
    tail = np.exp(-(((normalized - 0.76) / 0.16) ** 2))
    rng = np.random.default_rng(_seed(workload, backend, str(repeat)))
    factor = _repeat_factor(repeat)

    read = base * (
        0.54
        + 0.21 * np.sin(4.2 * np.pi * normalized + phase)
        + 0.40 * burst
        + 0.16 * tail
    )
    write = base * (
        0.38
        + 0.17 * np.cos(3.4 * np.pi * normalized + phase / 2)
        + 0.24 * burst
        + 0.29 * tail
    )
    read = factor * read + rng.normal(0.0, 0.035 * base, elapsed_seconds.size)
    write = factor * write + rng.normal(0.0, 0.030 * base, elapsed_seconds.size)
    return np.clip(read, 0.03, None), np.clip(write, 0.03, None)


def _speedup_metrics(
    workload: str,
    backend: str,
    repeat: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    workload_scale = WORKLOAD_BASE[workload]
    backend_scale = BACKEND_FACTOR[backend]
    capacity = (
        workload_scale
        * backend_scale
        * {"fs-native": 1.85, "3FS": 2.55, "pNFS": 2.20}[backend]
    )
    offered = workload_scale * 0.78 * SPEEDUPS
    throughput = capacity * (1.0 - np.exp(-offered / capacity))

    knee = {"fs-native": 1.4, "3FS": 2.2, "pNFS": 1.8}[backend]
    overload = np.maximum(SPEEDUPS / knee - 1.0, 0.0)
    read_p99_ms = (2.8 / backend_scale) * (1.0 + 0.13 * SPEEDUPS + 3.1 * overload**2)
    lag_seconds = 0.015 * SPEEDUPS + 1.9 * overload**2
    factor = _repeat_factor(repeat)
    return throughput * factor, read_p99_ms / factor, lag_seconds / factor


def _latency_metrics(
    workload: str,
    backend: str,
    repeat: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    percentile_shape = np.array([1.0, 1.75, 4.0])
    workload_scale = 0.82 + 0.32 * WORKLOAD_BASE[workload]
    backend_scale = {"fs-native": 1.35, "3FS": 0.78, "pNFS": 1.0}[backend]
    overload = max(
        REFERENCE_SPEEDUP / {"fs-native": 1.4, "3FS": 2.2, "pNFS": 1.8}[backend] - 1.0,
        0.0,
    )
    factor = 1.0 / _repeat_factor(repeat)
    read_ms = (
        2.2
        * workload_scale
        * backend_scale
        * percentile_shape
        * (1.0 + 1.8 * overload**2)
        * factor
    )
    write_ms = (
        1.8
        * workload_scale
        * backend_scale
        * percentile_shape
        * (1.0 + 1.3 * overload**2)
        * factor
    )
    dependency_ms = 12.0 * workload_scale * (1.0 + 22.0 * overload**2)
    buffer_ms = 8.0 * backend_scale * (1.0 + 16.0 * overload**2)
    delays = np.array(
        [
            dependency_ms,
            buffer_ms,
            dependency_ms + buffer_ms + 4.0 * (1.0 + overload),
        ]
    )
    return read_ms, write_ms, delays * factor


def _resource_values() -> tuple[np.ndarray, np.ndarray]:
    workload_factor = np.array([0.78, 0.94, 1.08, 1.0, 0.86])
    disk_base = {"fs-native": 82.0, "3FS": 61.0, "pNFS": 72.0}
    network_base = {"fs-native": 12.0, "3FS": 74.0, "pNFS": 66.0}
    disk = np.column_stack(
        [np.clip(disk_base[name] * workload_factor, 0, 98) for name in BACKENDS]
    )
    network = np.column_stack(
        [
            np.clip(
                network_base[name] * (0.86 + 0.18 * workload_factor),
                0,
                98,
            )
            for name in BACKENDS
        ]
    )
    return disk, network


def _node_values() -> tuple[np.ndarray, np.ndarray]:
    disk_skew = {
        "3FS": np.array([0.90, 0.96, 1.02, 1.04, 0.98, 1.12]),
        "pNFS": np.array([0.82, 0.95, 1.03, 1.08, 0.94, 1.34]),
    }
    network_skew = {
        "3FS": np.array([0.90, 1.02, 1.08, 0.96, 1.12, 1.24]),
        "pNFS": np.array([0.94, 0.98, 1.04, 1.10, 0.92, 1.18]),
    }
    disk = np.full((len(BACKENDS), 6), np.nan)
    network = np.full_like(disk, np.nan)
    for index, backend in enumerate(BACKENDS):
        if backend == "fs-native":
            continue
        disk[index] = np.clip(
            {"3FS": 61.0, "pNFS": 72.0}[backend] * 1.08 * disk_skew[backend],
            0,
            98,
        )
        network[index] = np.clip(
            {"3FS": 74.0, "pNFS": 66.0}[backend]
            * (0.86 + 0.18 * 1.08)
            * network_skew[backend],
            0,
            98,
        )
    return disk, network


def build_records() -> list:
    records = []
    elapsed_seconds = np.arange(0.0, 125.0, 5.0)

    for workload in WORKLOADS:
        for backend in BACKENDS:
            for repeat in REPEATS:
                read, write = _throughput(workload, backend, repeat, elapsed_seconds)
                for elapsed, read_value, write_value in zip(
                    elapsed_seconds, read, write
                ):
                    for metric, value in (
                        ("l2_read_gb_per_second", read_value),
                        ("l2_write_gb_per_second", write_value),
                    ):
                        records.append(
                            metric_record(
                                graph="throughput",
                                workload=workload,
                                backend=backend,
                                speedup=1.0,
                                repeat=repeat,
                                elapsed_seconds=float(elapsed),
                                metric=metric,
                                value=float(value),
                            )
                        )

                throughput, read_p99, lag = _speedup_metrics(workload, backend, repeat)
                for speedup, values in zip(SPEEDUPS, zip(throughput, read_p99, lag)):
                    for metric, value in zip(
                        (
                            "wall_throughput_gb_per_second",
                            "read_p99_latency_ms",
                            "max_schedule_lag_seconds",
                        ),
                        values,
                    ):
                        records.append(
                            metric_record(
                                graph="speedup",
                                workload=workload,
                                backend=backend,
                                speedup=float(speedup),
                                repeat=repeat,
                                metric=metric,
                                value=float(value),
                            )
                        )

    for workload in ("SWE-bench", "Conversation"):
        for backend in BACKENDS:
            for repeat in REPEATS:
                read, write, delays = _latency_metrics(workload, backend, repeat)
                for metric, value in zip(
                    (
                        "read_p50_latency_ms",
                        "read_p90_latency_ms",
                        "read_p99_latency_ms",
                        "write_p50_latency_ms",
                        "write_p90_latency_ms",
                        "write_p99_latency_ms",
                        "max_dependency_wait_ms",
                        "max_buffer_wait_ms",
                        "max_schedule_lag_ms",
                    ),
                    (*read, *write, *delays),
                ):
                    records.append(
                        metric_record(
                            graph="latency",
                            workload=workload,
                            backend=backend,
                            speedup=REFERENCE_SPEEDUP,
                            repeat=repeat,
                            metric=metric,
                            value=float(value),
                        )
                    )

    disk, network = _resource_values()
    for workload_index, workload in enumerate(WORKLOADS):
        for backend_index, backend in enumerate(BACKENDS):
            for repeat in REPEATS:
                factor = _repeat_factor(repeat)
                for metric, value in (
                    (
                        "disk_utilization_p95_percent",
                        disk[workload_index, backend_index] * factor,
                    ),
                    (
                        "network_utilization_p95_percent",
                        network[workload_index, backend_index] * factor,
                    ),
                ):
                    records.append(
                        metric_record(
                            graph="resource",
                            workload=workload,
                            backend=backend,
                            speedup=1.0,
                            repeat=repeat,
                            node="aggregate",
                            metric=metric,
                            value=min(float(value), 100.0),
                        )
                    )

    node_disk, node_network = _node_values()
    speedup_factors = {
        "disk": {
            1.0: {"fs-native": 1.0, "3FS": 1.0, "pNFS": 1.0},
            2.0: {"fs-native": 1.12, "3FS": 1.05, "pNFS": 1.09},
        },
        "network": {
            1.0: {"fs-native": 1.0, "3FS": 1.0, "pNFS": 1.0},
            2.0: {"fs-native": 1.04, "3FS": 1.10, "pNFS": 1.08},
        },
    }
    aggregate_disk = {"fs-native": 82.0 * 1.08}
    aggregate_network = {"fs-native": 12.0 * (0.86 + 0.18 * 1.08)}
    for index, backend in enumerate(BACKENDS):
        if backend != "fs-native":
            aggregate_disk[backend] = float(np.mean(node_disk[index]))
            aggregate_network[backend] = float(np.mean(node_network[index]))

    for speedup in (1.0, 2.0):
        for backend_index, backend in enumerate(BACKENDS):
            nodes = (
                ("aggregate",)
                if backend == "fs-native"
                else tuple(f"storage-{index}" for index in range(1, 7)) + ("aggregate",)
            )
            for repeat in REPEATS:
                repeat_factor = _repeat_factor(repeat)
                for node_index, node in enumerate(nodes):
                    if node == "aggregate":
                        base_disk = aggregate_disk[backend]
                        base_network = aggregate_network[backend]
                    else:
                        base_disk = node_disk[backend_index, node_index]
                        base_network = node_network[backend_index, node_index]
                    for metric, value in (
                        (
                            "disk_utilization_p95_percent",
                            base_disk
                            * speedup_factors["disk"][speedup][backend]
                            * repeat_factor,
                        ),
                        (
                            "network_utilization_p95_percent",
                            base_network
                            * speedup_factors["network"][speedup][backend]
                            * repeat_factor,
                        ),
                    ):
                        records.append(
                            metric_record(
                                graph="nodewise",
                                workload="SWE-bench",
                                backend=backend,
                                speedup=speedup,
                                repeat=repeat,
                                node=node,
                                metric=metric,
                                value=min(float(value), 100.0),
                            )
                        )

    nodes = np.arange(1, 7)
    scaling = {
        "fs-native": {
            "throughput": np.full(6, 1.55),
            "latency": np.full(6, 10.5),
            "disk": np.full(6, 71.0),
            "network": np.full(6, 12.0),
        },
        "3FS": {
            "throughput": 5.0 * (1.0 - np.exp(-nodes / 2.8)),
            "latency": 6.5 + 25.0 * np.exp(-nodes / 2.0),
            "disk": np.clip(88.0 / np.sqrt(nodes) + 4.0, 0, 98),
            "network": np.clip(35.0 + 65.0 * (1.0 - np.exp(-nodes / 2.0)), 0, 98),
        },
        "pNFS": {
            "throughput": 4.5 * (1.0 - np.exp(-nodes / 3.1)),
            "latency": 7.2 + 23.0 * np.exp(-nodes / 2.2),
            "disk": np.clip(82.0 / np.sqrt(nodes) + 6.0, 0, 98),
            "network": np.clip(29.0 + 63.0 * (1.0 - np.exp(-nodes / 2.3)), 0, 98),
        },
    }
    for backend in BACKENDS:
        for node_index, node_count in enumerate(nodes):
            for repeat in REPEATS:
                factor = _repeat_factor(repeat)
                for metric, value in (
                    (
                        "wall_throughput_gb_per_second",
                        scaling[backend]["throughput"][node_index] * factor,
                    ),
                    (
                        "read_p99_latency_ms",
                        scaling[backend]["latency"][node_index] / factor,
                    ),
                    (
                        "disk_utilization_p95_percent",
                        scaling[backend]["disk"][node_index] * factor,
                    ),
                    (
                        "network_utilization_p95_percent",
                        scaling[backend]["network"][node_index] * factor,
                    ),
                ):
                    records.append(
                        metric_record(
                            graph="scaling",
                            workload="SWE-bench",
                            backend=backend,
                            speedup=REFERENCE_SPEEDUP,
                            repeat=repeat,
                            node_count=int(node_count),
                            node="aggregate",
                            metric=metric,
                            value=min(float(value), 100.0)
                            if metric.endswith("_percent")
                            else float(value),
                        )
                    )
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the deterministic dummy report dataset."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "dummy",
        help="Dataset directory (default: report/data/dummy).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = build_records()
    write_dataset(
        args.output_dir,
        kind="dummy",
        source={
            "generator": "report/generate_dummy_data.py",
            "note": "Synthetic values for layout validation only.",
        },
        records=records,
    )
    print(f"Generated {len(records)} dummy metric rows in {args.output_dir}")


if __name__ == "__main__":
    main()
