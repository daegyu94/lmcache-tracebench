from dataclasses import replace

import pytest

from recorder.config import load_config
from recorder.main import apply_speedup, main, resolve_mountpoint


def test_mooncake_overrides_are_reflected_in_dry_run(capsys):
    assert (
        main(
            [
                "--config",
                "configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml",
                "--mountpoint",
                "/tmp/mount",
                "--mooncake-trace",
                "conversation",
                "--mooncake-num-requests",
                "all",
                "--l2-path",
                "/tmp/mooncake-{trace}",
                "--output-dir",
                "/tmp/mooncake-main-test",
                "--dry-run",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "storage.lct" in output
    assert "/tmp/mooncake-conversation" in output
    assert "/tmp/mount/mooncake-traces/conversation_trace.jsonl" in output
    assert "--num-requests" not in output


def test_mountpoint_resolves_relative_recorder_paths(capsys):
    config = resolve_mountpoint(
        load_config("configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml"),
        "/tmp/mount",
    )
    assert config.lmcache.l2.subpath == "lmcache-trace/mooncake-{trace}"
    assert (
        config.lmcache.l2.effective_path
        == "/tmp/mount/lmcache-trace/mooncake-toolagent"
    )
    assert (
        config.workload.mooncake.path
        == "/tmp/mount/mooncake-traces/toolagent_trace.jsonl"
    )

    assert (
        main(
            [
                "--config",
                "configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml",
                "--mountpoint",
                "/tmp/mount",
                "--dry-run",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "/tmp/mount/lmcache-trace/mooncake-toolagent" in output


def test_mountpoint_rejects_escaping_relative_path():
    config = load_config("configs/recorder/qwen3-coder-480b-tp8-base.yaml")
    with pytest.raises(ValueError, match="escapes mountpoint"):
        resolve_mountpoint(config, "/tmp/mount", l2_path="../lmcache")


def test_speedup_maps_to_backend_timing_controls():
    mooncake = apply_speedup(
        load_config("configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml"),
        5,
    )
    assert mooncake.workload.mooncake.time_scale == pytest.approx(0.2)

    tensormesh = apply_speedup(
        load_config("configs/recorder/qwen3-coder-480b-tp8-gaia.yaml"),
        5,
    )
    assert tensormesh.workload.pre_gap_scale == pytest.approx(0.2)


def test_speedup_rejects_tensormesh_max_pressure():
    config = load_config("configs/recorder/qwen3-coder-480b-tp8-base.yaml")
    config = replace(
        config,
        workload=replace(config.workload, timing_mode="max-pressure"),
    )
    with pytest.raises(ValueError, match="respect-gaps"):
        apply_speedup(config, 2)
