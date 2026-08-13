#!/usr/bin/env bash
# Download a pinned uv release from GitHub on a network-connected controller
# node, verifying it against the published sha256 checksum. The isolated
# replay node never runs this script; it only receives the extracted binary
# over rsync/scp (see benchmarks/replayer/staged_remote_replay.sh).
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/artifacts/uv_binary.sh download --version VERSION \
    --output-dir PATH [--platform PLATFORM] [--dry-run]

Options:
  --version VERSION    uv release tag, for example 0.12.3.
  --output-dir PATH    Directory that will contain the extracted `uv` binary.
  --platform PLATFORM  GitHub release target triple. Defaults to detecting
                        the controller's architecture (x86_64/aarch64) with
                        an unknown-linux-gnu suffix.
  --dry-run            Print the download plan without contacting GitHub.

Example:
  bash tools/artifacts/uv_binary.sh download --version 0.12.3 \
    --output-dir /tmp/uv-cache/0.12.3
EOF
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

require_option() {
  if (($# < 2)); then
    echo "Missing value for $1" >&2
    usage >&2
    exit 2
  fi
}

detect_platform() {
  case "$(uname -m)" in
    x86_64) printf 'x86_64-unknown-linux-gnu\n' ;;
    aarch64|arm64) printf 'aarch64-unknown-linux-gnu\n' ;;
    *) die "Unsupported controller architecture for uv download: $(uname -m) (pass --platform explicitly)" ;;
  esac
}

run_download() {
  local version="" output_dir="" platform="" dry_run=false

  while (($#)); do
    case "$1" in
      --version)
        require_option "$@"
        version="$2"
        shift 2
        ;;
      --output-dir)
        require_option "$@"
        output_dir="$2"
        shift 2
        ;;
      --platform)
        require_option "$@"
        platform="$2"
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
        echo "Unknown download argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done

  [[ -n "$version" && -n "$output_dir" ]] || { usage >&2; exit 2; }
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "uv version must look like X.Y.Z: $version"
  [[ -n "$platform" ]] || platform="$(detect_platform)"

  local asset="uv-${platform}.tar.gz"
  local base_url="https://github.com/astral-sh/uv/releases/download/${version}"
  local destination="$output_dir/uv"

  printf '[INFO] uv version: %s\n' "$version"
  printf '[INFO] Platform: %s\n' "$platform"
  printf '[INFO] Destination: %s\n' "$destination"

  if [[ -x "$destination" ]]; then
    printf '[INFO] uv binary already cached; not re-downloading: %s\n' "$destination"
    return
  fi
  if [[ "$dry_run" == true ]]; then
    printf '[DRY-RUN] download %s/%s and verify against %s.sha256\n' "$base_url" "$asset" "$asset"
    return
  fi

  command -v curl >/dev/null 2>&1 || die "curl is required on the controller node to download uv"
  command -v tar >/dev/null 2>&1 || die "tar is required on the controller node to extract uv"
  command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required on the controller node to verify uv"

  local work_dir
  work_dir="$(mktemp -d)"
  trap 'rm -rf -- "$work_dir"' RETURN

  curl -fsSL -o "$work_dir/$asset" "$base_url/$asset"
  curl -fsSL -o "$work_dir/$asset.sha256" "$base_url/$asset.sha256"
  (cd "$work_dir" && sha256sum -c "$asset.sha256") || die "uv checksum verification failed for $asset"

  local extract_dir="$work_dir/extract"
  mkdir -p -- "$extract_dir"
  tar -xzf "$work_dir/$asset" -C "$extract_dir"

  local extracted_uv
  extracted_uv="$(find "$extract_dir" -type f -name uv -perm -u+x -print -quit)"
  [[ -n "$extracted_uv" ]] || die "uv binary not found inside downloaded archive: $asset"

  mkdir -p -- "$output_dir"
  install -m 0755 -- "$extracted_uv" "$destination"
  printf '[INFO] Downloaded and verified: %s\n' "$destination"
}

if (($# == 0)); then
  usage >&2
  exit 2
fi

case "$1" in
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
