# Benchmark guide

이 디렉터리의 script는 LMCache trace를 record하고 replay하는 반복 실험을 위한
실행 진입점입니다. recorder와 replayer를 디렉터리로 분리해 각 workflow의 옵션과
출력 구조를 독립적으로 관리합니다. `.lct` contract와 replay semantics의 상세 설명은
[Replayer guide](../docs/replayer.md), recorder 설정은
[Recorder guide](../docs/recorder.md)를 기준으로 합니다.
처음 설치하는 환경에서 검증된 순서대로 실행하려면
[L2 benchmark quickstart](../docs/benchmark-quickstart.md)를 먼저 참고하세요.

```text
benchmarks/
├── recorder/   # workload를 실행해 l2.lct 생성
└── replayer/   # L2 trace를 backend/speedup별로 replay
```

## Before running

- recorder를 실행하려면 프로젝트 환경을 설치합니다.

  ```bash
  bash scripts/setup_runtime.sh --profile recorder
  ```

- replayer만 실행하려면 replayer profile을 설치합니다.

  ```bash
  bash scripts/setup_runtime.sh --profile replayer
  ```

- `replay_speed_sweep.sh`는 각 case 직전에 같은 `l2-root` base path를 삭제하고
  다시 만듭니다. speedup별 L2 하위 디렉터리는 만들지 않습니다. 기존 replay 결과가
  있는 output case 디렉터리는 계속 실행 전에 비어 있어야 합니다. `--keep-l2`를
  사용하면 L2 contents를 case 사이에 재사용하며, base path는 실행 시작 시 비어
  있어야 합니다.
- `replay_instances.sh`는 각 instance L2 path를 reset합니다.
- 실행 전에 `--dry-run`으로 trace, config, L2 path, output path와 최종 command를
  확인합니다.
- Speedup sweep에서 `--output-root BASE`를 지정하면
  `<BASE>-<UTC timestamp>/`를 자동 생성합니다. 생략하면 recorder output의 trace
  이름을 base로 사용합니다. 이미 `-YYYYMMDD-HHMMSS`가 붙은 경로는 그대로 사용합니다.
- `--l2-root`는 실제 storage mount를 가리키는 absolute path를 사용합니다.
  `--output-root`에는 JSON summary와 launcher log가 저장됩니다.

## Recorder scripts

| Script | 용도 |
| --- | --- |
| `recorder/record_source_traces.sh` | source별 baseline trace 생성 |
| `recorder/record_speed_sweep.sh` | workload speedup별 `.lct` 생성 |

`record_source_traces.sh`는 기본적으로 GAIA, WildClaw, SWE-bench의
`storage.lct`를 생성합니다. `--sources`에는 다음 source 이름을 사용할 수
있습니다.

- Tensormesh: `gaia`, `wildclaw`, `swebench`
- Mooncake: `mooncake-toolagent`, `mooncake-conversation`

L2 adapter trace가 필요하면 다음처럼 source와 trace 종류를 지정합니다.

```bash
bash benchmarks/recorder/record_source_traces.sh \
  --mountpoint /MNTPNT \
  --output-root outputs/source-traces \
  --trace-kind l2 \
  --sources gaia,wildclaw
```

Mooncake Tool/Agent와 Conversation을 기록하려면 다음처럼 실행합니다.

```bash
bash benchmarks/recorder/record_source_traces.sh \
  --mountpoint /MNTPNT \
  --output-root outputs/source-traces \
  --trace-kind l2 \
  --sources mooncake-toolagent,mooncake-conversation
```

Mooncake 입력 trace는 config의 `path`를 사용하며, 없으면 recorder가 다운로드합니다.
각 결과는 `mooncake-toolagent/` 또는 `mooncake-conversation/` 아래에 생성됩니다.
Mooncake trace의 전체 request를 선택하려면 `--dataset-percent 100`을 추가합니다.

`record_speed_sweep.sh`는 workload별 speedup마다 독립 trace를 생성합니다.
`--trace-kind l2`를 추가하면 각 speedup 디렉터리에 `l2.lct`를 생성합니다.

## Replayer scripts

| Script | 용도 |
| --- | --- |
| `replayer/replay_speed_sweep.sh` | 하나의 `.lct`를 여러 storage arrival-rate로 replay |
| `replayer/replay_backend_sweep.sh` | backend별 speedup sweep 실행 |
| `replayer/replay_workload_sweep.sh` | 여러 workload에 동일한 replay speedup sweep 적용 |
| `replayer/replay_instances.sh` | 하나의 trace를 여러 replay process로 복제 실행 |

