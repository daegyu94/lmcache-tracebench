"""Adapter around Tensormesh-Benchmark V3 workload loading for recording."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import WorkloadConfig
@dataclass(frozen=True)
class WorkloadPlan:
    sessions: list[Any]
    source_counts: dict[str, int]
    session_ids: list[str]
    v3_config: Any = None


def _load_tensormesh_modules(root: str | Path):
    root_path = Path(root).expanduser().resolve()
    src_path = root_path / "src"
    if not src_path.is_dir():
        raise FileNotFoundError(f"Tensormesh-Benchmark src directory not found: {src_path}")
    src_text = str(src_path)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)
    try:
        from v3_config import V3Config
        from v3_workload_generator import load_trace_sessions
    except ImportError as exc:
        raise RuntimeError(
            "Tensormesh-Benchmark V3 dependencies are unavailable; "
            "initialize the tracebench submodule and install its requirements"
        ) from exc
    return V3Config, load_trace_sessions


def load_workload(config: WorkloadConfig, *, verbose: bool = True) -> WorkloadPlan:
    """Load V3 sessions using its native mixed-source ordering."""
    V3Config, load_trace_sessions = _load_tensormesh_modules(config.tensormesh_root)
    v3_config = V3Config(
        source=config.source,
        dataset_model=config.dataset_model,
        num_sessions=config.num_sessions,
        max_turns_per_session=config.max_turns_per_session,
        max_input_tokens=config.max_input_tokens,
        max_concurrent_sessions=config.max_concurrent_sessions,
        mixed_session_order=config.mixed_session_order,
        mixed_source_order=config.mixed_source_order,
        timing_mode=config.timing_mode,
        pre_gap_scale=config.pre_gap_scale,
        flatten_tools=config.flatten_tools,
    )
    sessions = load_trace_sessions(
        v3_config,
        cache_dir=config.hf_cache_dir,
        streaming=config.hf_streaming,
        verbose=verbose,
    )
    source_counts: dict[str, int] = {}
    session_ids: list[str] = []
    for session in sessions:
        source = str(getattr(session, "source", "unknown"))
        source_counts[source] = source_counts.get(source, 0) + 1
        session_ids.append(str(getattr(session, "session_id", "")))
    return WorkloadPlan(sessions, source_counts, session_ids, v3_config)
