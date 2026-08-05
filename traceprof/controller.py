"""SSH controller for storage and optional replay-node profiling."""

from __future__ import annotations

import json
import os
import selectors
import shlex
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregate import aggregate_profiles
from .config import NodeConfig, ProfilerConfig


def _run_id() -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"profile-{now}-{os.getpid()}"


@dataclass
class _ActiveAgent:
    node: NodeConfig
    process: subprocess.Popen[str]


class RemoteProfiler:
    """Manage one profiler run across configured nodes."""

    def __init__(
        self,
        config: ProfilerConfig,
        *,
        output_dir: str | Path,
        run_id: str | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.output_dir = Path(output_dir).expanduser()
        self.run_id = run_id or _run_id()
        self.remote_run_root = (
            Path(config.remote_tmp_root) / self.run_id
        ).as_posix()
        self.profile_root = self.output_dir / "profile"
        self.active: dict[str, _ActiveAgent] = {}

    def _target(self, node: NodeConfig) -> str:
        user = node.user or self.config.ssh_user
        return f"{user}@{node.host}" if user else node.host

    def _remote_dir(self, node: NodeConfig) -> str:
        return f"{self.remote_run_root}/{node.name}"

    def _ssh_command(self, node: NodeConfig, subcommand: str, *args: str) -> list[str]:
        remote_script = f"{self._remote_dir(node)}/.storage_agent.sh"
        remote_parts = ["bash", shlex.quote(remote_script), shlex.quote(subcommand)]
        remote_parts.extend(shlex.quote(arg) for arg in args)
        command = " ".join(remote_parts)
        ssh_args = [self.config.ssh_binary, *self.config.ssh_options]
        if node.port is not None:
            ssh_args += ["-p", str(node.port)]
        return [*ssh_args, self._target(node), command]

    def _deploy_one(self, node: NodeConfig) -> tuple[NodeConfig, str, str, int]:
        ssh_args = [self.config.ssh_binary, *self.config.ssh_options]
        scp_args = [self.config.scp_binary, *self.config.ssh_options]
        if node.port is not None:
            ssh_args += ["-p", str(node.port)]
            scp_args += ["-P", str(node.port)]
        remote_dir = self._remote_dir(node)
        mkdir_result = subprocess.run(
            [
                *ssh_args,
                self._target(node),
                "mkdir -p " + shlex.quote(remote_dir),
            ],
            capture_output=True,
            text=True,
            timeout=self.config.startup_timeout_seconds,
            check=False,
        )
        if mkdir_result.returncode != 0:
            return (
                node,
                mkdir_result.stdout,
                mkdir_result.stderr,
                mkdir_result.returncode,
            )
        result = subprocess.run(
            [
                *scp_args,
                str(Path(__file__).with_name("storage_agent.sh")),
                f"{self._target(node)}:{remote_dir}/.storage_agent.sh",
            ],
            capture_output=True,
            text=True,
            timeout=self.config.startup_timeout_seconds,
            check=False,
        )
        return node, result.stdout, result.stderr, result.returncode

    def _common_args(self, node: NodeConfig) -> list[str]:
        args = ["--run-dir", self._remote_dir(node), "--root", self.remote_run_root]
        for device in node.devices:
            args += ["--device", device]
        for interface in node.interfaces:
            args += ["--interface", interface]
        return args

    def _preflight_one(self, node: NodeConfig) -> tuple[NodeConfig, str, str, int]:
        result = subprocess.run(
            self._ssh_command(node, "preflight", *self._common_args(node)),
            capture_output=True,
            text=True,
            timeout=self.config.startup_timeout_seconds,
            check=False,
        )
        return node, result.stdout, result.stderr, result.returncode

    def preflight(self) -> dict[str, Any]:
        """Run all node checks before any replay process is started."""
        missing_local = [
            binary
            for binary in (self.config.ssh_binary, self.config.scp_binary)
            if shutil.which(binary) is None
        ]
        if missing_local:
            raise RuntimeError(
                "profile controller is missing local tools: "
                + ", ".join(missing_local)
            )
        deployment_failures: list[str] = []
        with ThreadPoolExecutor(max_workers=len(self.config.nodes)) as executor:
            deployment_futures = [
                executor.submit(self._deploy_one, node) for node in self.config.nodes
            ]
            for future in as_completed(deployment_futures):
                node, stdout, stderr, returncode = future.result()
                if returncode != 0:
                    deployment_failures.append(
                        f"{node.name}: shell agent deployment failed ({returncode}): "
                        f"{stderr.strip() or stdout.strip()}"
                    )
        if deployment_failures:
            raise RuntimeError(
                "profile agent deployment failed:\n"
                + "\n".join(deployment_failures)
            )
        failures: list[str] = []
        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=len(self.config.nodes)) as executor:
            futures = [
                executor.submit(self._preflight_one, node)
                for node in self.config.nodes
            ]
            for future in as_completed(futures):
                node, stdout, stderr, returncode = future.result()
                if returncode != 0:
                    failures.append(
                        f"{node.name}: remote preflight failed ({returncode}): "
                        f"{stderr.strip() or stdout.strip()}"
                    )
                    continue
                try:
                    results[node.name] = json.loads(stdout.strip().splitlines()[-1])
                except json.JSONDecodeError as exc:
                    failures.append(
                        f"{node.name}: invalid preflight response: {exc}: {stdout!r}"
                    )
        if failures:
            raise RuntimeError("profile preflight failed:\n" + "\n".join(failures))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "profile_preflight.json").write_text(
            json.dumps({"run_id": self.run_id, "nodes": results}, indent=2) + "\n",
            encoding="utf-8",
        )
        return results

    def start(self) -> None:
        """Start all agents and wait for their READY markers."""
        for node in self.config.nodes:
            command = self._ssh_command(
                node,
                "run",
                *self._common_args(node),
                "--sample-interval",
                str(self.config.sample_interval_seconds),
                "--report-interval",
                str(self.config.report_interval_seconds),
                "--node-name",
                node.name,
                "--role",
                node.role,
                "--run-id",
                self.run_id,
            )
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self.active[node.name] = _ActiveAgent(node, process)

        selector = selectors.DefaultSelector()
        pending = set(self.active)
        for name, active in self.active.items():
            assert active.process.stdout is not None
            selector.register(active.process.stdout, selectors.EVENT_READ, name)
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        try:
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "timed out waiting for profiler agents: "
                        + ", ".join(sorted(pending))
                    )
                events = selector.select(remaining)
                if not events:
                    continue
                for key, _ in events:
                    line = key.fileobj.readline()
                    name = key.data
                    if not line:
                        selector.unregister(key.fileobj)
                        if self.active[name].process.poll() is not None:
                            raise RuntimeError(
                                f"profiler agent {name} exited before READY"
                            )
                        continue
                    if line.startswith("READY\t"):
                        pending.discard(name)
        except BaseException:
            self.stop()
            raise
        finally:
            selector.close()

    def _remote_action(
        self, node: NodeConfig, action: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._ssh_command(node, action, *self._common_args(node)),
            capture_output=True,
            text=True,
            timeout=self.config.stop_timeout_seconds,
            check=False,
        )

    def stop(self) -> None:
        """Ask each remote agent to stop and wait for its SSH process."""
        if not self.active:
            return
        with ThreadPoolExecutor(max_workers=len(self.active)) as executor:
            futures = {
                executor.submit(self._remote_action, active.node, "stop"): name
                for name, active in self.active.items()
            }
            for future in as_completed(futures):
                result = future.result()
                if result.returncode != 0:
                    name = futures[future]
                    print(
                        f"[WARN] profiler stop failed on {name}: "
                        f"{result.stderr.strip() or result.stdout.strip()}",
                        flush=True,
                    )
        deadline = time.monotonic() + self.config.stop_timeout_seconds
        for name, active in list(self.active.items()):
            remaining = max(0.1, deadline - time.monotonic())
            try:
                active.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                print(f"[WARN] terminating SSH profiler process on {name}", flush=True)
                active.process.terminate()
                try:
                    active.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    active.process.kill()
                    active.process.wait(timeout=5)

    def collect(self) -> Path:
        """Copy remote temporary results to the replay output directory."""
        self.profile_root.mkdir(parents=True, exist_ok=True)
        for node in self.config.nodes:
            local_node_dir = self.profile_root / node.name
            if local_node_dir.exists():
                raise RuntimeError(
                    f"local profiler output already exists for {node.name}: "
                    f"{local_node_dir}"
                )
            result = subprocess.run(
                [
                    self.config.scp_binary,
                    *self.config.ssh_options,
                    *(["-P", str(node.port)] if node.port is not None else []),
                    "-r",
                    f"{self._target(node)}:{self._remote_dir(node)}",
                    str(self.profile_root),
                ],
                capture_output=True,
                text=True,
                timeout=self.config.stop_timeout_seconds,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"failed to collect profiler output from {node.name}: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
            summary = local_node_dir / "summary.json"
            if not summary.is_file():
                raise RuntimeError(
                    f"profiler summary missing for {node.name}: {summary}"
                )
            local_agent = local_node_dir / ".storage_agent.sh"
            if local_agent.exists():
                local_agent.unlink()
        return self.profile_root

    def aggregate(self) -> dict[str, Any]:
        return aggregate_profiles(
            self.profile_root,
            self.output_dir / "profile_summary.json",
            run_id=self.run_id,
        )

    def cleanup(self) -> None:
        if not self.config.cleanup_on_success:
            return
        with ThreadPoolExecutor(max_workers=len(self.config.nodes)) as executor:
            futures = {
                executor.submit(self._remote_action, node, "cleanup"): node
                for node in self.config.nodes
            }
            for future in as_completed(futures):
                node = futures[future]
                result = future.result()
                if result.returncode != 0:
                    raise RuntimeError(
                        f"failed to clean profiler output on {node.name}: "
                        f"{result.stderr.strip() or result.stdout.strip()}"
                    )
