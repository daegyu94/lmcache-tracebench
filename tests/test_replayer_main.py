import json

import replayer.main as main_module
import replayer.runner as runner_module
from replayer.config import apply_overrides, load_config
from replayer.main import main
from replayer.runner import (
    _l2_io_interval_from_log_line,
    _l2_namespace_path,
    _l2_progress_from_log_line,
    _measure_l2_namespace_usage,
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


def test_l2_namespace_usage_uses_configured_client_path(tmp_path):
    l2_path = tmp_path / "l2"
    l2_path.mkdir()
    (l2_path / "object").write_bytes(b"payload")
    config = apply_overrides(
        load_config("configs/replayer/smoke.yaml"),
        l2_path=str(l2_path),
    )

    assert _l2_namespace_path(config) == l2_path
    usage = _measure_l2_namespace_usage(l2_path)
    assert usage["measurement_status"] == "ok"
    assert usage["bytes"] >= len(b"payload")


def test_l2_namespace_usage_reports_missing_path(tmp_path):
    usage = _measure_l2_namespace_usage(tmp_path / "missing")

    assert usage["measurement_status"] == "missing"
    assert usage["bytes"] is None


def test_l2_namespace_path_uses_nixl_file_path():
    config = load_config("configs/replayer/nixl-hf3fs.yaml")

    assert str(_l2_namespace_path(config)) == "/mnt/3fs/lmcache-trace-replay"


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


def test_l2_dry_run_prints_preflight_estimate(capsys, monkeypatch, tmp_path):
    trace = tmp_path / "l2.lct"
    trace.write_bytes(b"trace")
    calls = []
    monkeypatch.setattr(main_module, "read_trace_level", lambda _: "l2")
    monkeypatch.setattr(
        main_module,
        "run_l2_preflight",
        lambda trace_path, trace_percent: calls.append(
            (trace_path, trace_percent)
        ),
    )

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

    assert calls == [(trace, 10.0)]
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


def test_io_profile_option_is_reflected_in_dry_run(capsys, tmp_path):
    trace = tmp_path / "storage.lct"
    trace.touch()

    assert (
        main(
            [
                "--trace",
                str(trace),
                "--config",
                "configs/replayer/smoke.yaml",
                "--io-profile",
                "configs/profiling/local.yaml",
                "--dry-run",
            ]
        )
        == 0
    )

    assert "Profiler: nodes=1 sample=1s report=1s" in capsys.readouterr().out


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


def test_l2_io_interval_is_parsed_from_lmcache_log_line():
    line = (
        "LMCache INFO: FS native I/O interval: elapsed=10.001s interval=5.000s "
        "total_ops=30 total_bytes=3221225472 total_GiB/s=0.600000 "
        "read_ops=10 read_bytes=1073741824 read_GiB/s=0.200000 "
        "write_ops=20 write_bytes=2147483648 write_GiB/s=0.400000"
    )

    assert _l2_io_interval_from_log_line(line) == (
        10.001,
        5.0,
        30,
        3221225472,
        0.6,
        10,
        1073741824,
        0.2,
        20,
        2147483648,
        0.4,
    )
    assert _l2_io_interval_from_log_line("LMCache startup message") is None


def test_l2_replay_reports_start_and_submission_progress(capsys, monkeypatch, tmp_path):
    class FakeProcess:
        stdout = iter(
            [
                (
                    "L2 replay progress: elapsed=5.0s dispatched=75/100 "
                    "completed=70 pending=25 "
                    "in_flight(store=2 lookup=1 load=2) "
                    "bytes_submitted=1073741824\n"
                ),
                (
                    "FS native I/O interval: elapsed=5.0s interval=5.0s "
                    "total_ops=12 total_bytes=1610612736 total_GiB/s=0.300000 "
                    "read_ops=4 read_bytes=536870912 read_GiB/s=0.100000 "
                    "write_ops=8 write_bytes=1073741824 write_GiB/s=0.200000\n"
                ),
                (
                    "L2 replay progress: elapsed=10.0s dispatched=100/100 "
                    "completed=100 pending=0 "
                    "in_flight(store=0 lookup=0 load=0) "
                    "bytes_submitted=2147483648\n"
                ),
                (
                    "FS native I/O interval: elapsed=10.0s interval=5.0s "
                    "total_ops=9 total_bytes=805306368 total_GiB/s=0.150000 "
                    "read_ops=3 read_bytes=268435456 read_GiB/s=0.050000 "
                    "write_ops=6 write_bytes=536870912 write_GiB/s=0.100000\n"
                ),
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
    l2_path = tmp_path / "l2"
    l2_path.mkdir()
    config = apply_overrides(
        load_config("configs/replayer/smoke.yaml"),
        l2_path=str(l2_path),
        output_dir=str(output_dir),
    )
    monkeypatch.setattr(runner_module, "read_trace_level", lambda _: "l2")
    monkeypatch.setattr(runner_module, "run_l2_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner_module, "_run_prepare", lambda *_: 0)
    monkeypatch.setattr(
        runner_module,
        "_measure_l2_namespace_usage",
        lambda _: {
            "bytes": 7,
            "gb": 0.0,
            "gib": 0.0,
            "measurement_status": "ok",
            "measured_at_utc": "2026-08-18T00:00:00+00:00",
        },
    )
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
    assert "submitted_GiB" not in output
    assert "L2 submitted=100/100 (100.0%)" in output
    assert output.count("\r[progress]") == 2
    progress_output = output[output.index("\r[progress]") :]
    progress_output = progress_output[: progress_output.index("\n")]
    assert "\n" not in progress_output
    assert "[INFO] Replay complete." in output
    usage = json.loads((output_dir / "l2_usage.json").read_text())
    assert usage["scope"] == "client_visible_namespace"
    assert usage["path"] == str(l2_path)
    assert usage["after_prepare"]["measurement_status"] == "ok"
    assert usage["after_replay"]["measurement_status"] == "ok"


def test_l2_replay_writes_interval_io_tsv(monkeypatch, tmp_path):
    class FakeProcess:
        stdout = iter(
            [
                (
                    "FS native I/O interval: elapsed=5.0s interval=5.0s "
                    "total_ops=12 total_bytes=1610612736 total_GiB/s=0.300000 "
                    "read_ops=4 read_bytes=536870912 read_GiB/s=0.100000 "
                    "write_ops=8 write_bytes=1073741824 write_GiB/s=0.200000\n"
                ),
                (
                    "FS native I/O interval: elapsed=10.0s interval=5.0s "
                    "total_ops=9 total_bytes=805306368 total_GiB/s=0.150000 "
                    "read_ops=3 read_bytes=268435456 read_GiB/s=0.050000 "
                    "write_ops=6 write_bytes=536870912 write_GiB/s=0.100000\n"
                ),
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
    monkeypatch.setattr(runner_module, "read_trace_level", lambda _: "l2")
    monkeypatch.setattr(runner_module, "run_l2_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner_module, "_run_prepare", lambda *_: 0)
    monkeypatch.setattr(
        runner_module.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )

    assert runner_module.run_command(config, str(trace)) == 0

    replay_log = (output_dir / "lmcache-replay.log").read_text()
    assert "FS native I/O interval:" not in replay_log

    rows = (output_dir / "l2_io_interval.tsv").read_text().splitlines()
    assert rows == [
        (
            "elapsed_seconds\ttotal_ops\ttotal_gb_bytes\t"
            "total_gb_per_second\tread_ops\tread_gb_bytes\tread_gb_per_second\t"
            "write_ops\twrite_gb_bytes\twrite_gb_per_second"
        ),
        "5\t12\t1.611\t0.322\t4\t0.537\t0.107\t8\t1.074\t0.215",
        "10\t9\t0.805\t0.161\t3\t0.268\t0.054\t6\t0.537\t0.107",
    ]
