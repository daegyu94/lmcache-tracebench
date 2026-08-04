from recorder.config import RecorderConfig, gb_to_bytes, load_config


def test_gb_to_bytes_matches_existing_runner_convention():
    assert gb_to_bytes(1) == 1024**3


def test_example_config_loads():
    config = load_config("configs/recorder/example.yaml")
    assert config.model.tensor_parallel_size == 8
    assert config.model.gpu_ids == tuple(range(8))
    assert config.model.gpu_memory_utilization == 0.90
    assert config.model.kv_cache_memory_gb_per_gpu is None
    assert config.lmcache.l2.type == "fs_native"
    assert config.lmcache.l2.reset_on_start is True
    assert config.lmcache.l1.size_gb == 20
    assert config.lmcache.l1.init_size_gb == 20
    assert config.workload.progress_interval_seconds == 5


def test_qwen_tp8_smoke_config_uses_100_mixed_sessions():
    config = load_config("configs/recorder/qwen3-coder-480b-tp8-smoke.yaml")
    assert config.workload.source == "all"
    assert config.workload.num_sessions == 100
    assert config.workload.mixed_session_order == "round_robin"
    assert config.lmcache.l2.base_path.endswith("/mixed-smoke")


def test_qwen_tp8_config_has_no_session_cap():
    config = load_config("configs/recorder/qwen3-coder-480b-tp8-realistic.yaml")
    assert config.workload.source == "all"
    assert config.workload.num_sessions is None
    assert config.workload.timing_mode == "respect-gaps"
    assert config.workload.pre_gap_scale == 1.0
    assert config.workload.max_concurrent_sessions == 20
    assert config.lmcache.l1.size_gb == 20


def test_qwen_tp8_max_pressure_config_uses_80_sessions():
    config = load_config("configs/recorder/qwen3-coder-480b-tp8-max-pressure.yaml")
    assert config.workload.timing_mode == "max-pressure"
    assert config.workload.max_concurrent_sessions == 80


def test_source_specific_configs_disable_mixed_interleaving():
    for source in ("swebench", "gaia", "wildclaw"):
        config = load_config(f"configs/recorder/qwen3-coder-480b-tp8-{source}.yaml")
        assert config.workload.source == source
        assert config.workload.num_sessions is None
        assert config.workload.mixed_session_order == "original"
        assert config.lmcache.l2.base_path.endswith(f"/{source}")


def test_rejects_tp_gpu_mismatch():
    config = RecorderConfig(
        model=RecorderConfig().model.__class__(
            tensor_parallel_size=2,
            gpu_ids=(0,),
        )
    )
    try:
        config.validate()
    except ValueError as exc:
        assert "tensor_parallel_size" in str(exc)
    else:
        raise AssertionError("expected TP/GPU validation error")


def test_rejects_fractional_l1_init_size():
    config = RecorderConfig(
        lmcache=RecorderConfig().lmcache.__class__(
            l1=RecorderConfig().lmcache.l1.__class__(init_size_gb=0.25)
        )
    )
    try:
        config.validate()
    except ValueError as exc:
        assert "init_size_gb" in str(exc)
    else:
        raise AssertionError("expected L1 init-size validation error")


def test_rejects_both_kv_cache_and_gpu_utilization():
    config = RecorderConfig(
        model=RecorderConfig().model.__class__(
            gpu_memory_utilization=0.9,
            kv_cache_memory_gb_per_gpu=30,
        )
    )
    try:
        config.validate()
    except ValueError as exc:
        assert "either" in str(exc)
    else:
        raise AssertionError("expected KV cache/utilization validation error")
