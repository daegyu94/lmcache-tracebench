#!/usr/bin/env bash
# Shared helpers for storage-trace recorder scripts.

record_validate_mountpoint() {
  local mountpoint="${1-}"
  if [[ -z "$mountpoint" || "$mountpoint" != /* ]]; then
    echo "--mountpoint must be an absolute path" >&2
    return 1
  fi
}

record_validate_trace_kind() {
  local trace_kind="${1-}"
  case "$trace_kind" in
    storage|l2)
      ;;
    *)
      echo "--trace-kind must be storage or l2: $trace_kind" >&2
      return 1
      ;;
  esac
}

record_validate_source() {
  local source="${1-}"
  case "$source" in
    gaia|wildclaw|swebench|mooncake-toolagent|mooncake-conversation)
      ;;
    *)
      echo "Unsupported recorder source: $source" >&2
      return 1
      ;;
  esac
}

record_backend_for_source() {
  local source="${1-}"
  case "$source" in
    gaia|wildclaw|swebench)
      printf '%s\n' 'tensormesh'
      ;;
    mooncake-toolagent|mooncake-conversation)
      printf '%s\n' 'mooncake'
      ;;
    *)
      return 1
      ;;
  esac
}

record_workload_for_source() {
  local source="${1-}"
  case "$source" in
    gaia|wildclaw|swebench)
      printf '%s\n' "$source"
      ;;
    mooncake-toolagent)
      printf '%s\n' 'toolagent'
      ;;
    mooncake-conversation)
      printf '%s\n' 'conversation'
      ;;
    *)
      return 1
      ;;
  esac
}

record_default_workloads() {
  local backend="${1-}"
  case "$backend" in
    mooncake)
      printf '%s\n' 'toolagent,conversation'
      ;;
    tensormesh)
      printf '%s\n' 'gaia,wildclaw,swebench'
      ;;
    *)
      return 1
      ;;
  esac
}

record_config_for_workload() {
  local backend="${1-}"
  local workload="${2-}"
  case "$backend:$workload" in
    mooncake:toolagent|mooncake:conversation)
      printf '%s\n' 'configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml'
      ;;
    tensormesh:gaia|tensormesh:wildclaw|tensormesh:swebench)
      printf '%s\n' "configs/recorder/qwen3-coder-480b-tp8-${workload}.yaml"
      ;;
    *)
      return 1
      ;;
  esac
}

record_activate_project_venv() {
  local project_dir="${1-}"
  cd "$project_dir"
  if [[ ! -f .venv/bin/activate ]]; then
    echo "Project virtual environment is missing. Run: bash scripts/setup_runtime.sh" >&2
    return 1
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
}
