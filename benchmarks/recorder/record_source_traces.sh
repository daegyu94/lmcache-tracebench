#!/usr/bin/env bash
# Record independent SWE-bench, GAIA, and WildClaw LMCache storage traces.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/../.." && pwd)"
# shellcheck source=record_common.sh
source "$script_dir/record_common.sh"
output_root="outputs"
mountpoint=""
trace_kind="storage"
sources="gaia,wildclaw,swebench"

usage() {
  echo "Usage: bash benchmarks/recorder/record_source_traces.sh --mountpoint PATH [--output-root PATH] [--trace-kind storage|l2] [--sources LIST]"
}

while (($#)); do
  case "$1" in
    --output-root)
      if (($# < 2)); then
        usage >&2
        exit 2
      fi
      output_root="$2"
      shift 2
      ;;
    --mountpoint)
      if (($# < 2)); then
        usage >&2
        exit 2
      fi
      mountpoint="$2"
      shift 2
      ;;
    --trace-kind)
      if (($# < 2)); then
        usage >&2
        exit 2
      fi
      trace_kind="$2"
      shift 2
      ;;
    --sources)
      if (($# < 2)); then
        usage >&2
        exit 2
      fi
      sources="$2"
      shift 2
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

if ! record_validate_mountpoint "$mountpoint"; then
  usage >&2
  exit 2
fi

if ! record_validate_trace_kind "$trace_kind"; then
  usage >&2
  exit 2
fi

IFS=',' read -r -a source_list <<< "$sources"
if ((${#source_list[@]} == 0)); then
  echo "--sources must not be empty" >&2
  usage >&2
  exit 2
fi
for source in "${source_list[@]}"; do
  source="${source//[[:space:]]/}"
  if ! record_validate_tensormesh_source "$source"; then
    usage >&2
    exit 2
  fi
done

record_activate_project_venv "$project_dir"

run_stamp="$(date +%Y%m%d-%H%M%S)"
run_root="$output_root/source-traces-$run_stamp"
mkdir -p "$run_root"

echo "[INFO] Recording source traces under: $run_root"
echo "[INFO] Trace kind: $trace_kind"
echo "[INFO] Sources: ${sources}"
echo "[INFO] Each run resets only its own source-specific L2 directory."

# Record each requested source in the requested order.
for raw_source in "${source_list[@]}"; do
  source="${raw_source//[[:space:]]/}"
  config="$(record_config_for_workload tensormesh "$source")"
  echo "[INFO] Starting $source trace"
  python -m recorder.main \
    --config "$config" \
    --mountpoint "$mountpoint" \
    --trace-kind "$trace_kind" \
    --output-dir "$run_root/$source"
  echo "[INFO] Finished $source trace"
done

echo "[INFO] Completed all source traces: $run_root"
