#!/usr/bin/env bash
# Run the staged-remote report experiment matrix with the project venv.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/../.." && pwd -P)"
cd "$project_root"

if [[ ! -f .venv/bin/activate ]]; then
  echo "[ERROR] Project virtual environment is missing. Run: bash scripts/setup_runtime.sh --profile replayer-cpu" >&2
  exit 1
fi
source .venv/bin/activate
exec python benchmarks/evaluation/run_report_experiments.py "$@"
