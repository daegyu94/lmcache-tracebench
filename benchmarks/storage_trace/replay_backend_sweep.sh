#!/usr/bin/env bash
# Replay one storage trace across configured storage backends.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$BASH_SOURCE")" && pwd)"
project_dir="$(cd -- "$script_dir/../.." && pwd)"
trace=""
experiment="speedup"
speedups="1,2,4,8"
l1_sizes="20,40,80,160"
speedup="1"
output_root="outputs/replay-backend-sweep"
profile_config=""
dry_run=false
backend_specs=()

usage() {
  cat <<'EOF'
Usage:
  bash benchmarks/storage_trace/replay_backend_sweep.sh \
    --trace PATH \
    --backend-spec NAME=CONFIG@L2_PATH \
    [OPTIONS]

Required:
  --trace PATH             Source storage trace (.lct)
  --backend-spec SPEC      Repeatable backend mapping. NAME is an output label,
                           CONFIG is a replayer YAML, and L2_PATH is its target.

Options:
  --experiment MODE        speedup or l1-size (default: speedup)
  --speedups LIST          Speedup values for the speedup experiment
                           (default: 1,2,4,8)
  --l1-sizes LIST          L1 GiB values for the l1-size experiment
                           (default: 20,40,80,160)
  --speedup VALUE          Storage timestamp speedup for the l1-size experiment
                           (default: 1)
  --output-root PATH       Root for per-backend outputs (default:
                           outputs/replay-backend-sweep)
  --profile PATH           Optional storage profiling configuration
  --dry-run                Print commands without starting LMCache
  -h, --help               Show this help

Examples:
  bash benchmarks/storage_trace/replay_backend_sweep.sh \
    --trace /path/to/storage.lct \
    --backend-spec 'xfs=configs/replayer/fs-native.yaml@/mnt/xfs/lmcache-replay' \
    --backend-spec 'pnfs=configs/replayer/fs-native.yaml@/mnt/pnfs/lmcache-replay' \
    --backend-spec '3fs=configs/replayer/nixl-hf3fs.yaml@/mnt/3fs/lmcache-replay' \
    --experiment speedup \
    --speedups 1,2,4,8

  bash benchmarks/storage_trace/replay_backend_sweep.sh \
    --trace /path/to/storage.lct \
    --backend-spec 'xfs=configs/replayer/fs-native.yaml@/mnt/xfs/lmcache-replay' \
    --backend-spec 'pnfs=configs/replayer/fs-native.yaml@/mnt/pnfs/lmcache-replay' \
    --backend-spec '3fs=configs/replayer/nixl-hf3fs.yaml@/mnt/3fs/lmcache-replay' \
    --experiment l1-size \
    --l1-sizes 20,40,80,160 \
    --speedup 1
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
    --trace)
      require_value "$@"
      trace="$2"
      shift 2
      ;;
    --backend-spec)
      require_value "$@"
      backend_specs+=("$2")
      shift 2
      ;;
    --experiment)
      require_value "$@"
      experiment="$2"
      shift 2
      ;;
    --speedups)
      require_value "$@"
      speedups="$2"
      shift 2
      ;;
    --l1-sizes)
      require_value "$@"
      l1_sizes="$2"
      shift 2
      ;;
    --speedup)
      require_value "$@"
      speedup="$2"
      shift 2
      ;;
    --output-root)
      require_value "$@"
      output_root="$2"
      shift 2
      ;;
    --profile|--profile-config)
      require_value "$@"
      profile_config="$2"
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

