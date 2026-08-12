#!/usr/bin/env bash
# Replay one storage trace across a set of L1 capacity values.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$BASH_SOURCE")" && pwd)"
project_dir="$(cd -- "$script_dir/../.." && pwd)"
# shellcheck source=replay_common.sh
source "$script_dir/replay_common.sh"
trace=""
config=""
l2_root=""
output_root=""
output_root_set=false
l1_sizes="20,40,80,160"
speedup="1"
profile_config=""
dry_run=false
keep_l2=false

usage() {
  cat <<'EOF'
Usage:
  bash benchmarks/replayer/replay_l1_size_sweep.sh \
    --trace PATH \
    --config PATH \
    --l2-root ABSOLUTE_PATH \
    [OPTIONS]

Required:
  --trace PATH             Source storage trace (.lct)
  --config PATH            Replayer YAML configuration
  --l2-root PATH           Absolute L2 path reused and reset before each L1 size

Options:
  --l1-sizes LIST          Comma-separated positive integer GiB values
                           (default: 20,40,80,160)
  --speedup VALUE          Storage timestamp speedup (default: 1)
  --output-root PATH       Root for per-L1-size output (default:
                           outputs/replay-l2/<trace-name>-<UTC timestamp>)
  --profile PATH           Optional storage profiling configuration
  --keep-l2                Reuse L2 contents across cases; base must start empty
  --dry-run                Print every replay command without starting LMCache
  -h, --help               Show this help

Example:
  bash benchmarks/replayer/replay_l1_size_sweep.sh \
    --trace /path/to/storage.lct \
    --config configs/replayer/fs-native.yaml \
    --l2-root /mnt/lmcache-replay/workload \
    --output-root outputs/replay-l1-size-sweep/workload \
    --l1-sizes 20,40,80,160 \
    --speedup 8
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
      output_root_set=true
      shift 2
      ;;
    --profile|--profile-config)
      require_value "$@"
      profile_config="$2"
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
if [[ -n "$profile_config" ]]; then
  profile_config="$(resolve_project_path "$profile_config")"
  if [[ ! -f "$profile_config" ]]; then
    die "Profiler config not found: $profile_config"
  fi
fi
if [[ "$output_root_set" == false ]]; then
  output_root="$(replay_default_output_root "$(replay_trace_label "$trace")")"
fi

mkdir -p -- "$output_root"
sweep_log="$output_root/l1-size-sweep.log"
: > "$sweep_log"
exec > >(tee -a "$sweep_log") 2>&1
echo "[INFO] Replay L1 size sweep started"
echo "[INFO] Trace: $trace"
echo "[INFO] Config: $config"
echo "[INFO] L1 sizes (GiB): $l1_sizes"
echo "[INFO] Speedup: $speedup"
echo "[INFO] Sweep log: $sweep_log"

if ! python -c 'import math, sys; value = float(sys.argv[1]); raise SystemExit(0 if math.isfinite(value) and value > 0 else 1)' "$speedup"; then
  die "speedup must be a finite positive number: $speedup"
fi
if [[ -z "$(printf '%s' "$l1_sizes" | tr -d '[:space:],')" ]]; then
  die "--l1-sizes must not be empty"
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

reset_l2_root() {
  local path="$1"
  local label="$2"
  if [[ -L "$path" ]]; then
    die "$label is a symlink; refusing to reset it: $path"
  fi
  if [[ -e "$path" && ! -d "$path" ]]; then
    die "$label is not a directory: $path"
  fi
  if [[ "$path" == "/" ]]; then
    die "$label must not be the filesystem root"
  fi
  if [[ -d "$path" ]]; then
    echo "[INFO] Resetting $label: $path"
    rm -rf --one-file-system -- "$path"
  fi
  mkdir -p -- "$path"
}

while IFS= read -r raw_l1_size; do
  l1_size="$(printf '%s' "$raw_l1_size" | tr -d '[:space:]')"
  [[ -n "$l1_size" ]] || die "L1 size entries must not be empty"
  if [[ ! "$l1_size" =~ ^[0-9]+$ ]] || ((l1_size <= 0)); then
    die "L1 size must be a positive integer in GiB: $l1_size"
  fi
  case_name="l1-$l1_size""gb"
  if [[ "$keep_l2" == true ]]; then
    ensure_case_path_available "$l2_root" "L2 root"
  else
    reset_path="$l2_root"
    if [[ -L "$reset_path" ]]; then
      die "L2 root is a symlink; refusing to reset it: $reset_path"
    fi
    if [[ -e "$reset_path" && ! -d "$reset_path" ]]; then
      die "L2 root is not a directory: $reset_path"
    fi
  fi
  ensure_case_path_available "$output_root/$case_name" "output case path"
