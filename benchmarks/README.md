# Benchmark guide

이 디렉터리의 script는 storage trace를 record하고 replay하는 반복 실험을 위한
실행 진입점입니다. `.lct` contract와 replay semantics의 상세 설명은
[Replayer guide](../docs/replayer.md), recorder 설정은
[Recorder guide](../docs/recorder.md)를 기준으로 합니다.

## Before running

- 프로젝트 환경을 먼저 설치합니다.

  ```bash
  bash scripts/setup_runtime.sh --profile replayer
  ```

- replay마다 새로운 L2 root와 output root를 사용합니다. 기존 case 디렉터리가
  비어 있지 않으면 warm-cache 결과를 방지하기 위해 실행이 중단됩니다.
- 실행 전에 `--dry-run`으로 trace, config, L2 path, output path와 최종 command를
  확인합니다.
- `--l2-root`는 실제 storage mount를 가리키는 absolute path를 사용합니다.
  `--output-root`에는 JSON summary와 launcher log가 저장됩니다.

## Script index

| Script | 용도 |
| --- | --- |
| `storage_trace/record_source_traces.sh` | 외부 recorder source에서 baseline trace 생성 |
| `storage_trace/record_speed_sweep.sh` | workload speedup별 `.lct` record |
| `storage_trace/replay_speed_sweep.sh` | 하나의 `.lct`를 여러 storage arrival-rate로 replay |
| `storage_trace/replay_l1_size_sweep.sh` | 하나의 `.lct`를 여러 L1 capacity로 replay |
| `storage_trace/replay_backend_sweep.sh` | backend별 speedup/L1 sweep 실행 |
| `storage_trace/replay_workload_sweep.sh` | 여러 workload에 동일한 replay speedup sweep 적용 |
| `storage_trace/replay_instances.sh` | 하나의 trace를 여러 replay process로 복제 실행 |

실행 가능한 옵션은 각 script의 `--help`를 사용합니다.

## Recommended workflow

### 1. Record or obtain traces

trace asset을 준비한 뒤 workload별로 다음 구조를 유지합니다.

```text
trace-root/
└── <workload>/
    └── storage.lct
```

외부 trace asset의 다운로드와 directory layout은
[Trace assets guide](../docs/trace-assets.md)를 참고합니다.

### 2. Replay one trace across speedups

```bash
bash benchmarks/storage_trace/replay_speed_sweep.sh \
  --trace /path/to/workload/storage.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /mnt/lmcache-replay/workload \
  --output-root outputs/replay-speed-sweep/workload \
  --speedups 1,2,4,8
```

각 speedup은 독립적인 `x<SPEEDUP>/` L2와 output directory를 사용합니다.
단일 speedup만 확인할 때도 `--speedups 8`처럼 실행할 수 있습니다.

### Replay one trace across L1 sizes

L1 capacity에 따른 L1/L2 lookup 변화를 비교하려면 다음 script를 사용합니다.
각 case는 `l1-20gb/`, `l1-40gb/`처럼 독립적인 L2와 output path를 사용하며,
`l1-size-gb`와 `l1-init-size-gb`를 같은 값으로 설정합니다.

```bash
bash benchmarks/storage_trace/replay_l1_size_sweep.sh \
  --trace /path/to/workload/storage.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /mnt/lmcache-replay/workload \
  --output-root outputs/replay-l1-size-sweep/workload \
  --l1-sizes 20,40,80,160 \
  --speedup 1
```

`--speedup 8`을 지정하면 같은 L1 size sweep을 scaled-open arrival-rate 8배에서
수행합니다. 결과는 `l1-size-summary.json`, `l1-size-results.jsonl`과 각 case의
`cache_replay_stats.json`, `l2_replay_stats.json`에서 확인합니다.

### 3. Replay multiple workloads

```bash
bash benchmarks/storage_trace/replay_workload_sweep.sh \
  --trace-root /mnt/lmcache-traces/tensormesh-20260809 \
  --config configs/replayer/fs-native.yaml \
  --workloads tensormesh-wildclaw,tensormesh-other \
  --l2-root /mnt/lmcache-replay \
  --output-root outputs/replay-workload-sweep \
  --speedups 1,2,4,8
```

