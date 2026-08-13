#!/usr/bin/env bash
# Upload or download LMCache trace assets from a Hugging Face Dataset repository.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/../.." && pwd -P)"
python_command="${PYTHON:-python}"
if [[ -z "${PYTHON:-}" && -x "$project_root/.venv/bin/python" ]]; then
  python_command="$project_root/.venv/bin/python"
fi

usage() {
  cat <<'EOF'
Usage:
  bash tools/artifacts/hf_trace_asset.sh upload --repo-id REPO --filepath PATH \
    [--path-in-repo PATH] [--revision REVISION] [--clobber] [--dry-run]
  bash tools/artifacts/hf_trace_asset.sh download --repo-id REPO --path-in-repo PATH \
    --output-dir PATH [--revision REVISION] [--clobber] [--dry-run]
  bash tools/artifacts/hf_trace_asset.sh list --repo-id REPO [--revision REVISION]

Commands:
  upload    Upload a local file to a Hugging Face Dataset repository.
  download  Download a file from a Hugging Face Dataset repository.
  list      List files in a Hugging Face Dataset repository revision.

Options:
  --repo-id REPO       Dataset repository, for example daegyu94/lmcache-storage-traces.
  --filepath PATH      Local file to upload.
  --path-in-repo PATH  Dataset path. Upload defaults to the local basename.
  --output-dir PATH    Directory used for downloaded files.
  --revision REVISION  Dataset branch, tag, or commit. Defaults to main.
  --clobber            Replace an existing destination file.
  --dry-run            Print the operation without contacting Hugging Face.

Authentication:
  Set HF_TOKEN to a token with write access for upload, or authenticate the
  huggingface_hub client in the project environment. Downloads from public
  repositories do not require authentication.

Examples:
  bash tools/artifacts/hf_trace_asset.sh upload \
    --repo-id daegyu94/lmcache-storage-traces \
    --filepath /path/to/gaia.tar.gz \
    --path-in-repo tensormesh/gaia.tar.gz
  bash tools/artifacts/hf_trace_asset.sh download \
    --repo-id daegyu94/lmcache-storage-traces \
    --path-in-repo tensormesh/gaia.tar.gz \
    --output-dir downloads
EOF
}

require_hf_client() {
  if ! "$python_command" -c 'import huggingface_hub' >/dev/null 2>&1; then
    echo "huggingface_hub is required in the project Python environment." >&2
    echo "Install it with: python -m pip install huggingface_hub" >&2
    exit 1
  fi
}

require_option() {
  if (($# < 2)); then
    echo "Missing value for $1" >&2
    usage >&2
    exit 2
  fi
}

validate_repo_path() {
  local path="$1"
  case "$path" in
    ""|/*|../*|*/../*|*/..)
      echo "Dataset path must be relative and must not escape the repository: $path" >&2
      exit 2
      ;;
  esac
}

run_upload() {
  local repo_id=""
  local filepath=""
  local path_in_repo=""
  local revision="main"
  local clobber=false
  local dry_run=false

  while (($#)); do
    case "$1" in
      --repo-id)
        require_option "$@"
        repo_id="$2"
        shift 2
        ;;
      --filepath)
        require_option "$@"
        filepath="$2"
        shift 2
        ;;
      --path-in-repo)
        require_option "$@"
        path_in_repo="$2"
        shift 2
        ;;
      --revision)
        require_option "$@"
        revision="$2"
        shift 2
        ;;
      --clobber)
        clobber=true
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
        echo "Unknown upload argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done

  if [[ -z "$repo_id" || -z "$filepath" ]]; then
    usage >&2
    exit 2
  fi
  if [[ -z "$path_in_repo" ]]; then
    path_in_repo="$(basename -- "$filepath")"
  fi
  validate_repo_path "$path_in_repo"
  if [[ ! -f "$filepath" ]]; then
    echo "Upload file not found: $filepath" >&2
    exit 1
  fi

  local file_size_bytes
  file_size_bytes="$(stat --format=%s -- "$filepath")"
  printf '[INFO] Upload source: %s\n' "$filepath"
  printf '[INFO] Dataset: %s\n' "$repo_id"
  printf '[INFO] Dataset path: %s\n' "$path_in_repo"
  printf '[INFO] Revision: %s\n' "$revision"
  printf '[INFO] Size: %s bytes\n' "$file_size_bytes"

  if [[ "$dry_run" == true ]]; then
    printf '[INFO] Would upload the file to the Hugging Face Dataset.\n'
    return
  fi

  require_hf_client
  "$python_command" - "$repo_id" "$filepath" "$path_in_repo" "$revision" "$clobber" <<'PY'
