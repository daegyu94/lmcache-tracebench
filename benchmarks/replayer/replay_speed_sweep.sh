#!/usr/bin/env bash
# Replay one storage trace across a set of scaled-open speedups.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$BASH_SOURCE")" && pwd)"
project_dir="$(cd -- "$script_dir/../.." && pwd)"
trace=""
config=""
l2_root=""
output_root="outputs/replay-speed-sweep"
speedups="1,2,5,10"
profile_config=""
dry_run=false

usage() {
  cat <<'EOF'
Usage:
  bash benchmarks/replayer/replay_speed_sweep.sh \
    --trace PATH \
    --config PATH \
    --l2-root ABSOLUTE_PATH \
    [OPTIONS]

Required:
  --trace PATH             Source storage trace (.lct)
  --config PATH            Replayer YAML configuration
  --l2-root PATH           Absolute root; each speedup uses root/x<SPEEDUP>

Options:
  --speedups LIST          Comma-separated positive speedups (default: 1,2,5,10)
  --output-root PATH       Root for per-speedup output (default:
                           outputs/replay-speed-sweep)
  --profile PATH           Optional storage profiling configuration
  --dry-run                Print every replay command without starting LMCache
  -h, --help               Show this help

Examples:
  bash benchmarks/replayer/replay_speed_sweep.sh \
    --trace outputs/speed-sweep/tensormesh-gaia-x1/storage.lct \
    --config configs/replayer/fs-native.yaml \
    --l2-root /MNTPNT/lmcache-trace-replay/speed-sweep \
    --output-root outputs/replay/speed-sweep/gaia \
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
    --trace)
      require_value "$@"
      trace="$2"
      shift 2
      ;;
    --config)
      require_value "$@"
      config="$2"
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
[[ -n "$config" ]] || { usage >&2; exit 2; }
[[ -n "$l2_root" ]] || { usage >&2; exit 2; }
[[ "$l2_root" == /* ]] || die "--l2-root must be an absolute path: $l2_root"

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
config="$(resolve_project_path "$config")"
if [[ ! -f "$trace" ]]; then
  die "Trace file not found: $trace"
fi
if [[ ! -f "$config" ]]; then
  die "Replayer config not found: $config"
fi

mkdir -p -- "$output_root"
sweep_log="$output_root/sweep.log"
: > "$sweep_log"
exec > >(tee -a "$sweep_log") 2>&1
echo "[INFO] Replay speed sweep started"
echo "[INFO] Trace: $trace"
echo "[INFO] Config: $config"
echo "[INFO] Speedups: $speedups"
echo "[INFO] Sweep log: $sweep_log"

case_count=0
on_interrupt() {
  local signal="$1"
  echo "[ERROR] Replay speed sweep interrupted by SIG$signal after $case_count completed case(s)." >&2
  echo "[ERROR] See sweep log: $sweep_log" >&2
  exit $((128 + signal))
}
trap 'on_interrupt 2' INT
trap 'on_interrupt 15' TERM
if [[ -n "$profile_config" ]]; then
  profile_config="$(resolve_project_path "$profile_config")"
  if [[ ! -f "$profile_config" ]]; then
    die "Profiler config not found: $profile_config"
  fi
fi

if [[ -z "$(printf '%s' "$speedups" | tr -d '[:space:],')" ]]; then
  die "--speedups must not be empty"
fi

ensure_case_path_available() {
  local path="$1"
  local label="$2"
  if [[ -L "$path" ]]; then
    die "$label is a symlink; use a fresh case path: $path"
  fi
  if [[ -e "$path" && ! -d "$path" ]]; then
    die "$label is not a directory: $path"
  fi
  if [[ -d "$path" ]] && [[ -n "$(find -P "$path" -mindepth 1 -print -quit)" ]]; then
    die "$label is not empty; use a fresh sweep root to avoid warm-cache results: $path"
  fi
}

while IFS= read -r raw_speedup; do
  speedup="$(printf '%s' "$raw_speedup" | tr -d '[:space:]')"
  [[ -n "$speedup" ]] || die "speedup entries must not be empty"
  if ! python -c 'import math, sys; value = float(sys.argv[1]); raise SystemExit(0 if math.isfinite(value) and value > 0 else 1)' "$speedup"; then
    die "speedup must be a finite positive number: $speedup"
  fi
done < <(printf '%s\n' "$speedups" | tr ',' '\n')

while IFS= read -r raw_speedup; do
  speedup="$(printf '%s' "$raw_speedup" | tr -d '[:space:]')"
  case_name="x$speedup"
  ensure_case_path_available "$l2_root/$case_name" "L2 case path"
  ensure_case_path_available "$output_root/$case_name" "output case path"
done < <(printf '%s\n' "$speedups" | tr ',' '\n')

if [[ "$dry_run" == false ]]; then
  mkdir -p -- "$l2_root"
fi

results_jsonl="$output_root/sweep-results.jsonl"
summary_path="$output_root/sweep-summary.json"
: > "$results_jsonl"
overall_status=0

while IFS= read -r raw_speedup; do
  speedup="$(printf '%s' "$raw_speedup" | tr -d '[:space:]')"
  case_name="x$speedup"
  l2_path="$l2_root/$case_name"
  output_dir="$output_root/$case_name"
  command=(
    python -m replayer.main
    --trace "$trace"
    --config "$config"
    --speedup "$speedup"
    --l2-path "$l2_path"
    --output-dir "$output_dir"
  )
  if [[ -n "$profile_config" ]]; then
    command+=(--profile "$profile_config")
  fi
  if [[ "$dry_run" == true ]]; then
    command+=(--dry-run)
  else
    mkdir -p -- "$l2_path" "$output_dir"
  fi

  printf '[INFO] Replay speedup %s\n' "$speedup"
  printf '[INFO] Command:'
  printf ' %q' "${command[@]}"
  printf '\n'

  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  SECONDS=0
  if "${command[@]}"; then
    command_status=0
  else
    command_status=$?
    overall_status=1
  fi
  elapsed_seconds="$SECONDS"
  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  python - "$results_jsonl" "$speedup" "$l2_path" "$output_dir" \
    "$command_status" "$dry_run" "$started_at" "$ended_at" "$elapsed_seconds" \
    "$output_dir/lmcache-replay.log" <<'PY'
import json
import sys

(
    results_path,
    raw_speedup,
    l2_path,
    output_dir,
    raw_returncode,
    dry_run,
    started_at,
    ended_at,
    raw_elapsed_seconds,
    lmcache_log,
) = sys.argv[1:]
returncode = int(raw_returncode)
record = {
    "speedup": float(raw_speedup),
    "l2_path": l2_path,
    "output_dir": output_dir,
    "lmcache_log": lmcache_log,
    "returncode": returncode,
    "status": "dry_run" if dry_run == "true" else (
        "ok" if returncode == 0 else "failed"
    ),
    "started_at_utc": started_at,
    "ended_at_utc": ended_at,
    "elapsed_seconds": int(raw_elapsed_seconds),
}
with open(results_path, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
PY

  if ((command_status != 0)); then
    echo "[ERROR] Replay speedup $speedup failed with exit code $command_status" >&2
    echo "[ERROR] Inspect LMCache log: $output_dir/lmcache-replay.log" >&2
  fi
  case_count=$((case_count + 1))
done < <(printf '%s\n' "$speedups" | tr ',' '\n')

if [[ ! -s "$results_jsonl" ]]; then
  die "No speedup cases were executed. Check --speedups and $sweep_log"
fi

python - "$results_jsonl" "$summary_path" "$trace" "$config" "$l2_root" "$output_root" "$sweep_log" <<'PY'
import json
import sys
from pathlib import Path

results_path, summary_path, trace, config, l2_root, output_root, sweep_log = sys.argv[1:]
results = [
    json.loads(line)
    for line in Path(results_path).read_text(encoding="utf-8").splitlines()
    if line
]
summary = {
    "trace": trace,
    "config": config,
    "l2_root": l2_root,
    "output_root": output_root,
    "sweep_log": sweep_log,
    "speedups": [item["speedup"] for item in results],
    "results": results,
    "completed": len(results),
    "failed": sum(item["returncode"] != 0 for item in results),
}
Path(summary_path).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"[INFO] Replay speed sweep summary: {summary_path}")
PY

if ((overall_status != 0)); then
  echo "[ERROR] Replay speed sweep completed with failures. See: $sweep_log" >&2
  exit "$overall_status"
fi
echo "[INFO] Launcher log: $sweep_log"
echo "[INFO] Completed replay speed sweep under: $output_root"
