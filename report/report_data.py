# SPDX-License-Identifier: Apache-2.0
"""Shared dataset contract for report figures."""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CSV_FIELDS = (
    "graph",
    "workload",
    "backend",
    "speedup",
    "repeat",
    "node_count",
    "node",
    "elapsed_seconds",
    "metric",
    "value",
    "unit",
)

WORKLOAD_GROUPS = {
    "tensormesh": ("GAIA", "WildClaw", "SWE-bench"),
    "mooncake": ("ToolAgent", "Conversation"),
}
WORKLOADS = WORKLOAD_GROUPS["tensormesh"] + WORKLOAD_GROUPS["mooncake"]
WORKLOAD_LABELS = {
    "tensormesh-gaia": "GAIA",
    "tensormesh-wildclaw": "WildClaw",
    "tensormesh-swebench": "SWE-bench",
    "mooncake-toolagent": "ToolAgent",
    "mooncake-conversation": "Conversation",
}
BACKENDS = ("fs-native", "3FS", "pNFS")

METRIC_UNITS = {
    "l2_read_gb_per_second": "GB/s",
    "l2_write_gb_per_second": "GB/s",
    "wall_throughput_gb_per_second": "GB/s",
    "read_p50_latency_ms": "ms",
    "read_p90_latency_ms": "ms",
    "read_p99_latency_ms": "ms",
    "write_p50_latency_ms": "ms",
    "write_p90_latency_ms": "ms",
    "write_p99_latency_ms": "ms",
    "max_dependency_wait_ms": "ms",
    "max_buffer_wait_ms": "ms",
    "max_schedule_lag_ms": "ms",
    "max_schedule_lag_seconds": "s",
    "disk_utilization_p95_percent": "%",
    "network_utilization_p95_percent": "%",
}


class DatasetError(ValueError):
    """Raised when a report dataset violates the shared schema."""


@dataclass(frozen=True)
class MetricRecord:
    graph: str
    workload: str
    backend: str
    speedup: float
    repeat: int
    metric: str
    value: float
    unit: str
    node_count: int | None = None
    node: str | None = None
    elapsed_seconds: float | None = None

    def as_csv_row(self) -> dict[str, str | int | float]:
        return {
            "graph": self.graph,
            "workload": self.workload,
            "backend": self.backend,
            "speedup": f"{self.speedup:g}",
            "repeat": self.repeat,
            "node_count": "" if self.node_count is None else self.node_count,
            "node": self.node or "",
            "elapsed_seconds": ""
            if self.elapsed_seconds is None
            else f"{self.elapsed_seconds:g}",
            "metric": self.metric,
            "value": f"{self.value:.12g}",
            "unit": self.unit,
        }


@dataclass(frozen=True)
class ReportDataset:
    root: Path
    manifest: dict[str, Any]
    records: tuple[MetricRecord, ...]

    @property
    def kind(self) -> str:
        return str(self.manifest["kind"])

    def select(
        self,
        *,
        graph: str,
        metric: str,
        workload: str | None = None,
        backend: str | None = None,
        speedup: float | None = None,
        node_count: int | None = None,
        node: str | None = None,
    ) -> tuple[MetricRecord, ...]:
        selected = []
        for record in self.records:
            if record.graph != graph or record.metric != metric:
                continue
            if workload is not None and record.workload != workload:
                continue
            if backend is not None and record.backend != backend:
                continue
            if speedup is not None and not math.isclose(record.speedup, speedup):
                continue
            if node_count is not None and record.node_count != node_count:
                continue
            if node is not None and record.node != node:
                continue
            selected.append(record)
        return tuple(selected)


def metric_record(
    *,
    graph: str,
    workload: str,
    backend: str,
    speedup: float,
    repeat: int,
    metric: str,
    value: float,
    node_count: int | None = None,
    node: str | None = None,
    elapsed_seconds: float | None = None,
) -> MetricRecord:
    if metric not in METRIC_UNITS:
        raise DatasetError(f"unknown report metric: {metric}")
    return MetricRecord(
        graph=graph,
        workload=workload,
        backend=backend,
        speedup=float(speedup),
        repeat=int(repeat),
        node_count=node_count,
        node=node,
        elapsed_seconds=elapsed_seconds,
        metric=metric,
        value=float(value),
        unit=METRIC_UNITS[metric],
    )


