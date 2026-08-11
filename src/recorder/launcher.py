"""Build LMCache MP and vLLM commands for recorder runs."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .config import RecorderConfig, gb_to_bytes


@dataclass(frozen=True)
class CommandPlan:
    lmcache: list[str]
    vllm: list[str]
    env: dict[str, str]


def _adapter_payload(config: RecorderConfig) -> dict[str, object]:
    l2 = config.lmcache.l2
    payload: dict[str, object] = {
        "type": l2.type,
        "base_path": l2.effective_path,
    }
    if l2.num_workers is not None:
        payload["num_workers"] = l2.num_workers
    payload.update(l2.extra)
    return payload


def build_commands(config: RecorderConfig, *, trace_path: str) -> CommandPlan:
    config.validate()
    l1 = config.lmcache.l1
    lmcache = [
        config.runtime.lmcache_binary,
        "-m",
        "lmcache.v1.multiprocess.server",
        "--host",
        config.lmcache.host,
        "--port",
        str(config.lmcache.port),
        "--l1-size-gb",
        str(l1.size_gb),
        "--l1-init-size-gb",
        str(l1.init_size_gb),
        "--eviction-policy",
        l1.eviction_policy,
        "--l2-store-policy",
        l1.store_policy,
        "--chunk-size",
        str(config.lmcache.chunk_size),
        "--l2-adapter",
        json.dumps(_adapter_payload(config), sort_keys=True, separators=(",", ":")),
        "--trace-level",
        "storage",
        "--trace-output",
        trace_path,
    ]
    kv_transfer_config: dict[str, object] = {
        "kv_connector": "LMCacheMPConnector",
        "kv_role": "kv_both",
        "kv_connector_extra_config": {
            "lmcache.mp.host": f"tcp://{config.lmcache.host}",
            "lmcache.mp.port": config.lmcache.port,
        },
    }
    if config.lmcache.connector_module_path:
        kv_transfer_config["kv_connector_module_path"] = (
            config.lmcache.connector_module_path
        )
    vllm = [
        config.runtime.vllm_binary,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        config.model.id,
        "--host",
        config.runtime.vllm_host,
        "--port",
        str(config.runtime.vllm_port),
        "--tensor-parallel-size",
        str(config.model.tensor_parallel_size),
        "--dtype",
        config.model.dtype,
        "--kv-transfer-config",
        json.dumps(kv_transfer_config, separators=(",", ":")),
    ]
    if config.model.kv_cache_memory_gb_per_gpu is not None:
        vllm += [
            "--kv-cache-memory-bytes",
            str(gb_to_bytes(config.model.kv_cache_memory_gb_per_gpu)),
        ]
    elif config.model.gpu_memory_utilization is not None:
        vllm += [
            "--gpu-memory-utilization",
            str(config.model.gpu_memory_utilization),
        ]
    if config.model.max_model_len is not None:
        vllm += ["--max-model-len", str(config.model.max_model_len)]
    expert_parallel = config.model.enable_expert_parallel
    if expert_parallel is None:
        expert_parallel = (
            config.model.tensor_parallel_size == 8
            and "qwen3-coder-480b" in config.model.id.lower()
        )
    if expert_parallel:
        vllm.append("--enable-expert-parallel")
    env = {
        "PYTHONHASHSEED": config.runtime.python_hash_seed,
        "CUDA_VISIBLE_DEVICES": ",".join(str(gpu) for gpu in config.model.gpu_ids),
    }
    return CommandPlan(lmcache=lmcache, vllm=vllm, env=env)
