# SPDX-License-Identifier: Apache-2.0
"""Render report figures from a normalized dummy or measured dataset."""

from __future__ import annotations

import argparse
import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from report.report_data import (
    BACKENDS,
    WORKLOAD_GROUPS,
    WORKLOADS,
    MetricRecord,
    ReportDataset,
    load_dataset,
)

BACKEND_COLORS = {
    "xfs": "#0072B2",
    "3FS": "#D55E00",
    "pNFS": "#009E73",
}
READ_COLOR = "#0072B2"
WRITE_COLOR = "#E69F00"
FIGURES = ("throughput", "speedup", "latency", "resource", "nodewise", "scaling")


def _configure_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": 180,
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "legend.fontsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.22,
            "grid.linewidth": 0.7,
            "lines.linewidth": 1.8,
        }
    )


def _stable_seed(label: str) -> int:
    return int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:8], "big")


def _median_ci(values: Iterable[float], *, label: str) -> tuple[float, float, float]:
    samples = np.asarray(tuple(values), dtype=float)
    if samples.size == 0:
        return math.nan, math.nan, math.nan
    median = float(np.median(samples))
    if samples.size == 1:
        return median, median, median
    rng = np.random.default_rng(_stable_seed(label))
    indices = rng.integers(0, samples.size, size=(2000, samples.size))
    medians = np.median(samples[indices], axis=1)
    lower, upper = np.quantile(medians, (0.025, 0.975))
    return median, float(lower), float(upper)


def _series(
    records: Iterable[MetricRecord],
    *,
    x_attribute: str,
    label: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    grouped: dict[float, list[float]] = defaultdict(list)
    for record in records:
        x_value = getattr(record, x_attribute)
        if x_value is not None:
            grouped[float(x_value)].append(record.value)
    x_values = np.asarray(sorted(grouped), dtype=float)
    summaries = [
        _median_ci(grouped[value], label=f"{label}:{value}") for value in x_values
    ]
    if not summaries:
        empty = np.asarray([], dtype=float)
        return empty, empty, empty, empty
    median, lower, upper = (np.asarray(values) for values in zip(*summaries))
    return x_values, median, lower, upper


def _single_value(records: Iterable[MetricRecord], *, label: str) -> float:
    median, _lower, _upper = _median_ci(
        (record.value for record in records), label=label
    )
    return median


def _add_dataset_mark(fig: plt.Figure, dataset: ReportDataset) -> None:
    if dataset.kind != "dummy":
        return
    fig.text(
        0.995,
        0.006,
        "DUMMY DATA — NOT MEASURED",
        ha="right",
        va="bottom",
        fontsize=8,
        weight="bold",
        color="#555555",
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": "#FFF3CD",
            "edgecolor": "#D6B656",
            "linewidth": 0.7,
        },
    )


def _annotate_na(axis: plt.Axes) -> None:
    axis.text(
        0.5,
        0.5,
        "N/A",
        transform=axis.transAxes,
        ha="center",
        va="center",
        color="#666666",
        fontsize=12,
        weight="bold",
    )


def _assert_layout_clear(fig: plt.Figure) -> None:
    """Reject figure-level text/legend overlap and canvas clipping."""

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    canvas = fig.bbox
    graph_boxes = [axis.get_window_extent(renderer) for axis in fig.axes]
    figure_artists = [
        artist for artist in (*fig.texts, *fig.legends) if artist.get_visible()
    ]

    for artist in figure_artists:
        bbox = artist.get_window_extent(renderer)
        get_text = getattr(artist, "get_text", None)
        label = get_text() if get_text is not None else artist.__class__.__name__
        if any(bbox.overlaps(graph_box) for graph_box in graph_boxes):
            raise RuntimeError(f"Figure-level artist overlaps a graph box: {label!r}")
        if (
            bbox.x0 < canvas.x0 - 1
            or bbox.y0 < canvas.y0 - 1
            or bbox.x1 > canvas.x1 + 1
            or bbox.y1 > canvas.y1 + 1
        ):
            raise RuntimeError(
                f"Figure-level artist is clipped by the canvas: {label!r}"
            )

    for axis in fig.axes:
        bbox = axis.get_tightbbox(renderer)
        if bbox is None:
            continue
        if (
            bbox.x0 < canvas.x0 - 1
            or bbox.y0 < canvas.y0 - 1
            or bbox.x1 > canvas.x1 + 1
            or bbox.y1 > canvas.y1 + 1
        ):
            raise RuntimeError("Axis decoration is clipped by the canvas")


