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

Record 단계는 H100 TP=8에서 Qwen3-Coder workload를 실행하고 로컬 SSD의
`fs_native` L2에 KV object를 저장하며 `storage.lct`를 만듭니다. Replay 단계는
이 trace를 PoC/B300 cluster의 3FS, pNFS 등 다른 L2 backend에 재생해 동일한
operation sequence의 처리량, latency와 resource 사용량을 비교합니다. Record에
사용한 L2 backend와 replay 대상 backend는 같을 필요가 없습니다.

### Storage trace의 범위

Recorder의 `--trace-level storage`는 L2 adapter의 low-level `get`/`put` system
call이 아니라 LMCache `StorageManager`의 진입 시점과 인자를 기록합니다.

| 기록 API | 의미 |
| --- | --- |
| `reserve_write(keys, layout_desc, mode)` | KV chunk를 쓰기 위한 L1 object와 write lock을 예약 |
| `finish_write(keys)` | Object write 완료를 알리고 store policy에 따라 비동기 L2 저장을 시작 |
| `submit_prefetch_task(...)` | L1 miss object를 L2에서 가져오는 prefetch를 제출 |
| `read_prefetched_results.__enter__/__exit__(keys)` | Prefetched object의 read lock lifecycle을 기록 |
| `finish_read_prefetched(keys, extra_count)` | Prefetched object 사용 종료 후 read lock을 해제 |

`.lct`는 length-prefixed MessagePack binary입니다. 각 record에는 relative
monotonic timestamp, wall-clock timestamp, fully qualified API name과 직렬화된
인자가 들어 있고, header에는 record 당시의 `StorageManagerConfig`와 config
digest가 저장됩니다. Replay는 같은 key, object layout, API 순서와 호출
간격으로 새 L2 backend에 write/read를 재현합니다.

Storage trace에는 실제 KV payload, API 반환값·exception, record 환경의 API
완료 시각·latency, L1/L2 hit·miss 결과, adapter 내부 queue wait·I/O 완료
시각이 포함되지 않습니다. 따라서 `.lct`는 storage workload 재현 입력으로
사용하고, L2 성능은 replay 중 adapter·backend에서 새로 측정해야 합니다.

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

이 문서의 `/MNTPNT`는 실제 storage mount 경로로 바꿔 사용합니다.

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

## Recorder

Recorder는 LMCache MP와 vLLM을 시작하고 OpenAI-compatible endpoint로 workload를
전송합니다. 예제 config의 `lmcache.l2.reset_on_start: true`는 실행 전에 기존
`base_path`를 비우므로, 필요한 데이터는 백업하거나 이 값을 `false`로 바꾸세요.
`--dry-run`은 L2 디렉터리를 변경하지 않습니다.

### Tensormesh V3 workload

