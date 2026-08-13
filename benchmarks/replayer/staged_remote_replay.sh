#!/usr/bin/env bash
# Stage traces and replay software from a controller node to an isolated replay node.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/../.." && pwd -P)"

topology_file=""
phase=""
dry_run=false
run_name=""
assets=()
command_args=()
controller_python_version=""
runtime_mode=""
replay_python=""
replay_runtime_requirements=""
replay_package_index_url=""
replay_extra_index_url=""
replay_require_uv="false"

declare -A topology_values=()

usage() {
  cat <<'EOF'
Usage:
  bash benchmarks/replayer/staged_remote_replay.sh PHASE --topology PATH [OPTIONS]

Phases:
  check-prerequisites
                   Check replay-node Python, venv support, and basic system tools.
  prepare-trace    Download one or more HF archives on the controller and transfer
                   and extract them on the isolated replay node.
  prepare-replay   Clone/stage the tracebench repository and prepare the replay
                   node runtime according to topology runtime_mode.
  replay           Run a supplied replay/sweep command and retrieve its output even
                   when the remote command exits non-zero.
  all              Run prepare-trace, prepare-replay, and replay in order.

Options:
  --topology PATH  Required flat-scalar topology YAML. It has no implicit defaults.
  --asset PATH     HF path such as tensormesh/wildclaw.tar.gz. Repeatable for
                   prepare-trace and all.
  --run-name NAME  Required for replay and all. Must be unique on both nodes.
  --dry-run        Print controller, SSH, and transfer commands without executing.
  -h, --help       Show this help.

Replay command:
  Put -- before the remote command. Replace topology paths with placeholders:
    @REPO_ROOT@, @TRACE_ROOT@, @OUTPUT_ROOT@, @L2_ROOT@, @RUN_NAME@

Example:
  bash benchmarks/replayer/staged_remote_replay.sh all \
    --topology configs/replayer/staged-remote/topology.yaml \
    --asset tensormesh/wildclaw.tar.gz \
    --run-name wildclaw-speedup \
    -- bash benchmarks/replayer/replay_speed_sweep.sh \
      --trace @TRACE_ROOT@/tensormesh/wildclaw/l2.lct \
      --config @REPO_ROOT@/configs/replayer/fs-native.yaml \
      --l2-root @L2_ROOT@/wildclaw-speedup \
      --output-root @OUTPUT_ROOT@ \
      --speedups 1,2,4,8
EOF
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

warn() {
  echo "[WARN] $*" >&2
}

info() {
  echo "[INFO] $*"
}

require_value() {
  if (($# < 2)); then
    usage >&2
    exit 2
  fi
}

while (($#)); do
  case "$1" in
    check-prerequisites|prepare-trace|prepare-replay|replay|all)
      if [[ -n "$phase" ]]; then
        die "Only one phase may be specified."
      fi
      phase="$1"
      shift
      ;;
    --topology)
      require_value "$@"
      topology_file="$2"
      shift 2
      ;;
    --asset)
      require_value "$@"
      assets+=("$2")
      shift 2
      ;;
    --run-name)
      require_value "$@"
      run_name="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --)
      shift
      command_args=("$@")
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$phase" ]] || { usage >&2; exit 2; }
[[ -n "$topology_file" ]] || die "--topology is required; no topology defaults are provided."
[[ -f "$topology_file" ]] || die "Topology file not found: $topology_file"

# The topology intentionally uses only top-level scalar YAML entries. This keeps
# the controller bootstrap independent of a YAML package and makes every path
# explicit in one file.
load_topology() {
  local raw line key value
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    raw="${raw%$'\r'}"
    line="${raw%%#*}"
    if [[ "$line" =~ ^[[:space:]]*([A-Za-z0-9_.-]+)[[:space:]]*:[[:space:]]*(.*)[[:space:]]*$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      case "$value" in
        "\""*"\"") value="${value:1:${#value}-2}" ;;
        "'"*"'") value="${value:1:${#value}-2}" ;;
      esac
      topology_values["$key"]="$value"
    fi
  done < "$topology_file"
}

topology_get() {
  local key="$1"
  if [[ ! -v "topology_values[$key]" ]]; then
    return 1
  fi
  printf '%s\n' "${topology_values[$key]}"
}

require_topology_key() {
  local key="$1"
  local value
  value="$(topology_get "$key" || true)"
  [[ -n "$value" ]] || die "Topology key is required and has no default: $key"
}

load_topology
for key in \
  runtime_mode controller_repo_root controller_trace_root controller_output_root \
  replay_host replay_user replay_port replay_repo_root replay_venv_root \
  replay_trace_root replay_output_root replay_l2_root git_repo_url git_revision \
  hf_repo_id hf_revision transfer_method; do
  require_topology_key "$key"
done

runtime_mode="$(topology_get runtime_mode)"
controller_repo_root="$(topology_get controller_repo_root)"
controller_venv_root="$(topology_get controller_venv_root || true)"
controller_trace_root="$(topology_get controller_trace_root)"
controller_output_root="$(topology_get controller_output_root)"
replay_host="$(topology_get replay_host)"
replay_user="$(topology_get replay_user)"
replay_jump_user="$(topology_get replay_jump_user || true)"
replay_port="$(topology_get replay_port)"
replay_repo_root="$(topology_get replay_repo_root)"
replay_venv_root="$(topology_get replay_venv_root)"
replay_trace_root="$(topology_get replay_trace_root)"
replay_output_root="$(topology_get replay_output_root)"
replay_l2_root="$(topology_get replay_l2_root)"
replay_python="$(topology_get replay_python || true)"
replay_runtime_requirements="$(topology_get replay_runtime_requirements || true)"
replay_package_index_url="$(topology_get replay_package_index_url || true)"
replay_extra_index_url="$(topology_get replay_extra_index_url || true)"
replay_require_uv="$(topology_get replay_require_uv || printf false)"
git_repo_url="$(topology_get git_repo_url)"
git_revision="$(topology_get git_revision)"
hf_repo_id="$(topology_get hf_repo_id)"
hf_revision="$(topology_get hf_revision)"
transfer_method="$(topology_get transfer_method)"

case "$transfer_method" in
  rsync|scp) ;;
  *) die "transfer_method must be rsync or scp: $transfer_method" ;;
