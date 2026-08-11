#!/usr/bin/env bash
# Record one trace per workload and requested workload speedup.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/../.." && pwd)"
# shellcheck source=record_common.sh
source "$script_dir/record_common.sh"
backend=""
workloads=""
speedups="1,2,5,10"
mountpoint=""
output_root="outputs/speed-sweep"
mooncake_trace_root=""
mooncake_trace_root_set=false
dataset_percent=""
dataset_percent_set=false
trace_kind="storage"
keep_l2=false
dry_run=false

usage() {
  cat <<'EOF'
Usage:
  bash benchmarks/recorder/record_speed_sweep.sh --backend BACKEND --mountpoint PATH [OPTIONS]

Required:
  --backend BACKEND       mooncake or tensormesh
  --mountpoint PATH       Absolute storage mountpoint used by recorder configs

Options:
  --workloads LIST        Comma-separated workloads. Defaults to:
                          mooncake: toolagent,conversation
                          tensormesh: gaia,wildclaw,swebench
  --speedups LIST         Comma-separated positive speedups (default: 1,2,5,10)
  --output-root PATH      Root for per-run recorder output (default: outputs/speed-sweep)
  --mooncake-trace-root PATH
                          Directory containing <trace>_trace.jsonl
                          (default: MOUNTPOINT/mooncake-traces)
  --dataset-percent PERCENT
                          Select the first PERCENT of the dataset. Mooncake
                          uses requests; Tensormesh applies it to SWE-bench
                          sessions and ignores it for GAIA/WildClaw.
  --trace-kind KIND       storage or l2 (default: storage)
  --keep-l2               Keep the per-case LMCache L2 directory after recording
                          (default: clean it after each case)
  --dry-run               Print every recorder plan without starting services
  -h, --help              Show this help

Examples:
  bash benchmarks/recorder/record_speed_sweep.sh \
    --backend mooncake \
    --mountpoint /MNTPNT \
    --speedups 1,2,5,10 \
    --dataset-percent 10

  bash benchmarks/recorder/record_speed_sweep.sh \
    --backend tensormesh \
    --mountpoint /MNTPNT \
    --speedups 1,2,5,10
EOF
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

require_value() {
  if (($# < 2)); then
    usage >&2
    exit 2
  fi
}

while (($#)); do
  case "$1" in
    --backend)
      require_value "$@"
      backend="$2"
      shift 2
      ;;
    --workloads)
      require_value "$@"
      workloads="$2"
      shift 2
      ;;
    --mountpoint)
      require_value "$@"
      mountpoint="$2"
      shift 2
      ;;
    --speedups)
      require_value "$@"
      speedups="$2"
      shift 2
      ;;
    --output-root)
      require_value "$@"
      output_root="$2"
      shift 2
      ;;
    --mooncake-trace-root)
      require_value "$@"
      mooncake_trace_root="$2"
      mooncake_trace_root_set=true
      shift 2
      ;;
    --dataset-percent)
      require_value "$@"
      dataset_percent="$2"
      dataset_percent_set=true
      shift 2
      ;;
    --trace-kind)
      require_value "$@"
      trace_kind="$2"
      shift 2
      ;;
    --keep-l2)
      keep_l2=true
      shift
      ;;
    --dry-run)
      dry_run=true
      shift
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

[[ -n "$backend" ]] || { usage >&2; exit 2; }
if ! record_validate_mountpoint "$mountpoint"; then
  usage >&2
  exit 2
fi
if ! default_workloads="$(record_default_workloads "$backend")"; then
  die "--backend must be mooncake or tensormesh: $backend"
fi

if [[ -z "$workloads" ]]; then
  workloads="$default_workloads"
fi

if ! record_validate_trace_kind "$trace_kind"; then
  usage >&2
  exit 2
fi

if [[ "$mooncake_trace_root_set" == false ]]; then
  mooncake_trace_root=""
fi

record_activate_project_venv "$project_dir" || exit 1
echo "[INFO] Trace kind: $trace_kind"

clean_case_l2() {
  local workload="$1"
  local l2_prefix="$backend"

  local mount_root="${mountpoint%/}"
  local l2_parent="${mount_root}/lmcache-trace"
  local target="${l2_parent}/${l2_prefix}-${workload}"

  # The target is derived only from validated backend/workload values. Keep
  # an explicit path-boundary check before the destructive operation.
  case "$target" in
    "$l2_parent/mooncake-"*|"$l2_parent/tensormesh-"*)
      ;;
    *)
      die "refusing to clean unexpected L2 path: $target"
      ;;
  esac

  local cursor="$target"
  while [[ "$cursor" != "$mount_root" ]]; do
    if [[ "$cursor" == "/" ]]; then
      die "refusing to clean L2 path outside mountpoint: $target"
    fi
    if [[ -L "$cursor" ]]; then
      die "refusing to clean a path containing a symlink: $cursor"
    fi
    cursor="$(dirname -- "$cursor")"
  done

  if [[ -L "$mount_root" ]]; then
    die "refusing to clean a path with a symlink mountpoint: $mount_root"
  fi
  if [[ -e "$target" && ! -d "$target" ]]; then
    die "LMCache L2 path is not a directory: $target"
  fi
  if [[ -d "$target" ]]; then
    local nested_symlink
    nested_symlink="$(find -P "$target" -type l -print -quit)"
    if [[ -n "$nested_symlink" ]]; then
      die "refusing to recursively clean a directory containing a symlink: $nested_symlink"
    fi
  fi

  echo "[INFO] Cleaning LMCache L2 storage: $target"
  if [[ -e "$target" ]]; then
    rm -rf -- "$target"
  fi
  mkdir -p -- "$target"
}

