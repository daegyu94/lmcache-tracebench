# L2 benchmark quickstart

이 문서는 새 replay host에서 별도의 도움 없이 설치부터 결과 확인까지 순서대로
진행하기 위한 실행 가이드입니다. 이미 기록된 `l2.lct`로 L2 backend를 비교하는
경로를 기준으로 합니다. Trace를 직접 기록할 때는
[Recorder guide](recorder.md)를 참고하세요.

## 실험 범위

권장 실험 행렬은 다음과 같습니다.

```text
L2 backend × workload × speedup × repetition
```

`l1_size`는 direct L2 replay의 cache hit/miss를 바꾸지 않습니다. L2 replay가
StorageManager의 cache lifecycle을 우회하기 때문입니다. 이 옵션은 adapter I/O에
사용하는 replay buffer 용량의 민감도를 확인할 때만 별도 실험으로 사용합니다.

## 1. 시작 전 확인

검증 환경은 Ubuntu 24.04와 Python 3.12입니다. Git, `uv`, C/C++ build toolchain,
네트워크 연결, target filesystem의 읽기·쓰기 권한과 충분한 공간이 필요합니다.

```bash
command -v git
command -v uv
python3.12 --version
df -h /mnt/nvme
```

> [!CAUTION]
> Sweep의 `--l2-root`는 benchmark 전용 disposable directory여야 합니다.
> 기본 동작은 각 case 직전에 이 경로 전체를 삭제하고 다시 만드는 것입니다.
> 기존 데이터, mount root, symlink 또는 공유 경로를 지정하지 마세요.
> `--output-root`는 삭제하지 않으며 case별 결과를 보존합니다.

완전히 network가 차단된 환경에서는 Git repository, LMCache fork, Python wheel,
PyTorch build와 trace asset을 연결된 환경에서 미리 준비해야 합니다.

## 2. Replayer 설치

L2 trace replay만 수행할 때는 Tensormesh submodule이 필요하지 않습니다.

```bash
git clone https://github.com/daegyu94/lmcache-tracebench.git
cd lmcache-tracebench
git switch dev
bash scripts/setup_runtime.sh --profile replayer
source .venv/bin/activate
bash scripts/setup_runtime.sh --profile replayer --check
```

설치 결과를 확인합니다.

```bash
python -c "import importlib.metadata as m; print(m.distribution('lmcache').read_text('direct_url.json'))"
lmcache trace replay --help | grep -- '--l2-stats-out'
python -m pip check
```

`direct_url.json`에는 `daegyu94/LMCache`의 `priv/dg/l2-tracing` source가 나타나야
합니다. 현재 setup은 `fs_native` replay에도 NIXL package를 함께 설치하므로 NIXL을
설치할 수 없는 host에서는 profile을 분리하기 전까지 setup을 완료할 수 없습니다.

## 3. L2 trace 다운로드

HF Dataset archive는 `main` revision에서 받습니다. 현재는 Tensormesh trace가
업로드되어 있고, Mooncake의 `toolagent.tar.gz`와 `conversation.tar.gz`는 같은
`mooncake/` 디렉터리에 추가될 예정입니다.

```bash
bash tools/artifacts/hf_trace_asset.sh download \
  --repo-id daegyu94/lmcache-storage-traces \
  --revision main \
  --path-in-repo tensormesh/wildclaw.tar.gz \
  --output-dir /mnt/nvme/lmcache-l2-replay/traces

# 압축 내부를 먼저 확인하고 trace root 아래에 풉니다.
tar -tzf /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw.tar.gz
mkdir -p /mnt/nvme/lmcache-l2-replay/traces/tensormesh
tar -xzf /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw.tar.gz \
  -C /mnt/nvme/lmcache-l2-replay/traces/tensormesh

test -s /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct
du -h /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct
```

Mooncake archive가 업로드된 뒤에는 다음처럼 내려받고 풉니다.

```bash
bash tools/artifacts/hf_trace_asset.sh download \
  --repo-id daegyu94/lmcache-storage-traces \
  --revision main \
  --path-in-repo mooncake/toolagent.tar.gz \
  --output-dir /mnt/nvme/lmcache-l2-replay/traces
mkdir -p /mnt/nvme/lmcache-l2-replay/traces/mooncake
tar -xzf /mnt/nvme/lmcache-l2-replay/traces/mooncake/toolagent.tar.gz \
  -C /mnt/nvme/lmcache-l2-replay/traces/mooncake
test -s /mnt/nvme/lmcache-l2-replay/traces/mooncake/toolagent/l2.lct
```

