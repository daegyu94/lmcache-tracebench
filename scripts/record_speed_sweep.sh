#!/usr/bin/env bash
# Record one storage trace per workload and requested workload speedup.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
backend=""
workloads=""
speedups="1,2,5,10"
mountpoint=""
output_root="outputs/speed-sweep"
mooncake_trace_root=""
mooncake_trace_root_set=false
num_requests="1000"
num_requests_set=false
dry_run=false

usage() {
  cat <<'EOF'
Usage:
  bash scripts/record_speed_sweep.sh --backend BACKEND --mountpoint PATH [OPTIONS]

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
  --num-requests N|all    Mooncake request prefix (default: 1000)
  --dry-run               Print every recorder plan without starting services
  -h, --help              Show this help

Examples:
  bash scripts/record_speed_sweep.sh \
    --backend mooncake \
    --mountpoint /MNTPNT \
    --speedups 1,2,5,10 \
    --num-requests 1000

  bash scripts/record_speed_sweep.sh \
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
    --num-requests)
      require_value "$@"
      num_requests="$2"
      num_requests_set=true
      shift 2
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
[[ -n "$mountpoint" ]] || { echo "--mountpoint is required" >&2; usage >&2; exit 2; }
[[ "$mountpoint" == /* ]] || die "--mountpoint must be an absolute path: $mountpoint"
case "$backend" in
  mooncake)
    default_workloads="toolagent,conversation"
    ;;
  tensormesh)
    default_workloads="gaia,wildclaw,swebench"
    if [[ "$num_requests_set" == true ]]; then
      die "--num-requests is only valid with --backend mooncake"
    fi
    ;;
  *)
    die "--backend must be mooncake or tensormesh: $backend"
    ;;
esac

if [[ -z "$workloads" ]]; then
  workloads="$default_workloads"
fi

if [[ "$mooncake_trace_root_set" == false ]]; then
  mooncake_trace_root=""
fi

cd "$project_dir"
if [[ ! -f .venv/bin/activate ]]; then
  die "Project virtual environment is missing. Run: bash scripts/setup_runtime.sh"
fi
source .venv/bin/activate

IFS=',' read -r -a workload_list <<< "$workloads"
IFS=',' read -r -a speedup_list <<< "$speedups"
if ((${#workload_list[@]} == 0 || ${#speedup_list[@]} == 0)); then
  die "--workloads and --speedups must not be empty"
fi

output_root="${output_root%/}"
mooncake_trace_root="${mooncake_trace_root%/}"

for raw_speedup in "${speedup_list[@]}"; do
  speedup="${raw_speedup//[[:space:]]/}"
  [[ -n "$speedup" ]] || die "speedup entries must not be empty"
  if ! python -c 'import math, sys; value = float(sys.argv[1]); raise SystemExit(0 if math.isfinite(value) and value > 0 else 1)' "$speedup"; then
    die "speedup must be a finite positive number: $speedup"
  fi
done

for raw_workload in "${workload_list[@]}"; do
  workload="${raw_workload//[[:space:]]/}"
  case "$backend:$workload" in
    mooncake:toolagent|mooncake:conversation)
      config="configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml"
      ;;
    tensormesh:gaia|tensormesh:wildclaw|tensormesh:swebench)
      config="configs/recorder/qwen3-coder-480b-tp8-${workload}.yaml"
      ;;
    *)
      die "unsupported workload '$workload' for backend '$backend'"
      ;;
  esac

  for raw_speedup in "${speedup_list[@]}"; do
    speedup="${raw_speedup//[[:space:]]/}"
    run_name="${backend}-${workload}-x${speedup}"
    output_dir="${output_root}/${run_name}"
    command=(
      python -m recorder.main
      --config "$config"
      --mountpoint "$mountpoint"
      --speedup "$speedup"
      --output-dir "$output_dir"
    )

    if [[ "$backend" == mooncake ]]; then
      command+=(
        --mooncake-trace "$workload"
        --mooncake-num-requests "$num_requests"
      )
      if [[ "$mooncake_trace_root_set" == true ]]; then
        command+=(
          --mooncake-path "${mooncake_trace_root}/${workload}_trace.jsonl"
        )
      fi
    fi
    if [[ "$dry_run" == true ]]; then
      command+=(--dry-run)
    fi

    echo "[INFO] Recording ${run_name}"
    "${command[@]}"
  done
done

echo "[INFO] Completed speed sweep under: ${output_root}"
