# LMCache Tracebench

vLLM + LMCache MP 환경에서 Tensormesh-Benchmark V3 또는 Mooncake FAST'25
workload를 실행하고 LMCache storage trace를 기록·replay하는 실행 도구입니다.

## Repository layout

Tensormesh-Benchmark는 `third_party/Tensormesh-Benchmark` git submodule로
사용합니다. submodule은 `tracebench` branch를 가리키며 parent repository의
commit으로 고정됩니다.

Mooncake backend는 third-party source를 포함하지 않습니다. 프로젝트 내부 adapter가
[Mooncake FAST'25](https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release)의
익명화된 JSONL trace를 data cache로 내려받고, 설치된 vLLM의 `timed_trace` client로
재생합니다.

```bash
git clone <this-repository>
cd lmcache-tracebench
git submodule update --init --recursive
```

현재 parent repository의 작업 branch는 `dev`입니다.

## Prerequisites

Ubuntu 24.04, CUDA가 연결된 NVIDIA GPU, Python 3.12가 필요합니다. Recorder는 활성화한
프로젝트 `.venv`의 `python`으로 LMCache MP와 vLLM을 실행하므로, 두 package도
같은 `.venv`에 설치합니다.

```bash
bash scripts/setup_runtime.sh
```

스크립트는 `.venv` 생성, `pip` 보강, project·LMCache·vLLM·TensorMesh 의존성
설치와 runtime/GPU 검증을 수행합니다. 설치하지 않고 현재 환경만 확인하려면
`bash scripts/setup_runtime.sh --check`을 사용합니다.

`requirements/runtime.txt`는 이 환경에서 확인한 기본 runtime 조합을 고정합니다:
LMCache 0.5.1, vLLM 0.24.0, PyTorch 2.11.0. `lmcache-cli`가 아니라 GPU server와
trace 기능을 포함하는 `lmcache`를 사용합니다. CUDA 12.9 전용 wheel이 필요한
서버는 [LMCache 설치 문서](https://docs.lmcache.ai/getting_started/installation.html)를 따릅니다.

다른 버전을 시험하려면 기본 파일을 수정하지 말고 개인 requirements 파일을 만들고
그 파일을 지정합니다.

```bash
cp requirements/runtime.txt requirements/runtime.local.txt
# requirements/runtime.local.txt에서 lmcache, vllm 등의 version을 수정
bash scripts/setup_runtime.sh --runtime-requirements requirements/runtime.local.txt
```

`--runtime-requirements`는 `--check`와 함께도 사용할 수 있습니다. 버전을 바꾼 뒤에는
스크립트가 실행하는 `pip check`와 아래 smoke test를 반드시 통과시키세요.

실행 전 확인:

```bash
source .venv/bin/activate
python -c "import lmcache, lmcache.c_ops, vllm, openai, datasets; print('runtime imports: OK')"
```

`No module named 'lmcache'` 또는 `vllm` 오류는 다른 virtual environment를
활성화했거나 package 설치가 빠진 경우입니다.

## Recorder

Recorder는 LMCache MP server를 먼저 시작한 뒤 vLLM readiness를 기다리고,
Tensormesh V3 세션을 OpenAI-compatible endpoint로 전송합니다. LMCache에는
`trace-level=storage`, `fs_native` L2 adapter, `skip_l1` 설정이 전달됩니다.
예제 config의 `lmcache.l2.reset_on_start: true`는 실행할 때 기존 `base_path`를
삭제하고 빈 디렉터리로 다시 만듭니다. 이전 L2 데이터가 필요하면 실행 전에 백업하거나
이 값을 `false`로 바꾸세요. `--dry-run`은 L2 디렉터리를 변경하지 않습니다.
TP=8 config는 L2 write가 진행되는 동안 사용할 L1 staging 공간을 20 GB로
설정합니다. `skip_l1`이므로 L2 저장을 마친 KV는 L1에 계속 보관되지 않습니다.
1-GPU smoke와 smoke replay는 최소 검증용으로 1 GB를 사용합니다.
기본 `fs_native` 설정은 `use_odirect: true`로 Linux page cache를 우회합니다.
`lmcache.l2.num_workers`(replayer는 `l2_num_workers`)로 I/O worker thread 수를
조절할 수 있습니다. TP=8 기본값은 16이며, SSD saturation 및 tail latency 비교에는
`8`, `16`, `32`를 같은 trace로 replay해 측정하세요.

```bash
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-gaia.yaml \
  --output-dir outputs/gaia
```

`configs/recorder/qwen3-coder-480b-tp8-base.yaml`은 공통 TP=8 runtime·LMCache 설정이며 직접 실행하지
않습니다. Mixed workload의 source 비율, session ordering과 대표성 검증은 현재 **TBD**입니다.

SWE-bench, GAIA, WildClaw를 각각 독립 trace로 기록하려면 다음 source별 config를
사용합니다. 각 config는 별도 L2 경로를 사용합니다.

| Source | Config |
| --- | --- |
| SWE-bench | `configs/recorder/qwen3-coder-480b-tp8-swebench.yaml` |
| GAIA | `configs/recorder/qwen3-coder-480b-tp8-gaia.yaml` |
| WildClaw | `configs/recorder/qwen3-coder-480b-tp8-wildclaw.yaml` |

세 source trace를 순서대로 기록하려면 다음 script를 사용합니다.

```bash
bash scripts/record_source_traces.sh \
  --output-root /mnt/misc/lmcache-tracebench/outputs
```

각 결과는 timestamped directory 아래 `gaia/`, `wildclaw/`, `swebench/`에 저장됩니다.
작은 working set의 GAIA부터 시작하며, 한 source가 실패하면 script는 즉시 멈춥니다. 이때
실패한 source만 해당 config로 다시 실행하면 됩니다.

### Mooncake real-world workload

`workload.backend: mooncake`는 실제 온라인 요청에서 익명화한 `timestamp`,
`input_length`, `output_length`, `hash_ids`를 사용합니다. `hash_ids`는 512-token
prefix block 단위라 실제 prompt text 없이도 요청 간 KV prefix reuse를 보존합니다.
Mooncake source code를 실행하거나 vendor하지 않으며, JSONL은
`workload.mooncake.path`에 한 번 내려받아 이후 실행에서 재사용합니다.

Tool/Agent trace 1,000개 요청으로 시작하려면 다음 config를 사용합니다.

```bash
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-mooncake-toolagent.yaml \
  --output-dir outputs/qwen3-coder-tp8-mooncake-toolagent
```

전체 23,608개 요청은 config의 `workload.mooncake.num_requests`를 `null`로
설정합니다. `time_scale: 1.0`은 실제 약 1시간 arrival timeline을 유지하고,
`0.1`은 요청 간격을 10배 압축합니다. 전체 trace는 입력만 약 2.03억 token이므로
L2 용량을 확인한 뒤 `1,000 → 5,000 → null` 순서로 늘리세요.

서버를 띄우지 않고 trace 다운로드·검증과 LMCache, vLLM, workload command를
확인하려면:

```bash
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-mooncake-toolagent.yaml \
  --output-dir outputs/qwen3-coder-tp8-mooncake-toolagent \
  --dry-run \
  --load-workload
```

Mooncake 실행은 기존 결과에 더해 `vllm_benchmark.json`을 생성합니다.

`--output-dir`에는 `storage.lct`, `manifest.json`,
`request_stats.jsonl`, `session_outcomes.jsonl`, `lmcache.log`, `vllm.log`,
`workload.log`, `commands.json`, `workload.json`이 생성됩니다.
workload 실행 중에는 `progress_interval_seconds`에 따라 기본 5초마다 완료 session,
처리 turn, 성공·실패 수와 경과 시간이 stdout의 같은 줄에서 갱신됩니다. turn별 상세
내용은 화면에 반복 출력하지 않고 `workload.log`와 JSONL 결과에 기록합니다.

기본 Qwen3-Coder TP=8 설정은 B300 GPU 288 GB 중 1/4인 **72 GB/GPU**를 한
vLLM instance에 배정한다는 가정으로, H100 80 GB에서 이 quota를 모사하도록
`model.gpu_memory_utilization: 0.90`을 사용합니다. B300에서 직접 실행할 때는
동일한 quota가 되도록 `0.25`로 바꾸세요. 이 방식에서는 weight와 runtime을 제외한
예약 공간을 vLLM이 KV cache로 자동 사용하므로 `kv_cache_memory_gb_per_gpu`를
설정하지 않습니다.

KV cache를 정확한 값으로 고정해야 하는 별도 실험에서는
`gpu_memory_utilization: null`과 `kv_cache_memory_gb_per_gpu`를 함께 설정할 수
있습니다. 두 옵션은 동시에 유효하게 설정할 수 없습니다.

### Qwen3-Coder KV cache 대략치

Qwen3-Coder-480B-A35B-Instruct-AWQ의 KV cache가 BF16(2 byte)이고 TP=8일 때,
모델의 62 layer, 8 KV head, head dimension 128을 기준으로 GPU 하나에서 token 하나가
차지하는 KV cache는 다음과 같습니다.

```text
62 layers × K/V 2 × (8 KV heads / TP 8) × 128 × 2 bytes = 31,744 bytes/token/GPU
```

따라서 TP=8 전체에서는 token당 약 0.254 MB, LMCache chunk size가 128이면 chunk당
약 32.5 MB를 저장합니다. AWQ 4-bit weight의 이론적 최소 크기는 전체 약 240 GB,
GPU당 약 30 GB입니다. 실제 weight는 quantization metadata를 포함하므로 이보다 커질
수 있습니다. 72 GB quota에서 runtime/activation 등으로 사용할 공간을 가정한 KV
cache의 대략적인 상한은 다음과 같습니다.

| weight 외 runtime 공간 | GPU당 KV cache | TP=8 전체 KV cache | cache 가능한 token 수 |
| ---: | ---: | ---: | ---: |
| 8 GB | 약 34 GB | 약 272 GB | 약 107만 |
| 12 GB | 약 30 GB | 약 240 GB | 약 95만 |
| 16 GB | 약 26 GB | 약 208 GB | 약 82만 |

이는 `72 GB - 30 GB(이론적 AWQ weight) - runtime 공간`으로 계산한 근사치입니다.
실제 값은 vLLM 시작 로그의 `Available KV cache memory`를 기준으로 확정하세요.

이 값은 V3 trace의 고유 KV 총량을 목표값으로 만드는 옵션이 아니며, 실제
vLLM→LMCache offload 양은 workload와 cache 상태의 관측값으로 기록됩니다.

명령만 확인하려면 `--dry-run`을 사용합니다.

```bash
python -m recorder.main \
  --config configs/recorder/example.yaml \
  --dry-run
```

## Smoke test

`configs/recorder/smoke.yaml`은 1 GPU, 작은 Qwen 모델, V3 세션 10개·각 2 turn(최대 20개
request)으로 실제 storage trace를 만드는 최소 설정입니다. 첫 turn의 write와 두 번째
turn의 prefix reuse/prefetch 경로를 함께 확인합니다. V3 dataset은 `/mnt/std-ssd/hf-datasets`에
cache합니다. 최초 실행에는 Hugging Face dataset 접근 권한, 네트워크, 약 2.4 GB의
cache 공간이 필요하고, 이후 실행은 해당 cache를 재사용합니다.

```bash
source .venv/bin/activate
mkdir -p /mnt/std-ssd/lmcache-trace-smoke

python -m recorder.main \
  --config configs/recorder/smoke.yaml \
  --output-dir outputs/smoke
```

성공하면 `outputs/smoke/storage.lct`와 `outputs/smoke/manifest.json`이 생성됩니다.
문제가 생기면 `outputs/smoke/lmcache.log`와 `outputs/smoke/vllm.log`를 먼저 확인합니다.

방금 record한 trace를 새 StorageManager에 replay하려면 다음을 실행합니다. Replay는
vLLM이나 GPU server를 다시 시작하지 않으며, `configs/replayer/smoke.yaml`의 별도 L2
경로를 사용합니다.

```bash
python -m replayer.main \
  outputs/smoke/storage.lct \
  --config configs/replayer/smoke.yaml
```

결과는 `outputs/smoke-replay/trace_replay_summary.json`과
`outputs/smoke-replay/trace_replay_ops.csv`에 저장됩니다. 먼저 실행 command만 보려면
끝에 `--dry-run`을 추가합니다.

## Replayer

Replayer는 저장된 `.lct`를 LMCache `trace replay` command로 한 번 실행합니다.
실행 시 `fs_native` adapter와 replayer 설정의 L1 크기를 적용합니다.

```bash
python -m replayer.main \
  path/to/storage.lct \
  --config configs/replayer/example.yaml
```

실행 전 command만 확인하려면 `--dry-run`을 추가합니다.

## Tests

```bash
source .venv/bin/activate
pytest
```