[[ -n "$trace" ]] || { usage >&2; exit 2; }
if ((${#backend_specs[@]} == 0)); then
  die "at least one --backend-spec is required"
fi
case "$experiment" in
  speedup|l1-size)
    ;;
  *)
    die "--experiment must be speedup or l1-size: $experiment"
    ;;
esac

cd "$project_dir"
if [[ ! -f .venv/bin/activate ]]; then
  die "Project virtual environment is missing. Run: bash scripts/setup_runtime.sh --profile replayer"
fi
source .venv/bin/activate

resolve_project_path() {
  local path="$1"
  if [[ "$path" != /* ]]; then
    path="$project_dir/$path"
  fi
  realpath -- "$path"
}

trace="$(resolve_project_path "$trace")"
if [[ ! -f "$trace" ]]; then
  die "Trace file not found: $trace"
fi
if [[ -n "$profile_config" ]]; then
  profile_config="$(resolve_project_path "$profile_config")"
  if [[ ! -f "$profile_config" ]]; then
    die "Profiler config not found: $profile_config"
  fi
fi

mkdir -p -- "$output_root"
sweep_log="$output_root/backend-sweep.log"
results_jsonl="$output_root/backend-results.jsonl"
summary_path="$output_root/backend-summary.json"
: > "$sweep_log"
: > "$results_jsonl"
exec > >(tee -a "$sweep_log") 2>&1

echo "[INFO] Replay backend sweep started"
echo "[INFO] Experiment: $experiment"
echo "[INFO] Trace: $trace"
echo "[INFO] Backend specs: ${#backend_specs[@]}"
echo "[INFO] Output root: $output_root"
echo "[INFO] Sweep log: $sweep_log"

record_result() {
  local backend="$1"
  shift
  python - "$results_jsonl" "$backend" "$@" <<'PY'
import json
import sys

(
    results_path,
    backend,
    experiment,
    config,
    l2_path,
    output_dir,
    status,
    raw_returncode,
    started_at,
    ended_at,
    raw_elapsed_seconds,
    reason,
) = sys.argv[1:]
record = {
    "backend": backend,
    "experiment": experiment,
    "config": config,
    "l2_path": l2_path,
    "output_dir": output_dir,
    "status": status,
    "returncode": int(raw_returncode),
    "started_at_utc": started_at,
    "ended_at_utc": ended_at,
    "elapsed_seconds": int(raw_elapsed_seconds),
}
if reason:
    record["reason"] = reason
with open(results_path, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
PY
}

for spec in "${backend_specs[@]}"; do
  backend="${spec%%=*}"
  value="${spec#*=}"
  backend_config="${value%@*}"
  backend_l2_path="${value##*@}"

  if [[ ! "$backend" =~ ^[A-Za-z0-9._-]+$ ]]; then
    die "backend name contains unsupported characters: $backend"
  fi
  if [[ -z "$backend_config" || -z "$backend_l2_path" || "$backend_config" == "$value" ]]; then
    die "backend spec must use NAME=CONFIG@L2_PATH: $spec"
  fi
  if [[ "$backend_l2_path" != /* ]]; then
    die "backend L2 path must be absolute: $backend_l2_path"
  fi

  if [[ "$backend_config" != /* ]]; then
    backend_config="$project_dir/$backend_config"
  fi
  if [[ ! -f "$backend_config" ]]; then
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[ERROR] Backend $backend skipped: config not found: $backend_config" >&2
    record_result "$backend" "$experiment" "$backend_config" "$backend_l2_path" \
      "$output_root/$backend" "preflight_failed" 2 "$now" "$now" 0 \
      "backend config not found"
    continue
  fi
  backend_config="$(realpath -- "$backend_config")"

  command=()
  backend_output_root="$output_root/$backend"
  case "$experiment" in
    speedup)
      command=(
        bash "$script_dir/replay_speed_sweep.sh"
        --trace "$trace"
        --config "$backend_config"
        --l2-root "$backend_l2_path"
        --output-root "$backend_output_root"
        --speedups "$speedups"
      )
      ;;
    l1-size)
      command=(
        bash "$script_dir/replay_l1_size_sweep.sh"
        --trace "$trace"
        --config "$backend_config"
        --l2-root "$backend_l2_path"
        --output-root "$backend_output_root"
        --l1-sizes "$l1_sizes"
        --speedup "$speedup"
      )
      ;;
  esac
  if [[ -n "$profile_config" ]]; then
    command+=(--profile "$profile_config")
  fi
  if [[ "$dry_run" == true ]]; then
    command+=(--dry-run)
  fi

  echo "[INFO] Replay backend $backend"
  printf '[INFO] Command:'
  printf ' %q' "${command[@]}"
  printf '\n'

  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  SECONDS=0
  if "${command[@]}"; then
    command_status=0
    if [[ "$dry_run" == true ]]; then
      result_status="dry_run"
    else
      result_status="ok"
    fi
    reason=""
  else
    command_status=$?
    result_status="failed"
    reason="backend replay failed; inspect $backend_output_root"
    echo "[ERROR] Backend $backend failed with exit code $command_status" >&2
  fi
  elapsed_seconds="$SECONDS"
  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  record_result "$backend" "$experiment" "$backend_config" "$backend_l2_path" \
    "$backend_output_root" "$result_status" "$command_status" "$started_at" \
    "$ended_at" "$elapsed_seconds" "$reason"
done

if [[ ! -s "$results_jsonl" ]]; then
  die "No backend cases were executed. Check --backend-spec and $sweep_log"
fi

python - "$results_jsonl" "$summary_path" "$trace" "$experiment" "$output_root" \
  "$speedups" "$l1_sizes" "$speedup" "$sweep_log" <<'PY'
import json
import sys
from pathlib import Path

(
    results_path,
    summary_path,
    trace,
    experiment,
    output_root,
    raw_speedups,
    raw_l1_sizes,
    raw_speedup,
    sweep_log,
) = sys.argv[1:]
results = [
    json.loads(line)
    for line in Path(results_path).read_text(encoding="utf-8").splitlines()
    if line
]
summary = {
    "trace": trace,
    "experiment": experiment,
    "output_root": output_root,
    "speedups": [item.strip() for item in raw_speedups.split(",") if item.strip()],
    "l1_sizes_gb": [
        item.strip() for item in raw_l1_sizes.split(",") if item.strip()
    ],
    "speedup": raw_speedup,
    "backends": [item["backend"] for item in results],
    "results": results,
    "completed": len(results),
    "failed": sum(item["returncode"] != 0 for item in results),
    "sweep_log": sweep_log,
}
Path(summary_path).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"[INFO] Replay backend sweep summary: {summary_path}")
PY

failed_count="$(python -c 'import json, sys; print(json.load(open(sys.argv[1]))["failed"])' "$summary_path")"
if ((failed_count > 0)); then
  echo "[ERROR] Replay backend sweep completed with failures. See: $sweep_log" >&2
  exit 1
fi
echo "[INFO] Completed replay backend sweep under: $output_root"
echo "[INFO] Sweep log: $sweep_log"
