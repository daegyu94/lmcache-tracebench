import os
import shutil
import subprocess
from pathlib import Path

REAL_SCRIPT = Path(__file__).parents[1] / "scripts/setup_runtime.sh"


def _copy_script_into(project_root):
    # setup_runtime.sh derives project_root from its own BASH_SOURCE
    # location, so it must be copied into an isolated project tree rather
    # than invoked from the real repo (that would touch the real .venv).
    scripts_dir = project_root / "scripts"
    scripts_dir.mkdir(parents=True)
    script_copy = scripts_dir / "setup_runtime.sh"
    shutil.copy(REAL_SCRIPT, script_copy)
    script_copy.chmod(0o755)
    return script_copy


def _path_without_uv(env):
    dirs = env["PATH"].split(os.pathsep)
    dirs = [d for d in dirs if not (Path(d) / "uv").is_file()]
    return os.pathsep.join(dirs)


def _write_fake_python(fake_python, python_log, env_log):
    # Fake Python that creates a venv on demand and logs every invocation so
    # tests can assert on the pip commands setup_runtime.sh issued. The venv
    # it creates is just a copy of itself, so later `-m pip` calls land back
    # here and get logged too. It also logs selected env vars so tests can
    # verify NO_GPU_EXT propagation.
    fake_python.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {python_log}
printf 'NO_GPU_EXT=%s\\n' "${{NO_GPU_EXT:-}}" >> {env_log}
case "$1" in
  -m)
    case "$2" in
      venv)
        dest="${{@: -1}}"
        mkdir -p "$dest/bin"
        cp {fake_python} "$dest/bin/python"
        ;;
      pip)
        if [[ "$3" == "--version" ]]; then exit 1; fi
        ;;
    esac
    ;;