`conversation`도 `toolagent`와 같은 방식으로
`mooncake/conversation.tar.gz`를 다운로드하고 압축 해제합니다. archive 내부
구조가 예상과 다르면 `tar -tzf ARCHIVE.tar.gz` 출력에서 `l2.lct` 위치를 먼저
확인한 뒤 `--trace`에 그 경로를 지정합니다.

Dataset file 목록은 다음 명령으로 확인합니다.

```bash
bash tools/artifacts/hf_trace_asset.sh list \
  --repo-id daegyu94/lmcache-storage-traces \
  --revision main
```

## 4. 단일 replay로 설치 검증

먼저 1%만 `fs_native`에 replay합니다. 최종 command와 경로를 확인한 뒤
`--dry-run`을 제거해 실행합니다.

```bash
python -m replayer.main \
  --trace /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-path /mnt/nvme/lmcache-l2-replay/kvcache/smoke \
  --output-dir outputs/replay-smoke/wildclaw \
  --trace-percent 1 \
  --dry-run
```

단일 `python -m replayer.main` 명령은 L2 경로를 자동 삭제하지 않으므로 비어 있는
전용 경로를 사용하세요. 성공하면 다음 파일이 생성됩니다.

```text
outputs/replay-smoke/wildclaw/
├── l2_prepare_manifest.json
├── l2_replay_stats.json
├── l2_replay_summary.md
├── lmcache-prepare.log
└── lmcache-replay.log
```

결과 JSON은 다음처럼 확인할 수 있습니다.

```bash
python -c "import json; print(json.dumps(json.load(open('outputs/replay-smoke/wildclaw/l2_replay_stats.json')), indent=2))"
```

### 4.1. 로컬 I/O profiler smoke test

실제 storage node 없이 profiler 연결과 agent 수집 경로만 확인하려면
`configs/profiling/local.yaml`을 사용합니다. 이 설정은 `localhost`에 SSH로
접속해 loopback interface `lo`의 counter를 1초 간격으로 수집합니다. 먼저
replay host에서 passwordless SSH가 되는지 확인합니다.

```bash
ssh localhost true
```

그 다음 짧은 replay에 `--io-profile`을 추가합니다.

```bash
python -m replayer.main --trace /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct --config configs/replayer/fs-native.yaml --l2-path /mnt/nvme/lmcache-l2-replay/kvcache/local-smoke --output-dir outputs/replay-local-profile/wildclaw --trace-percent 1 --io-profile configs/profiling/local.yaml
```

이 예제는 network counter만 수집합니다. local NVMe I/O도 확인하려면 먼저
`lsblk -ndo NAME,TYPE`으로 실제 physical disk를 확인한 뒤
`configs/profiling/local.yaml`의 `devices`에 `/dev/...`를 추가하세요. 결과는
`outputs/replay-local-profile/wildclaw/profile/local/`과
`profile_summary.json`에서 확인합니다. `--io-profile`의 기존 호환 alias는
`--node-profile`과 `--profile`입니다.

### 4.2. 실제 storage node I/O profiling

실제 backend benchmark에서는 replay host가 SSH로 각 storage node에 profiler agent를
배포합니다. `configs/profiling/storage.yaml`은 네 개의 예시 node
(`storage_node1`~`storage_node4`)를 가리키므로, 그대로 실행하지 말고 cluster의
host, physical device, network interface로 바꿔 별도 파일에 저장합니다.

예를 들어 storage node 하나를 측정할 때 다음처럼 작성합니다.

```bash
cp configs/profiling/storage.yaml configs/profiling/storage.local.yaml
```

```yaml
sample_interval_seconds: 5
report_interval_seconds: 5
remote_tmp_root: /tmp/lmcache-tracebench-profile
ssh_user: benchmark

nodes:
  - name: storage_node1
    host: 10.0.0.11
    devices:
      - /dev/nvme0n1
    interfaces:
      - bond0
```

실제 값은 storage node에서 확인합니다.

```bash
ssh -o BatchMode=yes benchmark@10.0.0.11 true
ssh benchmark@10.0.0.11 'lsblk -ndo NAME,TYPE && ip -br link'
ssh benchmark@10.0.0.11 \
  'test -r /sys/class/block/nvme0n1/stat && test -r /sys/class/net/bond0/statistics/rx_bytes'
```

