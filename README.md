# LMCache Tracebench

vLLM + LMCache MP 환경에서 Tensormesh-Benchmark V3 또는 Mooncake FAST'25
workload를 실행하고 LMCache storage trace를 기록·재생하는 도구입니다.

## Overview

```text
Tensormesh V3 / Mooncake timed trace
                  ↓
Recorder → vLLM API server → LMCache MP → L2 storage
                                      ↓
                                  storage.lct
                                      ↓
                                  Replayer
```

Record 단계는 workload를 실행하고 로컬 SSD의 `fs_native` L2에 KV object를 저장하며
`storage.lct`를 만듭니다. Replay 단계는 이 trace를 다른 L2 backend에 재생해 동일한
operation sequence의 처리량, latency와 resource 사용량을 비교합니다. Record에
사용한 L2 backend와 replay 대상 backend는 같을 필요가 없습니다. Storage trace의
기록 범위와 `.lct` contract는 [Replayer guide](docs/replayer.md#storage-trace-contract)를
참고하세요.

## Repository setup

Tensormesh-Benchmark는 `third_party/Tensormesh-Benchmark` Git submodule로
사용합니다. submodule은 `tracebench` branch를 가리키며 상위 저장소의 commit으로
고정됩니다.

Mooncake backend는 third-party source를 포함하지 않습니다. 프로젝트 내부 adapter가
[Mooncake FAST'25](https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release)의
익명화된 JSONL trace를 데이터 캐시로 내려받고, 설치된 vLLM의 `timed_trace` client로
재생합니다.

```bash
git clone <this-repository>
cd lmcache-tracebench
git submodule update --init --recursive
```

## Prerequisites

Ubuntu 24.04와 Python 3.12가 필요합니다. Recorder는 CUDA가 연결된 NVIDIA GPU가
필요하지만, storage trace Replayer는 `fs_native` 기준 GPU·vLLM·모델 없이 실행할 수
있습니다. 모든 profile은 같은 프로젝트 `.venv`를 사용합니다.

이 문서와 상세 가이드의 `/MNTPNT`는 실제 storage mount 경로로 바꿔 사용합니다.

설치 profile은 실행 역할에 따라 두 개로 나뉩니다.

| Profile | 설치 내용 | 실행 환경 |
| --- | --- | --- |
| `recorder` | LMCache tracebench fork, PyTorch, vLLM, dataset, OpenAI/TensorMesh package | GPU에서 trace 생성; 기본 profile |
| `replayer` | LMCache tracebench fork, PyTorch, NIXL package | `fs_native`, NIXL/HF3FS trace replay |

Recorder 설치:

```bash
bash scripts/setup_runtime.sh --profile recorder
```

Replayer 설치(`fs_native`와 NIXL/HF3FS 모두 지원):

```bash
bash scripts/setup_runtime.sh --profile replayer
```

`bash scripts/setup_runtime.sh`처럼 profile을 생략하면 기존 동작과 호환되도록
`recorder`가 선택됩니다. 현재 환경만 검사하려면 각 명령에 `--check`를 추가합니다.
의존성 정의는 `requirements/common.txt`, `requirements/recorder.txt`,
`requirements/replayer.txt`에 분리되어 있으며, 기존
`requirements/runtime.txt`는 Recorder용 호환 profile입니다. `--check`가
아닌 실행은 선택한 runtime requirements를 다시 설치하므로 기존에 설치된 LMCache
fork나 다른 version은 profile의 지정 version으로 교체됩니다.

다른 버전을 시험하려면 해당 profile 파일을 복사해 지정합니다.

```bash
cp requirements/common.txt requirements/common.local.txt
cp requirements/recorder.txt requirements/recorder.local.txt
# requirements/common.local.txt에서 lmcache version을 수정하고,
# requirements/recorder.local.txt의 -r common.txt를 -r common.local.txt로 바꿉니다.
bash scripts/setup_runtime.sh \
  --profile recorder \
  --runtime-requirements requirements/recorder.local.txt
```

`--runtime-requirements`는 `--check`와 함께도 사용할 수 있습니다. 버전을 바꾼 뒤에는
스크립트가 실행하는 `pip check`와 아래 smoke test를 반드시 통과시키세요.

### LMCache tracebench fork

두 profile은 모두 L2 operation profiling과 replay latency 통계
(`--l2-stats-out`)가 포함된 LMCache `v0.5.1-tracebench` 태그를 사용합니다.
`setup_runtime.sh`는 선택한 profile의 requirements를 강제 재설치하므로, 기존에
설치한 PyPI LMCache나 다른 fork가 남아 있지 않습니다. 별도의 LMCache source
checkout이나 patch 적용은 필요하지 않습니다. 설치는 현재 virtual environment의
PyTorch/CUDA로 native extension을 빌드합니다.

```bash
# recorder 또는 replayer profile을 선택합니다.
bash scripts/setup_runtime.sh --profile recorder
```

설치 후에는 다음 명령으로 확인합니다.

```bash
python -c "import lmcache, lmcache.c_ops; print('LMCache import: OK')"
lmcache trace replay --help | rg -- '--l2-stats-out'
python -m pip check
```

`setuptools-scm` 설정상 하이픈이 포함된 태그의 package metadata가 `0.1.dev...`로
표시될 수 있지만, 설치된 소스는 `v0.5.1-tracebench` 태그의 커밋입니다. 설치 출처를
확인하려면 다음을 실행합니다.

```bash
python -c "import importlib.metadata as m; print(m.distribution('lmcache').read_text('direct_url.json'))"
```

실행 전 확인:

```bash
source .venv/bin/activate
python -c "import lmcache, lmcache.c_ops, vllm, openai, datasets; print('runtime imports: OK')"
```

`No module named 'lmcache'` 또는 `vllm` 오류는 다른 virtual environment를
활성화했거나 package 설치가 빠진 경우입니다.

## Guides

- [Recorder guide](docs/recorder.md): Tensormesh V3, Mooncake workload, GPU quota와 Recorder output
- [Replayer guide](docs/replayer.md): `.lct` contract, replay backend와 profiling
- [Trace release assets](docs/trace-assets.md): GitHub Release upload/download

## Smoke test

`configs/recorder/smoke.yaml`은 1 GPU, 작은 Qwen 모델, V3 세션 10개·각 2 turn(최대
20개 request)으로 실제 storage trace를 만드는 최소 설정입니다. 첫 turn의 write와
두 번째 turn의 prefix reuse/prefetch 경로를 함께 확인합니다. V3 dataset은
`workload.hf_cache_dir`에 지정한 경로에 cache합니다. 실행 전에 이 값을
`/MNTPNT/hf-datasets`와 같은 실제 경로로 바꾸세요. 최초 실행에는 Hugging
Face dataset 접근 권한, 네트워크, 약 2.4 GB의 cache 공간이 필요하고, 이후 실행은
해당 cache를 재사용합니다.

```bash
source .venv/bin/activate
mkdir -p /MNTPNT/lmcache-trace-smoke

python -m recorder.main \
  --config configs/recorder/smoke.yaml \
  --base-path /MNTPNT/lmcache-trace-smoke \
  --output-dir outputs/smoke
```

성공하면 `outputs/smoke/storage.lct`와 `outputs/smoke/manifest.json`이 생성됩니다.
문제가 생기면 `outputs/smoke/lmcache.log`와 `outputs/smoke/vllm.log`를 먼저 확인합니다.

방금 record한 trace를 새 StorageManager에 replay하려면 다음을 실행합니다. Replay는
vLLM이나 GPU server를 다시 시작하지 않으며, `configs/replayer/smoke.yaml`의 별도 L2
경로를 사용합니다.

```bash
python -m replayer.main \
  --trace outputs/smoke/storage.lct \
  --config configs/replayer/smoke.yaml
```

Replay 중 storage node의 NVMe와 network counter를 수집하려면 `--profile`을
추가합니다.

```bash
python -m replayer.main \
  --trace outputs/smoke/storage.lct \
  --config configs/replayer/smoke.yaml \
  --profile configs/profiling/storage.yaml
```

설정, 결과 파일과 counter 해석은 [Replay profiling](docs/replayer.md#replay-profiling)을
참고하세요. 결과는 `outputs/smoke-replay/trace_replay_summary.json`과
`outputs/smoke-replay/trace_replay_ops.csv`에 저장됩니다. 먼저 실행 command만 보려면
끝에 `--dry-run`을 추가합니다.

## Tests

```bash
source .venv/bin/activate
python -m pytest
```
