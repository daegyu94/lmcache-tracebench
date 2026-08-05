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

Ubuntu 24.04와 Python 3.12가 필요합니다. Recorder는 CUDA가 연결된 NVIDIA GPU가
필요하지만, storage trace Replayer는 `fs_native` 기준 GPU·vLLM·모델 없이 실행할 수
있습니다. 모든 profile은 같은 프로젝트 `.venv`를 사용합니다.

설치 profile은 공통 runtime과 역할별 runtime으로 나뉩니다.

| Profile | 설치 내용 | 실행 환경 |
| --- | --- | --- |
| `common` | LMCache trace CLI, PyTorch, 공통 Python package | Recorder/Replayer의 기반 |
| `recorder` | common + vLLM, dataset, OpenAI/TensorMesh package | GPU에서 trace 생성; 기본 profile |
| `replayer-fs-native` | common + 추가 package 없음 | `fs_native`/O_DIRECT trace replay |
| `replayer-nixl-hf3fs` | common + NIXL package | NIXL+HF3FS trace replay |

Recorder 설치:

```bash
bash scripts/setup_runtime.sh --profile recorder
```

Replayer 설치:

```bash
# fs_native
bash scripts/setup_runtime.sh --profile replayer-fs-native

# NIXL + HF3FS
bash scripts/setup_runtime.sh --profile replayer-nixl-hf3fs
```

`bash scripts/setup_runtime.sh`처럼 profile을 생략하면 기존 동작과 호환되도록
`recorder`가 선택됩니다. 현재 환경만 검사하려면 각 명령에 `--check`를 추가합니다.
의존성 정의는 `requirements/common.txt`, `requirements/recorder.txt`,
`requirements/replayer-fs-native.txt`, `requirements/replayer-nixl-hf3fs.txt`에
분리되어 있으며, 기존 `requirements/runtime.txt`는 Recorder용 통합 profile입니다.

다른 버전을 시험하려면 해당 profile 파일을 복사해 지정합니다.

```bash
cp requirements/recorder.txt requirements/recorder.local.txt
# requirements/recorder.local.txt에서 lmcache, vllm 등의 version을 수정
bash scripts/setup_runtime.sh \
  --profile recorder \
  --runtime-requirements requirements/recorder.local.txt
```

`--runtime-requirements`는 `--check`와 함께도 사용할 수 있습니다. 버전을 바꾼 뒤에는
스크립트가 실행하는 `pip check`와 아래 smoke test를 반드시 통과시키세요.

### L2 profiling용 LMCache tracebench fork 사용

L2 operation profiling과 replay latency 통계(`--l2-stats-out`)를 사용하려면 PyPI의
기본 LMCache 대신 tracebench fork의 `v0.5.1-tracebench` 태그를 설치합니다. 이
변경사항은 별도로 LMCache 소스를 checkout하거나 patch할 필요 없이 pip의 Git URL
형태로 설치할 수 있습니다. 먼저 사용할 profile을 설치해 `.venv`와 공통 의존성을
준비한 뒤, 기존 의존성을 바꾸지 않도록 LMCache만 교체합니다.

```bash
# recorder라면 --profile recorder, fs_native replayer라면
# --profile replayer-fs-native를 사용합니다.
bash scripts/setup_runtime.sh --profile recorder
source .venv/bin/activate
python -m pip install --force-reinstall --no-deps --no-build-isolation \
  "lmcache @ git+https://github.com/daegyu94/LMCache.git@v0.5.1-tracebench"
```

`--no-build-isolation`은 현재 설치된 PyTorch/CUDA 환경에 맞춰 LMCache native
extension을 빌드하기 위한 옵션이며, `--no-deps`는 검증된 vLLM/PyTorch 조합을
LMCache 설치 과정에서 교체하지 않도록 합니다. 설치 후에는 다음 명령으로 확인합니다.

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

### Trace release asset 관리

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
  --filepath /mnt/misc/lmcache-tracebench/outputs/source-traces-20260804-082231/wildclaw/storage.lct
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

### Mooncake real-world workload