실행 가능한 옵션은 각 script의 `--help`를 사용합니다.

### Sweep script overview

| Script | Sweep unit | Case execution order |
| --- | --- | --- |
| `replayer/replay_speed_sweep.sh` | 하나의 trace × speedup | `x1 → x2 → x4 → x8` |
| `replayer/replay_workload_sweep.sh` | workload × speedup | workload 순서 안에서 speedup 순서 |
| `replayer/replay_backend_sweep.sh` | backend × speedup | backend 순서 안에서 speedup 순서 |

각 case는 별도의 `python -m replayer.main` 실행이며, sweep script들은 이 단일
replay를 반복 호출하는 launcher입니다. 실제 LMCache L2 replay는
`src/replayer/runner.py`가 실행하는 `lmcache trace replay` subprocess가 담당합니다.
입력이 `l2.lct`이면 measured replay 전에 source-resident read object를 준비하며,
`lmcache-prepare.log`와 `l2_prepare_manifest.json`도 case 디렉터리에 생성됩니다.

## Recommended workflow

### 1. Record or obtain traces

trace asset을 준비한 뒤 workload별로 다음 구조를 유지합니다.

```text
trace-root/
└── <workload>/
    └── l2.lct
```

외부 trace asset의 다운로드와 directory layout은
[Trace assets guide](../docs/trace-assets.md)를 참고합니다.

### 2. Replay one trace across speedups

```bash
bash benchmarks/replayer/replay_speed_sweep.sh \
  --trace /path/to/workload/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /mnt/lmcache-replay/workload \
  --output-root outputs/replay-l2/workload \
  --speedups 1,2,4,8
```

각 speedup은 같은 L2 base path를 실행 직전에 reset하고, 독립적인
`x<SPEEDUP>/` output directory를 사용합니다.
단일 speedup만 확인할 때도 `--speedups 8`처럼 실행할 수 있습니다.
float speedup도 지원하므로 `--speedups 1.0,1.5,2.0`처럼 지정할 수 있습니다.
위 예시의 실제 output root는
`outputs/replay-l2/workload-<UTC timestamp>/`입니다. Shell에서 `$(date ...)`를
직접 붙일 필요가 없습니다.

### 3. Replay multiple workloads

```bash
bash benchmarks/replayer/replay_workload_sweep.sh \
  --trace-root /mnt/lmcache-traces/tensormesh \
  --config configs/replayer/fs-native.yaml \
  --workloads wildclaw,gaia,swebench \
  --l2-root /mnt/lmcache-replay \
  --output-root outputs/replay-workload-sweep \
  --speedups 1,2,4,8
```

이 script는 workload를 순차 실행합니다. 한 workload가 실패해도 다음 workload를
계속 실행하고, 마지막 exit code와 summary에 실패를 반영합니다.

### 4. Sweep storage backends

backend별 adapter/config와 L2 경로를 바꿔가며 speedup sweep을 하려면
`replayer/replay_backend_sweep.sh`를 사용합니다. `NAME`은 결과 label이고, 실제 adapter는
`CONFIG`, storage target은 `L2_PATH`가 결정합니다. 따라서 `xfs`와 `pnfs`는 같은
`fs-native.yaml`을 사용하되 mount path를 다르게 지정하고, `3fs`는
`nixl-hf3fs.yaml`을 사용합니다.

```bash
bash benchmarks/replayer/replay_backend_sweep.sh \
  --trace /mnt/nvme/lmcache-traces/tensormesh/wildclaw/l2.lct \
  --backend-spec 'xfs=configs/replayer/fs-native.yaml@/mnt/xfs/lmcache-replay' \
  --backend-spec 'pnfs=configs/replayer/fs-native.yaml@/mnt/pnfs/lmcache-replay' \
  --backend-spec '3fs=configs/replayer/nixl-hf3fs.yaml@/mnt/3fs/lmcache-replay' \
  --experiment speedup \
  --speedups 1,2,4,8 \
  --output-root outputs/replay-backend-sweep/tensormesh-wildclaw
```

`--backend-spec`가 입력된 순서대로 backend가 실행되고, 각 backend 안에서는
`--speedups`에 입력한 순서대로 case가 실행됩니다. 위 명령의 전체 순서는 다음과
같습니다.