def _save(fig: plt.Figure, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_layout_clear(fig)
    fig.savefig(destination, facecolor="white")
    plt.close(fig)


def plot_throughput_family(
    dataset: ReportDataset,
    family: str,
    workloads: tuple[str, ...],
    output_dir: Path,
) -> None:
    fig, axes = plt.subplots(
        len(workloads),
        len(BACKENDS),
        figsize=(13.6, 3.0 * len(workloads)),
        sharex=True,
        sharey=True,
        squeeze=False,
    )
    maximum = 0.0
    for row, workload in enumerate(workloads):
        for column, backend in enumerate(BACKENDS):
            axis = axes[row, column]
            plotted = False
            for metric, color, linestyle in (
                ("l2_read_gb_per_second", READ_COLOR, "-"),
                ("l2_write_gb_per_second", WRITE_COLOR, "--"),
            ):
                records = dataset.select(
                    graph="throughput",
                    metric=metric,
                    workload=workload,
                    backend=backend,
                    speedup=1.0,
                )
                x, median, lower, upper = _series(
                    records,
                    x_attribute="elapsed_seconds",
                    label=f"throughput:{workload}:{backend}:{metric}",
                )
                if x.size == 0:
                    continue
                plotted = True
                maximum = max(maximum, float(np.max(upper)))
                axis.plot(x, median, color=color, linestyle=linestyle)
                if np.any(upper > lower):
                    axis.fill_between(
                        x, lower, upper, color=color, alpha=0.10, linewidth=0
                    )
            if not plotted:
                _annotate_na(axis)
            if row == 0:
                axis.set_title(backend)

    if maximum > 0:
        axes[0, 0].set_ylim(0, maximum * 1.08)
    fig.legend(
        handles=(
            Line2D([0], [0], color=READ_COLOR, label="L2 read"),
            Line2D(
                [0],
                [0],
                color=WRITE_COLOR,
                linestyle="--",
                label="L2 write",
            ),
        ),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.965),
        ncols=2,
        frameon=False,
    )
    fig.suptitle(
        f"Time-varying L2 throughput — {family.title()}",
        fontsize=15,
        weight="bold",
        y=0.995,
    )
    fig.text(
        0.032,
        0.48,
        "Throughput [GB/s]",
        rotation=90,
        va="center",
        ha="center",
    )
    fig.supxlabel("Elapsed time [s]", y=0.048)
    for row, workload in enumerate(workloads):
        axes[row, -1].yaxis.set_label_position("right")
        axes[row, -1].set_ylabel(
            workload,
            rotation=270,
            labelpad=20,
            va="center",
            weight="bold",
        )
    _add_dataset_mark(fig, dataset)
    fig.tight_layout(rect=(0.040, 0.055, 0.98, 0.90), h_pad=1.2, w_pad=1.0)
    _save(fig, output_dir / f"l2-throughput-{family}.png")


def plot_speedup_impact(dataset: ReportDataset, output_dir: Path) -> None:
    rows = (
        ("wall_throughput_gb_per_second", "Wall throughput [GB/s]"),
        ("read_p99_latency_ms", "Read p99 latency [ms]"),
        ("max_schedule_lag_seconds", "Max schedule lag [s]"),
    )
    fig, axes = plt.subplots(
        len(rows),
        len(WORKLOADS),
        figsize=(15.8, 9.0),
        sharex=True,
        squeeze=False,
    )
    for column, workload in enumerate(WORKLOADS):
        for row, (metric, ylabel) in enumerate(rows):
            axis = axes[row, column]
            plotted = False
            for backend in BACKENDS:
                records = dataset.select(
                    graph="speedup",
                    metric=metric,
                    workload=workload,
                    backend=backend,
                )
                x, median, lower, upper = _series(
                    records,
                    x_attribute="speedup",
                    label=f"speedup:{workload}:{backend}:{metric}",
                )
                if x.size == 0:
                    continue
                plotted = True
                axis.plot(
                    x,
                    median,
                    color=BACKEND_COLORS[backend],
                    marker="o",
                    markersize=3.8,
                    label=backend,
                )
                if np.any(upper > lower):
                    axis.fill_between(
                        x,
                        lower,
                        upper,
                        color=BACKEND_COLORS[backend],
                        alpha=0.10,
                        linewidth=0,
                    )
            if not plotted:
                _annotate_na(axis)
            axis.set_ylim(bottom=0)
            if column == 0:
                axis.set_ylabel(ylabel)
        axes[0, column].set_title(workload)
        axes[-1, column].set_xlabel("Replay speedup [x]")

    speedups = sorted(
        {record.speedup for record in dataset.records if record.graph == "speedup"}
    )
    if speedups:
        for axis in axes.ravel():
            axis.set_xscale("log", base=2)
            axis.set_xticks(speedups, labels=tuple(f"{value:g}" for value in speedups))
    fig.legend(
        handles=tuple(
            Line2D([0], [0], color=BACKEND_COLORS[name], marker="o", label=name)
            for name in BACKENDS
        ),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.965),
        ncols=3,
        frameon=False,
    )
    fig.suptitle(
        "Replay speedup impact by workload and backend",
        fontsize=15,
        weight="bold",
        y=0.995,
    )
    _add_dataset_mark(fig, dataset)
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    _save(fig, output_dir / "replay-speedup-impact.png")