esac
if [[ -n "$replay_jump_user" && "$transfer_method" != rsync ]]; then
  die "transfer_method must be rsync when replay_jump_user is set (scp has no --rsync-path equivalent for sudo wrapping): $transfer_method"
fi
case "$replay_require_uv" in
  true|false) ;;
  *) die "replay_require_uv must be true or false: $replay_require_uv" ;;
esac
case "$runtime_mode" in
  remote_install)
    require_topology_key replay_python
    require_topology_key replay_runtime_requirements
    ;;
  copy_venv)
    require_topology_key controller_venv_root
    ;;
  *)
    die "runtime_mode must be remote_install or copy_venv: $runtime_mode"
    ;;
esac
[[ "$replay_port" =~ ^[0-9]+$ ]] || die "replay_port must be an integer: $replay_port"
[[ "$controller_repo_root" == /* ]] || die "controller_repo_root must be absolute"
if [[ "$runtime_mode" == copy_venv ]]; then
  [[ "$controller_venv_root" == /* ]] || die "controller_venv_root must be absolute"
fi
[[ "$controller_trace_root" == /* ]] || die "controller_trace_root must be absolute"
[[ "$controller_output_root" == /* ]] || die "controller_output_root must be absolute"
[[ "$replay_repo_root" == /* ]] || die "replay_repo_root must be absolute"
[[ "$replay_venv_root" == /* ]] || die "replay_venv_root must be absolute"
[[ "$replay_trace_root" == /* ]] || die "replay_trace_root must be absolute"
[[ "$replay_output_root" == /* ]] || die "replay_output_root must be absolute"
[[ "$replay_l2_root" == /* ]] || die "replay_l2_root must be absolute"
if [[ "$runtime_mode" == remote_install ]]; then
  case "$replay_python" in
    *[!A-Za-z0-9_./-]*) die "replay_python must be an executable path or command name: $replay_python" ;;
  esac
fi
replay_expected_venv="${replay_repo_root%/}/.venv"
if [[ "$runtime_mode" == copy_venv ]]; then
  controller_expected_venv="${controller_repo_root%/}/.venv"
  [[ "$controller_venv_root" == "$controller_expected_venv" ]] || \
    die "controller_venv_root must be controller_repo_root/.venv for path repair"
fi
[[ "$replay_venv_root" == "$replay_expected_venv" ]] || \
  die "replay_venv_root must be replay_repo_root/.venv"
path_values=("$controller_repo_root" "$replay_repo_root" "$replay_venv_root")
if [[ "$runtime_mode" == copy_venv ]]; then
  path_values+=("$controller_venv_root")
fi
for path_value in "${path_values[@]}"; do
  [[ "$path_value" != *'|'* && "$path_value" != *'&'* && "$path_value" != *$'\n'* ]] || \
    die "repository and venv paths cannot contain '|', '&', or newlines: $path_value"
done

ssh_target="${replay_user}@${replay_host}"

# replay_jump_user가 설정된 경우, jump_user로 SSH 로그인한 뒤 모든 remote
# command를 sudo -n -u $replay_user로 실행한다. 그렇지 않으면 기존대로
# replay_user로 직접 SSH한다.
if [[ -n "$replay_jump_user" ]]; then
  ssh_target="${replay_jump_user}@${replay_host}"
  sudo_prefix="sudo -n -u $replay_user"
  rsync_remote_path="$sudo_prefix rsync"
else
  ssh_target="${replay_user}@${replay_host}"
  sudo_prefix=""
  rsync_remote_path="rsync"
fi

shell_quote() {
  printf '%q' "$1"
}

remote_exec() {
  local command="$1"
  local quoted_command
  printf -v quoted_command '%q' "$command"
  local ssh_command
  if [[ -n "$sudo_prefix" ]]; then
    ssh_command="$sudo_prefix bash -lc $quoted_command"
  else
    ssh_command="bash -lc $quoted_command"
  fi
  if [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] ssh -o BatchMode=yes -o RemoteCommand=none -o RequestTTY=no -p %s %s %s\n' \
      "$replay_port" "$ssh_target" "$ssh_command"
    return 0
  fi
  ssh -o BatchMode=yes -o RemoteCommand=none -o RequestTTY=no -p "$replay_port" "$ssh_target" "$ssh_command"
}

remote_path_exists() {
  local path="$1"
  local quoted_path
  printf -v quoted_path '%q' "$path"
  local remote_command="test -e $quoted_path || test -L $quoted_path"
  local ssh_command
  if [[ -n "$sudo_prefix" ]]; then
    local quoted_remote_command
    printf -v quoted_remote_command '%q' "$remote_command"
    ssh_command="$sudo_prefix bash -lc $quoted_remote_command"
  else
    ssh_command="$remote_command"
  fi
  if [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] test -e %s (remote path status unknown; treating as absent)\n' \
      "$path"
    return 1
  fi
  ssh -o BatchMode=yes -o RemoteCommand=none -o RequestTTY=no -p "$replay_port" "$ssh_target" "$ssh_command"
}

local_path_exists() {
  [[ -e "$1" || -L "$1" ]]
}

require_transport_command() {
  if [[ "$dry_run" == true ]]; then
    return
  fi
  command -v "$1" >/dev/null 2>&1 || die "$1 is required on the controller node"
}

rsync_ssh_command="ssh -o BatchMode=yes -o RemoteCommand=none -o RequestTTY=no -p $replay_port"

transfer_file() {
  local local_path="$1"
  local remote_path="$2"
  local remote_parent
  remote_parent="$(dirname -- "$remote_path")"

  if remote_path_exists "$remote_path"; then
    warn "Remote file already exists; not overwriting: $remote_path"
    return 0
  fi
  remote_exec "mkdir -p $(shell_quote "$remote_parent")"
  if [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] %s %s -> %s:%s\n' "$transfer_method" \
      "$local_path" "$ssh_target" "$remote_path"
    return 0
  fi
  require_transport_command "$transfer_method"
  if [[ "$transfer_method" == rsync ]]; then
    rsync -a --ignore-existing -e "$rsync_ssh_command" \
      --rsync-path "$rsync_remote_path" \
      -- "$local_path" "$ssh_target:$remote_path"
  else
    scp -q -P "$replay_port" -o BatchMode=yes \
      -- "$local_path" "$ssh_target:$remote_path"
  fi
}

transfer_directory() {
  local local_path="$1"
  local remote_path="$2"
  local remote_parent
  remote_parent="$(dirname -- "$remote_path")"

  if remote_path_exists "$remote_path"; then
    warn "Remote directory already exists; not overwriting: $remote_path"
    return 0
  fi
  remote_exec "mkdir -p $(shell_quote "$remote_parent")"
  if [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] %s directory %s -> %s:%s\n' "$transfer_method" \
      "$local_path" "$ssh_target" "$remote_path"
    return 0
  fi
  require_transport_command "$transfer_method"
  if [[ "$transfer_method" == rsync ]]; then
    rsync -a --ignore-existing -e "$rsync_ssh_command" \
      --rsync-path "$rsync_remote_path" \
      -- "$local_path/" "$ssh_target:$remote_path/"
  else
    scp -q -r -P "$replay_port" -o BatchMode=yes \
      -- "$local_path" "$ssh_target:$remote_parent/"
  fi
}

retrieve_directory() {
  local remote_path="$1"
  local local_path="$2"
  local local_parent
  local_parent="$(dirname -- "$local_path")"

  if ! remote_path_exists "$remote_path"; then
    warn "Remote result directory does not exist; nothing to retrieve: $remote_path"
    return 0
  fi
  if local_path_exists "$local_path"; then
    warn "Local result directory already exists; not overwriting: $local_path"
    return 1
  fi
  if [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] retrieve %s:%s -> %s\n' "$ssh_target" "$remote_path" "$local_path"
    return 0
  fi
  require_transport_command "$transfer_method"
  mkdir -p -- "$local_parent"
  if [[ "$transfer_method" == rsync ]]; then
    mkdir -- "$local_path"
    rsync -a --ignore-existing -e "$rsync_ssh_command" \
      --rsync-path "$rsync_remote_path" \
      -- "$ssh_target:$remote_path/" "$local_path/"
  else
    scp -q -r -P "$replay_port" -o BatchMode=yes \
      -- "$ssh_target:$remote_path" "$local_parent/"
  fi
}

validate_asset() {
  local asset="$1"
  [[ "$asset" == *.tar.gz ]] || die "asset must end in .tar.gz: $asset"
  case "$asset" in
    ""|/*|../*|*/../*|*/..|*' '*|*$'\n')
      die "asset must be a relative .tar.gz path without traversal or spaces: $asset"
      ;;
  esac
}

