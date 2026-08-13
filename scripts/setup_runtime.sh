#!/usr/bin/env bash
# Install or verify the recorder or replayer runtime.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_python="${project_root}/.venv/bin/python"
python_interpreter=""
index_url=""
extra_index_url=""
check_only=false
force_reinstall=false
profile="recorder"
runtime_requirements=""
runtime_requirements_override=false
project_install="${project_root}[test,live]"
extra_requirements=()

usage() {
    echo "Usage: bash scripts/setup_runtime.sh [--check] [--force-reinstall] [--profile PROFILE] [--python PATH] [--index-url URL] [--extra-index-url URL] [--runtime-requirements PATH]" >&2
    echo "Profiles: recorder, replayer-cpu, replayer-gpu" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            check_only=true
            shift
            ;;
        --force-reinstall)
            force_reinstall=true
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
        --python)
            if [[ $# -lt 2 ]]; then
                usage
                exit 2
            fi
            python_interpreter="$2"
            shift 2
            ;;
        --index-url)
            if [[ $# -lt 2 ]]; then
                usage
                exit 2
            fi
            index_url="$2"
            shift 2
            ;;
        --extra-index-url)
            if [[ $# -lt 2 ]]; then
                usage
                exit 2
            fi
            extra_index_url="$2"
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
    replayer-cpu|replayer-gpu)
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

if [[ -n "${python_interpreter}" ]]; then
    if [[ "${python_interpreter}" == /* ]]; then
        [[ -x "${python_interpreter}" ]] || {
            echo "Requested Python interpreter is not executable: ${python_interpreter}" >&2
            exit 1
        }
    elif ! command -v "${python_interpreter}" >/dev/null 2>&1; then
        echo "Requested Python interpreter was not found: ${python_interpreter}" >&2
        exit 1
    fi
elif ! command -v uv >/dev/null 2>&1; then
    echo "uv is required when --python is not provided." >&2
    exit 1
fi

if [[ "${profile}" == "recorder" || "${profile}" == "replayer-gpu" ]]; then
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        echo "nvidia-smi was not found; a CUDA-enabled NVIDIA GPU is required for ${profile}." >&2
        exit 1
    fi
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
fi

if [[ "${check_only}" == false ]]; then
    if [[ ! -x "${venv_python}" ]]; then
        if [[ -n "${python_interpreter}" ]]; then
            "${python_interpreter}" -m venv "${project_root}/.venv"
        else
            uv venv --seed --python 3.12 "${project_root}/.venv"
        fi
    fi

    if ! "${venv_python}" -m pip --version >/dev/null 2>&1; then
        if [[ -n "${python_interpreter}" ]]; then
            "${venv_python}" -m ensurepip --upgrade
        else
            uv pip install --python "${venv_python}" pip
        fi
    fi

    install_options=()
    pip_options=()
    if [[ -n "${index_url}" ]]; then
        pip_options+=(--index-url "${index_url}")
    fi
    if [[ -n "${extra_index_url}" ]]; then
        pip_options+=(--extra-index-url "${extra_index_url}")
    fi
    # replayer-cpu never touches a GPU, so pull torch from the CPU-only wheel
    # index and build LMCache with NO_GPU_EXT=1. The CPU torch wheel has no
    # CUDA runtime, so LMCache's CUDAExtension build for lmcache.c_ops would
    # fail (torch.cuda._is_compiled() is False → torch's internal CUDA_HOME is
    # None regardless of the env var). NO_GPU_EXT=1 skips the c_ops .so build
    # entirely; at runtime the lmcache.c_ops PEP 562 shim resolves to
    # CpuDeviceOps (pure Python), which the replay CLI uses via
    # StubCPUDevice. replayer-gpu and recorder keep the default index
    # (CUDA-enabled torch) and build the full c_ops extension. pip already
    # picks the highest version across all indexes, so adding the CPU index
    # as --extra-index-url makes 2.11.0+cpu win over the untagged CUDA wheel
    # on PyPI (PEP 440 ranks local versions higher).
    torch_pip_options=("${pip_options[@]}")
    requirements_pip_options=("${pip_options[@]}")
    if [[ "${profile}" == "replayer-cpu" ]]; then
        torch_pip_options=(--index-url https://download.pytorch.org/whl/cpu)
        requirements_pip_options+=(
            --extra-index-url https://download.pytorch.org/whl/cpu
        )
        export NO_GPU_EXT=1
    fi
    if [[ "${force_reinstall}" == true ]]; then
        install_options=(--upgrade --force-reinstall)
        "${venv_python}" -m pip install "${pip_options[@]}" --upgrade pip
    fi
    # LMCache is installed from a VCS source with --no-build-isolation below.
    # Bootstrap its build backend before pip resolves that source distribution.
    "${venv_python}" -m pip install "${pip_options[@]}" "${install_options[@]}" "setuptools>=77.0.3,<81.0.0"
    # LMCache's metadata build imports torch before pip installs the full
    # requirements file, so bootstrap the pinned torch build dependency too.
    # Re-pin setuptools alongside torch so torch's own setuptools<82 constraint
    # cannot pull 81.x from PyPI and break LMCache's <81.0.0 pin.
    "${venv_python}" -m pip install "${torch_pip_options[@]}" "${install_options[@]}" \
        "torch==2.11.0" "setuptools>=77.0.3,<81.0.0"
    # By default, pip installs only missing or incompatible requirements. Use
    # --force-reinstall when intentionally resetting the selected runtime.
    "${venv_python}" -m pip install "${requirements_pip_options[@]}" "${install_options[@]}" --no-build-isolation \
        -r "${runtime_requirements}" \
        "${extra_requirements[@]}"
    "${venv_python}" -m pip install "${pip_options[@]}" -e "${project_install}"
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
        "import importlib.util, lmcache, lmcache.c_ops, openai; assert importlib.util.find_spec('nixl'); print('replayer imports: OK')"
fi
"${venv_python}" -c \
    "from importlib.metadata import version; print('runtime versions:', ', '.join(f'{name}={version(name)}' for name in ('lmcache', 'torch', 'fsspec')))"
"${venv_python}" -m pip check
"${venv_python}" -m lmcache.v1.multiprocess.server --help >/dev/null
"${project_root}/.venv/bin/lmcache" trace replay --help >/dev/null

if [[ -n "${python_interpreter}" ]]; then
    echo "LMCache Tracebench ${profile} runtime is ready: ${venv_python} (base Python: ${python_interpreter})"
else
    echo "LMCache Tracebench ${profile} runtime is ready: ${venv_python}"
fi
