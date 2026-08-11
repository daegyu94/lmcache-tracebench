import pytest

from recorder.config import load_config
from recorder.launcher import build_commands
from replayer.config import ReplayerConfig
from replayer.runner import build_command, build_prepare_command


def test_recorder_commands_enable_mp_fs_native_trace():
    config = load_config("configs/recorder/example.yaml")
    commands = build_commands(config, trace_path="outputs/run/storage.lct")
    assert "--trace-level" in commands.lmcache
    assert commands.lmcache[commands.lmcache.index("--trace-level") + 1] == "storage"
    assert "fs_native" in commands.lmcache[commands.lmcache.index("--l2-adapter") + 1]
    assert (
        '"base_path":"lmcache-trace/tensormesh-all"'
        in commands.lmcache[commands.lmcache.index("--l2-adapter") + 1]
    )
    assert (
        '"use_odirect":true'
        in commands.lmcache[commands.lmcache.index("--l2-adapter") + 1]
    )
    assert (
        "LMCacheMPConnector"
        in commands.vllm[commands.vllm.index("--kv-transfer-config") + 1]
    )
    assert (
        "lmcache.integration.vllm.lmcache_mp_connector"
        in commands.vllm[commands.vllm.index("--kv-transfer-config") + 1]
    )
    assert commands.vllm[commands.vllm.index("--port") + 1] == "8000"
    assert commands.vllm[commands.vllm.index("--gpu-memory-utilization") + 1] == "0.9"
    assert "--kv-cache-memory-bytes" not in commands.vllm
    assert "--enable-expert-parallel" in commands.vllm
    assert commands.env["PYTHONHASHSEED"] == "0"


def test_recorder_commands_support_l2_adapter_trace():
    config = load_config("configs/recorder/example.yaml")
    commands = build_commands(
        config,
        trace_path="outputs/run/l2.lct",
        trace_level="l2",
    )
    assert commands.lmcache[commands.lmcache.index("--trace-level") + 1] == "l2"


def test_replayer_is_one_shot_and_uses_skip_l1():
    command = build_command(ReplayerConfig(), "trace.lct")
    assert command[:4] == ["lmcache", "trace", "replay", "trace.lct"]
    assert command[command.index("--l1-init-size-gb") + 1] == "1"
    assert command[command.index("--l2-store-policy") + 1] == "skip_l1"
    adapter = command[command.index("--l2-adapter") + 1]
    assert '"type":"fs_native"' in adapter
    assert command[command.index("--speedup") + 1] == "1.0"
    assert command[command.index("--trace-percent") + 1] == "100.0"


def test_l2_prepare_command_uses_same_replay_configuration():
    command = build_prepare_command(ReplayerConfig(), "l2.lct")
    assert command[:4] == ["lmcache", "trace", "replay", "l2.lct"]
    assert command[-2:] == ["--prepare-l2", "--prepare-only"]


@pytest.mark.parametrize("trace_percent", [0.0, -1.0, 100.1, float("nan")])
def test_replayer_rejects_invalid_trace_percent(trace_percent):
    with pytest.raises(ValueError, match="trace_percent"):
        ReplayerConfig(trace_percent=trace_percent).validate()
