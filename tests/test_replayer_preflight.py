from types import SimpleNamespace

import replayer.preflight as preflight_module
from replayer.preflight import run_l2_preflight, summarize_l2_plan

GB = 1_000_000_000


def _operation(operation, keys=(), sizes=(), t_mono=0.0):
    args = {"keys": list(keys)}
    if sizes:
        args["object_sizes"] = list(sizes)
    return SimpleNamespace(operation=operation, args=args, t_mono=t_mono)


def _plan():
    return SimpleNamespace(
        operations=[
            _operation("store", ("a", "c"), (15 * GB, 30 * GB), 1.0),
            _operation("lookup_task", ("a",), t_mono=2.0),
            _operation("delete", ("b",), t_mono=3.0),
        ],
        prepare_objects={"a": 10 * GB, "b": 20 * GB},
        trace_percent=10.0,
        source_operations_total=30,
    )


def test_summarize_l2_plan_estimates_overwrite_delete_and_peak():
    summary = summarize_l2_plan(_plan(), trace_path="trace.lct")

    assert summary["operations_selected"] == 3
    assert summary["operations_selected_by_type"] == {
        "store": 1,
        "lookup_task": 1,
        "load_task": 0,
        "unlock": 0,
        "delete": 1,
    }
    assert summary["logical_kv_estimate"] == {
        "unit": "GB",
        "after_prepare_gb": 30.0,
        "store_submission_gb": 45.0,
        "unique_candidate_gb": 65.0,
        "peak_gb": 65.0,
        "final_gb": 45.0,
        "after_prepare_objects": 2,
        "peak_objects": 3,
        "final_objects": 2,
    }


def test_run_l2_preflight_prints_gb_and_optionally_writes_json(
    capsys, monkeypatch, tmp_path
):
    monkeypatch.setattr(preflight_module, "_load_l2_plan", lambda *_: _plan())

    summary = run_l2_preflight("trace.lct", 10.0, output_dir=tmp_path)

    output = capsys.readouterr().out
    assert "peak=65.000 GB" in output
    assert "final=45.000 GB" in output
    assert "bytes" not in output
    assert (tmp_path / "l2_preflight.json").is_file()
    assert summary["logical_kv_estimate"]["unit"] == "GB"