`ssh -o BatchMode=yes`가 실패하면 replay를 시작하기 전에 SSH key, user, host와
port를 고칩니다. 다른 SSH port를 사용하면 node 아래에 `port: 2222`를 추가합니다.
Profiler agent는 원격 Python이나 LMCache를 요구하지 않지만, 원격 host에 `bash`,
`awk`, `cat`, `date`, `sleep`, `readlink`와 Linux sysfs가 있어야 합니다. Bond
interface와 slave interface를 동시에 넣지 말고, partition device 대신 실제
physical device를 지정하세요.

이제 1% 단일 replay에서 profiling 연결과 결과 경로를 확인합니다.

```bash
python -m replayer.main \
  --trace /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-path /mnt/nvme/lmcache-l2-replay/kvcache/profile-smoke \
  --output-dir outputs/replay-profile-smoke/wildclaw \
  --trace-percent 1 \
  --io-profile configs/profiling/storage.local.yaml
```

성공하면 다음 파일을 확인합니다.

```text
outputs/replay-profile-smoke/wildclaw/
├── profile_preflight.json
├── profile_summary.json
└── profile/storage_node1/
    ├── disk.tsv
    ├── network.tsv
    ├── samples.jsonl
    ├── summary.json
    └── agent.log
```

예를 들어 device별 I/O를 표 형태로 확인합니다.

```bash
column -ts $'\t' \
  outputs/replay-profile-smoke/wildclaw/profile/storage_node1/disk.tsv | less -S
python -m json.tool outputs/replay-profile-smoke/wildclaw/profile_summary.json
```

실험에 사용할 profiling 조건을 고정했다면 speedup sweep에도 같은 config를
전달합니다. Profiler는 각 replay case마다 새로 시작·종료되며 preparation I/O는
측정 구간에서 제외됩니다.

```bash
bash benchmarks/replayer/replay_speed_sweep.sh \
  --trace /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /mnt/nvme/lmcache-l2-replay/kvcache/wildclaw \
  --output-root outputs/replay-l2/wildclaw \
  --speedups 1,2,4 \
  --io-profile configs/profiling/storage.local.yaml
```

각 case의 `profile/`과 `profile_summary.json`을 `l2_replay_stats.json`의 replay
latency/throughput과 함께 비교합니다. `profile_summary.json`은 node별 counter를
합친 결과이고, 원본 시간 구간은 각 node의 `disk.tsv`와 `network.tsv`에 있습니다.
Replay client network까지 보고 싶으면 config에 `replay_node`를 추가하되, storage
node와 같은 interface를 중복 집계하지 않도록 합니다.

## 5. Speedup sweep

`--speedups`만 변경하고 trace, backend config, worker 수, direct I/O, replay host와
profiling 조건을 고정합니다. Script는 각 case 전에 같은 `--l2-root`를 reset합니다.
`--output-root`를 생략하면 UTC timestamp가 포함된 새 결과 경로를 만듭니다.

```bash
bash benchmarks/replayer/replay_speed_sweep.sh \
  --trace /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /mnt/nvme/lmcache-l2-replay/kvcache/wildclaw \
  --output-root outputs/replay-l2/wildclaw \
  --speedups 1,2,4,8
```

실제 결과 root는 `outputs/replay-l2/wildclaw-<UTC timestamp>/`가 됩니다.
`$(date ...)`를 직접 붙일 필요가 없습니다. 결과 root의
`sweep-summary.json`, `sweep-summary.csv`와 각
`x<SPEEDUP>/l2_replay_stats.json`, `x<SPEEDUP>/l2_replay_summary.md`를
확인합니다. Latency와 throughput 외에 `schedule_lag`, dependency/buffer wait와
`drain_time`을 함께 비교하세요.

## 6. Workload sweep

Trace는 다음 구조로 준비합니다.

```text
/mnt/nvme/lmcache-l2-replay/traces/tensormesh/
├── gaia/l2.lct
└── wildclaw/l2.lct
```

현재 launcher의 기본 trace 이름은 아직 이전 형식이므로 `--trace-name l2.lct`를
반드시 지정합니다. 순수 workload 차이만 비교하려면 speedup을 1로 고정합니다.

```bash
bash benchmarks/replayer/replay_workload_sweep.sh \
  --trace-root /mnt/nvme/lmcache-l2-replay/traces/tensormesh \
  --trace-name l2.lct \
  --config configs/replayer/fs-native.yaml \
  --workloads gaia,wildclaw \
  --l2-root /mnt/nvme/lmcache-l2-replay/kvcache/workloads \
  --speedups 1
```

