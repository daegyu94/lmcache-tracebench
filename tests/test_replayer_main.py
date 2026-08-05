from replayer.main import main
from replayer.config import load_config
from replayer.runner import _progress_from_log_line, build_command


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


def test_backend_configs_extend_common_base():
    fs_native = load_config("configs/replayer/fs-native.yaml")
    nixl_hf3fs = load_config("configs/replayer/nixl-hf3fs.yaml")

    assert fs_native.l2_store_policy == "skip_l1"
    assert fs_native.l2_adapter["type"] == "fs_native"
    assert fs_native.l2_adapter["use_odirect"] is True
    assert nixl_hf3fs.l2_adapter["type"] == "nixl_store_dynamic"
    assert nixl_hf3fs.l2_adapter["backend_params"]["use_direct_io"] == "true"
    assert "HF3FS" in " ".join(build_command(nixl_hf3fs, "storage.lct"))


def test_replay_progress_is_parsed_from_lmcache_log_line():
    assert _progress_from_log_line("[2026] [21/3508] OK StorageManager.finish_write") == (
        21,
        3508,
    )
    assert _progress_from_log_line("LMCache startup message") is None