validate_run_name() {
  [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || \
    die "run-name must contain only letters, digits, '.', '_' or '-': $1"
}

hf_helper="$project_root/tools/artifacts/hf_trace_asset.sh"
if [[ ! -f "$hf_helper" ]]; then
  hf_helper="$controller_repo_root/tools/artifacts/hf_trace_asset.sh"
fi

prepare_trace_asset() {
  local asset="$1"
  local local_archive remote_archive asset_dir archive_name trace_name
  validate_asset "$asset"
  local_archive="$controller_trace_root/$asset"
  remote_archive="$replay_trace_root/$asset"
  asset_dir="${asset%/*}"
  archive_name="${asset##*/}"
  trace_name="${archive_name%.tar.gz}"

  if [[ "$dry_run" == false ]]; then
    mkdir -p -- "$(dirname -- "$local_archive")"
  fi
  if local_path_exists "$local_archive"; then
    warn "Controller archive already exists; not downloading or overwriting: $local_archive"
  elif [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] download hf://%s@%s/%s -> %s\n' \
      "$hf_repo_id" "$hf_revision" "$asset" "$local_archive"
  else
    [[ -f "$hf_helper" ]] || die "HF helper not found: $hf_helper"
    bash "$hf_helper" download \
      --repo-id "$hf_repo_id" \
      --revision "$hf_revision" \
      --path-in-repo "$asset" \
      --output-dir "$controller_trace_root"
  fi

  transfer_file "$local_archive" "$remote_archive"
  local remote_category="$replay_trace_root/$asset_dir"
  local remote_trace_dir="$remote_category/$trace_name"
  if remote_path_exists "$remote_trace_dir"; then
    warn "Remote extracted trace already exists; not overwriting: $remote_trace_dir"
  else
    remote_exec "mkdir -p $(shell_quote "$remote_category") && tar --keep-old-files -xzf $(shell_quote "$remote_archive") -C $(shell_quote "$remote_category")"
  fi
  info "Trace staged: $asset"
}

prepare_repository() {
  if local_path_exists "$controller_repo_root"; then
    warn "Controller repository path already exists; not cloning or overwriting: $controller_repo_root"
  elif [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] git clone --branch %s %s %s\n' \
      "$git_revision" "$git_repo_url" "$controller_repo_root"
  else
    mkdir -p -- "$(dirname -- "$controller_repo_root")"
    git clone --branch "$git_revision" "$git_repo_url" "$controller_repo_root"
  fi

  if [[ ! -d "$controller_repo_root" && "$dry_run" == false ]]; then
    die "Controller repository is not available: $controller_repo_root"
  fi
  transfer_directory "$controller_repo_root" "$replay_repo_root"
}

check_remote_prerequisites() {
  local remote_command
  remote_command="set -euo pipefail"$'\n'
  remote_command+='required_commands=(bash tar find sed ln mkdir dirname pwd)'$'\n'
  if [[ "$runtime_mode" == remote_install ]]; then
    remote_command+='required_commands+=(git)'$'\n'
  fi
  remote_command+='missing=()'$'\n'
  remote_command+='for command_name in "${required_commands[@]}"; do command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name"); done'$'\n'
  remote_command+='if ((${#missing[@]})); then echo "Missing replay-node commands: ${missing[*]}" >&2; echo "Install bash, tar, findutils, sed, coreutils, the approved Python runtime, and git for remote_install requirements." >&2; exit 1; fi'$'\n'
  if [[ "$runtime_mode" == remote_install ]]; then
    remote_command+="base_python=$(shell_quote "$replay_python")"$'\n'
    remote_command+='if [[ "$base_python" == /* ]]; then base_python_path="$base_python"; else base_python_path="$(command -v "$base_python" || true)"; fi'$'\n'
    remote_command+='[[ -x "$base_python_path" ]] || { echo "Replay Python was not found: $base_python" >&2; exit 1; }'$'\n'
    remote_command+='"$base_python_path" -c '\''import sys; assert sys.version_info >= (3, 10), sys.version; import ensurepip, venv'\'''$'\n'
  remote_command+='python_version="$($base_python_path -c '\''import sys; print("%d.%d" % sys.version_info[:2])'\'')"'$'\n'
    remote_command+='echo "Replay Python: $python_version ($base_python_path)"'$'\n'
  else
    remote_command+='python_path=""'$'\n'
    remote_command+='for candidate in python3.12 python3 python; do candidate_path="$(command -v "$candidate" || true)"; if [[ -n "$candidate_path" ]]; then python_path="$candidate_path"; break; fi; done'$'\n'
    remote_command+='[[ -n "$python_path" ]] || { echo "No Python interpreter was found on the replay node." >&2; exit 1; }'$'\n'
    remote_command+='"$python_path" -c '\''import sys; assert sys.version_info >= (3, 10), sys.version'\'''$'\n'
  remote_command+='python_version="$($python_path -c '\''import sys; print("%d.%d" % sys.version_info[:2])'\'')"'$'\n'
    remote_command+='echo "Replay Python: $python_version ($python_path)"'$'\n'
  fi
  if [[ "$replay_require_uv" == true ]]; then
    remote_command+='command -v uv >/dev/null 2>&1 || { echo "uv is required by replay_require_uv=true but was not found." >&2; exit 1; }'$'\n'
    remote_command+='echo "uv: $(uv --version)"'$'\n'
  elif [[ "$runtime_mode" == remote_install ]]; then
    remote_command+='if command -v uv >/dev/null 2>&1; then echo "uv: $(uv --version)"; else echo "uv: not found (optional; Python venv + pip fallback will be used)"; fi'$'\n'
  fi
  remote_command+='echo "Replay-node prerequisites: OK"'$'\n'
  remote_exec "$remote_command"
}

