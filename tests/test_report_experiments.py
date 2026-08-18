import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "benchmarks/report/run_report_experiments.sh"
TOPOLOGY = ROOT / "configs/replayer/staged-remote/example.yaml"


def _command(state_root: Path) -> list[str]:
    return [
        "bash",
        str(RUNNER),
        "--topology",
        str(TOPOLOGY),
        "--graph",
        "speedup",
        "--backend-spec",
        "fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native",
        "--workloads",
        "tensormesh-swebench",
        "--speedups",
        "1,1.25",
        "--trace-percent",
        "10",
        "--repeats",
        "1",
        "--skip-prepare",
        "--dry-run",
        "--state-root",
        str(state_root),
    ]


def test_report_runner_dry_run_resumes_completed_cases(tmp_path):
    state_root = tmp_path / "report-state"

    first = subprocess.run(
        _command(state_root),
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Case speedup/tensormesh-swebench/fs-native/baseline/1/r1" in first.stdout
    assert "@TRACE_ROOT@/tensormesh/swebench/l2.lct" in first.stdout

    summary_path = state_root / "matrix-summary.json"
    first_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert first_summary["planned"] == 2
    assert first_summary["completed"] == 2
    assert first_summary["resume_skipped"] == 0

    plan = json.loads(
        (state_root / "matrix-plan.json").read_text(encoding="utf-8")
    )
    assert {case["trace_metadata"]["trace_percent"] for case in plan["cases"]} == {
        10.0
    }

    second = subprocess.run(
        _command(state_root),
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Case " not in second.stdout
    second_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert second_summary["completed"] == 2
    assert second_summary["resume_skipped"] == 2


def test_report_runner_full_preset_resolves_full_trace(tmp_path):
    state_root = tmp_path / "full-preset-state"
    command = [
        "bash",
        str(RUNNER),
        "--topology",
        str(TOPOLOGY),
        "--graph",
        "throughput",
        "--backend-spec",
        "fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native",
        "--workloads",
        "tensormesh-gaia,mooncake-toolagent",
        "--workload-preset",
        "full",
        "--repeats",
        "1",
        "--skip-prepare",
        "--dry-run",
        "--state-root",
        str(state_root),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    assert "--trace-percent 100" in result.stdout
    assert "Replay schedule estimate" in result.stdout
    assert "Minimum sequential replay schedule" in result.stdout

    plan = json.loads(
        (state_root / "matrix-plan.json").read_text(encoding="utf-8")
    )
    percents = {
        case["workload"]: case["trace_percent"] for case in plan["cases"]
    }
    assert percents == {
        "tensormesh-gaia": 100.0,
        "mooncake-toolagent": 100.0,
    }
    for case in plan["cases"]:
        assert case["source_submission_window_seconds"] > 0
        assert (
            case["duration_estimate"]["schedule_seconds"]
            == case["source_submission_window_seconds"] / case["speedup"]
        )

    run_config = json.loads(
        (state_root / "run-config.json").read_text(encoding="utf-8")
    )
    assert run_config["duration_estimate"]["estimated_case_count"] == 2