def _optional_float(value: str) -> float | None:
    return None if value == "" else float(value)


def _optional_int(value: str) -> int | None:
    return None if value == "" else int(value)


def _validate_record(record: MetricRecord, *, location: str) -> None:
    if not record.graph or not record.workload or not record.backend:
        raise DatasetError(f"{location}: graph/workload/backend must be non-empty")
    if not math.isfinite(record.speedup) or record.speedup <= 0:
        raise DatasetError(f"{location}: speedup must be finite and positive")
    if record.repeat <= 0:
        raise DatasetError(f"{location}: repeat must be positive")
    if record.node_count is not None and record.node_count <= 0:
        raise DatasetError(f"{location}: node_count must be positive")
    if record.elapsed_seconds is not None and record.elapsed_seconds < 0:
        raise DatasetError(f"{location}: elapsed_seconds must be non-negative")
    if not math.isfinite(record.value):
        raise DatasetError(f"{location}: value must be finite")
    expected_unit = METRIC_UNITS.get(record.metric)
    if expected_unit is None:
        raise DatasetError(f"{location}: unknown metric {record.metric!r}")
    if record.unit != expected_unit:
        raise DatasetError(
            f"{location}: {record.metric} uses {record.unit!r}, "
            f"expected {expected_unit!r}"
        )


def write_dataset(
    root: Path,
    *,
    kind: str,
    source: dict[str, Any],
    records: Iterable[MetricRecord],
) -> None:
    if kind not in {"dummy", "measured"}:
        raise DatasetError("dataset kind must be 'dummy' or 'measured'")
    materialized = tuple(records)
    if not materialized:
        raise DatasetError("dataset must contain at least one metric record")
    for index, record in enumerate(materialized, start=2):
        _validate_record(record, location=f"metrics.csv:{index}")

    root.mkdir(parents=True, exist_ok=True)
    metrics_path = root / "metrics.csv"
    with metrics_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for record in sorted(
            materialized,
            key=lambda item: (
                item.graph,
                item.workload,
                item.backend,
                item.speedup,
                item.repeat,
                item.node_count or 0,
                item.node or "",
                item.elapsed_seconds or 0.0,
                item.metric,
            ),
        ):
            writer.writerow(record.as_csv_row())

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "metrics_file": metrics_path.name,
        "record_count": len(materialized),
        "source": source,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_dataset(root: Path) -> ReportDataset:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise DatasetError(f"missing report dataset manifest: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise DatasetError(f"invalid report dataset manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise DatasetError("report dataset manifest must be an object")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise DatasetError(
            "unsupported report dataset schema_version: "
            f"{manifest.get('schema_version')!r}"
        )
    if manifest.get("kind") not in {"dummy", "measured"}:
        raise DatasetError("manifest kind must be 'dummy' or 'measured'")
    metrics_name = manifest.get("metrics_file")
    if not isinstance(metrics_name, str) or Path(metrics_name).name != metrics_name:
        raise DatasetError("manifest metrics_file must be a local file name")
    metrics_path = root / metrics_name
    if not metrics_path.is_file():
        raise DatasetError(f"missing report metric file: {metrics_path}")

    records: list[MetricRecord] = []
    with metrics_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise DatasetError(
                f"{metrics_path}: expected columns {CSV_FIELDS}, got {reader.fieldnames}"
            )
        for line_number, row in enumerate(reader, start=2):
            try:
                record = MetricRecord(
                    graph=row["graph"],
                    workload=row["workload"],
                    backend=row["backend"],
                    speedup=float(row["speedup"]),
                    repeat=int(row["repeat"]),
                    node_count=_optional_int(row["node_count"]),
                    node=row["node"] or None,
                    elapsed_seconds=_optional_float(row["elapsed_seconds"]),
                    metric=row["metric"],
                    value=float(row["value"]),
                    unit=row["unit"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise DatasetError(
                    f"{metrics_path}:{line_number}: invalid metric row"
                ) from exc
            _validate_record(record, location=f"{metrics_path}:{line_number}")
            records.append(record)

    if manifest.get("record_count") != len(records):
        raise DatasetError(
            "manifest record_count does not match metrics.csv: "
            f"{manifest.get('record_count')} != {len(records)}"
        )
    if not records:
        raise DatasetError("report dataset contains no metric records")
    return ReportDataset(root=root, manifest=manifest, records=tuple(records))