ensure_controller_venv() {
  if local_path_exists "$controller_venv_root"; then
    warn "Controller venv already exists; not rebuilding or overwriting: $controller_venv_root"
  elif [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] bash %s --profile replayer-cpu\n' \
      "$controller_repo_root/scripts/setup_runtime.sh"
  else
    [[ -x "$controller_repo_root/scripts/setup_runtime.sh" ]] || \
      die "Controller setup script not found: $controller_repo_root/scripts/setup_runtime.sh"
    bash "$controller_repo_root/scripts/setup_runtime.sh" --profile replayer-cpu
  fi
  if [[ "$dry_run" == false && ! -x "$controller_venv_root/bin/python" ]]; then
    die "Controller venv is not ready: $controller_venv_root"
  fi
  if [[ "$dry_run" == false ]]; then
    controller_python_version="$($controller_venv_root/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])')" || \
      die "Unable to read controller Python version from $controller_venv_root"
    [[ "$controller_python_version" =~ ^[0-9]+\.[0-9]+$ ]] || \
      die "Invalid controller Python minor version: $controller_python_version"
  else
    controller_python_version="unknown"
  fi
}

verify_remote_runtime() {
  local remote_command
  remote_command="set -euo pipefail"$'\n'
  remote_command+="venv=$(shell_quote "$replay_venv_root")"$'\n'
  remote_command+="base_python=$(shell_quote "$replay_python")"$'\n'
  remote_command+='if [[ "$base_python" == /* ]]; then base_python_path="$base_python"; else base_python_path="$(command -v "$base_python" || true)"; fi'$'\n'
  remote_command+='[[ -x "$base_python_path" ]] || { echo "Replay Python was not found: $base_python" >&2; exit 1; }'$'\n'
  remote_command+='expected_version="$($base_python_path -c '\''import sys; print("%d.%d" % sys.version_info[:2])'\'')"'$'\n'
  remote_command+='actual_version="$($venv/bin/python -c '\''import sys; print("%d.%d" % sys.version_info[:2])'\'')"'$'\n'
  remote_command+='[[ "$actual_version" == "$expected_version" ]] || { echo "Replay venv Python $actual_version does not match $expected_version" >&2; exit 1; }'$'\n'
  remote_command+='"$venv/bin/python" -c '\''import lmcache, lmcache.c_ops, replayer'\'''$'\n'
  remote_command+='"$venv/bin/python" -m pip check'$'\n'
  remote_exec "$remote_command"
}

