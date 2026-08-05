#!/usr/bin/env bash
# Create GitHub Releases and upload or download LMCache trace assets.
set -euo pipefail

max_release_asset_bytes=$((2 * 1024 * 1024 * 1024))
split_chunk_bytes=$((1900 * 1024 * 1024))

usage() {
  cat <<'EOF'
Usage:
  bash scripts/release_asset.sh release --tag TAG [--title TITLE] [--notes TEXT] [--target COMMITISH] [--dry-run]
  bash scripts/release_asset.sh upload --tag TAG --filename NAME --filepath PATH [--clobber] [--dry-run]
  bash scripts/release_asset.sh download --tag TAG --filename NAME --output-dir PATH [--clobber] [--keep-parts] [--dry-run]

Commands:
  release   Create a GitHub Release. TAG is created at the current HEAD unless
            --target is supplied.
  upload    Upload PATH as NAME to an existing Release. Files at or above 2 GiB
            are split into NAME.part-001, NAME.part-002, and so on.
  download  Download NAME. If split parts exist, they are downloaded, concatenated
            into OUTPUT-DIR/NAME, and removed unless --keep-parts is supplied.

Authentication:
  Every command except --help requires the GitHub CLI (gh) to be installed and
  authenticated for github.com. Run: gh auth login

Examples:
  bash scripts/release_asset.sh release --tag tensormesh-benchmark-20260805 \
    --title "Tensormesh benchmark traces (2026-08-05)"
  bash scripts/release_asset.sh upload --tag tensormesh-benchmark-20260805 \
    --filename wildclaw_storage.lct \
    --filepath /mnt/misc/lmcache-tracebench/outputs/source-traces-20260804-082231/wildclaw/storage.lct
  bash scripts/release_asset.sh download --tag tensormesh-benchmark-20260805 \
    --filename swebench_storage.lct --output-dir downloads
EOF
}

require_gh_auth() {
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh CLI is required. Install it from https://cli.github.com/." >&2
    exit 1
  fi
  if ! gh auth status --hostname github.com >/dev/null 2>&1; then
    echo "GitHub CLI is not authenticated. Run: gh auth login" >&2
    exit 1
  fi
}