Workload와 speedup을 함께 행렬로 실행하려면 `--speedups 1,2,4,8`로 바꿉니다.
Workload마다 operation 수와 byte 수가 다르므로 elapsed time만 비교하지 말고
read/write byte, operation count, submission window와 prepare byte도 기록합니다.

## 7. Backend sweep

각 `--backend-spec`은 `NAME=CONFIG@L2_PATH` 형식입니다. `NAME`은 결과 label이고
실제 backend는 config와 mount가 결정됩니다. 각 path는 해당 backend의 별도
benchmark 전용 directory여야 합니다.

```bash
bash benchmarks/replayer/replay_backend_sweep.sh \
  --trace /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct \
  --backend-spec 'xfs=configs/replayer/fs-native.yaml@/mnt/xfs/lmcache-replay' \
  --backend-spec 'pnfs=configs/replayer/fs-native.yaml@/mnt/pnfs/lmcache-replay' \
  --backend-spec '3fs=configs/replayer/nixl-hf3fs.yaml@/mnt/3fs/lmcache-replay' \
  --experiment speedup \
  --speedups 1,2,4,8
```

각 mount가 의도한 backend인지, worker/concurrency와 direct I/O 정책이 비교 가능한지
확인하세요. Backend와 concurrency가 동시에 바뀌면 backend 차이로만 해석할 수
없습니다.

## 8. L1 size 옵션의 제한된 사용

다음 launcher는 `l2.lct`에도 실행되지만 direct L2 replay의 cache hit/miss 실험은
아닙니다. L1-backed replay buffer 용량이 dispatch backpressure와 OOM에 미치는
영향을 확인할 때만 사용합니다.

```bash
bash benchmarks/replayer/replay_l1_size_sweep.sh \
  --trace /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-root /mnt/nvme/lmcache-l2-replay/kvcache/l1-buffer \
  --l1-sizes 20,40,80,160 \
  --speedup 1
```

Serving 환경의 L1 capacity에 따른 hit/miss, eviction 또는 L2 workload 변화를
측정하려면 L1 size별로 실제 workload를 다시 record해야 합니다. 이 실험은 동일한
L2 operation stream을 replay하는 backend benchmark와 분리합니다.

## 9. 고정변수 체크리스트

| Sweep | 변경 변수 | 고정할 변수 |
| --- | --- | --- |
| Backend | adapter, target mount | trace, speedup, trace percent, worker/concurrency, direct I/O, replay host, cold-cache 상태 |
| Speedup | `--speedups` | backend, trace, worker/concurrency, replay buffer, profiling, background load |
| Workload | `l2.lct` | backend, speedup, trace percent 정책, worker/concurrency, replay host, cold-cache 상태 |
| L1 buffer | `--l1-sizes` | backend, trace, speedup, worker/concurrency; cache hit 실험으로 해석하지 않음 |

모든 비교에서 trace hash와 LMCache commit, host/CPU/NUMA/memory/kernel, mount option,
config, 실행 시각, background workload, 반복 횟수와 순서를 기록합니다. 최소 3회
반복하고 사전에 정한 warm-up 및 제외 규칙을 모든 backend에 동일하게 적용하세요.
기본 sweep은 cold target을 만들기 위해 case마다 L2 path를 reset합니다.

## 10. 실패 시 확인 순서

1. `bash scripts/setup_runtime.sh --profile replayer --check`
2. Trace가 존재하고 크기가 0보다 큰지 확인
3. `--dry-run`으로 최종 command와 target path 확인
4. Case의 `lmcache-prepare.log` 확인
5. Case의 `lmcache-replay.log` 확인
6. 상위 `sweep.log`, `workload-sweep.log` 또는 `backend-sweep.log` 확인

대표 오류는 다음처럼 해석합니다.

- `missing end marker`: record가 정상 종료되지 않은 불완전한 trace입니다.
- `trace file not found`: workload sweep의 `--trace-name l2.lct`와 directory 구조를
  확인합니다.
- `Invalid argument`와 direct I/O 오류: target filesystem의 `O_DIRECT` 지원과
  alignment를 확인합니다.
- NIXL/HF3FS 초기화 오류: cluster의 NIXL/HF3FS 설치와 backend config를 확인합니다.
- Output case가 비어 있지 않다는 오류: 기존 결과를 보존하고 새 output root를
  사용합니다.

Metric과 event 의미는 [Replayer guide](replayer.md)와 [L2 tracing specification](l2-tracing.md)을 참고하세요.
