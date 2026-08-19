"""Mooncake FAST'25 timed-trace preparation and vLLM recording helpers."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RecorderConfig, MooncakeWorkloadConfig


MOONCAKE_TRACE_URLS = {
    "conversation": (
        "https://raw.githubusercontent.com/kvcache-ai/Mooncake/main/"
        "FAST25-release/traces/conversation_trace.jsonl"
    ),
    "toolagent": (
        "https://raw.githubusercontent.com/kvcache-ai/Mooncake/main/"
        "FAST25-release/traces/toolagent_trace.jsonl"
    ),
}


@dataclass(frozen=True)
class MooncakePlan:
    trace: str
    path: Path
    source_url: str
    total_requests: int
    selected_requests: int
    total_input_tokens: int
    total_output_tokens: int
    max_input_tokens: int
    max_total_tokens: int
    first_timestamp_ms: float
    last_timestamp_ms: float
    dataset_percent: float | None = None

    @property
    def source_counts(self) -> dict[str, int]:
        return {f"mooncake-{self.trace}": self.selected_requests}


def _trace_url(config: MooncakeWorkloadConfig) -> str:
    return config.url or MOONCAKE_TRACE_URLS[config.trace]


def ensure_mooncake_trace(config: MooncakeWorkloadConfig) -> tuple[Path, str]:
    """Return a local trace path, atomically downloading the official file."""
    path = Path(config.path).expanduser().resolve()
    source_url = _trace_url(config)
    if path.is_file():
        return path, source_url
    if path.exists():
        raise ValueError(f"Mooncake trace path is not a file: {path}")
    if not config.download_if_missing:
        raise FileNotFoundError(f"Mooncake trace file not found: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".part",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            with urllib.request.urlopen(source_url, timeout=60) as response:
                shutil.copyfileobj(response, output)
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return path, source_url


def _number(entry: dict[str, Any], field: str, line_number: int) -> float:
    value = entry.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(
            f"Mooncake trace line {line_number} has invalid {field}: {value!r}"
        )
    return float(value)


def _validated_entries(path: Path):
    """Yield validated Mooncake entries while checking timestamp ordering."""
    previous_timestamp_ms: float | None = None
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Mooncake trace line {line_number} is not valid JSON"
                ) from exc
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Mooncake trace line {line_number} must contain an object"
                )

            timestamp = _number(entry, "timestamp", line_number)
            input_length = int(_number(entry, "input_length", line_number))
            output_length = int(_number(entry, "output_length", line_number))
            hash_ids = entry.get("hash_ids")
            if not isinstance(hash_ids, list) or not all(
                isinstance(item, int) and not isinstance(item, bool)
                for item in hash_ids
            ):
                raise ValueError(
                    f"Mooncake trace line {line_number} has invalid hash_ids"
                )
            if (
                previous_timestamp_ms is not None
                and timestamp < previous_timestamp_ms
            ):
                raise ValueError(
                    "Mooncake trace timestamps must be nondecreasing; "
                    f"line {line_number} has {timestamp} after "
                    f"{previous_timestamp_ms}"
                )
            previous_timestamp_ms = timestamp
            yield timestamp, input_length, output_length


def _validate_dataset_percent(dataset_percent: float) -> float:
    if (
        not math.isfinite(dataset_percent)
        or dataset_percent <= 0
        or dataset_percent > 100
    ):
        raise ValueError("dataset_percent must be greater than 0 and at most 100")
    return float(dataset_percent)


def prepare_mooncake_workload(
    config: MooncakeWorkloadConfig,
    *,
    dataset_percent: float | None = None,
) -> MooncakePlan:
    """Download, validate, and summarize a Mooncake dataset prefix."""
    if dataset_percent is not None:
        dataset_percent = _validate_dataset_percent(dataset_percent)
        if config.num_requests is not None:
            raise ValueError(
                "dataset_percent cannot be combined with num_requests; "
                "leave num_requests unset when selecting by percentage"
            )
    path, source_url = ensure_mooncake_trace(config)
    total_requests = sum(1 for _ in _validated_entries(path))
    if total_requests == 0:
        raise ValueError(f"Mooncake trace is empty: {path}")

    if dataset_percent is not None:
        selected_limit = math.ceil(total_requests * dataset_percent / 100.0)
    elif config.num_requests is None:
        selected_limit = total_requests
    else:
        selected_limit = config.num_requests
    if total_requests < selected_limit:
        raise ValueError(
            f"Mooncake trace has {total_requests} requests, fewer than configured "
            f"num_requests={selected_limit}"
        )

    selected_requests = 0
    total_input_tokens = 0
    total_output_tokens = 0
    max_input_tokens = 0
    max_total_tokens = 0
    first_timestamp_ms: float | None = None
    last_timestamp_ms: float | None = None

    for timestamp, input_length, output_length in _validated_entries(path):
        if selected_requests >= selected_limit:
            break
        selected_requests += 1
        total_input_tokens += input_length
        total_output_tokens += output_length
        max_input_tokens = max(max_input_tokens, input_length)
        max_total_tokens = max(
            max_total_tokens,
            input_length + output_length,
        )
        if first_timestamp_ms is None:
            first_timestamp_ms = timestamp
        last_timestamp_ms = timestamp

    assert first_timestamp_ms is not None
    assert last_timestamp_ms is not None
    return MooncakePlan(
        trace=config.trace,
        path=path,
        source_url=source_url,
        total_requests=total_requests,
        selected_requests=selected_requests,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        max_input_tokens=max_input_tokens,
        max_total_tokens=max_total_tokens,
        first_timestamp_ms=first_timestamp_ms,
        last_timestamp_ms=last_timestamp_ms,
        dataset_percent=dataset_percent,
    )


def build_mooncake_benchmark_command(
    config: RecorderConfig,
    plan: MooncakePlan,
    *,
    result_path: Path,
) -> list[str]:
    """Build the vLLM timed-trace client command for a prepared trace."""
    mooncake = config.workload.mooncake
    command = [
        config.runtime.vllm_binary,
        "-m",
        "vllm.entrypoints.cli.main",
        "bench",
        "serve",
        "--backend",
        "openai",
        "--base-url",
        f"http://{config.runtime.vllm_host}:{config.runtime.vllm_port}",
        "--endpoint",
        "/v1/completions",
        "--model",
        config.model.id,
        "--dataset-name",
        "timed_trace",
        "--dataset-path",
        str(plan.path),
        "--num-prompts",
        str(plan.selected_requests),
        "--self-timed",
        "--timed-trace-chunk-hash-size",
        str(mooncake.chunk_hash_size),
        "--timed-trace-sec-multiplier",
        str(0.001 * mooncake.time_scale),
        "--ignore-eos",
        "--disable-shuffle",
        "--temperature",
        "0",
        "--save-result",
        "--save-detailed",
        "--result-dir",
        str(result_path.parent),
        "--result-filename",
        result_path.name,
    ]
    if mooncake.max_concurrent_requests is not None:
        command += [
            "--max-concurrency",
            str(mooncake.max_concurrent_requests),
        ]
    return command


def load_mooncake_result(path: Path) -> dict[str, Any]:
    """Load and minimally validate the vLLM benchmark result."""
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise ValueError(f"vLLM benchmark result must be an object: {path}")
    for field in ("num_prompts", "completed", "failed"):
        if not isinstance(result.get(field), int):
            raise ValueError(f"vLLM benchmark result is missing integer {field}")
    return result


def write_mooncake_request_stats(result: dict[str, Any], path: Path) -> None:
    """Write compact per-request JSONL using vLLM's detailed result arrays."""
    num_prompts = int(result["num_prompts"])
    detailed_fields = (
        "input_lens",
        "output_lens",
        "start_times",
        "ttfts",
        "itls",
        "errors",
    )
    for field in detailed_fields:
        values = result.get(field)
        if not isinstance(values, list) or len(values) != num_prompts:
            raise ValueError(
                "vLLM benchmark detailed result has invalid "
                f"{field}: expected {num_prompts} entries"
            )

    input_lens = result["input_lens"]
    output_lens = result["output_lens"]
    start_times = result["start_times"]
    ttfts = result["ttfts"]
    itls = result["itls"]
    errors = result["errors"]

    with path.open("w", encoding="utf-8", buffering=1) as stream:
        for index in range(num_prompts):
            error = errors[index] or ""
            row = {
                "request_index": index,
                "input_tokens": input_lens[index],
                "output_tokens": output_lens[index],
                "start_time": start_times[index],
                "ttft": ttfts[index],
                "itl_count": len(itls[index] or []),
                "successful": not bool(error),
                "error_message": str(error),
            }
            stream.write(json.dumps(row, sort_keys=True) + "\n")
