import json
from dataclasses import replace
from pathlib import Path

import pytest

from recorder.config import RecorderConfig, MooncakeWorkloadConfig, load_config
from recorder.mooncake import (
    build_mooncake_benchmark_command,
    prepare_mooncake_workload,
    write_mooncake_request_stats,
)


def _write_trace(path: Path) -> None:
    rows = [
        {
            "timestamp": 1000,
            "input_length": 1024,
            "output_length": 32,
            "hash_ids": [10, 11],
        },
        {
            "timestamp": 2500,
            "input_length": 1536,
            "output_length": 64,
            "hash_ids": [10, 11, 12],
        },
        {
            "timestamp": 4000,
            "input_length": 512,
            "output_length": 16,
            "hash_ids": [20],
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_mooncake_config_loads():
    config = load_config(
        "configs/recorder/qwen3-coder-480b-tp8-mooncake-toolagent.yaml"
    )

    assert config.workload.backend == "mooncake"
    assert config.workload.mooncake.trace == "toolagent"
    assert config.workload.mooncake.num_requests == 1000
    assert config.workload.mooncake.chunk_hash_size == 512


def test_prepare_and_build_mooncake_timed_trace(tmp_path: Path):
    trace_path = tmp_path / "toolagent.jsonl"
    _write_trace(trace_path)
    mooncake = MooncakeWorkloadConfig(
        trace="toolagent",
        path=str(trace_path),
        download_if_missing=False,
        num_requests=2,
        time_scale=0.1,
        max_concurrent_requests=8,
    )
    base = RecorderConfig()
    config = replace(
        base,
        workload=replace(base.workload, backend="mooncake", mooncake=mooncake),
    )

    plan = prepare_mooncake_workload(mooncake)
    command = build_mooncake_benchmark_command(
        config,
        plan,
        result_path=tmp_path / "result.json",
    )

    assert plan.total_requests == 3
    assert plan.selected_requests == 2
    assert plan.total_input_tokens == 2560
    assert plan.total_output_tokens == 96
    assert plan.max_total_tokens == 1600
    assert command[command.index("--dataset-name") + 1] == "timed_trace"
    assert command[command.index("--num-prompts") + 1] == "2"
    assert command[command.index("--timed-trace-sec-multiplier") + 1] == "0.0001"
    assert command[command.index("--max-concurrency") + 1] == "8"


def test_rejects_out_of_order_mooncake_timestamps(tmp_path: Path):
    trace_path = tmp_path / "bad.jsonl"
    trace_path.write_text(
        '\n'.join(
            [
                json.dumps(
                    {
                        "timestamp": 2,
                        "input_length": 1,
                        "output_length": 1,
                        "hash_ids": [1],
                    }
                ),
                json.dumps(
                    {
                        "timestamp": 1,
                        "input_length": 1,
                        "output_length": 1,
                        "hash_ids": [2],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = MooncakeWorkloadConfig(
        path=str(trace_path),
        download_if_missing=False,
        num_requests=None,
    )

    with pytest.raises(ValueError, match="nondecreasing"):
        prepare_mooncake_workload(config)


def test_write_mooncake_request_stats(tmp_path: Path):
    output = tmp_path / "request_stats.jsonl"
    write_mooncake_request_stats(
        {
            "num_prompts": 2,
            "input_lens": [10, 20],
            "output_lens": [2, 0],
            "start_times": [0.0, 1.0],
            "ttfts": [0.1, 0.0],
            "itls": [[0.01], []],
            "errors": ["", "request failed"],
        },
        output,
    )

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]["successful"] is True
    assert rows[0]["itl_count"] == 1
    assert rows[1]["successful"] is False
    assert rows[1]["error_message"] == "request failed"


def test_rejects_incomplete_mooncake_benchmark_details(tmp_path: Path):
    with pytest.raises(ValueError, match="input_lens"):
        write_mooncake_request_stats(
            {
                "num_prompts": 2,
                "input_lens": [10],
                "output_lens": [2, 3],
                "start_times": [0.0, 1.0],
                "ttfts": [0.1, 0.2],
                "itls": [[], []],
                "errors": ["", ""],
            },
            tmp_path / "request_stats.jsonl",
        )
