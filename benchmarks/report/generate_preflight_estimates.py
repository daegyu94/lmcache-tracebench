# SPDX-License-Identifier: Apache-2.0
"""Generate workload preflight tables and trace-percent presets.

The generator reads the same L2 ``.lct`` files used by the replay runner and
calls :func:`replayer.preflight.analyze_l2_trace`.  The resulting Markdown is
intended to answer two questions before a remote run starts:

* how many operations and how much logical KV payload are selected at a fixed
  ``trace_percent``; and
* which conservative ``trace_percent`` is suitable for a logical KV target.

The target values use decimal units (1 TB = 1,000 GB), matching the preflight
GB calculation.  A generated preset is tied to the trace checksums recorded in
the JSON output; regenerate it when the source traces change.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from replayer.preflight import analyze_l2_trace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKDOWN = Path(__file__).with_name("preflight-estimates.md")
DEFAULT_JSON = Path(__file__).with_name("preflight-estimates.json")
DEFAULT_PRESETS = Path(__file__).with_name("workload-presets.json")

WORKLOADS = (
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
DEFAULT_PERCENTAGES = (20.0, 40.0, 60.0, 80.0, 100.0)
DEFAULT_TARGETS_GB = {
    "0.5tb": 500.0,
    "1tb": 1_000.0,
    "2tb": 2_000.0,
    "4tb": 4_000.0,
}
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Target:
    name: str
    gb: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_csv(raw: str, option: str) -> tuple[float, ...]:
    values: list[float] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            raise ValueError(f"{option} contains an empty value")
        value = float(item)
        if not math.isfinite(value) or not 0 < value <= 100:
            raise ValueError(f"{option} must be finite and in (0, 100]: {item}")
        values.append(value)
    if not values:
        raise ValueError(f"{option} must not be empty")
    return tuple(dict.fromkeys(values))


def _parse_targets(raw_values: list[str]) -> tuple[Target, ...]:
    values = raw_values or [f"{name}={gb:g}" for name, gb in DEFAULT_TARGETS_GB.items()]
    targets: list[Target] = []
    for raw in values:
        if "=" not in raw:
            raise ValueError(f"target must use NAME=GB: {raw}")
        name, raw_gb = raw.split("=", 1)
        name = name.strip().lower()
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"invalid target name: {name}")
        gb = float(raw_gb)
        if not math.isfinite(gb) or gb <= 0:
            raise ValueError(f"target GB must be positive and finite: {raw}")
        targets.append(Target(name, gb))
    return tuple(targets)


def _peak(summary: dict[str, Any]) -> float:
    return float(summary["logical_kv_estimate"]["peak_gb"])


def _floor_percent(value: float) -> float:
    # Two decimal places avoid rounding a conservative target estimate upward.
    return math.floor(max(value, 0.01) * 100.0) / 100.0


def _interpolated_percent(
    rows: list[dict[str, Any]],
    target_gb: float,
) -> float:
    """Return a safe first guess from the fixed percentage table.

    Peak logical footprint is a prefix maximum, so it is monotonic even when
    individual store/delete operations fluctuate.  The guess is deliberately
    reduced by five percent; the selected point is then checked with a real
    preflight parse before it is emitted.
    """

    points = [(0.0, 0.0)] + [
        (float(row["trace_percent"]), _peak(row)) for row in rows
    ]
    if target_gb >= points[-1][1]:
        return 100.0
    for (left_percent, left_peak), (right_percent, right_peak) in itertools.pairwise(
        points
    ):
        if target_gb <= right_peak:
            if right_peak <= left_peak:
                guess = left_percent
            else:
                fraction = (target_gb - left_peak) / (right_peak - left_peak)
                guess = left_percent + fraction * (right_percent - left_percent)
            return _floor_percent(guess * 0.95)
    return 100.0


def _interpolated_peak(
    rows: list[dict[str, Any]],
    percent: float,
) -> float:
    points = [(0.0, 0.0)] + [
        (float(row["trace_percent"]), _peak(row)) for row in rows
    ]
    for (left_percent, left_peak), (right_percent, right_peak) in itertools.pairwise(
        points
    ):
        if percent <= right_percent:
            if right_percent <= left_percent:
                return left_peak
            fraction = (percent - left_percent) / (right_percent - left_percent)
            return left_peak + fraction * (right_peak - left_peak)
    return points[-1][1]


def _interpolated_value(
    rows: list[dict[str, Any]],
    percent: float,
    field: str,
) -> float:
    """Interpolate a scalar preflight field at a selected prefix."""
    points = [(0.0, 0.0)] + [
        (float(row["trace_percent"]), float(row[field])) for row in rows
    ]
    for (left_percent, left_value), (right_percent, right_value) in itertools.pairwise(
        points
    ):
        if percent <= right_percent:
            if right_percent <= left_percent:
                return left_value
            fraction = (percent - left_percent) / (right_percent - left_percent)
            return left_value + fraction * (right_value - left_value)
    return points[-1][1]


def _select_target(
    trace_path: Path,
    rows: list[dict[str, Any]],
    target: Target,
    cache: dict[float, dict[str, Any]],
    *,
    validate: bool,
) -> dict[str, Any]:
    full = rows[-1]
    if _peak(full) <= target.gb:
        return {
            "target_gb": target.gb,
            "trace_percent": 100.0,
            "estimated_peak_gb": _peak(full),
            "source_submission_window_seconds": float(
                full["source_submission_window_seconds"]
            ),
            "within_target": True,
            "selection": "full trace is already within target",
        }

    percent = _interpolated_percent(rows, target.gb)
    estimated_peak = _interpolated_peak(rows, percent)
    source_window = _interpolated_value(
        rows, percent, "source_submission_window_seconds"
    )
    measured_peak = None
    operations_selected = None
    if validate:
        if percent not in cache:
            cache[percent] = analyze_l2_trace(trace_path, percent)
        summary = cache[percent]
        measured_peak = _peak(summary)
        estimated_peak = measured_peak
        source_window = float(summary["source_submission_window_seconds"])
        operations_selected = summary["operations_selected"]
    return {
        "target_gb": target.gb,
        "trace_percent": percent,
        "estimated_peak_gb": estimated_peak,
        "source_submission_window_seconds": source_window,
        "within_target": estimated_peak <= target.gb,
        "selection": (
            "measured prefix with interpolation headroom"
            if validate
            else "interpolated from fixed-percent preflight rows with five percent headroom"
        ),
        "operations_selected": operations_selected,
        **({"measured_peak_gb": measured_peak} if validate else {}),
    }


def _workload_path(trace_root: Path, workload: str, trace_name: str) -> Path:
    return trace_root / TRACE_DIRS[workload] / trace_name


def _public_summary(summary: dict[str, Any], workload: str, trace_name: str) -> dict[str, Any]:
    result = dict(summary)
    result["trace_path"] = f"{TRACE_DIRS[workload]}/{trace_name}"
    return result


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _markdown(
    *,
    source_revision: str,
    generated_at: str,
    records: dict[str, dict[str, Any]],
    targets: tuple[Target, ...],
    presets: dict[str, dict[str, Any]],
) -> str:
    lines = [
        "# Workload preflight estimates",
        "",
        (
            "이 문서는 현재 `l2.lct`를 `trace_percent` prefix로 선택했을 때의 "
            "operation 수와 logical KV payload를 사전에 확인하기 위한 자료다."
        ),
        (
            "실제 replay 전의 용량 계획에 사용하며, filesystem allocation이나 backend "
            "metadata 오버헤드는 포함하지 않는다."
        ),
        "",
        f"- Generated: `{generated_at}`",
        f"- Trace root: `{source_revision}`",
        "- Unit: decimal GB/TB (`1 TB = 1,000 GB`)",
        "- Capacity column: prefix replay의 `peak_gb`",
        "- Assumption: selected store가 성공하고 submission 순서대로 overwrite/delete가 반영됨",
        "- Preset target mapping: fixed-percent preflight row interpolation with five percent headroom",
        "- Duration estimate: source first-to-last submission window divided by replay speedup; schedule lower bound only",
        (
            "- `source window s` is the raw timestamp difference between the first and "
            "last selected submission; for speedup S, the schedule lower bound is "
            "`source window / S`"
        ),
        (
            "- Target preset windows are interpolated from fixed-percent rows unless "
            "`--validate-targets` is used"
        ),
        "",
        "## Fixed trace-percent estimates",
        "",
    ]
    for workload in WORKLOADS:
        record = records[workload]
        lines.extend(
            [
                f"### `{workload}`",
                "",
                "| trace_percent | selected operations | store | lookup | load | unlock | delete | source window s | peak GB | final GB |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in record["percentages"]:
            counts = row["operations_selected_by_type"]
            estimate = row["logical_kv_estimate"]
            lines.append(
                "| {percent:g}% | {selected:,} | {store:,} | {lookup:,} | {load:,} | "
                "{unlock:,} | {delete:,} | {window} | {peak} | {final} |".format(
                    percent=float(row["trace_percent"]),
                    selected=int(row["operations_selected"]),
                    store=int(counts["store"]),
                    lookup=int(counts["lookup_task"]),
                    load=int(counts["load_task"]),
                    unlock=int(counts["unlock"]),
                    delete=int(counts["delete"]),
                    window=_fmt(float(row["source_submission_window_seconds"])),
                    peak=_fmt(float(estimate["peak_gb"])),
                    final=_fmt(float(estimate["final_gb"])),
                )
            )
        lines.extend(["", f"Trace SHA-256: `{record['sha256']}`", ""])

    lines.extend(
        [
            "## Presets",
            "",
            (
                "`full`은 모든 canonical workload에서 full trace를 사용한다."
            ),
            (
                "엄격하게 모든 workload를 target 안에 넣으려면 `0.5tb`, `1tb`, `2tb`, "
                "`4tb` preset을 사용한다."
            ),
            "",
            "| preset | workload | target GB | trace_percent | source window s | estimated peak GB |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for preset_name, preset in presets.items():
        for workload in WORKLOADS:
            item = preset["workloads"][workload]
            target = item.get("target_gb")
            lines.append(
                "| `{preset}` | `{workload}` | {target} | {percent:g}% | {window} | {peak} |".format(
                    preset=preset_name,
                    workload=workload,
                    target="full" if target is None else _fmt(float(target)),
                    percent=float(item["trace_percent"]),
                    window=_fmt(float(item["source_submission_window_seconds"])),
                    peak=_fmt(float(item["estimated_peak_gb"])),
                )
            )
    lines.extend(
        [
            "",
            "### Usage",
            "",
            "```bash",
            "# Full-trace preset for all workloads",
            "bash benchmarks/report/run_report_experiments.sh \\",
            "  --topology configs/replayer/staged-remote/b300.yaml \\",
            "  --graph speedup \\",
            "  --workload-preset full \\",
            "  --backend-spec 'fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native'",
            "```",
            "",
            (
                "Preset은 이 문서와 함께 생성된 `workload-presets.json`의 trace checksum을 "
                "기준으로 한다. trace archive가 바뀌면 이 generator를 다시 실행한다."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def generate(args: argparse.Namespace) -> None:
    trace_root = Path(args.trace_root).expanduser().resolve()
    percentages = _parse_csv(args.percentages, "--percentages")
    targets = _parse_targets(args.target)
    workloads = (
        tuple(item.strip() for item in args.workloads.split(",") if item.strip())
        if args.workloads
        else WORKLOADS
    )
    unknown = set(workloads) - set(WORKLOADS)
    if unknown:
        raise ValueError(f"unknown workload(s): {', '.join(sorted(unknown))}")

    records: dict[str, dict[str, Any]] = {}
    input_records: dict[str, dict[str, Any]] = {}
    if args.input_json:
        input_payload = json.loads(
            Path(args.input_json).expanduser().read_text(encoding="utf-8")
        )
        input_records = input_payload.get("workloads", input_payload)
        if not isinstance(input_records, dict):
            raise ValueError("--input-json workloads must be an object")
    target_records: dict[str, dict[str, dict[str, Any]]] = {}
    for workload in workloads:
        trace_path = _workload_path(trace_root, workload, args.trace_name)
        if not trace_path.is_file():
            raise FileNotFoundError(f"trace not found: {trace_path}")
        print(f"[INFO] Analyzing {workload}: {trace_path}", flush=True)
        rows: list[dict[str, Any]] = []
        cache: dict[float, dict[str, Any]] = {}
        previous = input_records.get(workload)
        if isinstance(previous, list):
            previous_rows = previous
        elif isinstance(previous, dict):
            previous_rows = previous.get("percentages")
        else:
            previous_rows = None
        if previous_rows:
            previous_by_percent = {
                float(row["trace_percent"]): row for row in previous_rows
            }
            if all(percent in previous_by_percent for percent in percentages):
                rows = [
                    _public_summary(
                        previous_by_percent[percent], workload, args.trace_name
                    )
                    for percent in percentages
                ]
                cache.update(previous_by_percent)
                print("[INFO]   reusing fixed-percent rows from --input-json", flush=True)
        if not rows:
            for percent in percentages:
                print(f"[INFO]   trace_percent={percent:g}", flush=True)
                cache[percent] = analyze_l2_trace(trace_path, percent)
                rows.append(_public_summary(cache[percent], workload, args.trace_name))
        target_records[workload] = {}
        for target in targets:
            print(f"[INFO]   target={target.name} ({target.gb:g} GB)", flush=True)
            target_records[workload][target.name] = _select_target(
                trace_path,
                rows,
                target,
                cache,
                validate=args.validate_targets,
            )
        records[workload] = {
            "trace_path": f"{TRACE_DIRS[workload]}/{args.trace_name}",
            "sha256": _sha256(trace_path),
            "size_bytes": trace_path.stat().st_size,
            "percentages": rows,
            "targets": target_records[workload],
        }

    # A generated file always includes every canonical workload.  This keeps
    # preset lookup deterministic; a subset run is useful for debugging
    # but cannot replace the checked-in preset file.
    if set(records) != set(WORKLOADS):
        missing = ", ".join(sorted(set(WORKLOADS) - set(records)))
        raise ValueError(f"all canonical workloads are required; missing: {missing}")

    target_by_name = {target.name: target for target in targets}

    def strict_item(workload: str, target_name: str) -> dict[str, Any]:
        item = dict(records[workload]["targets"][target_name])
        item["target_gb"] = target_by_name[target_name].gb
        return item

    def full_item(workload: str) -> dict[str, Any]:
        full = records[workload]["percentages"][-1]
        return {
            "target_gb": None,
            "trace_percent": 100.0,
            "estimated_peak_gb": _peak(full),
            "source_submission_window_seconds": float(
                full["source_submission_window_seconds"]
            ),
            "within_target": True,
            "selection": "full trace",
        }

    presets: dict[str, dict[str, Any]] = {
        "full": {
            "description": "all canonical workloads use their full trace",
            "workloads": {workload: full_item(workload) for workload in WORKLOADS},
        }
    }
    for target in targets:
        presets[target.name] = {
            "description": f"strict logical KV target of {target.gb:g} GB for every workload",
            "target_gb": target.gb,
            "workloads": {
                workload: strict_item(workload, target.name) for workload in WORKLOADS
            },
        }

    generated_at = args.generated_at or "local preflight run"
    source_revision = args.source_revision or str(trace_root)
    document = _markdown(
        source_revision=source_revision,
        generated_at=generated_at,
        records=records,
        targets=targets,
        presets=presets,
    )
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_revision": source_revision,
        "unit": "decimal_gb",
        "percentages": list(percentages),
        "targets_gb": {target.name: target.gb for target in targets},
        "workloads": records,
        "presets": presets,
    }
    for path, content in (
        (Path(args.output_markdown), document),
        (Path(args.output_json), json.dumps(payload, indent=2, sort_keys=True) + "\n"),
        (
            Path(args.output_presets),
            json.dumps(
                {
                    "schema_version": 1,
                    "generated_at": generated_at,
                    "source_revision": source_revision,
                    "unit": "decimal_gb",
                    "workloads": records,
                    "presets": presets,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"[INFO] Wrote {path}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--trace-root",
        required=True,
        help="root containing tensormesh/{gaia,wildclaw,swebench}/l2.lct and mooncake/{toolagent,conversation}/l2.lct",
    )
    parser.add_argument("--trace-name", default="l2.lct")
    parser.add_argument(
        "--workloads",
        help="comma-separated canonical workload names (all five by default)",
    )
    parser.add_argument(
        "--percentages",
        default="20,40,60,80,100",
        help="fixed table percentages (default: 20,40,60,80,100)",
    )
    parser.add_argument(
        "--target",
        action="append",
        metavar="NAME=GB",
        help="logical target; repeatable (defaults: 0.5tb=500, 1tb=1000, 2tb=2000, 4tb=4000)",
    )
    parser.add_argument(
        "--input-json",
        help="reuse fixed-percent workload rows from an earlier generator JSON output",
    )
    parser.add_argument(
        "--validate-targets",
        action="store_true",
        help="reparse each selected target prefix for measured validation (slow for large traces)",
    )
    parser.add_argument("--source-revision", help="trace release or git revision label")
    parser.add_argument("--generated-at", help="recorded generation timestamp")
    parser.add_argument("--output-markdown", default=str(DEFAULT_MARKDOWN))
    parser.add_argument("--output-json", default=str(DEFAULT_JSON))
    parser.add_argument("--output-presets", default=str(DEFAULT_PRESETS))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        generate(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