def plot_latency_diagnostics(dataset: ReportDataset, output_dir: Path) -> None:
    row_specs = (
        (
            "Read task latency [ms]",
            ("read_p50_latency_ms", "read_p90_latency_ms", "read_p99_latency_ms"),
            ("p50", "p90", "p99"),
        ),
        (
            "Write task latency [ms]",
            (
                "write_p50_latency_ms",
                "write_p90_latency_ms",
                "write_p99_latency_ms",
            ),
            ("p50", "p90", "p99"),
        ),
        (
            "Replay delay [ms]",
            (
                "max_dependency_wait_ms",
                "max_buffer_wait_ms",
                "max_schedule_lag_ms",
            ),
            ("dep. max", "buffer max", "schedule max"),
        ),
    )
    workloads = ("SWE-bench", "Conversation")
    speedups = [
        record.speedup for record in dataset.records if record.graph == "latency"
    ]
    reference_speedup = max(speedups, default=2.0)
    fig, axes = plt.subplots(3, len(workloads), figsize=(8.8, 9.0), squeeze=False)
    x = np.arange(3)

    for column, workload in enumerate(workloads):
        for row, (ylabel, metrics, tick_labels) in enumerate(row_specs):
            axis = axes[row, column]
            plotted = False
            for backend in BACKENDS:
                values = [
                    _single_value(
                        dataset.select(
                            graph="latency",
                            metric=metric,
                            workload=workload,
                            backend=backend,
                            speedup=reference_speedup,
                        ),
                        label=f"latency:{workload}:{backend}:{metric}",
                    )
                    for metric in metrics
                ]
                if all(math.isnan(value) for value in values):
                    continue
                plotted = True
                axis.plot(
                    x,
                    values,
                    color=BACKEND_COLORS[backend],
                    marker="o",
                    markersize=3.8,
                    label=backend,
                )
            if not plotted:
                _annotate_na(axis)
            axis.set_xticks(x, tick_labels, rotation=18, ha="right")
            axis.set_ylim(bottom=0)
            if column == 0:
                axis.set_ylabel(ylabel)
        axes[0, column].set_title(workload)

    fig.legend(
        handles=tuple(
            Line2D([0], [0], color=BACKEND_COLORS[name], marker="o", label=name)
            for name in BACKENDS
        ),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.965),
        ncols=3,
        frameon=False,
    )
    fig.suptitle(
        f"Latency diagnostics at replay speedup x{reference_speedup:g}",
        fontsize=15,
        weight="bold",
        y=0.995,
    )
    _add_dataset_mark(fig, dataset)
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    _save(fig, output_dir / f"latency-breakdown-x{reference_speedup:g}.png")


def plot_resource_utilization(dataset: ReportDataset, output_dir: Path) -> None:
    specs = (
        ("disk_utilization_p95_percent", "Storage device utilization (p95)"),
        (
            "network_utilization_p95_percent",
            "Network directional utilization (p95)",
        ),
    )
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 4.6), sharey=True)
    x = np.arange(len(WORKLOADS))
    width = 0.24

    for axis, (metric, title) in zip(axes, specs):
        for backend_index, backend in enumerate(BACKENDS):
            values = []
            for workload in WORKLOADS:
                value = _single_value(
                    dataset.select(
                        graph="resource",
                        metric=metric,
                        workload=workload,
                        backend=backend,
                        speedup=1.0,
                        node="aggregate",
                    ),
                    label=f"resource:{workload}:{backend}:{metric}",
                )
                values.append(value)
            positions = x + (backend_index - 1) * width
            axis.bar(
                positions,
                values,
                width,
                color=BACKEND_COLORS[backend],
                label=backend,
            )
            for position, value in zip(positions, values):
                if math.isnan(value):
                    axis.text(
                        position,
                        2,
                        "N/A",
                        rotation=90,
                        ha="center",
                        va="bottom",
                        fontsize=7,
                        color="#666666",
                    )
        axis.set_title(title)
        axis.set_xticks(x, WORKLOADS, rotation=20, ha="right")
        axis.set_ylim(0, 100)
        axis.set_xlabel("Workload")

    axes[0].set_ylabel("Utilization [%]")
    fig.legend(
        handles=tuple(
            Line2D([0], [0], color=BACKEND_COLORS[name], linewidth=8, label=name)
            for name in BACKENDS
        ),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.94),
        ncols=3,
        frameon=False,
    )
    fig.suptitle(
        "Storage and network utilization at replay speedup x1",
        fontsize=15,
        weight="bold",
        y=0.995,
    )
    _add_dataset_mark(fig, dataset)
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    _save(fig, output_dir / "resource-utilization.png")


