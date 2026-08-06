"""Configuration and validation for trace recording."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def gb_to_bytes(value: float) -> int:
    """Convert the project's GB setting to the byte value expected by vLLM.

    This follows the convention used by hc-ssd-experiments: one configured GB
    is represented as 1024**3 bytes when passed to vLLM.
    """
    if value <= 0:
        raise ValueError(f"GB value must be positive: {value}")
    return int(value * (1024**3))


@dataclass(frozen=True)
class ModelConfig:
    id: str = "QuantTrio/Qwen3-Coder-480B-A35B-Instruct-AWQ"
    tensor_parallel_size: int = 8
    gpu_ids: tuple[int, ...] = tuple(range(8))
    dtype: str = "auto"
    max_model_len: int | None = 131072
    # H100 80 GB에서 72 GB quota를 모사한다. B300에서는 0.25를 사용한다.
    gpu_memory_utilization: float | None = 0.90
    # 지정하면 gpu_memory_utilization 대신 이 고정값을 vLLM에 전달한다.
    kv_cache_memory_gb_per_gpu: float | None = None
    enable_expert_parallel: bool | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    python_hash_seed: str = "0"
    startup_timeout_seconds: int = 1800
    vllm_binary: str = "python"
    lmcache_binary: str = "python"
    vllm_host: str = "127.0.0.1"
    vllm_port: int = 8000
    process_stop_timeout_seconds: int = 30


@dataclass(frozen=True)
class L1Config:
    size_gb: float = 1.0
    init_size_gb: int = 1
    eviction_policy: str = "noop"
    store_policy: str = "skip_l1"


@dataclass(frozen=True)
class L2Config:
    type: str = "fs_native"
    subpath: str = "lmcache-trace/tensormesh-all"
    num_workers: int | None = 8
    reset_on_start: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    resolved_path: str | None = field(default=None, repr=False, compare=False)

    @property
    def effective_path(self) -> str:
        """Return the final path passed to the LMCache adapter."""
        return self.resolved_path or self.subpath


@dataclass(frozen=True)
class LMCacheConfig:
    host: str = "127.0.0.1"
    port: int = 6555
    chunk_size: int = 256
    connector_module_path: str | None = (
        "lmcache.integration.vllm.lmcache_mp_connector"
    )
    l1: L1Config = field(default_factory=L1Config)
    l2: L2Config = field(default_factory=L2Config)


@dataclass(frozen=True)
class MooncakeWorkloadConfig:
    trace: str = "toolagent"
    path: str = "mooncake-traces/{trace}_trace.jsonl"
    url: str | None = None
    download_if_missing: bool = True
    num_requests: int | None = 1000
    time_scale: float = 1.0
    chunk_hash_size: int = 512
    max_concurrent_requests: int | None = 64


@dataclass(frozen=True)
class WorkloadConfig:
    backend: str = "tensormesh"
    tensormesh_root: str = "third_party/Tensormesh-Benchmark"
    dataset: str = "sammshen/lmcache-agentic-traces"
    preset: str = "mixed-realistic"
    source: str = "all"
    dataset_model: str | None = None
    num_sessions: int | None = 30
    max_turns_per_session: int | None = None
    max_input_tokens: int | None = None
    max_concurrent_sessions: int = 20
    progress_interval_seconds: float = 5.0
    timing_mode: str = "respect-gaps"
    pre_gap_scale: float = 1.0
    mixed_session_order: str = "round_robin"
    mixed_source_order: tuple[str, ...] = ("swebench", "gaia", "wildclaw")
    flatten_tools: bool = True
    hf_cache_dir: str | None = None
    hf_streaming: bool = False
    mooncake: MooncakeWorkloadConfig = field(default_factory=MooncakeWorkloadConfig)


@dataclass(frozen=True)
class OutputConfig:
    root: str = "outputs"
    run_id: str | None = None


@dataclass(frozen=True)
class RecorderConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    lmcache: LMCacheConfig = field(default_factory=LMCacheConfig)
    workload: WorkloadConfig = field(default_factory=WorkloadConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def validate(self) -> None:
        if self.model.tensor_parallel_size <= 0:
            raise ValueError("tensor_parallel_size must be positive")
        if self.model.tensor_parallel_size != len(self.model.gpu_ids):
            raise ValueError(
                "tensor_parallel_size must match len(gpu_ids): "
                f"{self.model.tensor_parallel_size} != {len(self.model.gpu_ids)}"
            )
        if self.model.gpu_memory_utilization is not None and not (
            0 < self.model.gpu_memory_utilization <= 1
        ):
            raise ValueError("gpu_memory_utilization must be in (0, 1]")
        if self.model.kv_cache_memory_gb_per_gpu is not None and (
            self.model.kv_cache_memory_gb_per_gpu <= 0
        ):
            raise ValueError("kv_cache_memory_gb_per_gpu must be positive")
        if (
            self.model.gpu_memory_utilization is not None
            and self.model.kv_cache_memory_gb_per_gpu is not None
        ):
            raise ValueError(
                "set either gpu_memory_utilization or "
                "kv_cache_memory_gb_per_gpu, not both"
            )
        if self.runtime.python_hash_seed == "":
            raise ValueError("python_hash_seed must not be empty")
        if not self.runtime.vllm_host:
            raise ValueError("vllm_host must not be empty")
        if not 1 <= self.runtime.vllm_port <= 65535:
            raise ValueError("vllm_port must be between 1 and 65535")
        if self.runtime.startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if self.runtime.process_stop_timeout_seconds <= 0:
            raise ValueError("process_stop_timeout_seconds must be positive")
        if self.lmcache.l1.size_gb <= 0:
            raise ValueError("LMCache L1 size_gb must be positive")
        if (
            not isinstance(self.lmcache.l1.init_size_gb, int)
            or isinstance(self.lmcache.l1.init_size_gb, bool)
            or self.lmcache.l1.init_size_gb <= 0
        ):
            raise ValueError("LMCache L1 init_size_gb must be a positive integer")
        if self.lmcache.chunk_size <= 0:
            raise ValueError("LMCache chunk_size must be positive")
        if self.lmcache.l2.type != "fs_native":
            raise ValueError("the recorder requires LMCache L2 type fs_native")
        if not self.lmcache.l2.subpath:
            raise ValueError("lmcache.l2.subpath must not be empty")
        subpath = Path(self.lmcache.l2.subpath)
        starts_with_home = bool(subpath.parts) and subpath.parts[0].startswith("~")
        if (
            not subpath.parts
            or subpath.is_absolute()
            or starts_with_home
            or ".." in subpath.parts
        ):
            raise ValueError(
                "lmcache.l2.subpath must stay within the recorder mountpoint"
            )
        if self.lmcache.l2.resolved_path is not None and not Path(
            self.lmcache.l2.resolved_path
        ).is_absolute():
            raise ValueError("LMCache L2 resolved_path must be absolute")
        if self.workload.backend not in {"tensormesh", "mooncake"}:
            raise ValueError("workload.backend must be 'tensormesh' or 'mooncake'")
        if self.workload.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")
        if self.workload.backend == "tensormesh":
            if self.workload.max_concurrent_sessions <= 0:
                raise ValueError("max_concurrent_sessions must be positive")
            if (
                self.workload.num_sessions is not None
                and self.workload.num_sessions <= 0
            ):
                raise ValueError("num_sessions must be positive when specified")
            if self.workload.mixed_session_order not in {"original", "round_robin"}:
                raise ValueError(
                    "mixed_session_order must be 'original' or 'round_robin'"
                )
            if (
                self.workload.source != "all"
                and self.workload.mixed_session_order != "original"
            ):
                raise ValueError(
                    "mixed_session_order applies only when workload.source is 'all'"
                )
        else:
            mooncake = self.workload.mooncake
            if mooncake.trace not in {"conversation", "toolagent"}:
                raise ValueError(
                    "workload.mooncake.trace must be 'conversation' or 'toolagent'"
                )
            if not mooncake.path:
                raise ValueError("workload.mooncake.path must not be empty")
            if mooncake.num_requests is not None and mooncake.num_requests <= 0:
                raise ValueError(
                    "workload.mooncake.num_requests must be positive when specified"
                )
            if mooncake.time_scale <= 0:
                raise ValueError("workload.mooncake.time_scale must be positive")
            if mooncake.chunk_hash_size <= 0:
                raise ValueError(
                    "workload.mooncake.chunk_hash_size must be positive"
                )
            if (
                mooncake.max_concurrent_requests is not None
                and mooncake.max_concurrent_requests <= 0
            ):
                raise ValueError(
                    "workload.mooncake.max_concurrent_requests must be positive"
                )


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _tuple_ints(value: Any, field_name: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
        raise ValueError(f"{field_name} must be a list of integers")
    return tuple(value)


def _tuple_strings(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)


def _load_raw_config(
    config_path: Path,
    *,
    ancestry: tuple[Path, ...] = (),
) -> dict[str, Any]:
    resolved_path = config_path.expanduser().resolve()
    if resolved_path in ancestry:
        chain = " -> ".join(str(item) for item in (*ancestry, resolved_path))
        raise ValueError(f"config extends cycle: {chain}")
    with resolved_path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream) or {}
    if not isinstance(loaded, dict):
        raise ValueError("recorder config must contain a mapping")

    extends = loaded.pop("extends", None)
    if extends is None:
        return loaded
    if not isinstance(extends, str) or not extends:
        raise ValueError("config extends must be a non-empty relative path")
    parent_path = resolved_path.parent / extends
    parent = _load_raw_config(parent_path, ancestry=(*ancestry, resolved_path))
    return _merge(parent, loaded)


def load_config(path: str | Path | None = None) -> RecorderConfig:
    raw: dict[str, Any] = {}
    if path is not None:
        raw = _load_raw_config(Path(path))

    defaults = RecorderConfig()
    model_raw = _merge({}, raw.get("model", {}))
    runtime_raw = _merge({}, raw.get("runtime", {}))
    lm_raw = _merge({}, raw.get("lmcache", {}))
    l1_raw = _merge({}, lm_raw.pop("l1", {}))
    l2_raw = _merge({}, lm_raw.pop("l2", {}))
    workload_raw = _merge({}, raw.get("workload", {}))
    mooncake_raw = _merge({}, workload_raw.pop("mooncake", {}))
    output_raw = _merge({}, raw.get("output", {}))

    model_values = {**defaults.model.__dict__, **model_raw}
    model_values["gpu_ids"] = _tuple_ints(
        model_raw.get("gpu_ids", list(defaults.model.gpu_ids)), "model.gpu_ids"
    )
    model = ModelConfig(**model_values)
    runtime = RuntimeConfig(**{**defaults.runtime.__dict__, **runtime_raw})
    l1 = L1Config(**{**defaults.lmcache.l1.__dict__, **l1_raw})
    if "base_path" in l2_raw:
        raise ValueError(
            "lmcache.l2.base_path was replaced by the relative "
            "lmcache.l2.subpath setting"
        )
    known_l2 = {"type", "subpath", "num_workers", "reset_on_start"}
    l2_values = {**defaults.lmcache.l2.__dict__, **l2_raw}
    l2 = L2Config(
        **{key: value for key, value in l2_values.items() if key in known_l2},
        extra={key: value for key, value in l2_raw.items() if key not in known_l2},
    )
    lm_values = {**defaults.lmcache.__dict__, **lm_raw}
    lm_values["l1"] = l1
    lm_values["l2"] = l2
    lmcache = LMCacheConfig(**lm_values)
    workload_values = {**defaults.workload.__dict__, **workload_raw}
    workload_values["mixed_source_order"] = _tuple_strings(
        workload_raw.get(
            "mixed_source_order", list(defaults.workload.mixed_source_order)
        ),
        "workload.mixed_source_order",
    )
    workload_values["mooncake"] = MooncakeWorkloadConfig(
        **{**defaults.workload.mooncake.__dict__, **mooncake_raw}
    )
    workload = WorkloadConfig(**workload_values)
    output = OutputConfig(**{**defaults.output.__dict__, **output_raw})
    config = RecorderConfig(model, runtime, lmcache, workload, output)
    config.validate()
    return config
