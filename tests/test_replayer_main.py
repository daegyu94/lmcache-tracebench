import replayer.runner as runner_module
from replayer.config import apply_overrides, load_config
from replayer.main import main
from replayer.runner import (
    _l2_progress_from_log_line,
    _progress_from_log_line,
    build_command,
)


def test_trace_option_is_reflected_in_dry_run(capsys, tmp_path):
    trace = tmp_path / "storage.lct"
    trace.touch()

    assert (
        main(
            [
                "--trace",
                str(trace),
                "--config",
                "configs/replayer/smoke.yaml",
                "--dry-run",
            ]
        )
        == 0
    )

    assert str(trace) in capsys.readouterr().out


def test_path_overrides_are_reflected_in_dry_run(capsys, tmp_path):
    trace = tmp_path / "storage.lct"
    trace.touch()
    base_path = tmp_path / "l2"
    output_dir = tmp_path / "output"

    assert (
        main(
            [
                "--trace",
                str(trace),
                "--config",
                "configs/replayer/smoke.yaml",
                "--l2-path",
                str(base_path),
                "--output-dir",
                str(output_dir),
                "--dry-run",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert f'"base_path":"{base_path}"' in output
    assert f"--output-dir {output_dir}" in output


def test_speedup_override_is_reflected_in_dry_run(capsys, tmp_path):
    trace = tmp_path / "storage.lct"
    trace.touch()

    assert (
        main(
            [
                "--trace",
                str(trace),
                "--config",
                "configs/replayer/smoke.yaml",
                "--speedup",
                "5",
                "--dry-run",
            ]
        )
        == 0
    )

    assert "--speedup 5.0" in capsys.readouterr().out


def test_trace_percent_override_is_reflected_in_dry_run(capsys, tmp_path):
    trace = tmp_path / "l2.lct"
    trace.touch()

    assert (
        main(
            [
                "--trace",
                str(trace),
                "--config",
                "configs/replayer/smoke.yaml",
                "--trace-percent",
                "10",
                "--dry-run",
            ]
        )
        == 0
    )

    assert "--trace-percent 10.0" in capsys.readouterr().out


def test_l1_size_overrides_are_reflected_in_dry_run(capsys, tmp_path):
    trace = tmp_path / "storage.lct"
    trace.touch()

    assert (
        main(
            [
                "--trace",
                str(trace),
                "--config",
                "configs/replayer/smoke.yaml",
                "--l1-size-gb",
                "40",
                "--l1-init-size-gb",
                "40",
                "--dry-run",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "--l1-size-gb 40.0" in output
    assert "--l1-init-size-gb 40" in output


def test_backend_configs_extend_common_base():
    fs_native = load_config("configs/replayer/fs-native.yaml")
    nixl_hf3fs = load_config("configs/replayer/nixl-hf3fs.yaml")

    assert fs_native.l2_store_policy == "skip_l1"
    assert fs_native.l2_adapter["type"] == "fs_native"
    assert fs_native.l2_adapter["use_odirect"] is True
    assert fs_native.l2_adapter["io_log_interval_sec"] == 5
    assert nixl_hf3fs.l2_adapter["type"] == "nixl_store_dynamic"
    assert nixl_hf3fs.l2_adapter["backend_params"]["use_direct_io"] == "true"
    assert "HF3FS" in " ".join(build_command(nixl_hf3fs, "storage.lct"))


def test_replay_progress_is_parsed_from_lmcache_log_line():
    assert _progress_from_log_line(
        "[2026] [21/3508] OK StorageManager.finish_write"
    ) == (
        21,
        3508,
    )
    assert _progress_from_log_line("LMCache startup message") is None


def test_l2_replay_progress_is_parsed_from_lmcache_log_line():
    line = (
        "LMCache INFO: L2 replay progress: elapsed=10.0s "
        "dispatched=75/100 completed=70 pending=25 "
        "in_flight(store=2 lookup=1 load=2) bytes_submitted=1073741824"
    )

    assert _l2_progress_from_log_line(line) == (
        10.0,
        75,
        100,
        70,
        25,
        2,
        1,
        2,
        1073741824,
    )
    assert _l2_progress_from_log_line("LMCache startup message") is None


def test_l2_replay_reports_start_and_submission_progress(capsys, monkeypatch, tmp_path):
    class FakeProcess:
        stdout = iter(
            [
                (
                    "L2 replay progress: elapsed=5.0s dispatched=75/100 "
                    "completed=70 pending=25 "
                    "in_flight(store=2 lookup=1 load=2) "
                    "bytes_submitted=1073741824\n"
                )
            ]
        )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def wait(self):
            return 0

    trace = tmp_path / "l2.lct"
    trace.touch()
    output_dir = tmp_path / "output"
    config = apply_overrides(
        load_config("configs/replayer/smoke.yaml"),
        output_dir=str(output_dir),
    )
    monkeypatch.setattr(runner_module, "_read_trace_level", lambda _: "l2")
    monkeypatch.setattr(runner_module, "_run_prepare", lambda *_: 0)
    monkeypatch.setattr(
        runner_module.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )

    assert runner_module.run_command(config, str(trace)) == 0

    output = capsys.readouterr().out
    assert "[INFO] Starting L2 replay." in output
    assert "L2 submitted=75/100 (75.0%)" in output
    assert "completed=70 pending=25 in_flight=5" in output
    assert "[INFO] Replay complete." in output