esac
exit 0
"""
    )
    fake_python.chmod(0o755)


def _write_fake_nvidia_smi(fake_bin):
    # replayer-gpu and recorder profiles require nvidia-smi on PATH.
    fake_nvidia_smi = fake_bin / "nvidia-smi"
    fake_nvidia_smi.write_text("#!/usr/bin/env bash\necho 'fake-gpu,0 MiB'\n")
    fake_nvidia_smi.chmod(0o755)


def _isolated_path_no_nvidia_smi(fake_bin, env):
    # Build a PATH that contains fake_bin (with fake python and symlinked
    # cp/mkdir/dirname) but excludes any system directory that ships
    # nvidia-smi, so the script's `command -v nvidia-smi` check fails on
    # hosts that happen to have a (possibly broken) system nvidia-smi.
    for tool in ("cp", "mkdir", "dirname"):
        src = shutil.which(tool)
        if src:
            (fake_bin / tool).symlink_to(src)
    nvidia_dirs = {
        d for d in env["PATH"].split(os.pathsep)
        if d and (Path(d) / "nvidia-smi").exists()
    }
    dirs = [str(fake_bin)] + [
        d for d in env["PATH"].split(os.pathsep)
        if d and d not in nvidia_dirs
    ]
    return os.pathsep.join(dirs)


def test_setup_runtime_requires_uv_without_explicit_python(tmp_path):
    project_root = tmp_path / "project"
    script_copy = _copy_script_into(project_root)
    (project_root / "requirements").mkdir()
    (project_root / "requirements/replayer.txt").write_text("\n")

    env = os.environ.copy()
    env["PATH"] = _path_without_uv(env)

    result = subprocess.run(
        ["bash", str(script_copy), "--check", "--profile", "replayer-cpu"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "uv is required when --python is not provided." in result.stderr


def test_setup_runtime_with_explicit_python_does_not_need_uv(tmp_path):
    # Passing --python selects the venv/ensurepip/pip fallback path, which
    # must work even when uv is nowhere on PATH (e.g. an isolated replay
    # node that only has the approved system Python).
    project_root = tmp_path / "project"
    script_copy = _copy_script_into(project_root)
    (project_root / "requirements").mkdir()
    (project_root / "requirements/replayer.txt").write_text("\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    env_log = tmp_path / "env.log"
    venv_bin = project_root / ".venv/bin"

    fake_python = fake_bin / "fake_python3.12"
    _write_fake_python(fake_python, python_log, env_log)

    env = os.environ.copy()
    env["PATH"] = _path_without_uv(env)

    result = subprocess.run(
        [
            "bash",
            str(script_copy),
            "--profile",
            "replayer-cpu",
            "--python",
            str(fake_python),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    # This fake python stack can't satisfy the later lmcache/replayer import
    # smoke tests, so we only assert on the venv-creation/install commands
    # setup_runtime.sh issued before that point.
    log_text = python_log.read_text()
    assert f"-m venv {project_root}/.venv" in log_text
    assert "-m ensurepip --upgrade" in log_text
    assert "-m pip install" in log_text
    assert (venv_bin / "python").is_file()
    assert result.returncode != 0  # fails later at the fake-stack import check
    assert "uv is required" not in result.stderr


def _assert_replayer_cpu_torch(log_text, env_text):
    """Shared assertions for the replayer-cpu profile."""
    torch_lines = [
        line for line in log_text.splitlines()
        if "torch==2.11.0" in line and "install" in line
    ]
    assert torch_lines, f"torch bootstrap not logged: {log_text!r}"
    assert all(
        "--index-url https://download.pytorch.org/whl/cpu" in line
        for line in torch_lines
    ), f"torch bootstrap must use CPU index: {torch_lines!r}"
    # Torch bootstrap must also re-pin setuptools so torch's setuptools<82
    # dep cannot pull 81.x from PyPI and break LMCache's <81.0.0 pin.
    assert all(
        "setuptools>=77.0.3,<81.0.0" in line for line in torch_lines
    ), f"torch bootstrap must pin setuptools: {torch_lines!r}"
    assert any(
        "--extra-index-url https://download.pytorch.org/whl/cpu" in line
        and "replayer.txt" in line
        for line in log_text.splitlines()
    ), f"requirements install must keep CPU torch index: {log_text!r}"
    # NO_GPU_EXT=1 must be exported so LMCache skips the c_ops CUDA build.
    env_lines = env_text.splitlines()
    assert any(
        line == "NO_GPU_EXT=1" for line in env_lines
    ), f"NO_GPU_EXT=1 must be exported for replayer-cpu: {env_lines!r}"


def test_setup_runtime_replayer_cpu_uses_cpu_torch_and_no_gpu_ext(tmp_path):
    # The replayer-cpu profile never touches a GPU, so setup_runtime.sh must
    # bootstrap torch from the CPU-only wheel index, keep that index
    # available during the requirements install so --force-reinstall cannot
    # swap torch back to a CUDA wheel, and export NO_GPU_EXT=1 so LMCache
    # skips the c_ops CUDA extension build (the PEP 562 c_ops shim resolves
    # to CpuDeviceOps at runtime, so the .so is not needed).
    project_root = tmp_path / "project"
    script_copy = _copy_script_into(project_root)
    (project_root / "requirements").mkdir()
    (project_root / "requirements/replayer.txt").write_text("lmcache\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    env_log = tmp_path / "env.log"
    venv_bin = project_root / ".venv/bin"

    fake_python = fake_bin / "fake_python3.12"
    _write_fake_python(fake_python, python_log, env_log)

    env = os.environ.copy()
    env["PATH"] = _path_without_uv(env)

    subprocess.run(
        [
            "bash",
            str(script_copy),
            "--profile",
            "replayer-cpu",
            "--python",
            str(fake_python),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    _assert_replayer_cpu_torch(python_log.read_text(), env_log.read_text())
    assert (venv_bin / "python").is_file()


def test_setup_runtime_replayer_gpu_uses_cuda_torch(tmp_path):
    # The replayer-gpu profile opts into CUDA torch and the full c_ops
    # build: setup_runtime.sh must NOT redirect torch to the CPU-only
    # index, must NOT export NO_GPU_EXT=1, and must require nvidia-smi.
    project_root = tmp_path / "project"
    script_copy = _copy_script_into(project_root)
    (project_root / "requirements").mkdir()
    (project_root / "requirements/replayer.txt").write_text("lmcache\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    env_log = tmp_path / "env.log"
    venv_bin = project_root / ".venv/bin"

    _write_fake_nvidia_smi(fake_bin)
    fake_python = fake_bin / "fake_python3.12"
    _write_fake_python(fake_python, python_log, env_log)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{_path_without_uv(env)}"

    subprocess.run(
        [
            "bash",
            str(script_copy),
            "--profile",
            "replayer-gpu",
            "--python",
            str(fake_python),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    log_text = python_log.read_text()
    torch_lines = [
        line for line in log_text.splitlines()
        if "torch==2.11.0" in line and "install" in line
    ]
    assert torch_lines, f"torch bootstrap not logged: {log_text!r}"
    assert all(
        "download.pytorch.org/whl/cpu" not in line for line in torch_lines
    ), f"replayer-gpu must not use CPU torch index: {torch_lines!r}"
    # NO_GPU_EXT must NOT be exported for the replayer-gpu profile.
    env_lines = env_log.read_text().splitlines()
    assert all(
        line != "NO_GPU_EXT=1" for line in env_lines
    ), f"NO_GPU_EXT=1 must not be exported for replayer-gpu: {env_lines!r}"
    assert (venv_bin / "python").is_file()


def test_setup_runtime_replayer_gpu_requires_nvidia_smi(tmp_path):
    # replayer-gpu must fail fast when nvidia-smi is absent, mirroring the
    # recorder profile's GPU requirement.
    project_root = tmp_path / "project"
    script_copy = _copy_script_into(project_root)
    (project_root / "requirements").mkdir()
    (project_root / "requirements/replayer.txt").write_text("\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    env_log = tmp_path / "env.log"

    fake_python = fake_bin / "fake_python3.12"
    _write_fake_python(fake_python, python_log, env_log)

    env = os.environ.copy()
    # PATH with no nvidia-smi anywhere (isolates the test from hosts that
    # have a broken system nvidia-smi).
    env["PATH"] = _isolated_path_no_nvidia_smi(fake_bin, env)

    result = subprocess.run(
        [
            shutil.which("bash"),
            str(script_copy),
            "--profile",
            "replayer-gpu",
            "--python",
            str(fake_python),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "nvidia-smi was not found" in result.stderr
    assert "replayer-gpu" in result.stderr


def test_setup_runtime_recorder_keeps_default_torch_index(tmp_path):
    # The recorder profile needs CUDA-enabled torch, so setup_runtime.sh must
    # NOT redirect torch to the CPU-only index and must NOT export
    # NO_GPU_EXT=1 when --profile recorder is used.
    project_root = tmp_path / "project"
    script_copy = _copy_script_into(project_root)
    (project_root / "requirements").mkdir()
    (project_root / "requirements/recorder.txt").write_text("lmcache\n")
    # The recorder profile also pulls the Tensormesh-Benchmark requirements;
    # point the script at a stub so the fake repo resolves it.
    tensor_mesh = project_root / "third_party/Tensormesh-Benchmark/src"
    tensor_mesh.mkdir(parents=True)
    (tensor_mesh / "requirements.txt").write_text("\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    env_log = tmp_path / "env.log"
    venv_bin = project_root / ".venv/bin"

    _write_fake_nvidia_smi(fake_bin)
    fake_python = fake_bin / "fake_python3.12"
    _write_fake_python(fake_python, python_log, env_log)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{_path_without_uv(env)}"

    subprocess.run(
        [
            "bash",
            str(script_copy),
            "--profile",
            "recorder",
            "--python",
            str(fake_python),
            "--runtime-requirements",
            "requirements/recorder.txt",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    log_text = python_log.read_text()
    torch_lines = [
        line for line in log_text.splitlines()
        if "torch==2.11.0" in line and "install" in line
    ]
    assert torch_lines, f"torch bootstrap not logged: {log_text!r}"
    assert all(
        "download.pytorch.org/whl/cpu" not in line for line in torch_lines
    ), f"recorder must not use CPU torch index: {torch_lines!r}"
    # NO_GPU_EXT must NOT be exported for the recorder profile.
    env_lines = env_log.read_text().splitlines()
    assert all(
        line != "NO_GPU_EXT=1" for line in env_lines
    ), f"NO_GPU_EXT=1 must not be exported for recorder: {env_lines!r}"
    assert (venv_bin / "python").is_file()
