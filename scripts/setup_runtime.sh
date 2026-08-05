#!/usr/bin/env bash
# Install or verify the recorder or replayer runtime.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_python="${project_root}/.venv/bin/python"
check_only=false
profile="recorder"
runtime_requirements=""
runtime_requirements_override=false
project_install="${project_root}[test,live]"
extra_requirements=()

usage() {
    echo "Usage: bash scripts/setup_runtime.sh [--check] [--profile PROFILE] [--runtime-requirements PATH]" >&2
    echo "Profiles: recorder (default), replayer" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            check_only=true
            shift
            ;;
        --profile)
            if [[ $# -lt 2 ]]; then
                usage
                exit 2
            fi
            profile="$2"
            shift 2
            ;;
        --runtime-requirements)
            if [[ $# -lt 2 ]]; then
                usage
                exit 2
            fi
            runtime_requirements="$2"
            runtime_requirements_override=true
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

case "${profile}" in
    recorder)
        default_runtime_requirements="${project_root}/requirements/recorder.txt"
        project_install="${project_root}[test,live]"
        extra_requirements=(
            -r
            "${project_root}/third_party/Tensormesh-Benchmark/src/requirements.txt"
        )
        ;;
    replayer)
        default_runtime_requirements="${project_root}/requirements/replayer.txt"
        project_install="${project_root}[test]"
        ;;
    *)
        echo "Unknown runtime profile: ${profile}" >&2
        usage
        exit 2
        ;;
esac

if [[ "${runtime_requirements_override}" == false ]]; then
    runtime_requirements="${default_runtime_requirements}"
fi

if [[ "${runtime_requirements}" != /* ]]; then
    runtime_requirements="${project_root}/${runtime_requirements}"
fi

if [[ ! -f "${runtime_requirements}" ]]; then
    echo "Runtime requirements file not found: ${runtime_requirements}" >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to create or repair the project virtual environment." >&2
    exit 1
fi

if [[ "${profile}" == "recorder" ]]; then
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        echo "nvidia-smi was not found; a CUDA-enabled NVIDIA GPU is required for recorder." >&2
        exit 1
    fi
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
fi

if [[ "${check_only}" == false ]]; then
    if [[ ! -x "${venv_python}" ]]; then
        uv venv --seed --python 3.12 "${project_root}/.venv"
    fi

    if ! "${venv_python}" -m pip --version >/dev/null 2>&1; then
        uv pip install --python "${venv_python}" pip
    fi

    "${venv_python}" -m pip install --upgrade pip
    # Reinstall the selected runtime so a previously installed LMCache build or
    # version cannot leak across recorder/replayer profile changes.
    "${venv_python}" -m pip install --upgrade --force-reinstall --no-build-isolation \
        -r "${runtime_requirements}" \
        "${extra_requirements[@]}"
    "${venv_python}" -m pip install -e "${project_install}"
fi

if [[ ! -x "${venv_python}" ]]; then
    echo "Project virtual environment not found: ${venv_python}" >&2
    exit 1
fi

if [[ "${profile}" == "recorder" ]]; then
    "${venv_python}" -c \
        "import lmcache, lmcache.c_ops, vllm, openai, datasets; print('recorder imports: OK')"
    "${venv_python}" -m vllm.entrypoints.openai.api_server --help >/dev/null
else
    "${venv_python}" -c \
        "import importlib.util, lmcache, lmcache.c_ops; assert importlib.util.find_spec('nixl'); print('replayer imports: OK')"
fi
"${venv_python}" -c \
    "from importlib.metadata import version; print('runtime versions:', ', '.join(f'{name}={version(name)}' for name in ('lmcache', 'torch', 'fsspec')))"
"${venv_python}" -m pip check
"${venv_python}" -m lmcache.v1.multiprocess.server --help >/dev/null
"${venv_python}" -m lmcache.cli.main trace replay --help >/dev/null

echo "LMCache Tracebench ${profile} runtime is ready: ${venv_python}"