prepare_remote_runtime() {
  if remote_path_exists "$replay_venv_root"; then
    warn "Remote venv already exists; not overwriting: $replay_venv_root"
    verify_remote_runtime
    return
  fi

  local remote_command
  remote_command="set -euo pipefail"$'\n'
  remote_command+="repo=$(shell_quote "$replay_repo_root")"$'\n'
  remote_command+='cd "$repo"'$'\n'
  remote_command+='[[ -x "$repo/scripts/setup_runtime.sh" ]] || { echo "Replay setup script not found: $repo/scripts/setup_runtime.sh" >&2; exit 1; }'$'\n'
  remote_command+="bash \"\$repo/scripts/setup_runtime.sh\" --profile replayer-cpu --python $(shell_quote "$replay_python") --runtime-requirements $(shell_quote "$replay_runtime_requirements")"
  if [[ -n "$replay_package_index_url" ]]; then
    remote_command+=" --index-url $(shell_quote "$replay_package_index_url")"
  fi
  if [[ -n "$replay_extra_index_url" ]]; then
    remote_command+=" --extra-index-url $(shell_quote "$replay_extra_index_url")"
  fi
  remote_command+=$'\n'
  remote_exec "$remote_command"
  verify_remote_runtime
}

repair_remote_venv() {
  local remote_command
  remote_command="set -euo pipefail"$'\n'
  remote_command+="venv=$(shell_quote "$replay_venv_root")"$'\n'
  remote_command+="old_root=$(shell_quote "$controller_repo_root")"$'\n'
  remote_command+="new_root=$(shell_quote "$replay_repo_root")"$'\n'
  remote_command+="expected_version=$(shell_quote "$controller_python_version")"$'\n'
  remote_command+='system_python=""'$'\n'
  remote_command+='system_version=""'$'\n'
  remote_command+='for candidate in python3.12 python3 python; do'$'\n'
  remote_command+='  candidate_path="$(command -v "$candidate" || true)"'$'\n'
  remote_command+='  [[ -n "$candidate_path" ]] || continue'$'\n'
  remote_command+='  candidate_version="$($candidate_path -c '\''import sys; print("%d.%d" % sys.version_info[:2])'\'')"'$'\n'
  remote_command+='  if [[ "$candidate_version" == "$expected_version" ]]; then system_python="$candidate_path"; system_version="$candidate_version"; break; fi'$'\n'
  remote_command+='done'$'\n'
  remote_command+='[[ -n "$system_python" && -x "$system_python" ]] || { echo "No Python $expected_version interpreter was found on the replay node" >&2; exit 1; }'$'\n'
  remote_command+='ln -sfn "$system_python" "$venv/bin/python"'$'\n'
  remote_command+='ln -sfn python "$venv/bin/python3"'$'\n'
  remote_command+='python_name="${system_python##*/}"'$'\n'
  remote_command+='ln -sfn python "$venv/bin/$python_name"'$'\n'
  remote_command+='system_home="$($system_python -c '\''import sys; print(sys.base_prefix)'\'')"'$'\n'
  remote_command+='sed -i -E "s|^home = .*|home = $system_home|" "$venv/pyvenv.cfg"'$'\n'
  remote_command+='while IFS= read -r -d "" file; do sed -i "s|$old_root|$new_root|g" "$file"; done < <(find "$venv" -type f -name "*.pth" -print0)'$'\n'
  remote_command+='for file in "$venv/bin"/*; do'$'\n'
  remote_command+='  if [[ -f "$file" ]] && IFS= read -r first < "$file" && [[ "$first" == "#!"*python* ]]; then'$'\n'
  remote_command+='    sed -i "1c#!$venv/bin/python" "$file"'$'\n'
  remote_command+='  fi'$'\n'
  remote_command+='done'$'\n'
  remote_command+='"$venv/bin/python" -c '\''import lmcache, lmcache.c_ops, replayer'\'''$'\n'
  remote_command+='"$venv/bin/python" -m pip check'$'\n'
  remote_exec "$remote_command"
}