require_value() {
  if (($# < 2)); then
    usage >&2
    exit 2
  fi
}

validate_asset_name() {
  local name="$1"
  if [[ -z "$name" || "$name" == */* || "$name" == . || "$name" == .. ]]; then
    echo "Asset filename must be a non-empty basename: $name" >&2
    exit 2
  fi
}

upload_asset() {
  local release_tag="$1"
  local upload_path="$2"
  local clobber="$3"
  local command=(gh release upload "$release_tag" "$upload_path")
  if [[ "$clobber" == true ]]; then
    command+=(--clobber)
  fi
  printf '[INFO] Command:'
  printf ' %q' "${command[@]}"
  printf '\n'
  "${command[@]}"
  printf '[INFO] Uploaded %s to release %s\n' "$(basename -- "$upload_path")" "$release_tag"
}

run_release() {
  local release_tag=""
  local title=""
  local notes="LMCache storage trace artifacts."
  local target=""
  local dry_run=false

  while (($#)); do
    case "$1" in
      --tag)
        require_value "$@"
        release_tag="$2"
        shift 2
        ;;
      --title)
        require_value "$@"
        title="$2"
        shift 2
        ;;
      --notes)
        require_value "$@"
        notes="$2"
        shift 2
        ;;
      --target)
        require_value "$@"
        target="$2"
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
        echo "Unknown release argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done

  if [[ -z "$release_tag" ]]; then
    usage >&2
    exit 2
  fi
  if [[ -z "$title" ]]; then
    title="$release_tag"
  fi

  if [[ "$dry_run" == true ]]; then
    printf '[INFO] Would create release %s\n' "$release_tag"
    exit 0
  fi

  require_gh_auth
  local command=(gh release create "$release_tag" --title "$title" --notes "$notes")
  if [[ -n "$target" ]]; then
    command+=(--target "$target")
  fi
  printf '[INFO] Command:'
  printf ' %q' "${command[@]}"
  printf '\n'
  "${command[@]}"
}

run_upload() {
  local release_tag=""
  local asset_name=""
  local file_path=""
  local clobber=false
  local dry_run=false

  while (($#)); do
    case "$1" in
      --tag)
        require_value "$@"
        release_tag="$2"
        shift 2
        ;;
      --filename)
        require_value "$@"
        asset_name="$2"
        shift 2
        ;;
      --filepath)
        require_value "$@"
        file_path="$2"
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

  if [[ -z "$release_tag" || -z "$asset_name" || -z "$file_path" ]]; then
    usage >&2
    exit 2
  fi
  validate_asset_name "$asset_name"
  if [[ ! -f "$file_path" ]]; then
    echo "Trace file not found: $file_path" >&2
    exit 1
  fi

  local file_size_bytes
  file_size_bytes="$(stat --format=%s -- "$file_path")"
  printf '[INFO] Release asset name: %s\n' "$asset_name"
  printf '[INFO] Upload source: %s\n' "$file_path"

  if [[ "$dry_run" == true ]]; then
    if ((file_size_bytes >= max_release_asset_bytes)); then
      local part_count=$(((file_size_bytes + split_chunk_bytes - 1) / split_chunk_bytes))
      printf '[INFO] Would split into %d parts of at most 1900 MiB\n' "$part_count"
      printf '[INFO] Would upload to release %s as %s.part-001 through %s.part-%03d\n' \
        "$release_tag" "$asset_name" "$asset_name" "$part_count"
    else
      printf '[INFO] Would upload to release %s as %s\n' "$release_tag" "$asset_name"
    fi
    return
  fi

  require_gh_auth
  gh release view "$release_tag" >/dev/null

  local file_dir
  file_dir="$(cd -- "$(dirname -- "$file_path")" && pwd -P)"
  local upload_dir
  upload_dir="$(mktemp -d "$file_dir/.lmcache-tracebench-release.XXXXXX")"
  cleanup_upload() {
    rm -rf -- "$upload_dir"
  }
  trap cleanup_upload EXIT

  if ((file_size_bytes >= max_release_asset_bytes)); then
    printf '[INFO] Splitting trace into parts of at most 1900 MiB\n'
    split \
      --bytes="$split_chunk_bytes" \
      --numeric-suffixes=1 \
      --suffix-length=3 \
      -- "$file_path" "$upload_dir/${asset_name}.part-"
    local upload_path
    for upload_path in "$upload_dir/${asset_name}.part-"*; do
      upload_asset "$release_tag" "$upload_path" "$clobber"
    done
  else
    local upload_path="$upload_dir/$asset_name"
    ln -s -- "$file_path" "$upload_path"
    upload_asset "$release_tag" "$upload_path" "$clobber"
  fi
}

run_download() {
  local release_tag=""
  local asset_name=""
  local output_dir=""
  local clobber=false
  local keep_parts=false
  local dry_run=false

  while (($#)); do
    case "$1" in
      --tag)
        require_value "$@"
        release_tag="$2"
        shift 2
        ;;
      --filename)
        require_value "$@"
        asset_name="$2"
        shift 2
        ;;
      --output-dir)
        require_value "$@"
        output_dir="$2"
        shift 2
        ;;
      --clobber)
        clobber=true
        shift
        ;;
      --keep-parts)
        keep_parts=true
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

  if [[ -z "$release_tag" || -z "$asset_name" || -z "$output_dir" ]]; then
    usage >&2
    exit 2
  fi
  validate_asset_name "$asset_name"

  if [[ "$dry_run" == true ]]; then
    printf '[INFO] Would download %s (or its split parts) from release %s into %s\n' \
      "$asset_name" "$release_tag" "$output_dir"
    return
  fi

  require_gh_auth
  gh release view "$release_tag" >/dev/null
  mkdir -p -- "$output_dir"
  local output_path="$output_dir/$asset_name"
  if [[ -e "$output_path" && "$clobber" != true ]]; then
    echo "Output file already exists: $output_path (use --clobber to replace it)" >&2
    exit 1
  fi

  local asset_names
  asset_names="$(gh release view "$release_tag" --json assets --jq '.assets[].name')"
  if [[ "$asset_names" == *$'\n'"$asset_name"$'\n'* || "$asset_names" == "$asset_name" || "$asset_names" == "$asset_name"$'\n'* ]]; then
    local command=(gh release download "$release_tag" --pattern "$asset_name" --dir "$output_dir")
    if [[ "$clobber" == true ]]; then
      command+=(--clobber)
    fi
    "${command[@]}"
    printf '[INFO] Downloaded %s\n' "$output_path"
    return
  fi

  if ! grep -Fqx "${asset_name}.part-001" <<<"$asset_names"; then
    echo "Asset not found in release $release_tag: $asset_name" >&2
    exit 1
  fi

  local command=(gh release download "$release_tag" --pattern "${asset_name}.part-*" --dir "$output_dir")
  if [[ "$clobber" == true ]]; then
    command+=(--clobber)
  fi
  "${command[@]}"

  local parts=("$output_dir/${asset_name}.part-"*)
  if [[ ! -e "${parts[0]}" ]]; then
    echo "No split parts were downloaded for: $asset_name" >&2
    exit 1
  fi
  cat "${parts[@]}" > "$output_path"
  if [[ "$keep_parts" != true ]]; then
    rm -f -- "${parts[@]}"
  fi
  printf '[INFO] Reconstructed %s from %d parts\n' "$output_path" "${#parts[@]}"
}

if (($# == 0)); then
  usage >&2
  exit 2
fi

case "$1" in
  release)
    shift
    run_release "$@"
    ;;
  upload)
    shift
    run_upload "$@"
    ;;
  download)
    shift
    run_download "$@"
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
