import json
from pathlib import Path

import pytest

from report.generate_dummy_data import build_records
from report.import_artifacts import import_artifacts
from report.report_data import load_dataset, write_dataset


def _write_matrix(path: Path, case: dict) -> None:
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")


def _base_case(result_dir: Path, graph: str) -> dict:
    return {
        "case_id": f"{graph}/tensormesh-swebench/xfs/baseline/2/r1",
        "status": "ok",
        "graph": graph,
        "workload": "tensormesh-swebench",
        "backend": "xfs",
        "node_count": "baseline",
        "speedup": 2.0,
        "repeat": 1,
        "result_dir": str(result_dir),
    }


def test_dummy_dataset_round_trip(tmp_path):
    output = tmp_path / "dummy"
    records = build_records()

    write_dataset(
        output,
        kind="dummy",
        source={"test": True},
        records=records,
    )
    dataset = load_dataset(output)

    assert dataset.kind == "dummy"
    assert len(dataset.records) == len(records)
    assert dataset.select(
        graph="speedup",
        workload="SWE-bench",
        backend="3FS",
        speedup=2.0,
        metric="wall_throughput_gb_per_second",
    )


def test_import_speedup_artifacts(tmp_path):
    result_root = tmp_path / "result"
    case_root = result_root / "x2"
    case_root.mkdir(parents=True)
    (case_root / "l2_replay_stats.json").write_text(
        json.dumps(
            {
                "throughput_bytes_per_second": 2_500_000_000,
                "max_schedule_lag_seconds": 0.25,
                "operations": {
                    "read": {
                        "FSNativeL2Adapter": {
                            "p99_latency_us": 3200,
                        }
                    },
                    "write": {},
                },
            }
        ),
        encoding="utf-8",
    )
    matrix = tmp_path / "matrix-results.jsonl"
    _write_matrix(matrix, _base_case(result_root, "speedup"))

    output = tmp_path / "measured"
    imported, warnings = import_artifacts(
        matrix,
        output_dir=output,
        network_rates={},
        allow_incomplete=False,
    )
    dataset = load_dataset(output)

    assert imported == 1
    assert warnings == []
    assert dataset.kind == "measured"
    throughput = dataset.select(
        graph="speedup",
        workload="SWE-bench",
        backend="xfs",
        speedup=2.0,
        metric="wall_throughput_gb_per_second",
    )
    assert [record.value for record in throughput] == [2.5]
    latency = dataset.select(
        graph="speedup",
        workload="SWE-bench",
        backend="xfs",
        speedup=2.0,
        metric="read_p99_latency_ms",
    )
    assert [record.value for record in latency] == [3.2]


def test_import_profile_artifacts(tmp_path):
    result_root = tmp_path / "result"
    case_root = result_root / "x2"
    node_root = case_root / "profile" / "storage-1"
    node_root.mkdir(parents=True)
    (case_root / "l2_replay_stats.json").write_text("{}\n", encoding="utf-8")
    (node_root / "summary.json").write_text(
        json.dumps({"node": "storage-1", "role": "storage"}),
        encoding="utf-8",
    )
    (node_root / "disk.tsv").write_text(
        "timestamp\telapsed_s\tinterval_s\tdevice\tread_bytes\twrite_bytes"
        "\tread_iops\twrite_iops\tread_mibps\twrite_mibps"
        "\tio_util_percent\n"
        "t1\t1\t1\tnvme0\t0\t0\t0\t0\t0\t0\t40\n"
        "t2\t2\t1\tnvme0\t0\t0\t0\t0\t0\t0\t80\n",
        encoding="utf-8",
    )
    (node_root / "network.tsv").write_text(
        "timestamp\telapsed_s\tinterval_s\tinterface\trx_bytes\ttx_bytes"
        "\trx_packets\ttx_packets\trx_mibps\ttx_mibps\trx_pps\ttx_pps"
        "\trx_errors\ttx_errors\trx_drops\ttx_drops\n"
        "t1\t1\t1\teth0\t0\t0\t0\t0\t100\t200\t0\t0\t0\t0\t0\t0\n"
        "t2\t2\t1\teth0\t0\t0\t0\t0\t300\t400\t0\t0\t0\t0\t0\t0\n",
        encoding="utf-8",
    )
    matrix = tmp_path / "matrix-results.jsonl"
    case = _base_case(result_root, "nodewise")
    case["backend"] = "3FS"
    case["case_id"] = "nodewise/tensormesh-swebench/3FS/baseline/2/r1"
    _write_matrix(matrix, case)

    output = tmp_path / "measured"
    imported, warnings = import_artifacts(
        matrix,
        output_dir=output,
        network_rates={"3FS": 100.0},
        allow_incomplete=False,
    )
    dataset = load_dataset(output)

    assert imported == 1
    assert warnings == []
    disk = dataset.select(
        graph="nodewise",
        workload="SWE-bench",
        backend="3FS",
        speedup=2.0,
        node="storage-1",
        metric="disk_utilization_p95_percent",
    )
    assert disk[0].value == pytest.approx(78.0)
    network = dataset.select(
        graph="nodewise",
        workload="SWE-bench",
        backend="3FS",
        speedup=2.0,
        node="storage-1",
        metric="network_utilization_p95_percent",
    )
    expected_mibps = 200 * 0.05 + 400 * 0.95
    expected_percent = expected_mibps * 1024**2 * 8 / (100 * 1e9) * 100
    assert network[0].value == pytest.approx(expected_percent)
