#!/usr/bin/env bash
# Replay multiple workload traces across a set of scaled-open speedups.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$BASH_SOURCE")" && pwd)"
project_dir="$(cd -- "$script_dir/../.." && pwd)"
# shellcheck source=replay_common.sh
source "$script_dir/replay_common.sh"
trace_root=""
trace_name="storage.lct"
config=""
workloads=""
l2_root=""
output_root=""
output_root_set=false
speedups="1,2,5,10"
profile_config=""
dry_run=false
keep_l2=false

usage() {
  cat <<'EOF'
Usage:
  bash benchmarks/replayer/replay_workload_sweep.sh \
    --trace-root PATH \
    --config PATH \
    --workloads LIST \
    --l2-root ABSOLUTE_PATH \
    [OPTIONS]

Required:
  --trace-root PATH        Root containing <WORKLOAD>/<TRACE_NAME>
  --config PATH            Replayer YAML configuration
  --workloads LIST         Comma-separated workload directory names
  --l2-root PATH           Absolute root; each workload uses root/<WORKLOAD>

Options:
  --trace-name NAME        Trace filename under each workload (default: storage.lct)
  --speedups LIST          Comma-separated positive speedups (default: 1,2,5,10)
  --output-root PATH       Root for workload and speedup outputs (default:
                           outputs/replay-l2/<workload>-<UTC timestamp>)
  --profile PATH           Optional storage profiling configuration
  --keep-l2                Do not reset existing L2 case paths; require them to
                           be empty (default is to reset each case path)
  --dry-run                Print every replay command without starting LMCache
  -h, --help               Show this help

Example:
  bash benchmarks/replayer/replay_workload_sweep.sh \
    --trace-root /mnt/nvme/lmcache-traces/tensormesh-20260809 \
    --trace-name storage.lct \
    --config configs/replayer/fs-native.yaml \
    --workloads tensormesh-wildclaw,tensormesh-other \
    --l2-root /mnt/nvme/lmcache-trace-replay \
    --output-root outputs/replay-workload-sweep \
    --speedups 1,2,4,8
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
    --trace-root)
      require_value "$@"
      trace_root="$2"
      shift 2
      ;;
    --trace-name)
      require_value "$@"
      trace_name="$2"
      shift 2
      ;;
    --config)
      require_value "$@"
      config="$2"
      shift 2
      ;;
    --workloads)
      require_value "$@"
      workloads="$2"
      shift 2
      ;;
    --l2-root)
      require_value "$@"
      l2_root="$2"
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
      output_root_set=true
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
    --keep-l2)
      keep_l2=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$trace_root" ]] || { usage >&2; exit 2; }
