import json

import pytest

from replayer.instances import (
    build_instance_command,
    build_instance_plans,
    main,
    run_instances,
)


def test_instance_plans_use_isolated_paths(tmp_path):
    plans = build_instance_plans(
        3,
        l2_root=tmp_path / "l2",
        output_root=tmp_path / "outputs",
    )

    assert [plan.instance_id for plan in plans] == [0, 1, 2]
    assert plans[0].l2_path == (tmp_path / "l2" / "instance-0").resolve()
    assert plans[2].output_dir == tmp_path / "outputs" / "instance-2"


def test_instance_command_overrides_l2_and_output(tmp_path):
    plan = build_instance_plans(
        1,
        l2_root=tmp_path / "l2",
        output_root=tmp_path / "outputs",
    )[0]

    command = build_instance_command(
        "storage.lct",
        "configs/replayer/fs-native.yaml",
        plan,
        python_executable="python",
        dry_run=True,
    )

    assert command[:3] == ["python", "-m", "replayer.main"]
    assert "--l2-path" in command
    assert str(plan.l2_path) in command
    assert "--output-dir" in command
    assert str(plan.output_dir) in command
    assert command[-1] == "--dry-run"


def test_instances_dry_run_prints_all_commands(capsys, tmp_path):
    trace = tmp_path / "storage.lct"
    trace.touch()

    assert (
        main(
            [
                "--instances",
                "2",
                "--trace",
                str(trace),
                "--config",
                "configs/replayer/smoke.yaml",
                "--l2-root",
                str(tmp_path / "l2"),
                "--output-root",
                str(tmp_path / "outputs"),
                "--dry-run",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Instance 0:" in output
    assert "Instance 1:" in output
    assert "instance-0" in output
    assert "instance-1" in output


def test_instances_write_summary_after_success(monkeypatch, tmp_path):
    trace = tmp_path / "storage.lct"
    trace.touch()
    plans = build_instance_plans(
        2,
        l2_root=tmp_path / "l2",
        output_root=tmp_path / "outputs",
    )
    for plan in plans:
        plan.l2_path.mkdir(parents=True)
        (plan.l2_path / "old-cache-entry").write_text("stale", encoding="utf-8")

    class CompletedProcess:
        def __init__(self):
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    class PopenStub:
        def __init__(self, *args, **kwargs):
            self.process = CompletedProcess()

        def __new__(cls, *args, **kwargs):
            return CompletedProcess()

    monkeypatch.setattr("replayer.instances.subprocess.Popen", PopenStub)

    assert (
        run_instances(
            trace=trace,
            config="configs/replayer/smoke.yaml",
            plans=plans,
            output_root=tmp_path / "outputs",
        )
        == 0
    )
    summary = json.loads(
        (tmp_path / "outputs" / "instances-summary.json").read_text()
    )
    assert summary["instances"] == 2
    assert [item["returncode"] for item in summary["results"]] == [0, 0]
    assert all(
        not (plan.l2_path / "old-cache-entry").exists() for plan in plans
    )


def test_instances_keep_l2_rejects_nonempty_paths(tmp_path):
    trace = tmp_path / "storage.lct"
    trace.touch()
    plans = build_instance_plans(
        1,
        l2_root=tmp_path / "l2",
        output_root=tmp_path / "outputs",
    )
    plans[0].l2_path.mkdir(parents=True)
    (plans[0].l2_path / "old-cache-entry").write_text("stale", encoding="utf-8")

    with pytest.raises(ValueError, match="not empty"):
        run_instances(
            trace=trace,
            config="configs/replayer/smoke.yaml",
            plans=plans,
            output_root=tmp_path / "outputs",
            keep_l2=True,
        )


def test_instance_count_and_l2_root_are_validated(tmp_path):
    with pytest.raises(ValueError, match="instances must be positive"):
        build_instance_plans(
            0,
            l2_root=tmp_path / "l2",
            output_root=tmp_path / "outputs",
        )
    with pytest.raises(ValueError, match="l2_root must be an absolute path"):
        build_instance_plans(
            1,
            l2_root="relative/l2",
            output_root=tmp_path / "outputs",
        )
