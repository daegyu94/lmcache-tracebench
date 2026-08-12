import json
import subprocess
from pathlib import Path

import traceprof
from traceprof.aggregate import aggregate_profiles
from traceprof.config import load_config
from traceprof.controller import RemoteProfiler


def test_default_profile_intervals_are_five_seconds():
    config = load_config("configs/profiling/storage.yaml")

    assert config.sample_interval_seconds == 5
    assert config.report_interval_seconds == 5
    assert all(node.role == "storage" for node in config.nodes)


def test_local_profile_config_uses_local_loopback():
    config = load_config("configs/profiling/local.yaml")

    assert len(config.nodes) == 1
    assert config.nodes[0].name == "local"
    assert config.nodes[0].host == "localhost"
    assert config.nodes[0].interfaces == ("lo",)
    assert config.nodes[0].devices == ()
    assert config.sample_interval_seconds == 1
    assert config.report_interval_seconds == 1


def test_replay_node_is_optional_and_marked_as_replay(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        """
nodes:
  - name: storage
    host: storage
    interfaces: [bond0]
replay_node:
  name: client
  host: node1
  interfaces: [eth1]
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert [node.name for node in config.nodes] == ["storage", "client"]
    assert config.nodes[-1].role == "replay"


def test_remote_agent_is_shell_only():
    assert traceprof.__file__ is not None
    script = Path(traceprof.__file__).with_name("storage_agent.sh")

    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0


def test_controller_invokes_deployed_shell_agent(tmp_path):
    config = load_config("configs/profiling/storage.yaml")
    profiler = RemoteProfiler(config, output_dir=tmp_path, run_id="test-run")

    command = profiler._ssh_command(config.nodes[0], "run", "--root", "/tmp/root")

    assert "python" not in " ".join(command)
    assert ".storage_agent.sh" in command[-1]
    assert "bash" in command[-1]


def test_aggregate_keeps_replay_and_storage_network_totals_separate(tmp_path):
    profile_root = tmp_path / "profile"
    for node, role, rx_bytes in (
        ("storage1", "storage", 100),
        ("client", "replay", 25),
    ):
        node_dir = profile_root / node
        node_dir.mkdir(parents=True)
        (node_dir / "summary.json").write_text(
            json.dumps(
                {
                    "node": node,
                    "role": role,
                    "duration_seconds": 5,
                    "interfaces": {
                        "bond0": {
                            "rx_bytes": rx_bytes,
                            "tx_bytes": 10,
                            "rx_packets": 1,
                            "tx_packets": 1,
                            "rx_errors": 0,
                            "tx_errors": 0,
                            "rx_drops": 0,
                            "tx_drops": 0,
                        }
                    },
                    "devices": {},
                }
            ),
            encoding="utf-8",
        )

    result = aggregate_profiles(
        profile_root, tmp_path / "profile_summary.json", run_id="test"
    )

    assert result["interface_totals_by_role"]["storage"]["bond0"]["rx_bytes"] == 100
    assert result["interface_totals_by_role"]["replay"]["bond0"]["rx_bytes"] == 25