def _natural_node_key(node: str) -> tuple[str, int]:
    prefix, separator, suffix = node.rpartition("-")
    if separator and suffix.isdigit():
        return prefix, int(suffix)
    return node, 0


def plot_nodewise_resource_utilization(
    dataset: ReportDataset,
    output_dir: Path,
) -> None:
    node_records = [
        record
        for record in dataset.records
        if record.graph == "nodewise" and record.node not in {None, "aggregate"}
    ]
    node_labels = tuple(
        sorted({str(record.node) for record in node_records}, key=_natural_node_key)
    ) + ("aggregate",)
    speedups = sorted(
        {record.speedup for record in dataset.records if record.graph == "nodewise"}
    )
    if not speedups:
        speedups = [1.0, 2.0]
    specs = (
        ("disk_utilization_p95_percent", "Storage device utilization (p95)"),
        (
            "network_utilization_p95_percent",
            "Network directional utilization (p95)",
        ),
    )
    fig, axes = plt.subplots(
        len(speedups),
        2,
        figsize=(11.2, 4.0 * len(speedups)),
        sharey=True,
        squeeze=False,
        layout="constrained",
    )
    image = None
    cmap = plt.get_cmap("YlOrRd").copy()
    cmap.set_bad("#E5E7EB")

    for row, speedup in enumerate(speedups):
        for column, (metric, title) in enumerate(specs):
            axis = axes[row, column]
            matrix = np.full((len(node_labels), len(BACKENDS)), np.nan)
            for node_index, node in enumerate(node_labels):
                for backend_index, backend in enumerate(BACKENDS):
                    matrix[node_index, backend_index] = _single_value(
                        dataset.select(
                            graph="nodewise",
                            metric=metric,
                            workload="SWE-bench",
                            backend=backend,
                            speedup=speedup,
                            node=node,
                        ),
                        label=f"nodewise:{speedup}:{node}:{backend}:{metric}",
                    )
            image = axis.imshow(matrix, aspect="auto", cmap=cmap, vmin=0, vmax=100)
            axis.set_title(f"{title}\nSWE-bench · replay speedup x{speedup:g}")
            axis.set_xticks(np.arange(len(BACKENDS)), BACKENDS)
            axis.set_yticks(np.arange(len(node_labels)), node_labels)
            axis.set_xlabel("L2 backend")
            axis.axhline(len(node_labels) - 1.5, color="#222222", linewidth=1.2)
            for cell_row in range(matrix.shape[0]):
                for cell_column in range(matrix.shape[1]):
                    value = matrix[cell_row, cell_column]
                    if np.isnan(value):
                        label, color = "N/A", "#555555"
                    else:
                        label = f"{value:.0f}"
                        color = "white" if value >= 62 else "#222222"
                    axis.text(
                        cell_column,
                        cell_row,
                        label,
                        ha="center",
                        va="center",
                        fontsize=9,
                        color=color,
                    )
            if column == 0:
                axis.set_ylabel("Storage node / aggregate")

    assert image is not None
    fig.colorbar(
        image,
        ax=axes.ravel().tolist(),
        label="p95 utilization [%]",
        shrink=0.86,
    )
    fig.suptitle(
        "SWE-bench node-wise and aggregate resource utilization",
        fontsize=15,
        weight="bold",
    )
    _add_dataset_mark(fig, dataset)
    _save(fig, output_dir / "resource-utilization-nodewise.png")


