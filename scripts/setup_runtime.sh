#!/usr/bin/env bash
# Install or verify the runtime needed for LMCache MP trace generation.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_python="${project_root}/.venv/bin/python"
check_only=false
runtime_requirements="${project_root}/requirements/runtime.txt"

usage() {
    echo "Usage: bash scripts/setup_runtime.sh [--check] [--runtime-requirements PATH]" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            check_only=true
            shift
            ;;
        --runtime-requirements)
            if [[ $# -lt 2 ]]; then
                usage
                exit 2
            fi
            runtime_requirements="$2"
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

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi was not found; a CUDA-enabled NVIDIA GPU is required." >&2
    exit 1
fi
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

if [[ "${check_only}" == false ]]; then
    if [[ ! -x "${venv_python}" ]]; then
        uv venv --seed --python 3.12 "${project_root}/.venv"
    fi

    if ! "${venv_python}" -m pip --version >/dev/null 2>&1; then
        uv pip install --python "${venv_python}" pip
    fi

    "${venv_python}" -m pip install --upgrade pip
    "${venv_python}" -m pip install \
        -e "${project_root}[test,live]" \
        -r "${runtime_requirements}" \
        -r "${project_root}/third_party/Tensormesh-Benchmark/src/requirements.txt"
fi

if [[ ! -x "${venv_python}" ]]; then
    echo "Project virtual environment not found: ${venv_python}" >&2
    exit 1
fi

"${venv_python}" -c \
    "import lmcache, lmcache.c_ops, vllm, openai, datasets; print('runtime imports: OK')"
"${venv_python}" -c \
    "from importlib.metadata import version; print('runtime versions:', ', '.join(f'{name}={version(name)}' for name in ('lmcache', 'vllm', 'torch', 'datasets', 'fsspec')))"
"${venv_python}" -m pip check
"${venv_python}" -m lmcache.v1.multiprocess.server --help >/dev/null
"${venv_python}" -m vllm.entrypoints.openai.api_server --help >/dev/null

echo "LMCache Tracebench runtime is ready: ${venv_python}"