```text
xfs:x1 → xfs:x2 → xfs:x4 → xfs:x8
  → pnfs:x1 → pnfs:x2 → pnfs:x4 → pnfs:x8
  → 3fs:x1 → 3fs:x2 → 3fs:x4 → 3fs:x8
```

실행 흐름을 한 단계 확장하면 다음과 같습니다.

```text
replay_backend_sweep.sh
├── replay_speed_sweep.sh --config fs-native.yaml --l2-root /mnt/xfs/...
│   ├── python -m replayer.main --speedup 1 --l2-path /mnt/xfs/...
│   ├── python -m replayer.main --speedup 2 --l2-path /mnt/xfs/...
│   ├── python -m replayer.main --speedup 4 --l2-path /mnt/xfs/...
│   └── python -m replayer.main --speedup 8 --l2-path /mnt/xfs/...
├── replay_speed_sweep.sh --config fs-native.yaml --l2-root /mnt/pnfs/...
│   └── ... 같은 pnfs L2 base를 case마다 reset
└── replay_speed_sweep.sh --config nixl-hf3fs.yaml --l2-root /mnt/3fs/...
    └── ... 같은 3fs L2 base를 case마다 reset
```

backend sweep은 병렬 실행하지 않습니다. 한 backend의 speedup sweep이 끝난 뒤
다음 backend로 넘어갑니다. 개별 replay가 실패해도 다음 case를 계속 시도하고,
최종 summary와 exit code에 실패를 기록합니다. speedup 하위 sweep은
각 case 직전에 같은 L2 base directory를 기본적으로 reset하며, 기존 output case directory가 남아 있으면
결과 보호를 위해 해당 sweep이 중단될 수 있습니다.

결과는 `output-root/<BACKEND>/` 아래에 저장되고, 상위
`backend-summary.json`, `backend-results.jsonl`, `backend-sweep.log`와 backend별
speedup summary/log가 생성됩니다. 각 backend에는 새로운 L2 path를 사용하고, mount가
실제로 해당 backend인지 확인하세요.

### 5. Inspect artifacts

L2 adapter trace replay case의 output은 다음과 같습니다.

```text
x8/
├── l2_prepare_manifest.json
├── l2_replay_stats.json
├── l2_replay_summary.md
├── lmcache-prepare.log
└── lmcache-replay.log
```

- `l2_prepare_manifest.json`: measured replay 전에 준비한 object/byte와 elapsed time
- `l2_replay_stats.json`: read/write task latency, bytes, throughput, submission timing,
  wait/drain 및 source/target outcome 비교 metric
- `l2_replay_summary.md`: 주요 metric의 사람이 읽는 요약
- `lmcache-prepare.log`, `lmcache-replay.log`: LMCache prepare/replay 원문 출력

L2 outcome mismatch는 다른 backend나 source concurrency를 재현하는 과정에서 생길
수 있는 비교 지표이며 그 자체로 replay 실패나 case 무효를 뜻하지 않습니다. Trace
구조 오류, missing/duplicate end marker, event drop, dispatch 오류 또는 drain timeout은
실패로 처리합니다.

speedup sweep은 `sweep-summary.json`, 비교용 `sweep-summary.csv`,
`sweep-results.jsonl`, `sweep.log`를 추가로 생성합니다. workload sweep은 상위 output root에
`workload-summary.json`, `workload-results.jsonl`, `workload-sweep.log`를
생성합니다. backend sweep은 상위 output root에 backend별 결과와
`backend-summary.json`, `backend-results.jsonl`, `backend-sweep.log`를 생성합니다.

## Interpretation

`--speedup`은 workload나 GPU compute를 배속하는 옵션이 아니라 기록된 L2
submission gap을 축소해 offered I/O rate를 높이는 scaled-open replay입니다.
Speedup별 `l2_replay_stats.json`의 latency/throughput뿐 아니라 schedule lag,
dependency/buffer wait와 drain time을 함께 비교해야 합니다.
이 결과만으로 application TTFT나 end-to-end throughput을 의미한다고 해석하지
않습니다. 각 field의 계산식과 해석은
[L2 replay metric guide](../docs/l2-replay-metrics.md)를 참고합니다.

동일 workload의 speedup 비교에서는 config, L2 backend, L1 설정을 고정하고
case별 L2 path를 분리합니다. 여러 replay process를 같은 physical storage에
동시에 실행하는 aggregate contention 실험은 `replay_instances.sh`를 사용합니다.
