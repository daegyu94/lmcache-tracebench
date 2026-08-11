# Recorder guide

설치 profile과 공통 경로 표기는 [README](../README.md)의
[Prerequisites](../README.md#prerequisites)를 먼저 참고하세요.

기본 `--trace-kind storage`는 `storage.lct`를 생성합니다. Backend I/O benchmark용
L2 adapter trace가 필요하면 같은 recorder command에 `--trace-kind l2`를 추가하며,
결과는 `l2.lct`에 저장됩니다. 상세 contract와 replay 의미론은
[L2 adapter trace](replayer.md#l2-adapter-trace)를 참고하세요.

Recorder는 LMCache MP와 vLLM을 시작하고 OpenAI-compatible endpoint로 workload를
전송합니다. 예제 config의 `lmcache.l2.reset_on_start: true`는 실행 전에
`--mountpoint`와 `lmcache.l2.subpath`로 정해진 L2 경로를 비웁니다. 필요한
데이터는 백업하거나 이 값을 `false`로 바꾸세요. `--dry-run`은 L2 디렉터리를
변경하지 않습니다.

## Tensormesh V3 workload

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
  --mountpoint /MNTPNT \
  --output-dir outputs/gaia
```

실행 명령만 확인하려면 `--dry-run`을 추가합니다.

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
각 source config의 `lmcache.l2.subpath`는
`lmcache-trace/tensormesh-<source>`이며, `--mountpoint`가 실제 storage mount
경로를 결정합니다.

SWE-bench의 전체 dataset 중 일부만 기록할 때는 `--dataset-percent`를 사용합니다.
비율은 source와 dataset-model filter를 적용한 전체 session 수에 대해 계산하고,
session 순서를 유지한 prefix를 선택합니다. GAIA와 WildClaw는 이 비율을 무시하고
전체 source dataset을 사용합니다. 실제 선택 session과 request(turn) 수는
`workload.json`과 `manifest.json`의 `total_sessions`, `selected_sessions`,
`total_turns`, `selected_turns`에서 확인할 수 있습니다.

```bash
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-swebench.yaml \
  --mountpoint /MNTPNT \
  --dataset-percent 10 \
  --output-dir outputs/swebench-10pct
```

`--dataset-percent`는 Tensormesh의 SWE-bench에 적용되며, GAIA와 WildClaw에서는
무시됩니다. 결과의 `dataset_percent_applied`가 실제 적용 여부를 나타냅니다.

```bash
bash benchmarks/recorder/record_source_traces.sh \
  --mountpoint /MNTPNT \
  --output-root /MNTPNT/lmcache-tracebench/outputs
```

각 결과는 timestamped 디렉터리 아래 `gaia/`, `wildclaw/`, `swebench/`에 저장됩니다.
작은 working set의 GAIA부터 시작하며, 한 source가 실패하면 script는 즉시 멈춥니다. 이때
실패한 source만 해당 config로 다시 실행하면 됩니다.

GAIA와 WildClaw만 L2 adapter trace로 기록하려면 `--trace-kind l2`와
`--sources gaia,wildclaw`를 추가합니다. 각 source 디렉터리에 `l2.lct`가 생성됩니다.

```bash
bash benchmarks/recorder/record_source_traces.sh \
  --mountpoint /MNTPNT \
  --output-root /MNTPNT/lmcache-tracebench/outputs \
  --trace-kind l2 \
  --sources gaia,wildclaw
```

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

## Speed sweep

### Why each speedup needs its own trace

`storage.lct`는 workload가 실제로 실행되는 동안 발생한 storage operation의
순서와 상대 시간 간격을 기록합니다. GPU compute 시간, serving-side scheduling,
request 처리 지연은 포함하지 않습니다. 따라서 원본 `storage.lct`를 replay 단계에서
배속하면 storage operation 간격만 압축되고 GPU compute 시간은 함께 조정되지 않아,
의도한 workload speedup을 재현하지 못합니다.

배속 비교에서는 recorder 단계에서 각 speedup으로 workload를 실행해
`storage.lct`를 별도로 생성해야 합니다. 아래 스크립트는 이 과정을 자동화하며,
배속별 결과 trace는 서로 다른 output 디렉터리에 저장합니다.

```text
Record phase: workload (GPU compute + serving + storage)

  source workload ──┬─ x1  ──> storage.lct (x1)  ──> replay x1
                     ├─ x2  ──> storage.lct (x2)  ──> replay x2
                     ├─ x5  ──> storage.lct (x5)  ──> replay x5
                     └─ x10 ──> storage.lct (x10) ──> replay x10

  One source trace cannot be replay-time scaled faithfully:
  GPU compute time is not represented in storage.lct, so only storage gaps shrink.
```

The timing limitation can also be viewed directly at the operation level:

```text
Record: full workload timeline

  GPU compute ── storage S1 ───── GPU compute ── storage S2
                    │                              │
                    └──────── storage.lct ────────┘

Replay: storage operations only

  storage S1 ─── storage S2
       └─ GPU compute time is not present in storage.lct

  Replay-time x5 compresses the storage gap, but does not scale GPU compute.
```

`benchmarks/recorder/record_speed_sweep.sh`는 workload별로 speedup마다 하나의
`storage.lct` 또는 `l2.lct`를
순차적으로 기록합니다. config의 L2 `subpath`는 `--mountpoint` 아래에서
사용합니다. 각 case는 시작할 때 L2 경로를 초기화하고, 실행이 끝나면 중간 L2
directory도 기본적으로 비웁니다. `storage.lct`와 로그가 있는 output directory는
그대로 보존하며, Hugging Face dataset cache와 Mooncake 입력 trace도 삭제하지
않습니다. 정리 직전에 L2 사용량을 측정해 각 case의 `l2_usage.json`과
전체 sweep의 `l2_usage.jsonl`에 bytes, decimal GB와 GiB를 기록합니다. 실행 후
L2 object를 남겨야 할 때만 `--keep-l2`를 추가하세요.

Mooncake 기록(기본 Tool/Agent·Conversation, 전체 trace의 10%):

```bash
bash benchmarks/recorder/record_speed_sweep.sh \
  --backend mooncake \
  --mountpoint /MNTPNT \
  --speedups 1,2,5,10 \
  --dataset-percent 10
```

SWE-bench는 전체 session의 비율을 선택하고, GAIA와 WildClaw는 비율을 무시하고
각 source의 전체 dataset을 사용합니다. 세 workload를 함께 실행하려면 다음과
같이 지정합니다.

```bash
bash benchmarks/recorder/record_speed_sweep.sh \
  --backend tensormesh \
  --workloads swebench,gaia,wildclaw \
  --mountpoint /MNTPNT \
  --speedups 1,2,5,10 \
  --dataset-percent 10
```

Tensormesh 기록(기본 GAIA·WildClaw·SWE-bench):

```bash
bash benchmarks/recorder/record_speed_sweep.sh \
  --backend tensormesh \
  --mountpoint /MNTPNT \
  --speedups 1,2,5,10
```

배속별 L2 adapter trace가 필요하면 같은 명령에 `--trace-kind l2`를 추가합니다.
각 speedup 디렉터리에 `l2.lct`가 생성됩니다.

결과는 다음과 같이 저장됩니다.

```text
outputs/speed-sweep/mooncake-toolagent-x1/storage.lct
outputs/speed-sweep/mooncake-toolagent-x5/storage.lct
outputs/speed-sweep/tensormesh-gaia-x1/storage.lct
outputs/speed-sweep/tensormesh-swebench-x10/storage.lct
```

Mooncake의 `speedup`은 `time_scale=1/speedup`으로, Tensormesh의 `speedup`은
`respect-gaps` 모드에서 `pre_gap_scale=1/speedup`으로 변환됩니다. Tensormesh의
`max-pressure`는 gap을 제거한 별도 baseline이므로 speedup sweep에 포함하지
않습니다. 실행 전 계획만 확인하려면 `--dry-run`을 추가합니다.

## GPU memory quota

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

## Mooncake real-world workload

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

각 JSONL 행은 request 하나이며 주요 field는 다음과 같습니다.

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
| `--dataset-percent` | JSONL 처음부터 선택할 request 비율. `10`은 전체 trace의 10% |
| `time_scale` | request 간격 배율. `1.0`은 원본 timeline, `0.1`은 10배 압축 |
| `chunk_hash_size` | `hash_id` 하나를 확장할 token 수. 공식 trace는 512 |
| `max_concurrent_requests` | 동시에 처리할 client request 상한 |

`--dataset-percent`는 shuffle sample이 아니라 timestamp 순서를 유지한 trace
prefix입니다. 선택 request 수는 `ceil(전체 request 수 × 비율 / 100)`으로 계산하며,
실제 전체·선택 request 수는 `workload.json`과 `manifest.json`의
`total_requests`, `selected_requests`에서 확인할 수 있습니다. `time_scale`은
request 수와 token 수는 바꾸지 않고 arrival 간격과 그에 따른 concurrency·I/O
timing만 조절합니다.

공통 config를 복제하지 않고 run별 dataset 비율을 바꾸려면
`--dataset-percent`를 사용합니다. 생략하면 config의 기본 동작을 따릅니다. 특정
L2 위치를 일회성으로 지정해야 할 때만 `--l2-path`를 추가하고, 일반 실행에서는
`--mountpoint`만 사용합니다.

Tool/Agent 또는 Conversation trace를 기록하려면 `TRACE`만 바꿔 다음 명령을
사용합니다. `TRACE`에는 `toolagent` 또는 `conversation`을 지정합니다.

```bash
TRACE=toolagent  # conversation으로 바꿔 실행할 수 있습니다.

python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml \
  --mountpoint /MNTPNT \
  --mooncake-trace "$TRACE" \
  --dataset-percent 10 \
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
| Tool/Agent 전체 23,608 requests | 약 23.83 TB | 약 52.63 TB |
| Conversation 전체 12,031 requests | 약 23.77 TB | 약 37.82 TB |

실행 중 vLLM benchmark의 progress bar가 완료 request 수, 처리율과 경과 시간을
표시하며 같은 출력을 `workload.log`에도 저장합니다.

## Recorder output

`--output-dir`에는 다음 파일이 생성됩니다.

| 파일 | 내용 |
| --- | --- |
| `storage.lct` | LMCache storage operation trace |
| `manifest.json` | Workload 요약, 오류와 process 종료 상태 |
| `request_stats.jsonl` | 요청별 latency와 성공 여부 |
| `session_outcomes.jsonl` | Session별 실행 결과 |
| `commands.json` | 실행 command와 환경 변수 |
| `workload.json` | 선택한 workload 정보 |
| `l2_usage.json` | speed sweep case 종료 직전 측정한 L2 사용량 |
| `lmcache.log`, `vllm.log`, `workload.log` | Process별 로그 |

Mooncake는 `vllm_benchmark.json`도 생성합니다. 문제가 발생하면 `manifest.json`을
확인한 뒤 각 process 로그를 살펴보세요.