prepare_replay() {
  prepare_repository
  check_remote_prerequisites
  case "$runtime_mode" in
    remote_install)
      prepare_remote_runtime
      info "Replay runtime prepared and verified on $ssh_target"
      ;;
    copy_venv)
      local remote_venv_preexisting=false
      if remote_path_exists "$replay_venv_root"; then
        remote_venv_preexisting=true
        warn "Remote venv already exists; not overwriting or repairing it: $replay_venv_root"
      fi
      ensure_controller_venv
      if [[ "$remote_venv_preexisting" == true ]]; then
        remote_exec "test -x $(shell_quote "$replay_venv_root/bin/python")"
      else
        transfer_directory "$controller_venv_root" "$replay_venv_root"
        repair_remote_venv
      fi
      info "Replay venv copied and verified on $ssh_target"
      ;;
  esac
}

expand_placeholder() {
  local value="$1"
  value="${value//@REPO_ROOT@/$replay_repo_root}"
  value="${value//@TRACE_ROOT@/$replay_trace_root}"
  value="${value//@OUTPUT_ROOT@/$remote_output_root}"
  value="${value//@L2_ROOT@/$replay_l2_root}"
  value="${value//@RUN_NAME@/$run_name}"
  printf '%s\n' "$value"
}

replay_run() {
  [[ -n "$run_name" ]] || die "--run-name is required for replay"
  validate_run_name "$run_name"
  ((${#command_args[@]} > 0)) || die "replay requires a command after --"

  local remote_output_root="$replay_output_root/$run_name"
  local local_output_root="$controller_output_root/$run_name"
  if remote_path_exists "$remote_output_root"; then
    warn "Remote output already exists; refusing to run or overwrite: $remote_output_root"
    return 1
  fi
  if local_path_exists "$local_output_root"; then
    warn "Controller output already exists; refusing to run or overwrite: $local_output_root"
    return 1
  fi

  local expanded_arg
  local expanded_command=()
  for arg in "${command_args[@]}"; do
    expanded_arg="$(expand_placeholder "$arg")"
    expanded_command+=("$expanded_arg")
  done

  local remote_command="set -euo pipefail; cd $(shell_quote "$replay_repo_root");"
  for arg in "${expanded_command[@]}"; do
    remote_command+=" $(shell_quote "$arg")"
  done

  info "Starting staged remote replay: $run_name"
  local replay_status=0
  set +e
  remote_exec "$remote_command"
  replay_status=$?
  set -e
  if ((replay_status == 0)); then
    info "Remote replay completed successfully: $run_name"
  else
    warn "Remote replay exited with status $replay_status: $run_name"
  fi

  local retrieve_status=0
  retrieve_directory "$remote_output_root" "$local_output_root" || retrieve_status=$?
  if [[ "$dry_run" == true ]]; then
    return "$replay_status"
  fi
  mkdir -p -- "$local_output_root"
  printf '%s\n' "$replay_status" > "$local_output_root/remote_exit_code"
  info "Replay results retrieved: $local_output_root"

  if ((replay_status != 0)); then
    return "$replay_status"
  fi
  return "$retrieve_status"
}

case "$phase" in
  check-prerequisites)
    check_remote_prerequisites
    ;;
  prepare-trace)
    ((${#assets[@]} > 0)) || die "prepare-trace requires at least one --asset"
    for asset in "${assets[@]}"; do
      prepare_trace_asset "$asset"
    done
    ;;
  prepare-replay)
    prepare_replay
    ;;
  replay)
    replay_run
    ;;
  all)
    ((${#assets[@]} > 0)) || die "all requires at least one --asset"
    [[ -n "$run_name" ]] || die "all requires --run-name"
    for asset in "${assets[@]}"; do
      prepare_trace_asset "$asset"
    done
    prepare_replay
    replay_run
    ;;
esac
