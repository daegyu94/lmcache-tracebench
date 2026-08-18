# SPDX-License-Identifier: Apache-2.0

"""Read-only L2 trace selection and logical KV payload estimation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

_GB_BYTES = 1_000_000_000
_OPERATION_NAMES = ("store", "lookup_task", "load_task", "unlock", "delete")


def read_trace_level(trace_path: str | Path) -> str:
    """Read the trace header level through the installed LMCache runtime."""
    from lmcache.v1.mp_observability.trace.reader import TraceReader

    with TraceReader(str(trace_path)) as reader:
        return reader.header.level


def _load_l2_plan(trace_path: str | Path, trace_percent: float) -> Any:
    from lmcache.cli.commands.trace.l2_driver import L2TracePlan

    return L2TracePlan.from_file(
        str(trace_path),
        trace_percent=trace_percent,
    )


def _gb(value: int) -> float:
    return round(value / _GB_BYTES, 3)


def summarize_l2_plan(
    plan: Any,
    *,
    trace_path: str | Path,
) -> dict[str, object]:
    """Estimate logical namespace payload for one selected L2 prefix.

    Stores are assumed to succeed and become visible in submission order.
    Deletes remove currently known keys. This preserves overwrite behavior
    while remaining a read-only capacity estimate rather than a target result.
    """
    operations = list(plan.operations)
    operation_counts = Counter(op.operation for op in operations)
    state = {key: int(size) for key, size in plan.prepare_objects.items()}
    candidate_sizes = dict(state)
    current_size = sum(state.values())
    peak_size = current_size
    peak_objects = len(state)
    store_submission_size = 0

    for operation in operations:
        if operation.operation == "store":
            keys = list(operation.args["keys"])
            sizes = [int(size) for size in operation.args["object_sizes"]]
            if len(keys) != len(sizes):
                raise ValueError(
                    "L2 store submission has mismatched keys and object_sizes"
                )
            store_submission_size += sum(sizes)
            for key, size in zip(keys, sizes, strict=True):
                current_size += size - state.get(key, 0)
                state[key] = size
                candidate_sizes[key] = max(size, candidate_sizes.get(key, 0))
        elif operation.operation == "delete":
            for key in operation.args["keys"]:
                current_size -= state.pop(key, 0)
        if current_size > peak_size:
            peak_size = current_size
            peak_objects = len(state)

    source_window = (
        operations[-1].t_mono - operations[0].t_mono
        if len(operations) > 1
        else 0.0
    )
    return {
        "schema_version": 1,
        "trace_path": str(Path(trace_path).expanduser()),
        "trace_percent": float(plan.trace_percent),
        "source_operations_total": int(plan.source_operations_total),
        "operations_selected": len(operations),
        "operations_selected_by_type": {
            name: operation_counts.get(name, 0) for name in _OPERATION_NAMES
        },
        "source_submission_window_seconds": source_window,
        "logical_kv_estimate": {
            "unit": "GB",
            "after_prepare_gb": _gb(sum(plan.prepare_objects.values())),
            "store_submission_gb": _gb(store_submission_size),
            "unique_candidate_gb": _gb(sum(candidate_sizes.values())),
            "peak_gb": _gb(peak_size),
            "final_gb": _gb(sum(state.values())),
            "after_prepare_objects": len(plan.prepare_objects),
            "peak_objects": peak_objects,
            "final_objects": len(state),
        },
        "assumptions": [
            "All selected target store submissions succeed.",
            "Store visibility and delete effects follow source submission order.",
            "Values are logical KV payload estimates, not filesystem allocation.",
        ],
    }


def analyze_l2_trace(
    trace_path: str | Path,
    trace_percent: float,
) -> dict[str, object]:
    """Parse an L2 trace and return its selected-prefix preflight summary."""
    plan = _load_l2_plan(trace_path, trace_percent)
    return summarize_l2_plan(plan, trace_path=trace_path)


def print_l2_preflight(summary: dict[str, object]) -> None:
    """Print the selected operation mix and logical capacity estimate in GB."""
    counts = summary["operations_selected_by_type"]
    estimate = summary["logical_kv_estimate"]
    assert isinstance(counts, dict)
    assert isinstance(estimate, dict)
    print(
        "[INFO] L2 preflight: "
        f"selected={summary['operations_selected']}/"
        f"{summary['source_operations_total']} "
        f"trace_percent={summary['trace_percent']:g}%"
    )
    print(
        "[INFO] Selected operations: "
        + " ".join(f"{name}={counts[name]}" for name in _OPERATION_NAMES)
    )
    print(
        "[INFO] Estimated logical KV payload: "
        f"after_prepare={estimate['after_prepare_gb']:.3f} GB "
        f"peak={estimate['peak_gb']:.3f} GB "
        f"final={estimate['final_gb']:.3f} GB "
        f"unique_candidate={estimate['unique_candidate_gb']:.3f} GB "
        f"store_submission={estimate['store_submission_gb']:.3f} GB"
    )


def run_l2_preflight(
    trace_path: str | Path,
    trace_percent: float,
    *,
    output_dir: Path | None = None,
) -> dict[str, object]:
    """Analyze and print an L2 prefix, optionally persisting its JSON artifact."""
    summary = analyze_l2_trace(trace_path, trace_percent)
    print_l2_preflight(summary)
    if output_dir is not None:
        output_path = output_dir / "l2_preflight.json"
        temporary_path = output_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(output_path)
        print(f"[INFO] L2 preflight artifact: {output_path}")
    return summary
