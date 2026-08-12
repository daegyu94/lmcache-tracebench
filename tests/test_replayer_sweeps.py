import re
import subprocess
from pathlib import Path


def test_speed_sweep_appends_timestamp_to_explicit_output_root(tmp_path):
    trace = tmp_path / "l2.lct"
    trace.touch()
    output_base = tmp_path / "outputs" / "wildclaw"

    result = subprocess.run(
        [
            "bash",
            "benchmarks/replayer/replay_speed_sweep.sh",
            "--trace",
            str(trace),
            "--config",
            "configs/replayer/fs-native.yaml",
            "--l2-root",
            str(tmp_path / "l2"),
            "--output-root",
            str(output_base),
            "--speedups",
            "1.5,2.5",
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    match = re.search(
        r"Sweep log: (.+/wildclaw-[0-9]{8}-[0-9]{6}/sweep\.log)",
        result.stdout,
    )
    assert match is not None
    assert Path(match.group(1)).is_file()


def test_speed_sweep_preserves_pre_timestamped_output_root(tmp_path):
    trace = tmp_path / "l2.lct"
    trace.touch()
    output_root = tmp_path / "outputs" / "wildclaw-20260812-134500"

    subprocess.run(
        [
            "bash",
            "benchmarks/replayer/replay_speed_sweep.sh",
            "--trace",
            str(trace),
            "--config",
            "configs/replayer/fs-native.yaml",
            "--l2-root",
            str(tmp_path / "l2"),
            "--output-root",
            str(output_root),
            "--speedups",
            "1",
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (output_root / "sweep.log").is_file()
