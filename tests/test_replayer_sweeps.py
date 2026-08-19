import re
import subprocess
from pathlib import Path

SWEEP_SCRIPT = Path(__file__).parents[1] / "benchmarks/replayer/replay_speed_sweep.sh"


def _run_reset_l2_root(path: Path, label: str = "L2 root") -> subprocess.CompletedProcess:
    # Exercise the real reset_l2_root() function body (extracted from the
    # production script) instead of re-implementing its logic in the test.
    function_body = subprocess.run(
        ["sed", "-n", "/^reset_l2_root() {/,/^}/p", str(SWEEP_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    script = (
        'die() { echo "[ERROR] $*" >&2; exit 1; }\n'
        f"{function_body}\n"
        'reset_l2_root "$1" "$2"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "reset_l2_root_test", str(path), label],
        capture_output=True,
        text=True,
    )


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


def test_reset_l2_root_clears_contents_without_recreating_the_directory(tmp_path):
    l2_root = tmp_path / "l2"
    l2_root.mkdir()
    (l2_root / "stale.data").write_text("leftover")
    (l2_root / "nested").mkdir()
    (l2_root / "nested/file.data").write_text("leftover")
    inode_before = l2_root.stat().st_ino

    result = _run_reset_l2_root(l2_root)

    assert result.returncode == 0, result.stderr
    # The directory node itself must survive untouched (same inode) so a real
    # mount at this path is never rmdir'd -- only its contents are cleared.
    assert l2_root.stat().st_ino == inode_before
    assert list(l2_root.iterdir()) == []


def test_reset_l2_root_creates_a_missing_directory(tmp_path):
    l2_root = tmp_path / "l2"

    result = _run_reset_l2_root(l2_root)

    assert result.returncode == 0, result.stderr
    assert l2_root.is_dir()


def test_reset_l2_root_resolves_a_symlink_and_clears_target_contents(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "stale.data").write_text("leftover")
    l2_root = tmp_path / "l2-link"
    l2_root.symlink_to(real_dir)

    result = _run_reset_l2_root(l2_root)

    assert result.returncode == 0, result.stderr
    # The symlink and its target directory must survive; only the target's
    # contents are cleared, mirroring the mountpoint-safe behavior.
    assert l2_root.is_symlink()
    assert real_dir.is_dir()
    assert list(real_dir.iterdir()) == []


def test_reset_l2_root_refuses_a_dangling_symlink(tmp_path):
    l2_root = tmp_path / "l2-link"
    l2_root.symlink_to(tmp_path / "nonexistent")

    result = _run_reset_l2_root(l2_root)

    assert result.returncode != 0
    assert "symlink target is not a directory" in result.stderr


def test_reset_l2_root_refuses_filesystem_root(tmp_path):
    result = _run_reset_l2_root(Path("/"))

    assert result.returncode != 0
    assert "must not be the filesystem root" in result.stderr