import os
import sys

from huggingface_hub import HfApi

repo_id, filepath, path_in_repo, revision, clobber = sys.argv[1:]
api = HfApi(token=os.environ.get("HF_TOKEN"))
existing_files = api.list_repo_files(
    repo_id=repo_id,
    repo_type="dataset",
    revision=revision,
)
if path_in_repo in existing_files and clobber != "true":
    raise SystemExit(
        f"Dataset file already exists: {path_in_repo} "
        "(use --clobber to replace it)"
    )

url = api.upload_file(
    path_or_fileobj=filepath,
    path_in_repo=path_in_repo,
    repo_id=repo_id,
    repo_type="dataset",
    revision=revision,
    commit_message=f"Upload trace asset {path_in_repo}",
)
print(f"[INFO] Uploaded: {url}")
PY
}

run_download() {
  local repo_id=""
  local path_in_repo=""
  local output_dir=""
  local revision="main"
  local clobber=false
  local dry_run=false

  while (($#)); do
    case "$1" in
      --repo-id)
        require_option "$@"
        repo_id="$2"
        shift 2
        ;;
      --path-in-repo)
        require_option "$@"
        path_in_repo="$2"
        shift 2
        ;;
      --output-dir)
        require_option "$@"
        output_dir="$2"
        shift 2
        ;;
      --revision)
        require_option "$@"
        revision="$2"
        shift 2
        ;;
      --clobber)
        clobber=true
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
        echo "Unknown download argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done

  if [[ -z "$repo_id" || -z "$path_in_repo" || -z "$output_dir" ]]; then
    usage >&2
    exit 2
  fi
  validate_repo_path "$path_in_repo"
  local destination="$output_dir/$path_in_repo"
  printf '[INFO] Dataset: %s\n' "$repo_id"
  printf '[INFO] Dataset path: %s\n' "$path_in_repo"
  printf '[INFO] Revision: %s\n' "$revision"
  printf '[INFO] Download destination: %s\n' "$destination"

  if [[ -e "$destination" && "$clobber" != true ]]; then
    echo "Download file already exists: $destination (use --clobber to replace it)" >&2
    exit 1
  fi
  if [[ "$dry_run" == true ]]; then
    printf '[INFO] Would download the file from the Hugging Face Dataset.\n'
    return
  fi

  require_hf_client
  mkdir -p -- "$(dirname -- "$destination")"
  "$python_command" - "$repo_id" "$path_in_repo" "$revision" "$destination" "$clobber" <<'PY'
import os
import shutil
import sys

from huggingface_hub import hf_hub_download

repo_id, path_in_repo, revision, destination, clobber = sys.argv[1:]
cached_path = hf_hub_download(
    repo_id=repo_id,
    filename=path_in_repo,
    repo_type="dataset",
    revision=revision,
    force_download=clobber == "true",
)
shutil.copyfile(cached_path, destination)
print(f"[INFO] Downloaded: {destination}")
PY
}

run_list() {
  local repo_id=""
  local revision="main"

  while (($#)); do
    case "$1" in
      --repo-id)
        require_option "$@"
        repo_id="$2"
        shift 2
        ;;
      --revision)
        require_option "$@"
        revision="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown list argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done

  if [[ -z "$repo_id" ]]; then
    usage >&2
    exit 2
  fi
  require_hf_client
  "$python_command" - "$repo_id" "$revision" <<'PY'
import os
import sys

from huggingface_hub import HfApi

repo_id, revision = sys.argv[1:]
api = HfApi(token=os.environ.get("HF_TOKEN"))
for filename in api.list_repo_files(
    repo_id=repo_id,
    repo_type="dataset",
    revision=revision,
):
    print(filename)
PY
}

if (($# == 0)); then
  usage >&2
  exit 2
fi

if (($# == 0)); then
  usage >&2
  exit 2
fi

case "$1" in
  upload)
    shift
    run_upload "$@"
    ;;
  download)
    shift
    run_download "$@"
    ;;
  list)
    shift
    run_list "$@"
    ;;
  -h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $1" >&2
    usage >&2
    exit 2
    ;;
esac
