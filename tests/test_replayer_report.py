import csv
import json

from replayer.report import render_l2_summary, write_l2_summary, write_speed_sweep_csv


def _stats(speedup: float = 2.0) -> dict:
    return {
        "speedup": speedup,
        "operations_selected": 30,
        "source_submission_window_seconds": 10.0,
        "actual_submission_window_seconds": 10.25,
        "total_replay_seconds": 11.0,
        "drain_seconds": 0.75,
        "total_bytes_submitted": 3_000_000_000,
        "throughput_bytes_per_second": 272_727_272.7,
        "mean_schedule_lag_seconds": 0.02,
        "max_schedule_lag_seconds": 0.5,
        "max_dependency_wait_seconds": 0.4,
        "total_dependency_wait_seconds": 2.0,
        "max_buffer_wait_seconds": 0.1,
        "total_buffer_wait_seconds": 0.8,
        "outcome_comparisons": {"store": 20, "load_task": 10},
        "outcome_mismatch_count": 1,
        "outcome_mismatch_rate": 1 / 30,
        "operations": {
            "read": {
                "ExampleAdapter": {
                    "submitted": 10,
                    "completed": 10,
                    "total_bytes": 1_000_000_000,
                    "average_latency_us": 2_000,
                    "p50_latency_us": 1_500,
                    "p90_latency_us": 3_000,
                    "p99_latency_us": 4_000,
                    "aggregate_throughput_gbps": 50.0,
                }
            },
            "write": {
                "ExampleAdapter": {
                    "submitted": 20,
                    "completed": 20,
                    "total_bytes": 2_000_000_000,
                    "average_latency_us": 4_000,
                    "p50_latency_us": 3_500,
                    "p90_latency_us": 5_000,
                    "p99_latency_us": 6_000,
                    "aggregate_throughput_gbps": 25.0,
                }
            },
        },
    }


def test_l2_summary_explains_key_results(tmp_path):
    stats_path = tmp_path / "l2_replay_stats.json"
    stats_path.write_text(json.dumps(_stats()), encoding="utf-8")

    summary_path = write_l2_summary(stats_path)
    summary = summary_path.read_text(encoding="utf-8")

    assert summary_path.name == "l2_replay_summary.md"
    assert "| Wall throughput | 0.273 GB/s |" in summary
    assert "| Mean schedule lag | 20.000 ms |" in summary
    assert "| Read | ExampleAdapter | 10 | 10 | 1.000 GB |" in summary
    assert "Outcome mismatch is a diagnostic metric" in summary


def test_render_l2_summary_accepts_missing_operation_stats():
    summary = render_l2_summary({"speedup": 1, "outcome_comparisons": {}})

    assert "| - | - | 0 | 0 | 0 B |" in summary


def test_speed_sweep_csv_flattens_successful_cases(tmp_path):
    x1 = tmp_path / "x1"
    x2 = tmp_path / "x2"
    x1.mkdir()
    x2.mkdir()
    (x1 / "l2_replay_stats.json").write_text(
        json.dumps(_stats(speedup=1.0)), encoding="utf-8"
    )
    (x2 / "l2_replay_stats.json").write_text(
        json.dumps(_stats(speedup=2.0)), encoding="utf-8"
    )
    results_path = tmp_path / "sweep-results.jsonl"
    results = [
        {"speedup": 1.0, "status": "ok", "output_dir": str(x1)},
        {"speedup": 2.0, "status": "ok", "output_dir": str(x2)},
        {"speedup": 4.0, "status": "failed", "output_dir": str(tmp_path / "x4")},
    ]
    results_path.write_text(
        "".join(json.dumps(item) + "\n" for item in results), encoding="utf-8"
    )

    csv_path = write_speed_sweep_csv(results_path)
    with csv_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert [row["speedup"] for row in rows] == ["1.0", "2.0"]
    assert rows[0]["wall_throughput_gb_per_second"] == "0.2727272727"
    assert rows[0]["read_average_latency_ms"] == "2.0"
    assert rows[1]["output_dir"] == str(x2)