[[ -n "$config" ]] || { usage >&2; exit 2; }
[[ -n "$workloads" ]] || { usage >&2; exit 2; }
[[ -n "$l2_root" ]] || { usage >&2; exit 2; }
[[ "$l2_root" == /* ]] || die "--l2-root must be an absolute path: $l2_root"
[[ -n "$trace_name" ]] || die "--trace-name must not be empty"
[[ -z "$(printf '%s' "$speedups" | tr -d '[:space:],')" ]] && die "--speedups must not be empty"

cd "$project_dir"
if [[ ! -f .venv/bin/activate ]]; then
  die "Project virtual environment is missing. Run: bash scripts/setup_runtime.sh --profile replayer"
fi
source .venv/bin/activate

resolve_existing_path() {
  local path="$1"
  if [[ "$path" != /* ]]; then
    path="$project_dir/$path"
  fi
  realpath -- "$path"
}

if [[ "$trace_root" != /* ]]; then
  trace_root="$project_dir/$trace_root"
fi
if [[ ! -d "$trace_root" ]]; then
  die "Trace root not found: $trace_root"
fi
trace_root="$(realpath -- "$trace_root")"

config="$(resolve_existing_path "$config")"
if [[ ! -f "$config" ]]; then
  die "Replayer config not found: $config"
fi
if [[ -n "$profile_config" ]]; then
  profile_config="$(resolve_existing_path "$profile_config")"
  if [[ ! -f "$profile_config" ]]; then
    die "Profiler config not found: $profile_config"
  fi
fi

if [[ "$output_root_set" == false ]]; then
  output_label="$(printf '%s' "$workloads" | tr -d '[:space:]' | tr ',' '-')"
  output_root="$(replay_default_output_root "$output_label")"
fi
mkdir -p -- "$output_root"
matrix_log="$output_root/workload-sweep.log"
results_jsonl="$output_root/workload-results.jsonl"
summary_path="$output_root/workload-summary.json"
: > "$matrix_log"
: > "$results_jsonl"
exec > >(tee -a "$matrix_log") 2>&1

echo "[INFO] Replay workload sweep started"
echo "[INFO] Trace root: $trace_root"
echo "[INFO] Trace name: $trace_name"
echo "[INFO] Config: $config"
echo "[INFO] Workloads: $workloads"
echo "[INFO] Speedups: $speedups"
echo "[INFO] Matrix log: $matrix_log"

case_count=0
overall_status=0

record_result() {
  local workload="$1"
  shift
  python - "$results_jsonl" "$workload" "$@" <<'PY'
import json
import sys

(
    results_path,
    workload,
    trace,
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
    "workload": workload,
    "trace": trace,
    "l2_root": l2_path,
    "output_root": output_dir,
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

while IFS= read -r raw_workload; do
  workload="$(printf '%s' "$raw_workload" | tr -d '[:space:]')"
  [[ -n "$workload" ]] || die "workload entries must not be empty"
  case "$workload" in
    .|..|*/*)
      die "workload must be a directory name without '/': $workload"
      ;;
  esac

  trace="$trace_root/$workload/$trace_name"
  workload_l2_root="$l2_root/$workload"
  workload_output_root="$output_root/$workload"

  if [[ ! -f "$trace" ]]; then
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[ERROR] Workload $workload skipped: trace not found: $trace" >&2
    record_result "$workload" "$trace" "$workload_l2_root" "$workload_output_root" \
      "preflight_failed" 2 "$now" "$now" 0 "trace file not found"
    overall_status=1
    case_count=$((case_count + 1))
    continue
  fi

  command=(
    bash "$script_dir/replay_speed_sweep.sh"
    --trace "$trace"
    --config "$config"
    --l2-root "$workload_l2_root"
    --output-root "$workload_output_root"
    --speedups "$speedups"
  )
  if [[ -n "$profile_config" ]]; then
    command+=(--profile "$profile_config")
  fi
  if [[ "$keep_l2" == true ]]; then
    command+=(--keep-l2)
  fi
  if [[ "$dry_run" == true ]]; then
    command+=(--dry-run)
  fi

  echo "[INFO] Replay workload $workload"
  printf '[INFO] Command:'
  for argument in "${command[@]}"; do
    printf ' %q' "$argument"
  done
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
    overall_status=1
    result_status="failed"
    reason="replay speed sweep failed; inspect $workload_output_root/sweep.log"
    echo "[ERROR] Workload $workload failed with exit code $command_status" >&2
  fi
  elapsed_seconds="$SECONDS"
  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  record_result "$workload" "$trace" "$workload_l2_root" "$workload_output_root" \
    "$result_status" "$command_status" "$started_at" "$ended_at" "$elapsed_seconds" "$reason"
  case_count=$((case_count + 1))
done < <(printf '%s\n' "$workloads" | tr ',' '\n')

if [[ ! -s "$results_jsonl" ]]; then
  die "No workloads were executed. Check --workloads and $matrix_log"
fi

python - "$results_jsonl" "$summary_path" "$trace_root" "$trace_name" "$config" \
  "$l2_root" "$output_root" "$speedups" "$matrix_log" <<'PY'
import json
import sys
from pathlib import Path

(
    results_path,
    summary_path,
    trace_root,
    trace_name,
    config,
    l2_root,
    output_root,
    speedups,
    matrix_log,
) = sys.argv[1:]
results = [
    json.loads(line)
    for line in Path(results_path).read_text(encoding="utf-8").splitlines()
    if line
]
summary = {
    "trace_root": trace_root,
    "trace_name": trace_name,
    "config": config,
    "l2_root": l2_root,
    "output_root": output_root,
    "speedups": [
        float(item.strip())
        for item in speedups.split(",")
        if item.strip()
    ],
    "workloads": [item["workload"] for item in results],
    "results": results,
    "completed": len(results),
    "failed": sum(item["returncode"] != 0 for item in results),
    "matrix_log": matrix_log,
}
Path(summary_path).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"[INFO] Replay workload sweep summary: {summary_path}")
PY

if ((overall_status != 0)); then
  echo "[ERROR] Replay workload sweep completed with failures. See: $matrix_log" >&2
  exit "$overall_status"
fi
echo "[INFO] Completed replay workload sweep under: $output_root"
echo "[INFO] Matrix log: $matrix_log"
