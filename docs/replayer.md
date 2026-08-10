# Replayer guide

설치 profile과 공통 경로 표기는 [README](../README.md)의
[Prerequisites](../README.md#prerequisites)를 먼저 참고하세요.

## Storage trace contract

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

`--speedup`은 workload나 GPU compute를 배속하지 않고, 이미 기록된 storage
record의 monotonic timestamp offset만 나누는 scaled-open replay 옵션입니다.
예를 들어 `--speedup 5`는 API 제출 schedule을 5배 압축하고, LMCache의
비동기 L2 I/O가 그 제출률을 따라가지 못할 때 발생하는 contention과 miss를
관찰하게 합니다. 실제 workload compute까지 바꾼 비교는 recorder speed sweep을
사용하고, 동일 trace의 storage arrival-rate 실험은 이 옵션을 사용합니다.

### How timestamp scaling works

각 record의 `t_mono`는 trace 시작 후 해당 API가 호출된 시각을 초 단위 offset으로
저장합니다. Replay는 `.lct` 안의 `t_mono`나 wall-clock `t_wall` 값을 다시 쓰지
않고, 실행 중 각 record의 목표 제출 시각을 다음처럼 계산합니다.

```text
target_i = replay_start_monotonic + record_i.t_mono / speedup
sleep_i  = max(0, target_i - current_monotonic)
```

예를 들어 원본 trace에 다음 timestamp가 기록되어 있다고 가정합니다.

| Record | 원본 `t_mono` | 원본 호출 간격 | `--speedup 5` 목표 offset | 배속 후 호출 간격 |
| --- | ---: | ---: | ---: | ---: |
| A | 0.0 s | - | 0.0 s | - |
| B | 2.0 s | 2.0 s | 0.4 s | 0.4 s |
| C | 5.0 s | 3.0 s | 1.0 s | 0.6 s |
| D | 11.0 s | 6.0 s | 2.2 s | 1.2 s |

즉 trace 시작 후 11초에 제출됐던 D는 replay 시작 후 2.2초가 목표가 되고,
모든 record 사이의 간격도 5분의 1로 줄어듭니다. 이는 timestamp 필드를
`0.0, 0.4, 1.0, 2.2`로 수정한 새 trace를 만드는 것이 아니라, 원본 offset을
나눈 값으로 현재 replay process의 `time.monotonic()` 기준 sleep 시간을 정하는
방식입니다.

목표 시각은 직전 record 기준이 아니라 replay 시작 시각 기준으로 매번 계산합니다.
따라서 C dispatch가 느려져 다음 record의 목표 시각을 이미 지난 경우에는 추가로
sleep하지 않고 D를 바로 dispatch합니다. 이때 뒤처진 시간을 되돌리기 위해 API를
병렬 호출하거나 record를 건너뛰지는 않으며, 원래 순서를 유지한 채 가능한 한
scaled schedule을 따라갑니다. 마지막 record dispatch 뒤에는 speedup과 별개로
비동기 Store/Prefetch 작업이 idle 상태가 될 때까지 drain을 기다립니다.

## Replay

Replayer는 저장된 `.lct`를 LMCache `trace replay` 명령으로 한 번 실행합니다.
공용 `base.yaml`을 상속하는 `fs-native.yaml` 또는 `nixl-hf3fs.yaml`을 선택합니다.
각 storage record는 trace의 monotonic timestamp 간격에 맞춰 재생되며, replay host가
더 느리면 원래 schedule보다 뒤처진 상태로 계속 진행합니다.
`--speedup`이 1보다 크면 이 timestamp 간격만 축소되며, replay 자체는 여전히
single-threaded API dispatch와 비동기 StorageManager/L2 controller를 사용합니다.

```bash
python -m replayer.main \
  --trace path/to/storage.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-path /MNTPNT/lmcache-trace-replay \
  --output-dir outputs/replay
```

실행 전 명령만 확인하려면 `--dry-run`을 추가합니다.
실행 중에는 터미널에 record 진행률을 표시하며, LMCache 원문 로그는
`output_dir/lmcache-replay.log`에 저장됩니다. `--l2-path`는 `fs_native`의
`l2_adapter.base_path`를, NIXL config에서는 `backend_params.file_path`를 덮어씁니다.
`--output-dir`는 summary, operation CSV와 로그를 저장할 디렉터리를 덮어씁니다.

## Replay speedup sweep

이미 기록된 하나의 `.lct`에 여러 `--speedup` 값을 적용해 storage arrival-rate를
비교하려면 `benchmarks/storage_trace/replay_speed_sweep.sh`를 사용합니다. 이 스크립트는
speedup별로 replay를 순차 실행하고, 각 case에 독립적인 L2 경로와 output 디렉터리를
사용합니다. 따라서 한 case의 warm cache가 다음 speedup에 영향을 주지 않습니다.

```bash
bash benchmarks/storage_trace/replay_speed_sweep.sh \
  --trace outputs/speed-sweep/tensormesh-gaia-x1/storage.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /MNTPNT/lmcache-trace-replay/speed-sweep \
  --output-root outputs/replay/speed-sweep/gaia \
  --speedups 1,2,5,10
```

각 case의 결과와 LMCache 로그는 `output-root/x<SPEEDUP>/`에 저장되며, 전체 실행
결과는 `sweep-summary.json`과 `sweep-results.jsonl`에 기록됩니다. `--profile`을
추가하면 speedup별 storage node profiling도 함께 실행합니다. 기존 case 경로가
비어 있지 않으면 warm-cache 결과를 방지하기 위해 실행을 중단하므로, 비교 실험에는
새 L2 root와 output root를 사용하세요. 실행 전 command만 확인하려면 `--dry-run`을
추가합니다.

## Replay workload sweep

여러 workload trace에 동일한 speedup sweep을 적용하려면
`benchmarks/storage_trace/replay_workload_sweep.sh`를 사용합니다. trace root 아래에
`<WORKLOAD>/storage.lct` 구조가 있어야 하며, workload별로 L2와 output 경로를
분리합니다.

```bash
bash benchmarks/storage_trace/replay_workload_sweep.sh \
  --trace-root /mnt/nvme/lmcache-traces/tensormesh-20260809 \
  --config configs/replayer/fs-native.yaml \
  --workloads tensormesh-wildclaw,tensormesh-other \
  --l2-root /mnt/nvme/lmcache-trace-replay \
  --output-root outputs/replay-workload-sweep \
  --speedups 1,2,4,8
```

각 workload의 결과는 `output-root/<WORKLOAD>/x<SPEEDUP>/`에 저장됩니다.
상위 launcher 로그와 workload별 결과는 각각 `workload-sweep.log`,
`workload-summary.json`, `workload-results.jsonl`에 기록됩니다. 한 workload가
실패해도 나머지 workload를 계속 실행하며, 마지막에 전체 exit code로 실패를 알립니다.
비교 실험에서는 이전 실행의 warm cache가 섞이지 않도록 새 L2 root와 output root를
사용하세요.

## Parallel replicated replay

한 replayer 노드에서 여러 MP instance의 storage 부하를 모사하려면
`benchmarks/storage_trace/replay_instances.sh`를 사용합니다. 이 기능은 동일한 `.lct`를 N개 독립 replay
process에서 병렬 실행하는 복제 모드만 제공합니다. 각 instance는 독립적인 L2
subdirectory와 output directory를 사용하지만, 같은 physical storage를 공유할 수
있습니다.

```bash
bash benchmarks/storage_trace/replay_instances.sh \
  --instances 8 \
  --trace outputs/speed-sweep/tensormesh-gaia-x5/storage.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /MNTPNT/lmcache-trace-replay \
  --output-root outputs/replay/gaia-x5-n8
```

실행 전에는 다음과 같이 N개 command만 확인할 수 있습니다.

```bash
bash benchmarks/storage_trace/replay_instances.sh \
  --instances 4 \
  --trace path/to/storage.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /MNTPNT/lmcache-trace-replay \
  --dry-run
```

결과는 `instance-0/`, `instance-1/` 등의 디렉터리와
`outputs/replay/gaia-x5-n8/instances-summary.json`에 저장됩니다. 각 instance의
LMCache 출력은 해당 디렉터리의 `lmcache-replay.log`, launcher 출력은
`launcher.log`에서 확인합니다. 실행 전 L2 instance 디렉터리가 비어 있는지
확인하세요.

동일 trace 복제는 storage backend의 aggregate 부하를 높이는 실험에는 적합하지만,
동일 KV key가 반복될 수 있으므로 N개의 서로 다른 workload를 정확히 재현하는
기능은 아닙니다. 또한 모든 replay process가 한 노드에서 실행되므로 N개 실제
replayer 노드의 network locality를 그대로 재현하지는 않습니다. 서로 다른 trace를
섞거나 여러 replayer 노드에 분산하는 기능은 Future Work입니다.

## Backend configuration

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

## Profiling

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
