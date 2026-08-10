#!/usr/bin/env bash
# Replay one storage trace through N isolated processes on this host.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/../.." && pwd)"

cd "$project_dir"
if [[ ! -f .venv/bin/activate ]]; then
  echo "Project virtual environment is missing. Run: bash scripts/setup_runtime.sh" >&2
  exit 1
fi
source .venv/bin/activate

exec python -m replayer.instances "$@"
