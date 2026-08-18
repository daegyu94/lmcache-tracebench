# LMCache Tracebench

LMCache Tracebench는 vLLM + LMCache MP workload에서 실제 L2 adapter task를
기록하고, 같은 operation stream을 storage backend에 재생하는 도구입니다.
Recorder는 TensorMesh V3 또는 Mooncake FAST'25 workload로 `l2.lct`를 만들고,
Replayer는 이를 `fs_native`, pNFS, NIXL/HF3FS 등의 target에 replay합니다.

StorageManager-level trace와 adapter-level trace의 차이, 그리고 replay가
보장하는 범위는 [L2 tracing guide](docs/l2-tracing.md)에서 설명합니다.

## Overview

```text
TensorMesh V3 / Mooncake timed trace
                  |
                  v
Recorder -> vLLM -> LMCache MP -> L2 adapter
                                  |
                                  v
                               l2.lct
                                  |
                                  v
Replayer --------------------> target L2 backend
```

Runtime source는 `src/recorder`, `src/replayer`, `src/traceprof`에 있습니다.
반복 실행 launcher는 [Benchmark guide](benchmarks/README.md), 보고서 실험 matrix는
[Staged remote report runner](benchmarks/report/README.md)를 참고하세요.

## Repository layout

| 경로 | 역할 |
| --- | --- |
| `src/` | 설치 가능한 recorder, replayer와 profiling package |
| `benchmarks/` | 반복 실험 launcher와 중단 재개 state 관리 |
| `configs/` | recorder, replayer와 profiling 설정 ([index](configs/README.md)) |
| `docs/` | 동작 contract와 운영 guide |
| `report/` | 보고서 본문, 정규화 dataset, renderer와 figure |
| `tools/` | trace asset과 외부 binary 관리 도구 ([index](tools/README.md)) |
| `tests/` | package와 workflow test |

실행 가능한 제품 코드는 `src/`에, 실험 orchestration은 `benchmarks/`에 둡니다.
`report/`의 Python module은 보고서 data와 figure를 함께 관리하는 저장소 전용
도구이므로 설치 package와 분리합니다.

## Repository setup

Recorder는 `third_party/Tensormesh-Benchmark` submodule을 사용합니다. 이미 기록된
L2 trace만 replay할 때는 submodule이 필요하지 않습니다.

```bash
git clone <this-repository>
cd lmcache-tracebench
git submodule update --init --recursive
```

Mooncake workload는 third-party source를 포함하지 않으며,
[Mooncake FAST'25](https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release)의
timed trace를 사용합니다.

## Prerequisites

검증 환경은 Ubuntu 24.04와 Python 3.12입니다. 모든 profile은 프로젝트의
`.venv`를 공유합니다.

| Profile | 용도 |
| --- | --- |
| `recorder` | GPU에서 vLLM/LMCache workload를 실행하고 trace 생성 |
| `replayer-cpu` | CPU torch 기반 L2 replay |
| `replayer-gpu` | CUDA torch가 필요한 replay node의 L2 replay |

```bash
bash scripts/setup_runtime.sh --profile recorder
bash scripts/setup_runtime.sh --profile replayer-cpu
bash scripts/setup_runtime.sh --profile replayer-gpu
```

현재 환경만 검사하려면 선택한 명령에 `--check`를 추가합니다. Profile을 생략하면
`recorder`가 선택됩니다. 설치 동작과 추가 옵션은 다음 명령을 기준으로 합니다.

```bash
bash scripts/setup_runtime.sh --help
```

Recorder에는 CUDA GPU가 필요하지만, `fs_native` L2 replay는 GPU·vLLM·모델 없이
실행할 수 있습니다. Recorder의 mount와 dataset 설정은
[Recorder guide](docs/recorder.md), replay host 설치부터 결과 확인까지의 최소 순서는
[L2 benchmark quickstart](docs/benchmark-quickstart.md)를 참고하세요.

## Start here

- Trace 생성: [Recorder guide](docs/recorder.md)
- 단일 replay, speedup, backend, profiling: [Replayer guide](docs/replayer.md)
- 격리 replay node 준비와 결과 회수: [Staged remote replay](docs/staged-remote-replay.md)
- Report figure별 matrix 실행: [Staged remote report runner](benchmarks/report/README.md)
- Trace archive 다운로드와 업로드: [Trace assets](docs/trace-assets.md)

각 command는 실행 전에 해당 script의 `--help`와 `--dry-run`으로 경로를
확인하세요. 특히 sweep의 L2 target은 기존 데이터나 mount root가 아닌 benchmark
전용 disposable directory여야 합니다.

## Guides

| 문서 | 기준 범위 |
| --- | --- |
| [Documentation guidelines](docs/documentation-guidelines.md) | 문서 역할과 중복 방지 규칙 |
| [L2 tracing guide](docs/l2-tracing.md) | L2 event, dependency, preparation, trace validity |
| [Recorder guide](docs/recorder.md) | Workload, recorder CLI와 output |
| [Replayer guide](docs/replayer.md) | Replay CLI, speedup, backend와 profiling |
| [L2 replay metric guide](docs/l2-replay-metrics.md) | Replay metric 정의 |
| [L2 benchmark quickstart](docs/benchmark-quickstart.md) | 새 replay host의 최소 실행 순서 |
| [Benchmark guide](benchmarks/README.md) | Launcher와 artifact layout |
| [Trace assets](docs/trace-assets.md) | GitHub/Hugging Face trace archive |
| [Staged remote replay](docs/staged-remote-replay.md) | Controller/replay node workflow |
| [Performance report](report/performance-evaluation.md) | 실험 설계와 주장-증거 연결 |

## Tests

프로젝트 환경을 활성화한 뒤 전체 test를 실행합니다.

```bash
source .venv/bin/activate
python -m pytest
```
