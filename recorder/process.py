"""Small process lifecycle helpers used by the live recorder."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass
class ManagedProcess:
    """A child process and the file receiving its combined stdout/stderr."""

    name: str
    command: list[str]
    process: subprocess.Popen[str]
    log_file: object

    def close_log(self) -> None:
        close = getattr(self.log_file, "close", None)
        if close is not None:
            close()


def start_process(
    name: str,
    command: list[str],
    *,
    env: Mapping[str, str],
    log_path: str | Path,
) -> ManagedProcess:
    """Start a command with a dedicated, line-buffered log file."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_file = path.open("w", encoding="utf-8", buffering=1)
    try:
        process = subprocess.Popen(
            command,
            env=dict(env),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except Exception:
        log_file.close()
        raise
    return ManagedProcess(name, list(command), process, log_file)


def _log_tail(path: str | Path, max_bytes: int = 4000) -> str:
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - max_bytes), os.SEEK_SET)
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def wait_for_http(
    managed: ManagedProcess,
    url: str,
    *,
    timeout_seconds: float,
    poll_seconds: float = 2.0,
) -> None:
    """Wait until an HTTP endpoint responds or the child exits/times out."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        return_code = managed.process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"{managed.name} exited with code {return_code} before readiness; "
                f"log tail:\n{_log_tail(managed.log_file.name)}"
            )
        try:
            with urllib.request.urlopen(url, timeout=min(poll_seconds, 5.0)) as response:
                if 200 <= response.status < 400:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(poll_seconds)
    raise TimeoutError(
        f"timed out waiting for {managed.name} at {url}; "
        f"log tail:\n{_log_tail(managed.log_file.name)}"
    )


def wait_for_tcp(
    managed: ManagedProcess,
    host: str,
    port: int,
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.5,
) -> None:
    """Wait until a process accepts a TCP connection on ``host:port``."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        return_code = managed.process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"{managed.name} exited with code {return_code} before readiness; "
                f"log tail:\n{_log_tail(managed.log_file.name)}"
            )
        try:
            with socket.create_connection((host, port), timeout=poll_seconds):
                return
        except OSError:
            time.sleep(poll_seconds)
    raise TimeoutError(
        f"timed out waiting for {managed.name} at {host}:{port}; "
        f"log tail:\n{_log_tail(managed.log_file.name)}"
    )


def stop_process(managed: ManagedProcess, *, timeout_seconds: float) -> int | None:
    """Terminate a process group and close its log file.

    vLLM workers can outlive the API-server process after a CUDA failure.  The
    workers remain in the session created by :func:`start_process`, so signal
    the group even when the group leader has already exited and wait for the
    entire group before deciding whether SIGKILL is required.
    """
    process_group_id = managed.process.pid
    deadline = time.monotonic() + timeout_seconds

    def group_exists() -> bool:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def wait_for_group() -> bool:
        while group_exists():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
        return True

    try:
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass

        if managed.process.poll() is None:
            try:
                managed.process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass

        if not wait_for_group():
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if managed.process.poll() is None:
                managed.process.wait(timeout=5)
        return managed.process.returncode
    finally:
        managed.close_log()
