"""Aggregate node-level profiler summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _add_metric(target: dict[str, Any], source: dict[str, Any], key: str) -> None:
    target[key] = target.get(key, 0) + source.get(key, 0)


def aggregate_profiles(
    profile_root: str | Path, output_path: str | Path, *, run_id: str
) -> dict[str, Any]:
    root = Path(profile_root)
    node_summaries: dict[str, Any] = {}
    disk_totals: dict[str, Any] = {}
    node_disk_totals: dict[str, dict[str, Any]] = {}
    cluster_disk_grand_total: dict[str, Any] = {}
    interface_totals: dict[str, Any] = {}
    interface_totals_by_role: dict[str, dict[str, Any]] = {}
    durations: list[float] = []

    for summary_path in sorted(root.glob("*/summary.json")):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        node_name = str(summary.get("node", summary_path.parent.name))
        node_summaries[node_name] = summary
        duration = float(summary.get("duration_seconds", 0))
        if duration > 0:
            durations.append(duration)
        node_total = node_disk_totals.setdefault(node_name, {})
        for device, metrics in summary.get("devices", {}).items():
            total = disk_totals.setdefault(device, {})
            for key in ("read_bytes", "write_bytes"):
                _add_metric(total, metrics, key)
                _add_metric(node_total, metrics, key)
                _add_metric(cluster_disk_grand_total, metrics, key)
        for interface, metrics in summary.get("interfaces", {}).items():
            total = interface_totals.setdefault(interface, {})
            role = str(summary.get("role", "storage"))
            role_totals = interface_totals_by_role.setdefault(role, {})
            role_total = role_totals.setdefault(interface, {})
            for key in (
                "rx_bytes",
                "tx_bytes",
                "rx_packets",
                "tx_packets",
                "rx_errors",
                "tx_errors",
                "rx_drops",
                "tx_drops",
            ):
                _add_metric(total, metrics, key)
                _add_metric(role_total, metrics, key)

    duration = max(durations, default=0.0)
    divisor = duration or 1.0
    for metrics in disk_totals.values():
        metrics["read_mibps_avg"] = metrics["read_bytes"] / divisor / 1024**2
        metrics["write_mibps_avg"] = metrics["write_bytes"] / divisor / 1024**2
    for node_name, metrics in node_disk_totals.items():
        if not metrics:
            continue
        node_divisor = float(node_summaries[node_name].get("duration_seconds", 0)) or 1.0
        metrics["read_mibps_avg"] = metrics["read_bytes"] / node_divisor / 1024**2
        metrics["write_mibps_avg"] = metrics["write_bytes"] / node_divisor / 1024**2
    if cluster_disk_grand_total:
        cluster_disk_grand_total["read_mibps_avg"] = (
            cluster_disk_grand_total["read_bytes"] / divisor / 1024**2
        )
        cluster_disk_grand_total["write_mibps_avg"] = (
            cluster_disk_grand_total["write_bytes"] / divisor / 1024**2
        )
    for metrics in interface_totals.values():
        metrics["rx_mibps_avg"] = metrics["rx_bytes"] / divisor / 1024**2
        metrics["tx_mibps_avg"] = metrics["tx_bytes"] / divisor / 1024**2
    for role_totals in interface_totals_by_role.values():
        for metrics in role_totals.values():
            metrics["rx_mibps_avg"] = metrics["rx_bytes"] / divisor / 1024**2
            metrics["tx_mibps_avg"] = metrics["tx_bytes"] / divisor / 1024**2

    result = {
        "schema_version": 1,
        "run_id": run_id,
        "duration_seconds": duration,
        "nodes": node_summaries,
        "cluster_disk_totals": disk_totals,
        "node_disk_totals": node_disk_totals,
        "cluster_disk_grand_total": cluster_disk_grand_total,
        "cluster_interface_totals": interface_totals,
        "interface_totals_by_role": interface_totals_by_role,
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