record_case_l2_usage() {
  local workload="$1"
  local output_dir="$2"
  local command_status="$3"
  local mount_root="${mountpoint%/}"
  local l2_path="${mount_root}/lmcache-trace/${backend}-${workload}"
  local usage_status="ok"
  local bytes=""

  if [[ -d "$l2_path" ]]; then
    if ! bytes="$(du -sb -- "$l2_path" | awk 'NR == 1 {print $1}')"; then
      usage_status="measurement_failed"
      bytes=""
    fi
  else
    usage_status="missing"
  fi

  mkdir -p -- "$output_dir"
  python - "$output_dir/l2_usage.json" "${output_root}/l2_usage.jsonl" \
    "$backend" "$workload" "$l2_path" "$bytes" "$usage_status" \
    "$command_status" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

usage_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
backend, workload, l2_path = sys.argv[3:6]
raw_bytes, usage_status, raw_exit_code = sys.argv[6:9]
bytes_used = int(raw_bytes) if raw_bytes else None
record = {
    "backend": backend,
    "workload": workload,
    "l2_path": l2_path,
    "bytes": bytes_used,
    "gb": round(bytes_used / 1_000_000_000, 3) if bytes_used is not None else None,
    "gib": round(bytes_used / (1024**3), 3) if bytes_used is not None else None,
    "measurement_status": usage_status,
    "record_exit_code": int(raw_exit_code),
    "measured_at_utc": datetime.now(timezone.utc).isoformat(),
}
usage_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
with summary_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"output_dir": str(usage_path.parent), **record}, sort_keys=True) + "\n")
print(
    f"[INFO] L2 storage usage: {record['gb']} GB "
    f"({record['bytes']} bytes) at {l2_path}"
)
PY
}

IFS=',' read -r -a workload_list <<< "$workloads"
IFS=',' read -r -a speedup_list <<< "$speedups"
if ((${#workload_list[@]} == 0 || ${#speedup_list[@]} == 0)); then
  die "--workloads and --speedups must not be empty"
fi

if [[ "$dataset_percent_set" == true ]]; then
  if ! python -c 'import math, sys; value = float(sys.argv[1]); raise SystemExit(0 if math.isfinite(value) and 0 < value <= 100 else 1)' "$dataset_percent"; then
    die "dataset-percent must be greater than 0 and at most 100: $dataset_percent"
  fi
  if [[ "$backend" == tensormesh ]]; then
    for raw_workload in "${workload_list[@]}"; do
      workload="${raw_workload//[[:space:]]/}"
      case "$workload" in
        swebench|gaia|wildclaw)
          ;;
        *)
          die "--dataset-percent requires a Tensormesh workload: swebench, gaia, or wildclaw"
          ;;
      esac
    done
  fi
fi

output_root="${output_root%/}"
mooncake_trace_root="${mooncake_trace_root%/}"
mkdir -p -- "$output_root"

for raw_speedup in "${speedup_list[@]}"; do
  speedup="${raw_speedup//[[:space:]]/}"
  [[ -n "$speedup" ]] || die "speedup entries must not be empty"
  if ! python -c 'import math, sys; value = float(sys.argv[1]); raise SystemExit(0 if math.isfinite(value) and value > 0 else 1)' "$speedup"; then
    die "speedup must be a finite positive number: $speedup"
  fi
done

for raw_workload in "${workload_list[@]}"; do
  workload="${raw_workload//[[:space:]]/}"
  if ! config="$(record_config_for_workload "$backend" "$workload")"; then
    die "unsupported workload '$workload' for backend '$backend'"
  fi

  for raw_speedup in "${speedup_list[@]}"; do
    speedup="${raw_speedup//[[:space:]]/}"
    run_name="${backend}-${workload}-x${speedup}"
    output_dir="${output_root}/${run_name}"
    command=(
      python -m recorder.main
      --config "$config"
      --mountpoint "$mountpoint"
      --trace-kind "$trace_kind"
      --speedup "$speedup"
      --output-dir "$output_dir"
    )

    if [[ "$backend" == mooncake ]]; then
      command+=(
        --mooncake-trace "$workload"
      )
      if [[ "$dataset_percent_set" == true ]]; then
        command+=(--dataset-percent "$dataset_percent")
      fi
      if [[ "$mooncake_trace_root_set" == true ]]; then
        command+=(
          --mooncake-path "${mooncake_trace_root}/${workload}_trace.jsonl"
        )
      fi
    fi
    if [[ "$backend" == tensormesh && "$dataset_percent_set" == true ]]; then
      command+=(--dataset-percent "$dataset_percent")
    fi
    if [[ "$dry_run" == true ]]; then
      command+=(--dry-run)
    fi

    echo "[INFO] Recording ${run_name}"
    if "${command[@]}"; then
      command_status=0
    else
      command_status=$?
    fi

    if [[ "$keep_l2" == false && "$dry_run" == false ]]; then
      record_case_l2_usage "$workload" "$output_dir" "$command_status"
      clean_case_l2 "$workload"
    elif [[ "$dry_run" == false ]]; then
      record_case_l2_usage "$workload" "$output_dir" "$command_status"
    fi

    if ((command_status != 0)); then
      exit "$command_status"
    fi
  done
done

echo "[INFO] Completed speed sweep under: ${output_root}"