def plot_storage_node_scaling(
    dataset: ReportDataset,
    output_dir: Path,
) -> None:
    specs = (
        (
            "wall_throughput_gb_per_second",
            "Aggregate L2 throughput",
            "GB/s",
        ),
        ("read_p99_latency_ms", "Read p99 latency", "ms"),
        (
            "disk_utilization_p95_percent",
            "Storage device utilization (p95)",
            "%",
        ),
        (
            "network_utilization_p95_percent",
            "Network directional utilization (p95)",
            "%",
        ),
    )
    node_counts = sorted(
        {
            record.node_count
            for record in dataset.records
            if record.graph == "scaling" and record.node_count is not None
        }
    )
    if not node_counts:
        node_counts = list(range(1, 7))
    fig, axes = plt.subplots(2, 2, figsize=(11.4, 8.0), sharex=True, squeeze=False)

    for axis, (metric, title, unit) in zip(axes.ravel(), specs):
        plotted = False
        for backend in BACKENDS:
            records = dataset.select(
                graph="scaling",
                metric=metric,
                workload="SWE-bench",
                backend=backend,
                speedup=2.0,
                node="aggregate",
            )
            baseline = [record for record in records if record.node_count is None]
            if baseline:
                value = _single_value(
                    baseline, label=f"scaling:{backend}:{metric}:baseline"
                )
                x = np.asarray(node_counts, dtype=float)
                median = np.full(len(node_counts), value)
                lower = upper = median
            else:
                x, median, lower, upper = _series(
                    records,
                    x_attribute="node_count",
                    label=f"scaling:{backend}:{metric}",
                )
            if x.size == 0:
                continue
            plotted = True
            axis.plot(
                x,
                median,
                color=BACKEND_COLORS[backend],
                linestyle="--" if backend == "xfs" else "-",
                marker="o",
                markersize=4,
                label=backend,
            )
            if np.any(upper > lower):
                axis.fill_between(
                    x,
                    lower,
                    upper,
                    color=BACKEND_COLORS[backend],
                    alpha=0.10,
                    linewidth=0,
                )
        if not plotted:
            _annotate_na(axis)
        axis.set_title(title)
        axis.set_ylabel(unit)
        axis.set_xticks(node_counts)
        axis.set_xlim(min(node_counts), max(node_counts))
        axis.set_ylim(bottom=0)
        if metric.endswith("_percent"):
            axis.set_ylim(0, 100)
        axis.grid(True, axis="y")

    for axis in axes[1, :]:
        axis.set_xlabel("Storage node count")
    fig.legend(
        handles=tuple(
            Line2D(
                [0],
                [0],
                color=BACKEND_COLORS[name],
                linestyle="--" if name == "xfs" else "-",
                marker="o",
                label=name,
            )
            for name in BACKENDS
        ),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.965),
        ncols=3,
        frameon=False,
    )
    fig.suptitle(
        "Storage-node scaling — SWE-bench at replay speedup x2",
        fontsize=15,
        weight="bold",
    )
    _add_dataset_mark(fig, dataset)
    fig.tight_layout(rect=(0, 0.04, 1, 0.90), h_pad=2.0, w_pad=1.4)
    _save(fig, output_dir / "storage-node-scaling.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render report figures from normalized report data."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "dummy",
        help="Directory containing manifest.json and metrics.csv.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "PNG destination. Defaults to report/figures for dummy data and "
            "report/figures/measured for measured data."
        ),
    )
    parser.add_argument(
        "--figure",
        action="append",
        choices=(*FIGURES, "all"),
        help="Figure family to render; repeatable (default: all).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = load_dataset(args.data_dir)
    output_dir = args.output_dir
    if output_dir is None:
        base = Path(__file__).resolve().parent / "figures"
        output_dir = base if dataset.kind == "dummy" else base / "measured"
    requested = args.figure or ["all"]
    figures = FIGURES if "all" in requested else tuple(dict.fromkeys(requested))

    _configure_style()
    if "throughput" in figures:
        for family, workloads in WORKLOAD_GROUPS.items():
            plot_throughput_family(dataset, family, workloads, output_dir)
    if "speedup" in figures:
        plot_speedup_impact(dataset, output_dir)
    if "latency" in figures:
        plot_latency_diagnostics(dataset, output_dir)
    if "resource" in figures:
        plot_resource_utilization(dataset, output_dir)
    if "nodewise" in figures:
        plot_nodewise_resource_utilization(dataset, output_dir)
    if "scaling" in figures:
        plot_storage_node_scaling(dataset, output_dir)
    print(
        f"Generated {dataset.kind} report figures from {args.data_dir} in {output_dir}"
    )


if __name__ == "__main__":
    main()