이 script는 workload를 순차 실행합니다. 한 workload가 실패해도 다음 workload를
계속 실행하고, 마지막 exit code와 summary에 실패를 반영합니다.

### 4. Sweep storage backends

backend별 adapter/config와 L2 경로를 바꿔가며 speedup 또는 L1 size sweep을 하려면
`replay_backend_sweep.sh`를 사용합니다. `NAME`은 결과 label이고, 실제 adapter는
`CONFIG`, storage target은 `L2_PATH`가 결정합니다. 따라서 `xfs`와 `pnfs`는 같은
`fs-native.yaml`을 사용하되 mount path를 다르게 지정하고, `3fs`는
`nixl-hf3fs.yaml`을 사용합니다.

```bash
bash benchmarks/storage_trace/replay_backend_sweep.sh \
  --trace /mnt/nvme/lmcache-traces/tensormesh-20260809/tensormesh-wildclaw/storage.lct \
  --backend-spec 'xfs=configs/replayer/fs-native.yaml@/mnt/xfs/lmcache-replay' \
  --backend-spec 'pnfs=configs/replayer/fs-native.yaml@/mnt/pnfs/lmcache-replay' \
  --backend-spec '3fs=configs/replayer/nixl-hf3fs.yaml@/mnt/3fs/lmcache-replay' \
  --experiment speedup \
  --speedups 1,2,4,8 \
  --output-root outputs/replay-backend-sweep/tensormesh-wildclaw
```

L1 capacity를 비교하려면 `--experiment l1-size --l1-sizes 20,40,80,160
--speedup 1`로 변경합니다. scaled-open과 함께 실험하려면 `--speedup 8`을
지정합니다. 결과는 `output-root/<BACKEND>/` 아래에 저장되고, 상위
`backend-summary.json`, `backend-results.jsonl`, `backend-sweep.log`와
backend별 기존 sweep summary/log가 생성됩니다. 각 backend에는 새로운 L2 path를
사용하고, mount가 실제로 해당 backend인지 확인하세요.

### 5. Inspect artifacts

일반적인 replay case의 output은 다음과 같습니다.

```text
x8/
├── cache_replay_stats.json
├── l2_replay_stats.json
├── lmcache-replay.log
├── trace_replay_ops.csv
└── trace_replay_summary.json
```

- `l2_replay_stats.json`: backend read/write submitted/completed, latency, bytes와 throughput
- `cache_replay_stats.json`: L2 lookup hit/miss와 L2 load 결과
- `trace_replay_summary.json`: replay API별 count, error, latency
- `lmcache-replay.log`: LMCache 원문 출력
- `trace_replay_ops.csv`: record 단위 replay 결과

speedup sweep은 `sweep-summary.json`, `sweep-results.jsonl`, `sweep.log`를
추가로 생성합니다. workload sweep은 상위 output root에
`workload-summary.json`, `workload-results.jsonl`, `workload-sweep.log`를
생성합니다. backend sweep은 상위 output root에 backend별 결과와
`backend-summary.json`, `backend-results.jsonl`, `backend-sweep.log`를 생성합니다.

## Interpretation

`--speedup`은 workload나 GPU compute를 배속하는 옵션이 아니라, 기록된 storage
timestamp gap을 축소해 API submission rate를 높이는 scaled-open replay입니다.
따라서 speedup별 `cache_replay_stats.json`의 hit/miss와
`l2_replay_stats.json`의 latency/throughput을 함께 비교해야 합니다.
이 결과만으로 application TTFT나 end-to-end throughput을 의미한다고 해석하지
않습니다. 자세한 제한사항은 [Replayer guide](../docs/replayer.md)를 참고합니다.

동일 workload의 speedup 비교에서는 config, L2 backend, L1 설정을 고정하고
case별 L2 path를 분리합니다. 여러 replay process를 같은 physical storage에
동시에 실행하는 aggregate contention 실험은 `replay_instances.sh`를 사용합니다.
