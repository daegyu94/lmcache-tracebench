#!/usr/bin/env bash
# Record independent SWE-bench, GAIA, and WildClaw LMCache storage traces.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
output_root="outputs"
mountpoint=""

usage() {
  echo "Usage: bash scripts/record_source_traces.sh --mountpoint PATH [--output-root PATH]"
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

if [[ -z "$mountpoint" || "$mountpoint" != /* ]]; then
  echo "--mountpoint must be an absolute path" >&2
  usage >&2
  exit 2
fi

cd "$project_dir"
if [[ ! -f .venv/bin/activate ]]; then
  echo "Project virtual environment is missing. Run: bash scripts/setup_runtime.sh" >&2
  exit 1
fi
source .venv/bin/activate

run_stamp="$(date +%Y%m%d-%H%M%S)"
run_root="$output_root/source-traces-$run_stamp"
mkdir -p "$run_root"

echo "[INFO] Recording source traces under: $run_root"
echo "[INFO] Each run resets only its own source-specific L2 directory."

# Start with the smallest working set to validate the environment quickly.
for source in gaia wildclaw swebench; do
  config="configs/recorder/qwen3-coder-480b-tp8-$source.yaml"
  echo "[INFO] Starting $source trace"
  python -m recorder.main \
    --config "$config" \
    --mountpoint "$mountpoint" \
    --output-dir "$run_root/$source"
  echo "[INFO] Finished $source trace"
done

echo "[INFO] Completed all source traces: $run_root"
