# Replayer guide

설치 profile과 공통 경로 표기는 [README](../README.md)의
[Prerequisites](../README.md#prerequisites)를 먼저 참고하세요.

## L2 adapter trace

Backend I/O 비교에는 recorder의 `--trace-kind l2`로 생성한 `l2.lct`를
사용합니다. 이 문서는 replay CLI와 실행 결과를 설명하며, trace level 선택 이유,
event/dependency, object preparation과 validity contract는
[L2 tracing guide](l2-tracing.md)를 기준으로 합니다.

Tracebench replayer는 L2 trace의 필요한 object preparation을 measured replay 전에
자동 실행합니다. Event 범위, synthetic object와 source/target outcome의 의미는
L2 tracing guide에서 관리합니다.

긴 trace의 앞부분만 실행하려면 `--trace-percent 10`처럼 지정합니다. 이 값은
시간이 아닌 source submission 개수 기준이며, 같은 prefix에 필요한 preparation만
수행합니다. 결과 field는 [L2 replay metric guide](l2-replay-metrics.md)를 따릅니다.

`--speedup`은 workload나 GPU compute를 배속하지 않고 기록된 submission 간격만
압축하는 scaled-open replay 옵션입니다. 예를 들어 `--speedup 5`는 L2 task 목표
제출 schedule을 5배 압축합니다. 실제 workload compute까지 바꾼 비교는 recorder
speed sweep을 사용하고, 동일 L2 trace의 arrival-rate 실험은 이 옵션을 사용합니다.

L2 trace에서도 `--speedup`은 I/O latency가 아니라 목표 제출 시각만 압축합니다.
Backend가 요청률을 감당하지 못하면 dependency wait, buffer wait, schedule lag와
마지막 submit 이후 drain이 증가하므로 total replay time은 더 이상 줄지 않거나
늘어날 수 있습니다. `l2_replay_stats.json`의 source/actual submission window,
schedule lag, drain time과 throughput을 함께 비교해야 합니다.

### How timestamp scaling works

L2 replay는 선택된 첫 submission을 시간 원점으로 정규화합니다. `.lct`의
`t_mono`와 `t_wall` 자체는 바꾸지 않고 각 operation의 목표 제출 시각을 다음처럼
계산합니다.

```text
schedule_origin = first_selected_op.t_mono
target_i = replay_start + (record_i.t_mono - schedule_origin) / speedup
earliest_submit_i = max(target_i, dependency_completion_time)
```

예를 들어 원본 trace에 다음 timestamp가 기록되어 있다고 가정합니다.

| Record | 원본 `t_mono` | 원본 호출 간격 | `--speedup 5` 목표 offset | 배속 후 호출 간격 |
| --- | ---: | ---: | ---: | ---: |
| A | 0.0 s | - | 0.0 s | - |
| B | 2.0 s | 2.0 s | 0.4 s | 0.4 s |
| C | 5.0 s | 3.0 s | 1.0 s | 0.6 s |
| D | 11.0 s | 6.0 s | 2.2 s | 1.2 s |

즉 첫 선택 operation으로부터 11초 뒤 제출됐던 D는 replay 시작 후 2.2초가
목표가 되고,
모든 record 사이의 간격도 5분의 1로 줄어듭니다. 이는 timestamp 필드를
`0.0, 0.4, 1.0, 2.2`로 수정한 새 trace를 만드는 것이 아니라, 원본 offset을
나눈 값으로 현재 replay process의 `time.monotonic()` 기준 sleep 시간을 정하는
방식입니다.

목표 시각은 직전 record 기준이 아니라 정규화한 replay 시작 시각 기준으로 매번
계산합니다.
따라서 C dispatch가 느려져 다음 record의 목표 시각을 이미 지난 경우에는 추가로
sleep하지 않고 D를 바로 dispatch합니다. 이때 뒤처진 시간을 되돌리기 위해 operation을 건너뛰지 않으며, dependency가 없는
task는 가능한 한 scaled schedule을 따라 제출합니다. 마지막 submission 뒤에는
speedup과 별개로 in-flight store, lookup과 load task가 끝날 때까지 drain합니다.

## Replay

Replayer는 저장된 `l2.lct`를 LMCache `trace replay` 명령으로 한 번 실행합니다.
공용 `base.yaml`을 상속하는 `fs-native.yaml` 또는 `nixl-hf3fs.yaml`을 선택하고,
아래처럼 L2 trace를 지정합니다.
Replay host가 목표 schedule보다 느리면 schedule lag와 drain time에 반영됩니다.

```bash
python -m replayer.main \
  --trace path/to/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-path /MNTPNT/lmcache-trace-replay \
  --output-dir outputs/replay \
  --trace-percent 10
```

실행 전 명령만 확인하려면 `--dry-run`을 추가합니다. 지정한 trace가 현재
host에서 읽을 수 있는 L2 trace이면 target을 변경하지 않고 선택된 op 수와
prepare/peak/final logical KV payload estimate를 GB 단위로 함께 출력합니다.
첫/마지막 submission timestamp 차이와 `speedup`으로 계산한 replay schedule 최소
하한도 출력하지만, preparation/backend startup·mount·drain 시간은 포함하지 않습니다.
Trace가 없거나 유효한 L2 trace가 아니면 command는 계속 표시하고 preflight를
건너뛴 이유를 warning으로 출력합니다.
실행 중에는 터미널에 record 진행률을 표시하며, LMCache 원문 로그는
`output_dir/lmcache-replay.log`에 저장됩니다. `--l2-path`는 `fs_native`의
`l2_adapter.base_path`를, NIXL config에서는 `backend_params.file_path`를 덮어씁니다.
`--output-dir`는 L2 prepare manifest, replay 통계와 로그를 저장할 디렉터리를
덮어씁니다.
실제 replay에서는 같은 preflight를 L2 preparation 전에 실행하고
`l2_preflight.json`에 보관합니다.
Prepare와 replay 종료 뒤의 client-visible L2 namespace 크기는
`l2_usage.json`에 기록하며, field 의미는
[L2 replay metric guide](l2-replay-metrics.md#l2-namespace-사용량)를 따릅니다.

## Replay speedup sweep

이미 기록된 하나의 `.lct`에 여러 `--speedup` 값을 적용해 storage arrival-rate를
비교하려면 `benchmarks/replayer/replay_speed_sweep.sh`를 사용합니다. 이 스크립트는
speedup별로 replay를 순차 실행하고, 각 case 전에 같은 L2 base path를 삭제한 뒤 다시
만듭니다. 결과 output 디렉터리는 case별로 분리되므로 한 case의 warm cache와 결과가
다음 speedup에 영향을 주지 않습니다.

```bash
bash benchmarks/replayer/replay_speed_sweep.sh \
  --trace outputs/speed-sweep/tensormesh-gaia-x1/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /MNTPNT/lmcache-trace-replay/speed-sweep \
  --output-root outputs/replay-l2/gaia \
  --speedups 1,2,5,10
```
`--output-root`는 base path이며 실제 root는
`outputs/replay-l2/gaia-<UTC timestamp>/`가 됩니다. 옵션을 생략하면 recorder
output인 trace parent 이름을 base로 사용합니다. 이미 `-YYYYMMDD-HHMMSS`가 붙은
경로는 그대로 사용하므로 기존 command와도 호환됩니다.

각 case의 결과와 LMCache 로그는 `output-root/x<SPEEDUP>/`에 저장되며, 전체 실행
결과는 `sweep-summary.json`과 `sweep-results.jsonl`에 기록됩니다. `--io-profile`을
추가하면 speedup별 storage node profiling도 함께 실행합니다. 같은 `l2-root` base
path가 각 case 직전에 삭제되고 다시 생성됩니다. 따라서 speedup별 L2 하위 경로를
따로 만들 필요가 없고, 이전 case의 warm cache도 남지 않습니다. `--dry-run`에서는
삭제하지 않습니다. `--keep-l2`를 사용하면 L2 contents를 case 사이에도 재사용하며,
base path는 실행 시작 시 비어 있어야 합니다. 기존 replay
결과를 보호하기 위해 `output-root/x<SPEEDUP>/`는 계속 비어 있어야 합니다.

## Replay workload sweep

여러 workload trace에 동일한 speedup sweep을 적용하려면
`benchmarks/replayer/replay_workload_sweep.sh`를 사용합니다. trace root 아래에
`<WORKLOAD>/l2.lct` 구조가 있어야 하며, workload별로 L2와 output 경로를
분리합니다.

```bash
bash benchmarks/replayer/replay_workload_sweep.sh \
  --trace-root /mnt/nvme/lmcache-traces/tensormesh \
  --config configs/replayer/fs-native.yaml \
  --workloads wildclaw,gaia,swebench \
  --l2-root /mnt/nvme/lmcache-trace-replay \
  --output-root outputs/replay-workload-sweep \
  --speedups 1,2,4,8
```

`--output-root`를 생략하면 요청한 workload 목록을 label로 만든
`outputs/replay-l2/<workload>-<UTC timestamp>/`가 자동 생성됩니다.
각 workload의 결과는 `output-root/<WORKLOAD>/x<SPEEDUP>/`에 저장됩니다.
상위 launcher 로그와 workload별 결과는 각각 `workload-sweep.log`,
`workload-summary.json`, `workload-results.jsonl`에 기록됩니다. 한 workload가
실패해도 나머지 workload를 계속 실행하며, 마지막에 전체 exit code로 실패를 알립니다.
각 workload의 speedup sweep은 같은 L2 base를 case 직전에 reset하므로 workload별
speedup 하위 경로를 따로 만들 필요가 없습니다. 기존 결과를 덮어쓰지 않도록
output root는 새 경로를 사용하세요.

## Parallel replicated replay

한 replayer 노드에서 여러 MP instance의 storage 부하를 모사하려면
`benchmarks/replayer/replay_instances.sh`를 사용합니다. 이 기능은 동일한 `.lct`를 N개 독립 replay
process에서 병렬 실행하는 복제 모드만 제공합니다. 각 instance는 독립적인 L2
subdirectory와 output directory를 사용하지만, 같은 physical storage를 공유할 수
있습니다.

```bash
bash benchmarks/replayer/replay_instances.sh \
  --instances 8 \
  --trace outputs/speed-sweep/tensormesh-gaia-x5/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /MNTPNT/lmcache-trace-replay \
  --output-root outputs/replay/gaia-x5-n8
```

실행 전에는 다음과 같이 N개 command만 확인할 수 있습니다.

```bash
bash benchmarks/replayer/replay_instances.sh \
  --instances 4 \
  --trace path/to/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /MNTPNT/lmcache-trace-replay \
  --dry-run
```
`--output-root`를 생략하면 trace parent 이름과 UTC timestamp를 사용한
`outputs/replay-l2/<trace-name>-<UTC timestamp>/`가 자동 생성됩니다.

결과는 `instance-0/`, `instance-1/` 등의 디렉터리와
`outputs/replay/gaia-x5-n8/instances-summary.json`에 저장됩니다. 각 instance의
LMCache 출력은 해당 디렉터리의 `lmcache-replay.log`, launcher 출력은
`launcher.log`에서 확인합니다. 기본적으로 실행 전에 각 L2 instance 디렉터리를
삭제하고 다시 생성합니다. 기존 L2 경로를 보존하려면 `--keep-l2`를 사용하며,
이때 instance 디렉터리는 비어 있어야 합니다.

동일 trace 복제는 storage backend의 aggregate 부하를 높이는 실험에는 적합하지만,
동일 KV key가 반복될 수 있으므로 N개의 서로 다른 workload를 정확히 재현하는
기능은 아닙니다. 또한 모든 replay process가 한 노드에서 실행되므로 N개 실제
replayer 노드의 network locality를 그대로 재현하지는 않습니다. 서로 다른 trace를
섞거나 여러 replayer 노드에 분산하는 기능은 Future Work입니다.

## Backend configuration

Replay는 trace header의 원본 L2 설정을 강제하지 않고 현재 config로 target L2
adapter를 생성합니다. 따라서 로컬 SSD의 `fs_native`로 record한 trace를 pNFS
mount나 NIXL/HF3FS에 재생할 수 있습니다.

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

1. **L2 replay 통계**는 target adapter에 store/load task를 제출한 시점부터 완료를
   관측한 시점까지의 latency를 `l2_replay_stats.json`에 기록합니다. Read/write별
   submitted/completed, bytes, average, p50/p90/p99/min/max latency와 aggregate
   throughput을 adapter별로 제공합니다. 단위는 microseconds입니다.
2. **Node profiling**은 Linux sysfs counter로 storage node의 block device와 network
   처리량을 수집합니다. Tracebench의 `--io-profile`이 제공하는 기능입니다.

`storage` trace의 per-API latency는 backend I/O latency가 아닙니다. Backend 비교에는
`l2` trace의 direct task latency와 top-level replay timing, node profile을 함께
사용하세요. Preparation I/O는 measured replay 통계와 node profile에서 제외됩니다.

Node profiler의 기본 설정은 `configs/profiling/storage.yaml`입니다.

```yaml
sample_interval_seconds: 5
report_interval_seconds: 5
remote_tmp_root: /tmp/lmcache-tracebench-profile

nodes:
  - hostname: storage_node1
    devices:
      - /dev/nvme0n1
      - /dev/nvme0n2
    interfaces:
      - bond0
```

`devices`와 `interfaces`의 각 항목은 bash brace 표현처럼 `{a..b}` range나
`{a,b,c}` list를 쓸 수 있습니다. 예를 들어 `/dev/nvme{2..7}n1`은
`/dev/nvme2n1`부터 `/dev/nvme7n1`까지 여섯 개로, `/dev/nvme{1,2,5}n1`은
지정한 세 개로만 확장됩니다. 이 확장은 config를 읽을 때 한 번 일어나며,
`devices`에 괄호가 없는 일반 경로는 그대로 유지됩니다.

`--io-profile` 실행 시 replay host는 SSH preflight 후 shell agent를 각 node의
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
`profile_summary.json`의 `cluster_disk_totals`는 device 이름별(여러 node에 걸쳐
합산), `node_disk_totals`는 node별로 그 node의 모든 device를 합산한 throughput,
`cluster_disk_grand_total`은 전체 cluster의 device 전부를 합산한 단일 총합입니다.

| 파일 | 내용 |
| --- | --- |
| `disk.tsv` | Device별 read/write byte, IOPS, MiB/s, utilization |
| `network.tsv` | Interface별 RX/TX byte, packet/s, MiB/s, error, drop |
| `samples.jsonl` | Sample wall-clock·monotonic timestamp |
| `summary.json` | Node별 전체 byte와 평균 MiB/s |
| `agent.log` | Profiler 시작·종료 로그 |

`disk.tsv`와 `network.tsv`의 `elapsed_s`는 정수 초이며, `timestamp`는 소수점 없는 UTC 초 단위입니다. `interval_s` 컬럼은 포함하지 않습니다.

Bond interface와 slave interface를 동시에 집계하면 traffic이 중복됩니다. 둘 중
하나만 선택하고, loop/partition device의 counter는 실제 physical device와 중복될
수 있으므로 장치 구성을 확인하세요. Replay client network도 필요하면 profile
config의 `replay_node`에 host와 interface를 추가합니다.
