from types import SimpleNamespace

import pytest

import recorder.workload as workload_module
from recorder.config import WorkloadConfig


class _FakeV3Config:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _fake_sessions():
    return [
        SimpleNamespace(
            session_id=f"swebench__{index}",
            source="swebench",
            turns=[object()] * (index + 1),
        )
        for index in range(10)
    ]


def test_load_workload_selects_swebench_percentage(monkeypatch):
    monkeypatch.setattr(
        workload_module,
        "_load_tensormesh_modules",
        lambda root: (_FakeV3Config, lambda *args, **kwargs: _fake_sessions()),
    )
    config = WorkloadConfig(source="swebench", num_sessions=None)

    plan = workload_module.load_workload(config, dataset_percent=10, verbose=False)

    assert plan.total_sessions == 10
    assert plan.total_turns == 55
    assert len(plan.sessions) == 1
    assert plan.selected_turns == 1
    assert plan.dataset_percent == 10
    assert plan.dataset_percent_applied is True


@pytest.mark.parametrize("source", ["gaia", "wildclaw"])
def test_load_workload_ignores_percentage_for_other_sources(monkeypatch, source):
    monkeypatch.setattr(
        workload_module,
        "_load_tensormesh_modules",
        lambda root: (_FakeV3Config, lambda *args, **kwargs: _fake_sessions()),
    )
    config = WorkloadConfig(source=source, num_sessions=2)

    plan = workload_module.load_workload(config, dataset_percent=10, verbose=False)

    assert plan.total_sessions == 10
    assert plan.total_turns == 55
    assert len(plan.sessions) == 10
    assert plan.selected_turns == 55
    assert plan.dataset_percent == 10
    assert plan.dataset_percent_applied is False
