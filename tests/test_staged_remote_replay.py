import os
import subprocess
import tarfile
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "benchmarks/replayer/staged_remote_replay.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def test_staged_remote_replay_retrieves_results_after_failure(tmp_path):
    controller = tmp_path / "controller"
    controller_repo = controller / "lmcache-tracebench"
    controller_venv = controller_repo / ".venv"
    controller_traces = controller / "traces"
    controller_outputs = controller / "outputs"
    remote = tmp_path / "remote"
    remote_repo = remote / "lmcache-tracebench"
    remote_traces = remote / "traces"
    remote_outputs = remote / "outputs"
    remote_l2 = remote / "kvcache"
    fake_bin = tmp_path / "fake-bin"

    (controller_repo / "scripts").mkdir(parents=True)
    _write_executable(
        controller_repo / "scripts/setup_runtime.sh",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    (controller_venv / "bin").mkdir(parents=True)
    (controller_venv / "pyvenv.cfg").write_text(
        "home = /usr\nversion_info = 3.12.3\n"
    )
    _write_executable(
        controller_venv / "bin/python",
        """#!/usr/bin/env bash
if [[ "$1" == "-c" && "$2" == *sys.version_info* ]]; then
  printf "3.12\\n"
fi
exit 0
""",
    )
    (controller_venv / "lib/python3.12/site-packages").mkdir(parents=True)
    (controller_venv / "lib/python3.12/site-packages/editable.pth").write_text(
        f"{controller_repo / 'src'}\n"
    )

    archive = controller_traces / "tensormesh/wildclaw.tar.gz"
    archive.parent.mkdir(parents=True)
    payload = tmp_path / "payload/wildclaw"
    payload.mkdir(parents=True)
    (payload / "l2.lct").write_text("fake trace\n")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="wildclaw")

    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3.12",
        """#!/usr/bin/env bash
if [[ \"$1\" == \"-c\" ]]; then
  if [[ \"$2\" == *sys.version_info* ]]; then printf \"3.12\\n\"; fi
  if [[ \"$2\" == *sys.base_prefix* ]]; then printf \"/usr\\n\"; fi
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
while (($#)); do
  case \"$1\" in
    -o|-p) shift 2 ;;
    *) target=\"$1\"; shift; break ;;
  esac
done
exec bash -lc \"$*\"
""",
    )
    _write_executable(
        fake_bin / "scp",
        """#!/usr/bin/env bash
set -euo pipefail
args=()
while (($#)); do
  case \"$1\" in
    -q|-r|--) shift ;;
    -P|-o) shift 2 ;;
    *) args+=(\"$1\"); shift ;;
  esac
done
src=\"${args[${#args[@]}-2]}\"
dst=\"${args[${#args[@]}-1]}\"
map_path() { case \"$1\" in *@*:*) printf '%s\\n' \"${1#*:}\" ;; *) printf '%s\\n' \"$1\" ;; esac; }
src_path=\"$(map_path \"$src\")\"
dst_path=\"$(map_path \"$dst\")\"
mkdir -p -- \"$(dirname -- \"$dst_path\")\"
cp -a -- \"$src_path\" \"$dst_path\"
""",
    )

    topology = tmp_path / "topology.yaml"
    topology.write_text(
        f"""runtime_mode: copy_venv
controller_repo_root: {controller_repo}
controller_venv_root: {controller_venv}
controller_trace_root: {controller_traces}
controller_output_root: {controller_outputs}
replay_host: fake-replay
replay_user: fake
replay_port: 22
replay_repo_root: {remote_repo}
replay_venv_root: {remote_repo}/.venv
replay_trace_root: {remote_traces}
replay_output_root: {remote_outputs}
replay_l2_root: {remote_l2}
git_repo_url: git@github.com:daegyu94/lmcache-tracebench.git
git_revision: dev
hf_repo_id: daegyu94/lmcache-storage-traces
hf_revision: main
transfer_method: scp
"""
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "all",
            "--topology",
            str(topology),
            "--asset",
            "tensormesh/wildclaw.tar.gz",
            "--run-name",
            "fake-failure",
            "--",
            "bash",
            "-c",
            "mkdir -p @OUTPUT_ROOT@; printf fake-result > @OUTPUT_ROOT@/result.txt; exit 7",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 7
    assert "Controller archive already exists; not downloading or overwriting" in result.stderr
    assert "Controller venv already exists; not rebuilding or overwriting" in result.stderr
    assert (remote_traces / "tensormesh/wildclaw/l2.lct").is_file()
    assert (remote_outputs / "fake-failure/result.txt").read_text() == "fake-result"
    assert (controller_outputs / "fake-failure/result.txt").read_text() == "fake-result"
    assert (
        controller_outputs / "fake-failure/remote_exit_code"
    ).read_text().strip() == "7"



def test_remote_install_prepares_runtime_on_replay_node(tmp_path):
    controller = tmp_path / "controller"
    controller_repo = controller / "lmcache-tracebench"
    controller_traces = controller / "traces"
    controller_outputs = controller / "outputs"
    remote = tmp_path / "remote"
    remote_repo = remote / "lmcache-tracebench"
    remote_traces = remote / "traces"
    remote_outputs = remote / "outputs"
    remote_l2 = remote / "kvcache"
    fake_bin = tmp_path / "fake-bin"

    (controller_repo / "scripts").mkdir(parents=True)
    _write_executable(
        controller_repo / "scripts/setup_runtime.sh",
        """#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
printf '%s\\n' "$*" > "$repo_root/setup_args"
mkdir -p "$repo_root/.venv/bin"
ln -sfn "$(command -v python3.12)" "$repo_root/.venv/bin/python"
printf 'home = /usr\\nversion_info = 3.12.3\\n' > "$repo_root/.venv/pyvenv.cfg"
""",
    )

    archive = controller_traces / "tensormesh/wildclaw.tar.gz"
    archive.parent.mkdir(parents=True)
    payload = tmp_path / "payload/wildclaw"
    payload.mkdir(parents=True)
    (payload / "l2.lct").write_text("fake trace\\n")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="wildclaw")

    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3.12",
        """#!/usr/bin/env bash
if [[ "$1" == "-c" ]]; then
  if [[ "$2" == *sys.version_info* ]]; then printf "3.12\\n"; fi
  if [[ "$2" == *sys.base_prefix* ]]; then printf "/usr\\n"; fi
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
while (($#)); do
  case "$1" in
    -o|-p) shift 2 ;;
    *) target="$1"; shift; break ;;
  esac
done
exec bash -lc "$*"
""",
    )
    _write_executable(
        fake_bin / "scp",
        """#!/usr/bin/env bash
set -euo pipefail
args=()
while (($#)); do
  case "$1" in
    -q|-r|--) shift ;;
    -P|-o) shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
src="${args[${#args[@]}-2]}"
dst="${args[${#args[@]}-1]}"
map_path() { case "$1" in *@*:*) printf '%s\\n' "${1#*:}" ;; *) printf '%s\\n' "$1" ;; esac; }
src_path="$(map_path "$src")"
dst_path="$(map_path "$dst")"
mkdir -p -- "$(dirname -- "$dst_path")"
cp -a -- "$src_path" "$dst_path"
""",
    )

    topology = tmp_path / "topology.yaml"
    topology.write_text(
        f"""runtime_mode: remote_install
controller_repo_root: {controller_repo}
controller_trace_root: {controller_traces}
controller_output_root: {controller_outputs}
replay_host: fake-replay
replay_user: fake
replay_port: 22
replay_repo_root: {remote_repo}
replay_venv_root: {remote_repo}/.venv
replay_python: python3.12
replay_runtime_requirements: requirements/replayer.txt
replay_package_index_url: https://pypi.intra.example.com/simple
replay_trace_root: {remote_traces}
replay_output_root: {remote_outputs}
replay_l2_root: {remote_l2}
git_repo_url: git@github.com:daegyu94/lmcache-tracebench.git
git_revision: dev
hf_repo_id: daegyu94/lmcache-storage-traces
hf_revision: main
transfer_method: scp
"""
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "all",
            "--topology",
            str(topology),
            "--asset",
            "tensormesh/wildclaw.tar.gz",
            "--run-name",
            "remote-failure",
            "--",
            "bash",
            "-c",
            "mkdir -p @OUTPUT_ROOT@; printf fake-result > @OUTPUT_ROOT@/result.txt; exit 7",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 7
    assert "Replay-node prerequisites: OK" in result.stdout
    assert "Replay runtime prepared and verified" in result.stdout
    assert "--python python3.12" in (remote_repo / "setup_args").read_text()
    assert "--index-url https://pypi.intra.example.com/simple" in (
        remote_repo / "setup_args"
    ).read_text()
    assert (remote_repo / ".venv/bin/python").is_symlink()
    assert (remote_traces / "tensormesh/wildclaw/l2.lct").is_file()
    assert (remote_outputs / "remote-failure/result.txt").read_text() == "fake-result"
    assert (controller_outputs / "remote-failure/result.txt").read_text() == "fake-result"
    assert (
        controller_outputs / "remote-failure/remote_exit_code"
    ).read_text().strip() == "7"
