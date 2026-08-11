"""Adapter around Tensormesh-Benchmark V3 workload loading for recording."""

from __future__ import annotations

import math
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
    total_sessions: int = 0
    total_turns: int = 0
    dataset_percent: float | None = None
    dataset_percent_applied: bool = False

    @property
    def selected_turns(self) -> int:
        """Return the number of requests represented by selected sessions."""
        return sum(len(getattr(session, "turns", ())) for session in self.sessions)


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


def _validate_dataset_percent(dataset_percent: float) -> float:
    if (
        not math.isfinite(dataset_percent)
        or dataset_percent <= 0
        or dataset_percent > 100
    ):
        raise ValueError("dataset_percent must be greater than 0 and at most 100")
    return float(dataset_percent)


def load_workload(
    config: WorkloadConfig,
    *,
    dataset_percent: float | None = None,
    verbose: bool = True,
) -> WorkloadPlan:
    """Load V3 sessions and optionally select a deterministic dataset prefix.

    The percentage is defined over sessions after the configured source and
    dataset-model filters.  It is applied to SWE-bench.  GAIA and WildClaw
    intentionally ignore the option and load their complete source dataset.
    """
    if dataset_percent is not None:
        dataset_percent = _validate_dataset_percent(dataset_percent)
        if config.source not in {"swebench", "gaia", "wildclaw"}:
            raise ValueError(
                "dataset_percent requires a SWE-bench, GAIA, or WildClaw source"
            )

    apply_percent = dataset_percent is not None and config.source == "swebench"

    V3Config, load_trace_sessions = _load_tensormesh_modules(config.tensormesh_root)
    v3_config = V3Config(
        source=config.source,
        dataset_model=config.dataset_model,
        # Load the complete filtered source before applying a percentage so
        # the denominator is the actual dataset size, not the config default.
        num_sessions=(
            None
            if dataset_percent is not None
            else config.num_sessions
        ),
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
    total_sessions = len(sessions)
    total_turns = sum(len(getattr(session, "turns", ())) for session in sessions)
    if apply_percent:
        selected_count = math.ceil(total_sessions * dataset_percent / 100.0)
        sessions = sessions[:selected_count]
        if verbose:
            print(
                f"  Selected {len(sessions)} of {total_sessions} session(s) "
                f"({dataset_percent:g}%)"
            )
    elif dataset_percent is not None and verbose:
        print(
            f"  Ignoring dataset percentage for {config.source}; "
            f"using all {total_sessions} session(s)"
        )

    source_counts: dict[str, int] = {}
    session_ids: list[str] = []
    for session in sessions:
        source = str(getattr(session, "source", "unknown"))
        source_counts[source] = source_counts.get(source, 0) + 1
        session_ids.append(str(getattr(session, "session_id", "")))
    return WorkloadPlan(
        sessions,
        source_counts,
        session_ids,
        v3_config,
        total_sessions=total_sessions,
        total_turns=total_turns,
        dataset_percent=dataset_percent,
        dataset_percent_applied=apply_percent,
    )
