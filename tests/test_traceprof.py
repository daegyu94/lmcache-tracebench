import json
import subprocess
from pathlib import Path

import pytest

import traceprof
from traceprof.aggregate import aggregate_profiles
from traceprof.config import expand_brace_pattern, load_config
from traceprof.controller import RemoteProfiler


def test_default_profile_intervals_are_five_seconds():
    config = load_config("configs/profiling/storage.yaml")

    assert config.sample_interval_seconds == 5
    assert config.report_interval_seconds == 5
    assert all(node.role == "storage" for node in config.nodes)


def test_local_profile_config_uses_local_loopback():
    config = load_config("configs/profiling/local.yaml")

    assert len(config.nodes) == 1
    assert config.nodes[0].hostname == "localhost"
    assert config.nodes[0].interfaces == ("lo",)
    assert config.nodes[0].devices == ()
    assert config.sample_interval_seconds == 1
    assert config.report_interval_seconds == 1


def test_replay_node_is_optional_and_marked_as_replay(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        """
nodes:
  - hostname: storage
    interfaces: [bond0]
replay_node:
  hostname: node1
  interfaces: [eth1]
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert [node.hostname for node in config.nodes] == ["storage", "node1"]
    assert config.nodes[-1].role == "replay"


def test_remote_agent_is_shell_only():
    assert traceprof.__file__ is not None
    script = Path(traceprof.__file__).with_name("storage_agent.sh")

    script_text = script.read_text(encoding="utf-8")
    assert script_text.startswith("#!/usr/bin/env bash")
    assert "timestamp\\telapsed_s\\tinterval_s" not in script_text
    assert "timestamp\\telapsed_s\\tdevice" in script_text
    assert "timestamp\\telapsed_s\\tinterface" in script_text
    assert "seconds_int" in script_text
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


def test_aggregate_sums_throughput_per_node_and_across_the_cluster(tmp_path):
    profile_root = tmp_path / "profile"
    devices_by_node = {
        "weka02": {
            "/dev/nvme2n1": {"read_bytes": 100, "write_bytes": 50},
            "/dev/nvme3n1": {"read_bytes": 200, "write_bytes": 25},
        },
        "weka03": {
            "/dev/nvme1n1": {"read_bytes": 10, "write_bytes": 5},
        },
    }
    for node, devices in devices_by_node.items():
        node_dir = profile_root / node
        node_dir.mkdir(parents=True)
        (node_dir / "summary.json").write_text(
            json.dumps(
                {
                    "node": node,
                    "role": "storage",
                    "duration_seconds": 10,
                    "interfaces": {},
                    "devices": devices,
                }
            ),
            encoding="utf-8",
        )

    result = aggregate_profiles(
        profile_root, tmp_path / "profile_summary.json", run_id="test"
    )

    assert result["node_disk_totals"]["weka02"]["read_bytes"] == 300
    assert result["node_disk_totals"]["weka02"]["write_bytes"] == 75
    assert result["node_disk_totals"]["weka03"]["read_bytes"] == 10
    assert result["node_disk_totals"]["weka03"]["write_bytes"] == 5
    assert result["cluster_disk_grand_total"]["read_bytes"] == 310
    assert result["cluster_disk_grand_total"]["write_bytes"] == 80
    assert result["node_disk_totals"]["weka02"]["read_mibps_avg"] == pytest.approx(
        300 / 10 / 1024**2
    )


def test_expand_brace_pattern_passes_through_plain_strings():
    assert expand_brace_pattern("/dev/nvme0n1") == ["/dev/nvme0n1"]


def test_expand_brace_pattern_expands_an_ascending_range():
    assert expand_brace_pattern("/dev/nvme{2..7}n1") == [
        "/dev/nvme2n1",
        "/dev/nvme3n1",
        "/dev/nvme4n1",
        "/dev/nvme5n1",
        "/dev/nvme6n1",
        "/dev/nvme7n1",
    ]


def test_expand_brace_pattern_expands_a_descending_range():
    assert expand_brace_pattern("nvme{3..1}") == ["nvme3", "nvme2", "nvme1"]


def test_expand_brace_pattern_expands_a_comma_list():
    assert expand_brace_pattern("/dev/nvme{1,2,4,5,6,7}n1") == [
        "/dev/nvme1n1",
        "/dev/nvme2n1",
        "/dev/nvme4n1",
        "/dev/nvme5n1",
        "/dev/nvme6n1",
        "/dev/nvme7n1",
    ]


def test_expand_brace_pattern_expands_multiple_groups_as_cartesian_product():
    assert expand_brace_pattern("nvme{2,3}n{1,2}") == [
        "nvme2n1",
        "nvme2n2",
        "nvme3n1",
        "nvme3n2",
    ]


@pytest.mark.parametrize("spec", ["nvme{}", "nvme{2..}", "nvme{..7}", "nvme{2...7}"])
def test_expand_brace_pattern_rejects_malformed_groups(spec):
    with pytest.raises(ValueError):
        expand_brace_pattern(spec)


def test_b300_storage_config_expands_every_node_device_range():
    config = load_config("configs/profiling/b300_storage.yaml")

    devices_by_node = {node.hostname: node.devices for node in config.nodes}
    assert devices_by_node["weka02"] == (
        "/dev/nvme2n1",
        "/dev/nvme3n1",
        "/dev/nvme4n1",
        "/dev/nvme5n1",
        "/dev/nvme6n1",
        "/dev/nvme7n1",
    )
    assert devices_by_node["weka03"] == (
        "/dev/nvme1n1",
        "/dev/nvme2n1",
        "/dev/nvme4n1",
        "/dev/nvme5n1",
        "/dev/nvme6n1",
        "/dev/nvme7n1",
    )
    for name in ("weka04", "weka05", "weka06", "weka07"):
        assert devices_by_node[name] == (
            "/dev/nvme2n1",
            "/dev/nvme3n1",
            "/dev/nvme4n1",
            "/dev/nvme5n1",
            "/dev/nvme6n1",
        )