`workload.backend: mooncake`는 실제 online request의 timestamp, token 길이와 익명화된
prefix 구조를 vLLM `timed_trace`로 실행합니다. Trace schema, synthetic prompt 생성,
timing/scaling, KV 용량 계산과 결과 해석은
[`docs/wiki/Home.md`](docs/wiki/Home.md#mooncake)를 기준으로 합니다.

모델이나 LMCache config 없이 JSONL을 다운로드하고 검증하려면 다음 전용 command를
사용합니다. 기본 실행은 Conversation과 Tool/Agent trace를 모두 내려받아 각각
`conversation_trace.jsonl`, `toolagent_trace.jsonl`로 저장합니다.

```bash
python -m recorder.mooncake_cli \
  --path /mnt/std-ssd/traces/mooncake
```

`--trace toolagent` 또는 `--trace conversation`으로 하나만 선택할 수 있습니다.
`--path`는 다운로드 디렉터리를 명시하기 위해 필수입니다.
이미 받은 파일만 검증하려면 `--no-download`를 추가합니다.

공통 config로 Tool/Agent trace 1,000개 요청을 시작하려면:

```bash
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml \
  --mooncake-trace toolagent \
  --mooncake-path /mnt/std-ssd/traces/mooncake/toolagent_trace.jsonl \
  --output-dir outputs/qwen3-coder-tp8-mooncake-toolagent
```

Conversation은 같은 config에서 JSONL만 바꿉니다.

```bash
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml \
  --mooncake-trace conversation \
  --mooncake-path /mnt/std-ssd/traces/mooncake/conversation_trace.jsonl \
  --output-dir outputs/qwen3-coder-tp8-mooncake-conversation
```

공통 config는 L2 `base_path`의 `{trace}`를 선택한 trace 이름으로 치환하므로,
Tool/Agent와 Conversation은 각각 `mooncake-toolagent`, `mooncake-conversation`
디렉터리를 사용합니다.

전체 recorder 실행에서도 파일이 없으면 같은 다운로드·검증 로직을 자동으로
수행합니다. `recorder.main --load-workload`는 Tensormesh dataset 확인 전용입니다.
기존 `qwen3-coder-480b-tp8-mooncake-toolagent.yaml`은 실행 중인 작업과 기존 command
호환을 위해 유지하지만, 새 실행에는 공통 config를 사용하세요.

Mooncake 실행은 기존 결과에 더해 `vllm_benchmark.json`을 생성합니다.

`--output-dir`에는 `storage.lct`, `manifest.json`,
`request_stats.jsonl`, `session_outcomes.jsonl`, `lmcache.log`, `vllm.log`,
`workload.log`, `commands.json`, `workload.json`이 생성됩니다.
Tensormesh workload 실행 중에는 `progress_interval_seconds`에 따라 기본 5초마다 완료 session,
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
  --trace outputs/smoke/storage.lct \
  --config configs/replayer/smoke.yaml
```

Replay 중 storage node의 NVMe와 network counter를 수집하려면 --profile을
추가합니다. 각 node의 preflight가 먼저 실행되며, 기본값은 5초마다 counter를
샘플링하고 같은 5초 구간의 diff를 tab-delimited TSV로 기록합니다.

```bash
python -m replayer.main --trace outputs/smoke/storage.lct --config configs/replayer/smoke.yaml --profile configs/profiling/storage.yaml
```

Profiler는 storage node의 /sys counter를 사용하므로 iostat나 nvme-cli가
필수는 아닙니다. 결과는 profile_summary.json과 profile/<node>/disk.tsv,
profile/<node>/network.tsv에 저장됩니다. 원격 node에는 project checkout이나
Python이 필요하지 않습니다. 원격 agent의 samples와 log는 run별 /tmp 디렉터리에
저장됩니다. shell agent를 /tmp 아래에 자동 배포해 실행하며, bash, awk, cat,
date, sleep, readlink와 sysfs counter를 preflight에서 확인합니다. 프로젝트
전체를 전송하지 않습니다.
/tmp/lmcache-tracebench-profile/<run-id>/에 임시 저장되며, 수집·집계가 성공한
뒤 삭제됩니다. 실패하면 장애 분석을 위해 원격 결과를 보존합니다.

Replay client의 network도 비교하려면 profile config의 replay_node에 node1과
interface를 추가합니다. 기본 설정에는 포함되지 않습니다. bond0와 해당 slave
interface를 동시에 지정하면 traffic이 중복 집계되므로 둘 중 하나만 선택하세요.
Counter 의미와 L2 operation 계측의 차이는 [L2 및 disk/network profiling 문서](docs/wiki/Home.md#l2-및-disknetwork-profiling)를
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
  --config configs/replayer/fs-native.yaml
```

실행 전 command만 확인하려면 `--dry-run`을 추가합니다.
실행 중에는 터미널에 record 진행률을 표시하며, LMCache 원문 로그는
`output_dir/lmcache-replay.log`에 저장됩니다.

## Tests

```bash
source .venv/bin/activate
pytest
```