기본 workload backend는 Hugging Face의
[`sammshen/lmcache-agentic-traces`](https://huggingface.co/datasets/sammshen/lmcache-agentic-traces)를
사용하는 Tensormesh V3입니다. 약 787개 multi-turn session과 24,881개 LLM
iteration으로 구성되며 SWE-bench, GAIA, WildClaw source를 제공합니다.

이 dataset의 agentic trace는 Recorder가 생성하는 `storage.lct`와 다릅니다.

- **Agentic trace:** vLLM에 보낼 request sequence를 정의하는 workload 입력
- **Storage trace:** workload 실행 중 LMCache `StorageManager` API 호출을 기록한 결과

Dataset의 각 row는 session 하나의 LLM iteration이며 주요 field는 다음과 같습니다.

| Field | 의미 |
| --- | --- |
| `session_id` | 같은 agent task의 iteration을 묶는 식별자 |
| `model` | 원래 trajectory를 생성한 모델 |
| `input` | 현재 iteration까지 누적된 OpenAI-format message |
| `output_length` | 이 iteration의 completion token 수 |
| `pre_gap` | 직전 response 완료 후 다음 request까지의 실제 tool 실행·처리 시간 |

Iteration이 진행될수록 assistant response와 tool result가 추가되는 strict
prefix-growth 구조이므로 LMCache의 KV reuse와 L2 store/retrieve를 평가하기
적합합니다. Recorder는 원래 agent나 tool을 다시 실행하지 않고 누적 message를
API request로 전송합니다. `respect-gaps`는 `pre_gap`을 반영하고,
`max-pressure`는 이 간격을 제거해 storage와 serving system에 더 높은 부하를
줍니다.

```bash
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-gaia.yaml \
  --base-path /MNTPNT/lmcache-trace/gaia \
  --output-dir outputs/gaia
```

실행할 command만 확인하려면 `--dry-run`을 추가합니다.

`configs/recorder/qwen3-coder-480b-tp8-base.yaml`은 공통 TP=8 runtime·LMCache
설정이며 직접 실행하지 않습니다.

SWE-bench, GAIA, WildClaw를 각각 독립 trace로 기록하려면 다음 source별 config를
사용합니다. 각 config는 별도 L2 경로를 사용합니다.

| Source | Config |
| --- | --- |
| SWE-bench | `configs/recorder/qwen3-coder-480b-tp8-swebench.yaml` |
| GAIA | `configs/recorder/qwen3-coder-480b-tp8-gaia.yaml` |
| WildClaw | `configs/recorder/qwen3-coder-480b-tp8-wildclaw.yaml` |

- **SWE-bench:** GitHub issue를 해결하며 code 탐색, patch 작성, test/debug를
  반복하는 coding-agent trajectory
- **GAIA:** Web search, file 분석과 여러 단계 reasoning이 필요한
  research-agent trajectory
- **WildClaw:** Search, code generation, productivity와 creative task가 섞인
  multi-tool agent trajectory

세 source trace를 순서대로 기록하려면 다음 script를 사용합니다.
실행 전에 source별 config의 `lmcache.l2.base_path`를 실제 mount 아래의 서로 다른
경로로 설정하세요.

```bash
bash scripts/record_source_traces.sh \
  --output-root /MNTPNT/lmcache-tracebench/outputs
```

각 결과는 timestamped directory 아래 `gaia/`, `wildclaw/`, `swebench/`에 저장됩니다.
작은 working set의 GAIA부터 시작하며, 한 source가 실패하면 script는 즉시 멈춥니다. 이때
실패한 source만 해당 config로 다시 실행하면 됩니다.

`source-traces-20260804-082231` 실행에서 모든 session을 record한 실측 결과는
다음과 같습니다. KV cache 점유량은 L2 directory의 filesystem 사용량이고, trace는
생성된 `storage.lct`의 크기입니다.

| Source | L2 KV cache 경로 | KV cache 점유량 | `storage.lct` 크기 |
| --- | --- | ---: | ---: |
| GAIA | `/MNTPNT/lmcache-trace/gaia` | 537G | 135M |
| SWE-bench | `/MNTPNT/lmcache-trace/swebench` | 4.9T | 5.9G |
| WildClaw | `/MNTPNT/lmcache-trace/wildclaw` | 56G | 24M |

L2 점유량은 실제 KV object의 filesystem 사용량이고, `storage.lct`는 payload
대신 API 호출과 인자를 저장하므로 훨씬 작습니다. 이 수치는 해당 실행과 dataset
revision의 관측값이며 model, session 수, chunk size, cache policy가 바뀌면
달라집니다.

Mixed workload의 source 비율, session ordering, timing policy와 대표성 검증은
현재 **TBD**입니다.

### GPU memory quota

기본 Qwen3-Coder TP=8 설정은 GPU당 72 GB quota를 가정해 H100 80 GB에서
`model.gpu_memory_utilization: 0.90`을 사용합니다. B300 288 GB에서 같은 quota를
적용하려면 `0.25`로 바꾸세요. H100의 VRAM은 B300의 약 28%이고, 실제
B300 cluster에서 여러 model instance가 resource를 공유할 수 있다는 가정에서
단일 instance의 memory quota와 KV cache 규모를 맞춘 것입니다. Compute 성능을
동일하게 만드는 설정은 아닙니다.

KV cache를 정확한 값으로 고정하는 실험에서는 `gpu_memory_utilization: null`과
`kv_cache_memory_gb_per_gpu`를 함께 설정합니다. 두 옵션에는 동시에 값을 지정할 수
없습니다.

Qwen3-Coder-480B-A35B-Instruct-AWQ의 FP16 KV cache는 TP=8 전체에서
token당 253,952 byte입니다.

```text
62 layers × K/V 2 × KV heads 8 × head_dim 128 × 2 bytes
= 253,952 bytes/token
```

### Mooncake real-world workload

`workload.backend: mooncake`는 실제 online request의 timestamp, token 길이와 익명화된
prefix 구조를 vLLM `timed_trace`로 실행합니다. Mooncake source code는 실행하거나
vendor하지 않고
[`kvcache-ai/Mooncake` FAST'25 release](https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release/traces)의
공식 JSONL만 다운로드합니다.

| Trace | 성격 | 요청 수 | 입력 token | 출력 token |
| --- | --- | ---: | ---: | ---: |
| `toolagent_trace.jsonl` | Tool/agent serving | 23,608 | 202,940,084 | 4,299,817 |
| `conversation_trace.jsonl` | 대화형 serving | 12,031 | 144,793,823 | 4,122,048 |

두 trace는 독립된 timeline과 prefix-reuse 분포를 가지므로 각각 record합니다.
별도의 mixed workload 정책 없이 JSONL을 합치지 않습니다.

각 JSONL line은 request 하나이며 주요 field는 다음과 같습니다.

| Field | 의미 |
| --- | --- |
| `timestamp` | Trace 시작 후 request 도착 시각(ms) |
| `input_length` | vLLM에 전송할 prompt token 수 |
| `output_length` | EOS와 관계없이 생성할 output token 수 |
| `hash_ids` | 512-token block 단위의 익명화된 prompt content 식별자 |

실제 prompt text는 포함되지 않습니다. `timed_trace` loader는 `hash_id`를
deterministic seed로 사용해 synthetic token을 만듭니다. Recorder는
`chunk_hash_size: 512`와 `PYTHONHASHSEED=0`을 설정해 실행 간 token sequence와
LMCache key가 일관되게 유지합니다.

모델이나 LMCache를 시작하지 않고 Conversation과 Tool/Agent JSONL을 다운로드하고
검증하려면 다음 명령을 사용합니다.

```bash
python -m recorder.mooncake_cli \
  --path /MNTPNT/mooncake-traces
```

`--trace toolagent` 또는 `--trace conversation`으로 하나만 선택할 수 있고,
이미 받은 파일만 검증하려면 `--no-download`를 추가합니다.

공통 config `configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml`의 주요
scaling 설정은 다음과 같습니다.

| 설정 | 의미 |
| --- | --- |
| `num_requests` | JSONL 처음부터 실행할 request 수. `null`은 전체 trace |
| `time_scale` | Request 간격 배율. `1.0`은 원본 timeline, `0.1`은 10배 압축 |
| `chunk_hash_size` | `hash_id` 하나를 확장할 token 수. 공식 trace는 512 |
| `max_concurrent_requests` | 동시에 처리할 client request 상한 |

`num_requests`는 shuffle sample이 아니라 timestamp 순서를 유지한 trace prefix입니다.
`1,000`, `5,000`, `null` 순서로 늘려 storage 용량과 실행 시간을 확인할 수
있습니다. `time_scale`은 request set과 token 수는 바꾸지 않고 arrival 간격과
그에 따른 concurrency·I/O timing만 조절합니다.

공통 config를 복제하지 않고 run별 값을 바꾸려면 `--mooncake-num-requests`와
`--base-path`를 사용합니다. `all`은 YAML의 `null`과 같으며 전체 trace를
선택합니다.

Tool/Agent 또는 Conversation trace를 기록하려면 `TRACE`만 바꿔 다음 명령을
사용합니다. `TRACE`에는 `toolagent` 또는 `conversation`을 지정합니다.

```bash
TRACE=toolagent  # conversation으로 바꿔 실행할 수 있습니다.

python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml \
  --mooncake-trace "$TRACE" \
  --mooncake-path "/MNTPNT/mooncake-traces/${TRACE}_trace.jsonl" \
  --mooncake-num-requests all \
  --base-path "/MNTPNT/lmcache-trace/mooncake-${TRACE}" \
  --output-dir "outputs/qwen3-coder-tp8-mooncake-${TRACE}"
```

실행할 때마다 `TRACE`를 하나씩 선택하며, 출력과 L2 경로는
`mooncake-${TRACE}` suffix로 분리됩니다. 두 경로 모두 `reset_on_start: true`이므로
같은 trace를 다시 시작하면 기존 KV object를 지웁니다. 한 server에서는 GPU와 port
충돌을 피해 순차 실행하세요.

Mooncake `hash_ids`의 고유 512-token block을 기준으로 계산한 nominal
unique prefix KV 용량은 다음과 같습니다. 실제 filesystem 사용량은
LMCache chunk, metadata, reuse와 실행 성공 여부에 따라 달라집니다.

| Trace 범위 | 고유 prefix KV 추정 | 중복 제거 없는 논리 KV 처리량 |
| --- | ---: | ---: |
| Tool/Agent 처음 1,000 requests | 약 1.59 TB | 약 2.54 TB |
| Conversation 처음 1,000 requests | 약 2.80 TB | 약 3.58 TB |
| Tool/Agent 전체 23,608 requests | 약 23.83 TB | 약 52.63 TB |
| Conversation 전체 12,031 requests | 약 23.77 TB | 약 37.82 TB |

실행 중 vLLM benchmark의 progress bar가 완료 request 수, 처리율과 경과 시간을
표시하며 같은 출력을 `workload.log`에도 저장합니다.

### Recorder output

`--output-dir`에는 다음 파일이 생성됩니다.

| 파일 | 내용 |
| --- | --- |
| `storage.lct` | LMCache storage operation trace |
| `manifest.json` | Workload 요약, 오류와 process 종료 상태 |
| `request_stats.jsonl` | 요청별 latency와 성공 여부 |
| `session_outcomes.jsonl` | Session별 실행 결과 |
| `commands.json` | 실행 command와 환경 변수 |
| `workload.json` | 선택한 workload 정보 |
| `lmcache.log`, `vllm.log`, `workload.log` | Process별 로그 |

Mooncake는 `vllm_benchmark.json`도 생성합니다. 문제가 발생하면 `manifest.json`을
확인한 뒤 각 process 로그를 살펴보세요.

## Trace release assets

`scripts/release_asset.sh`는 GitHub Release 생성, trace upload, download를 제공합니다.
`--help`를 제외한 모든 command는 `gh` CLI 설치와 GitHub 인증이 필요합니다.

```bash
gh auth login
```

Release를 먼저 만듭니다.

```bash
bash scripts/release_asset.sh release \
  --tag tensormesh-benchmark-20260805 \
  --title "Tensormesh benchmark traces (2026-08-05)"
```

기록한 trace 하나를 기존 GitHub Release의 asset으로 등록하려면 다음을 실행합니다.
`--filename`은 Release에 표시할 asset 이름이고, `--filepath`은 로컬 파일 경로입니다.

```bash
bash scripts/release_asset.sh upload --tag tensormesh-benchmark-20260805 \
  --filename wildclaw_storage.lct \
  --filepath /MNTPNT/lmcache-tracebench/outputs/source-traces-20260804-082231/wildclaw/storage.lct
```

위 예시는 `wildclaw_storage.lct`를 해당 release에 업로드합니다. 같은 이름의 asset을
교체하려면 `--clobber`를 추가하고, 실제 업로드 없이 이름과 command를 확인하려면
`--dry-run`을 사용합니다. 같은 `--tag`에도 서로 다른 `--filename`을 지정하면 여러 trace
file을 추가할 수 있습니다. GitHub Release asset 하나는 2 GiB 미만이어야 하므로 2 GiB 이상의
file은 script가 자동으로 최대 1900 MiB 크기의 `NAME.part-001`, `NAME.part-002` 형식 asset으로
분할해 업로드합니다. Split part는 업로드가 끝나면 삭제됩니다.

Release에서 trace를 내려받으려면 다음을 실행합니다. split asset은 자동으로 결합해
`--output-dir`에 원본 파일을 만들며, `--keep-parts`를 지정하지 않으면 다운로드한 part를
정리합니다.

```bash
bash scripts/release_asset.sh download \
  --tag tensormesh-benchmark-20260805 \
  --filename swebench_storage.lct \
  --output-dir downloads
```

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

설정, 결과 파일과 counter 해석은 아래 [Replay profiling](#replay-profiling)을
참고하세요.

결과는 `outputs/smoke-replay/trace_replay_summary.json`과
`outputs/smoke-replay/trace_replay_ops.csv`에 저장됩니다. 먼저 실행 command만 보려면
끝에 `--dry-run`을 추가합니다.

## Replayer

Replayer는 저장된 `.lct`를 LMCache `trace replay` command로 한 번 실행합니다.
공용 `base.yaml`을 상속하는 `fs-native.yaml` 또는 `nixl-hf3fs.yaml`을 선택합니다.
각 storage record는 trace의 monotonic timestamp 간격에 맞춰 재생되며, replay host가
더 느리면 원래 schedule보다 뒤처진 상태로 계속 진행합니다.

```bash
python -m replayer.main \
  --trace path/to/storage.lct \
  --config configs/replayer/fs-native.yaml \
  --base-path /MNTPNT/lmcache-trace-replay \
  --output-dir outputs/replay
```

실행 전 command만 확인하려면 `--dry-run`을 추가합니다.
실행 중에는 터미널에 record 진행률을 표시하며, LMCache 원문 로그는
`output_dir/lmcache-replay.log`에 저장됩니다. `--base-path`는 `fs_native`의
`l2_adapter.base_path`를, NIXL config에서는 `backend_params.file_path`를 덮어씁니다.
`--output-dir`는 summary, operation CSV와 로그를 저장할 directory를 덮어씁니다.

### Replay backend

Replay는 trace header의 원본 L2 설정을 강제하지 않고 현재 config의 adapter로
새 `StorageManager`를 만듭니다. 따라서 로컬 SSD의 `fs_native`로 record한 trace를
pNFS mount나 NIXL/HF3FS에 재생할 수 있습니다.

pNFS가 `/MNTPNT`에 mount되어 있다면 `configs/replayer/fs-native.yaml`의
`base_path`를 mount 아래 경로로 지정합니다. LMCache에서는 `fs_native`이지만 실제
I/O는 pNFS client와 server를 통과합니다.

NIXL과 HF3FS가 설치된 cluster에서는
`configs/replayer/nixl-hf3fs.yaml`을 사용하고 `file_path`와
`max_capacity_gb`를 환경에 맞게 바꿉니다. 설정은 다음 adapter를 생성합니다.

```json
{
  "type": "nixl_store_dynamic",
  "backend": "HF3FS",
  "backend_params": {
    "file_path": "/MNTPNT/lmcache-replay",
    "use_direct_io": "true",
    "max_capacity_gb": "30720"
  }
}
```

pNFS mount를 NIXL의 `POSIX` backend로 접근할 때는 `backend`를 `POSIX`,
`file_path`를 pNFS mount 아래 경로로 설정합니다. Adapter 이름과 parameter는
설치된 LMCache/NIXL version에서 확인하세요.

Backend를 비교할 때는 대상 L2 경로를 빈 상태로 시작하고, `l1_size_gb`,
alignment, eviction/store policy와 trace timing을 같게 유지한 채 L2 adapter만
바꿉니다. Record 환경의 cache file은 필요하지 않으며 `.lct` schema와 호환되는
LMCache version을 사용해야 합니다.

### Replay profiling

Replay 계측은 두 계층으로 구분합니다.

1. **L2 operation profiling**은 LMCache adapter/controller에서 `get`, `put`의
   queue wait, I/O 시작·완료, object 크기와 latency를 수집합니다.
2. **Node profiling**은 Linux sysfs counter로 storage node의 block device와 network
   처리량을 수집합니다. Tracebench의 `--profile`이 제공하는 기능입니다.

Replay dispatcher가 기록하는 API latency는 `StorageManager` API를 호출하는
동기 구간입니다. `finish_write` 후 L2 store와 `submit_prefetch_task` 후
retrieve는 비동기로 진행될 수 있으므로 API latency를 backend I/O latency로
해석하지 않습니다. L2 성능 비교에는 adapter/controller의 queue wait, service
time, byte 수, throughput과 p50/p90/p99 latency를 함께 수집하세요.

Node profiler의 기본 설정은 `configs/profiling/storage.yaml`입니다.

```yaml
sample_interval_seconds: 5
report_interval_seconds: 5
remote_tmp_root: /tmp/lmcache-tracebench-profile

nodes:
  - name: storage_node1
    host: storage_node1
    devices:
      - /dev/nvme0n1
      - /dev/nvme0n2
    interfaces:
      - bond0
```

`--profile` 실행 시 replay host는 SSH preflight 후 shell agent를 각 node의
`/tmp/lmcache-tracebench-profile/<run-id>/`에 배포합니다. 원격 node에는
project checkout, Python, LMCache, `iostat`, `nvme-cli`가 필요하지 않으며
`bash`, `awk`, `cat`, `date`, `sleep`, `readlink`와 sysfs만 사용합니다. 수집과
집계가 성공하면 원격 임시 경로를 삭제하고, 실패하면 분석을 위해 보존합니다.

Profiler는 다음 sysfs counter를 report interval별 diff로 계산합니다.

| 대상 | Counter | 결과 |
| --- | --- | --- |
| Block device | `/sys/class/block/<device>/stat` | Read/write byte, IOPS, MiB/s, I/O utilization |
| Network interface | `/sys/class/net/<interface>/statistics/` | RX/TX byte, packet/s, MiB/s, error, drop |

Block sector는 512 byte로 환산하며 마지막의 짧은 구간도 종료 시 flush합니다.
결과는 `output_dir/profile/<node>/`와 `profile_summary.json`에 저장됩니다.

| 파일 | 내용 |
| --- | --- |
| `disk.tsv` | Device별 read/write byte, IOPS, MiB/s, utilization |
| `network.tsv` | Interface별 RX/TX byte, packet/s, MiB/s, error, drop |
| `samples.jsonl` | Sample wall-clock·monotonic timestamp |
| `summary.json` | Node별 전체 byte와 평균 MiB/s |
| `agent.log` | Profiler 시작·종료 로그 |

Bond interface와 slave interface를 동시에 집계하면 traffic이 중복됩니다. 둘 중
하나만 선택하고, loop/partition device의 counter는 실제 physical device와 중복될
수 있으므로 장치 구성을 확인하세요. Replay client network도 필요하면 profile
config의 `replay_node`에 host와 interface를 추가합니다.

## Tests

```bash
source .venv/bin/activate
python -m pytest
```
