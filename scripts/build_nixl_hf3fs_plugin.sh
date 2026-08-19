#!/usr/bin/env bash
# Build the NIXL HF3FS plugin (libplugin_HF3FS.so) and deploy it into the venv.
#
# The pip-installed NIXL wheel does not ship the HF3FS plugin, so HF3FS backend
# replay requires building it from NIXL source against the HF3FS usrbio
# headers/libraries and dropping the plugin into the venv NIXL plugin directory.
#
# Requirements (see docs/hf3fs-backend.md for the full rationale):
#   - NIXL source tree (e.g. ai-dynamo/nixl v1.3.2) with meson subprojects fetched.
#   - HF3FS usrbio headers at /usr/include/hf3fs/ and libs at /usr/lib/ (or
#     symlinked there), i.e. hf3fs.h, hf3fs_usrbio.h, hf3fs_expected.h,
#     libhf3fs_api_shared.so, hf3fs_usrbio.so.
#   - meson, ninja, g++, pybind11 (with pkgconfig on PKG_CONFIG_PATH).
#   - patchelf, to inject the RPATH the installed plugins already carry.
#
# Usage:
#   bash scripts/build_nixl_hf3fs_plugin.sh \
#     --source /path/to/nixl-src \
#     --venv /path/to/tracebench/.venv \
#     [--plugin-subdir .nixl_cu12.mesonpy.libs] \
#     [--hf3fs-lib /opt/3fs/lib]
#
# Defaults target the standard replay-node layout described in the docs.
set -euo pipefail

nixl_src=""
venv_root=""
plugin_subdir=".nixl_cu12.mesonpy.libs"
hf3fs_lib="/opt/3fs/lib"
build_werror=false

usage() {
  cat <<'EOF'
Usage:
  bash scripts/build_nixl_hf3fs_plugin.sh \
    --source PATH --venv PATH [OPTIONS]

Required:
  --source PATH          NIXL source tree (contains meson.build and src/)
  --venv PATH            Project venv root (contains lib/python3.12/site-packages/)

Options:
  --plugin-subdir DIR    NIXL mesonpy libs directory under site-packages
                         (default: .nixl_cu12.mesonpy.libs)
  --hf3fs-lib PATH       Directory holding libhf3fs_api_shared.so and
                         hf3fs_usrbio.so (default: /opt/3fs/lib)
  --build-werror         Keep meson's werror=true (fails on HF3FS -Werror=address)
  -h, --help             Show this help
EOF
}

while (($#)); do
  case "$1" in
    --source)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      nixl_src="$2"; shift 2 ;;
    --venv)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      venv_root="$2"; shift 2 ;;
    --plugin-subdir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      plugin_subdir="$2"; shift 2 ;;
    --hf3fs-lib)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      hf3fs_lib="$2"; shift 2 ;;
    --build-werror)
      build_werror=true; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$nixl_src" ]] || { echo "[ERROR] --source is required" >&2; usage >&2; exit 2; }
[[ -n "$venv_root" ]] || { echo "[ERROR] --venv is required" >&2; usage >&2; exit 2; }

nixl_src="$(cd "$(dirname "$nixl_src")" && pwd)/$(basename "$nixl_src")"
venv_root="$(cd "$(dirname "$venv_root")" && pwd)/$(basename "$venv_root")"

[[ -f "$nixl_src/meson.build" ]] || { echo "[ERROR] NIXL source not found: $nixl_src/meson.build" >&2; exit 1; }
[[ -d "$venv_root/lib/python3.12/site-packages" ]] || {
  echo "[ERROR] venv site-packages not found under $venv_root" >&2; exit 1;
}

plugin_dir="$venv_root/lib/python3.12/site-packages/$plugin_subdir/plugins"
[[ -d "$plugin_dir" ]] || {
  echo "[ERROR] NIXL plugin dir not found: $plugin_dir" >&2; exit 1;
}

# ---------------------------------------------------------------------------
# 1. Configure with meson. -Dwerror=false keeps the HF3FS source's invalid
#    `if (&x->ior == nullptr)` address check from aborting the build.
# ---------------------------------------------------------------------------
echo "[INFO] Configuring NIXL build (enable_plugins=HF3FS)"
werror_flag="true"
if [[ "$build_werror" == false ]]; then
  werror_flag="false"
fi
(
  cd "$nixl_src"
  meson setup build -Denable_plugins=HF3FS -Dwerror="$werror_flag"
)

# ---------------------------------------------------------------------------
# 2. Build only the HF3FS plugin target (plus its internal deps are pulled in
#    by ninja automatically).
# ---------------------------------------------------------------------------
echo "[INFO] Building libplugin_HF3FS.so"
(
  cd "$nixl_src"
  ninja -C build src/plugins/hf3fs/libplugin_HF3FS.so
)

plugin_artifact="$nixl_src/build/src/plugins/hf3fs/libplugin_HF3FS.so"
[[ -f "$plugin_artifact" ]] || {
  echo "[ERROR] plugin not produced: $plugin_artifact" >&2; exit 1;
}

# ---------------------------------------------------------------------------
# 3. Deploy into the venv plugin directory.
# ---------------------------------------------------------------------------
echo "[INFO] Deploying to $plugin_dir"
cp -- "$plugin_artifact" "$plugin_dir/libplugin_HF3FS.so"

# ---------------------------------------------------------------------------
# 4. Inject the same RPATH the installed plugins carry. A hand-built plugin has
#    no RPATH, so it cannot find libnixl_common.so / libfile_utils.so located
#    one directory up (.nixl_*_mesonpy.libs).
# ---------------------------------------------------------------------------
libs_rel="../../nixl_cu12.libs"
if [[ "$plugin_subdir" == *cu13* ]]; then
  libs_rel="../../nixl_cu13.libs"
fi
echo "[INFO] Injecting RPATH \$ORIGIN/..:\$ORIGIN/$libs_rel"
if ! command -v patchelf >/dev/null 2>&1; then
  echo "[ERROR] patchelf not found; install it (e.g. apt-get install -y patchelf)" >&2
  exit 1
fi
patchelf --set-rpath "\$ORIGIN/..:\$ORIGIN/$libs_rel" \
  "$plugin_dir/libplugin_HF3FS.so"

# ---------------------------------------------------------------------------
# 5. Sanity check: usrbio library must be reachable. Prefer the system default
#    (e.g. /usr/local/lib in the loader cache); otherwise advise LD_LIBRARY_PATH.
# ---------------------------------------------------------------------------
echo "[INFO] Verifying dynamic dependencies"
if ! ldd "$plugin_dir/libplugin_HF3FS.so" 2>/dev/null | grep -qi "not found"; then
  echo "[INFO] All dynamic dependencies resolve without LD_LIBRARY_PATH."
else
  echo "[WARN] Some dependencies unresolved without LD_LIBRARY_PATH;"
  echo "[WARN] set LD_LIBRARY_PATH=$hf3fs_lib when running replay."
  echo "[WARN] Unresolved entries:"
  ldd "$plugin_dir/libplugin_HF3FS.so" 2>/dev/null | grep -i "not found" || true
fi

echo "[INFO] Done: $plugin_dir/libplugin_HF3FS.so"