done < <(printf '%s\n' "$l1_sizes" | tr ',' '\n')

if [[ "$dry_run" == false && "$keep_l2" == true ]]; then
  mkdir -p -- "$l2_root"
elif [[ "$dry_run" == true && "$keep_l2" == false ]]; then
  echo "[INFO] Dry run: existing L2 roots will not be reset"
fi

results_jsonl="$output_root/l1-size-results.jsonl"
summary_path="$output_root/l1-size-summary.json"
: > "$results_jsonl"
overall_status=0

while IFS= read -r raw_l1_size; do
  l1_size="$(printf '%s' "$raw_l1_size" | tr -d '[:space:]')"
  case_name="l1-$l1_size""gb"
  l2_path="$l2_root"
  output_dir="$output_root/$case_name"
  if [[ "$dry_run" == false && "$keep_l2" == false ]]; then
    reset_l2_root "$l2_root" "L2 root"
  fi
  command=(
    python -m replayer.main
    --trace "$trace"
    --config "$config"
    --speedup "$speedup"
    --l1-size-gb "$l1_size"
    --l1-init-size-gb "$l1_size"
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

  printf '[INFO] Replay L1 size %s GiB\n' "$l1_size"
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
  else
    command_status=$?
    overall_status=1
    result_status="failed"
  fi
  elapsed_seconds="$SECONDS"
  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  python - "$results_jsonl" "$l1_size" "$speedup" "$l2_path" "$output_dir" \
    "$command_status" "$result_status" "$started_at" "$ended_at" "$elapsed_seconds" \
    "$output_dir/lmcache-replay.log" <<'PY'
import json
import sys

(
    results_path,
    raw_l1_size,
    raw_speedup,
    l2_path,
    output_dir,
    raw_returncode,
    status,
    started_at,
    ended_at,
    raw_elapsed_seconds,
    lmcache_log,
) = sys.argv[1:]
record = {
    "l1_init_size_gb": int(raw_l1_size),
    "l1_size_gb": float(raw_l1_size),
    "speedup": float(raw_speedup),
    "l2_path": l2_path,
    "output_dir": output_dir,
    "lmcache_log": lmcache_log,
    "returncode": int(raw_returncode),
    "status": status,
    "started_at_utc": started_at,
    "ended_at_utc": ended_at,
    "elapsed_seconds": int(raw_elapsed_seconds),
}
with open(results_path, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
PY

  if ((command_status != 0)); then
    echo "[ERROR] Replay L1 size $l1_size GiB failed with exit code $command_status" >&2
    echo "[ERROR] Inspect LMCache log: $output_dir/lmcache-replay.log" >&2
  fi
done < <(printf '%s\n' "$l1_sizes" | tr ',' '\n')

if [[ ! -s "$results_jsonl" ]]; then
  die "No L1 size cases were executed. Check --l1-sizes and $sweep_log"
fi

python - "$results_jsonl" "$summary_path" "$trace" "$config" "$l2_root" "$output_root" \
  "$l1_sizes" "$speedup" "$sweep_log" <<'PY'
import json
import sys
from pathlib import Path

(
    results_path,
    summary_path,
    trace,
    config,
    l2_root,
    output_root,
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
    "config": config,
    "l2_root": l2_root,
    "output_root": output_root,
    "l1_sizes_gb": [int(item.strip()) for item in raw_l1_sizes.split(",") if item.strip()],
    "speedup": float(raw_speedup),
    "sweep_log": sweep_log,
    "results": results,
    "completed": len(results),
    "failed": sum(item["returncode"] != 0 for item in results),
}
Path(summary_path).write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"[INFO] Replay L1 size sweep summary: {summary_path}")
PY

if ((overall_status != 0)); then
  echo "[ERROR] Replay L1 size sweep completed with failures. See: $sweep_log" >&2
  exit "$overall_status"
fi
echo "[INFO] Completed replay L1 size sweep under: $output_root"
echo "[INFO] Sweep log: $sweep_log"
