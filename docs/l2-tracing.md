# LMCache record/replay와 L2-level tracing

이 문서는 기존 LMCache storage trace가 무엇을 재현하는지, 왜 서로 다른
record/replay 환경에서 L1 lifecycle이 어긋날 수 있는지, 그리고 backend I/O
비교를 위해 도입한 L2-level trace가 어떤 경계와 의미론을 갖는지를 설명합니다.

실행 방법과 config 옵션은 [Recorder guide](recorder.md)와
[Replayer guide](replayer.md)를 authoritative reference로 사용합니다.

## 1. 결론

기존 `storage` trace는 LMCache `StorageManager`의 public API workload를
재현합니다. 따라서 L1 eviction, lock, hit/miss, 비동기 controller의 상태가
replay 환경에서 record 환경과 달라질 수 있습니다. `skip_l1`과 L1 capacity를
같게 설정해도 이 동적 상태까지 같아지는 것은 아닙니다.

이 trace를 사용해 pNFS, 3FS, local filesystem 같은 backend의 I/O를 비교하면
backend 차이와 L1 상태 차이가 결과에 섞일 수 있습니다. 특히 다음과 같은
warning이 발생할 수 있습니다.

```text
finish write on non-write-locked key
finish read on non-existing key
```

따라서 목적을 분리합니다.

| 측정 목적 | 사용할 trace | 재현하는 것 |
| --- | --- | --- |
| serving/L1 동작과 StorageManager lifecycle | `storage.lct` | `reserve_*`, `finish_*`, prefetch 호출과 L1 상태 전이의 workload |
| L2 backend I/O와 storage-node 자원 사용량 | `l2.lct` | L2 adapter task, key reuse, batch/byte size, 제출·완료 event와 replay에서 파생한 dependency |

`l2.lct`는 L1을 재현하지 않는다는 뜻이 아니라, **L1을 workload 생성기의
원인으로 사용하고 backend 비교 시에는 L2 adapter 경계에서 다시 시작한다**는
뜻입니다.

## 2. 기존 LMCache storage record/replay

### 2.1 기록 경계

기존 `--trace-kind storage`는 low-level filesystem `read`/`write` 또는
syscall을 기록하지 않습니다. `StorageManager` public API의 진입 시점과 인자를
기록합니다.

대표적인 record는 다음과 같습니다.

| API | 역할 |
| --- | --- |
| `reserve_write(keys, layout_desc, mode)` | L1 object와 write lock 예약 |
| `finish_write(keys)` | L1 write 완료 및 비동기 L2 store 시작 |
| `submit_prefetch_task(...)` | L1 miss object의 L2 prefetch 제출 |
| `read_prefetched_results.__enter__/__exit__(keys)` | prefetch 결과의 read-lock lifecycle |
| `finish_read_prefetched(keys, extra_count)` | read access 종료 및 lock 해제 |

각 `.lct` record에는 대략 다음 정보가 있습니다.

- trace 시작 기준의 relative monotonic timestamp
- wall-clock timestamp
- fully-qualified API name
- codec으로 직렬화한 입력 인자
- record 시점의 `StorageManagerConfig`와 config digest

Recorder는 API **진입 시점**에 event를 발행합니다. API의 반환값, exception,
완료 시각은 record에 포함되지 않습니다. EventBus가 concurrent producer의
event를 파일에 선형화하므로 replay는 그 파일 순서를 사용하지만, 원본
thread/request identity나 내부 worker의 실행 순서를 복원하지는 않습니다.

### 2.2 replay 동작

Replay는 trace header의 config를 그대로 복원하지 않고 replay-side config로
새 `StorageManager`를 생성합니다. 이후 record를 파일 순서대로 single-threaded
dispatch하고, 각 record의 `t_mono / speedup`을 목표 제출 시각으로 사용합니다.

```text
target_i = replay_start + record_i.t_mono / speedup
```

`finish_write` 뒤의 L2 store와 `submit_prefetch_task` 뒤의 load는 비동기입니다.
따라서 API dispatch가 끝났다는 것과 L2 I/O가 끝났다는 것은 서로 다른 사건입니다.
Replay는 마지막 record 뒤에 async store/prefetch drain을 기다리지만, 각 record
사이에 모든 L2 I/O가 완료되기를 기다리지는 않습니다.

## 3. 기존 trace의 한계

### 3.1 입력만 기록하므로 결과가 달라진다

`reserve_write`는 실행 환경의 L1 상태에 따라 성공한 key의 subset을 반환할 수
있습니다. 하지만 trace에는 반환값이 없고 `finish_write(keys)`의 입력만 남습니다.
다음과 같은 차이가 생깁니다.

```text
Record                              Replay
------                              ------
key가 없음                          key가 이미 L1에 있음
reserve_write(A, mode="new") 성공  reserve_write(A, mode="new") 실패
write-lock 획득                     write-lock 없음
finish_write(A) 성공                finish_write(A) 호출
                                    warning: non-write-locked key
```

이것은 API 순서가 `finish_write` 먼저 도착한 것이 아닙니다. API 순서는 같지만
첫 번째 호출의 결과와 L1 상태가 달라진 것입니다.

### 3.2 한 번의 차이가 뒤의 workload를 바꾼다

예를 들어 L1 capacity가 A와 B 두 object 정도이고, trace가 다음과 같다고
가정합니다.

```text
reserve(A) → finish(A)
reserve(B) → finish(B)
reserve(C) → finish(C)
reserve(A) → finish(A)
```

Record에서는 C를 넣을 때 A가 eviction되어 마지막 `reserve(A)`가 성공할 수
있습니다. Replay에서는 비동기 처리 시점이 달라 B가 eviction되고 A가 남을 수
있습니다. 그러면 마지막 `reserve(A, mode="new")`가 실패하고, 뒤의
`finish(A)`도 write-lock 없이 실행됩니다.

그 결과는 단일 warning으로 끝나지 않을 수 있습니다.

```text
eviction 대상 차이
  → reserve 결과 차이
  → finish_write 실패
  → 해당 key의 L2 store 미제출
  → 후속 prefetch hit/miss 차이
  → L1 resident set 차이 확대
  → 다음 reserve/finish/read lifecycle 추가 실패
```

`finish_write`에서 성공한 key만 L2 StoreController로 전달되므로 실패한 key의
L2 write가 자동으로 보상되지는 않습니다. `finish_read`는 반대로 기존 read lock을
해제하는 단계이므로, key가 없을 때 warning이 나더라도 그 호출 자체가 새로운
L2 read를 발생시키지는 않습니다. L2 read는 앞선 prefetch/lookup 단계에서 이미
제출되었거나 제출되지 않은 상태입니다.

### 3.3 같은 `skip_l1`과 L1 size로도 해결되지 않는 이유

`skip_l1`은 L2 store policy입니다. L1 object 예약, lock, eviction과
`StorageManager` API lifecycle을 없애지 않습니다. L1 capacity와 policy를 같게
맞추면 정적 조건은 같아지지만 다음 동적 조건은 여전히 달라질 수 있습니다.

- 시작 시 L1/L2가 비어 있는지와 warm state
- L2 lookup/load/store 완료 시각
- async queue의 backlog와 worker scheduling
- host, CPU, filesystem, network backend의 처리 속도
- API 입력만 있고 반환값·exception·payload가 없다는 trace contract

또한 `speedup`은 GPU workload나 I/O latency를 배속하지 않고 API 제출 timestamp
간격만 압축합니다. 원본보다 높은 제출률에서는 queue contention과 state
divergence가 더 빨리 나타날 수 있습니다.

그러므로 storage trace는 “원본과 같은 L1 결과를 보장하는 deterministic replay”가
아니라 “StorageManager-level storage workload replay”로 해석해야 합니다.

## 4. 왜 L2-level trace가 필요한가

이 프로젝트의 backend 실험 목표는 L1 정책 자체가 아니라 다음을 비교하는
것입니다.

- pNFS, 3FS, local filesystem 등의 aggregate replay time과 throughput
- backend가 처리하는 read/write byte 및 batch shape
- storage node의 SSD와 network bandwidth 사용량
- 요청률을 높였을 때 queueing, contention, drain이 어떻게 변하는지

이 목적에서는 record 환경의 L1 hit/miss가 replay 환경의 L1 hit/miss로 다시
계산되도록 둘 필요가 없습니다. 오히려 L1 controller를 통과하면서 달라진
요청을 backend workload 차이로 잘못 해석할 수 있습니다.

따라서 L2-level trace는 `StorageManager`보다 아래, OS syscall보다 위인
**L2 adapter task 경계**에서 기록합니다.

```text
기존 storage trace:
vLLM → StorageManager/L1 → async controller → L2 adapter → backend/syscall
       ^ record/replay 경계

L2-level trace:
vLLM → StorageManager/L1 → async controller → L2 adapter → backend/syscall
                                           ^ record/replay 경계
```

이 경계는 backend 구현이 달라도 공통인 adapter 의미론을 보존하면서, 특정
filesystem의 syscall 세부사항에 trace가 종속되는 것을 피합니다.

## 5. L2-level trace 설계

### 5.1 실제로 기록하는 이벤트

L2 trace의 `header.level`은 `l2`입니다. 각 record에는 공통으로 trace 시작 기준
`t_mono`, wall-clock `t_wall`, event 이름과 event별 metadata가 들어갑니다.
Operation을 나타내는 별도 `op` 필드는 없으며, `l2.store.submitted` 같은 event
이름이 operation을 구분합니다.

| Event | 주요 metadata |
| --- | --- |
| `l2.store.submitted` | `adapter_index`, `task_id`, `keys`, `object_sizes` |
| `l2.store.completed` | `task_id`, `succeeded_count`, `failed_count`, `bytes_transferred` |
| `l2.lookup_task.submitted` | `request_id`, `task_id`, `keys`, `layout_desc` |
| `l2.lookup_task.completed` | `request_id`, `task_id`, `hit_indices` |
| `l2.load_task.submitted` | `request_id`, `task_id`, `keys`, `object_sizes` |
| `l2.load_task.completed` | `request_id`, `task_id`, `success_indices` |
| `l2.unlock.submitted` | `request_id`, `keys` |
| `l2.delete.submitted` | `keys` |

Key 목록의 길이가 batch 크기를 나타냅니다. 실제 KV payload는 저장하지 않습니다.
따라서 replayer는 기록된 byte size에 맞는 synthetic buffer를 사용합니다. Payload
내용에 따라 compression이나 deduplication 성능이 달라지는 backend를 평가할 때는
이 trace만으로 충분하지 않습니다.

현재 `ObjectKey` codec은 `chunk_hash`, `model_name`, `kv_rank`와
`object_group_id`를 기록하지만 `cache_salt`는 기록하지 않습니다. Non-empty
`cache_salt`로 key namespace를 분리하는 workload는 서로 다른 key가 replay에서
같은 key가 될 수 있으므로 이 기능의 지원 범위 밖입니다.

Dependency ID는 trace에 직접 기록하지 않습니다. Replayer가 task ID, request ID,
key와 source completion 결과를 pre-scan하여 필요한 dependency를 파생합니다.
Trace 마지막에는 recorder와 EventBus의 drop count를 담은 종료 marker가 기록되며,
marker가 없거나 drop count가 0이 아니면 replay를 거부합니다.

### 5.2 read object prepare

Prepare 대상은 모든 read object가 아닙니다. Source에서 lookup hit가 발생했지만
그 lookup보다 앞선 successful store completion을 trace에서 찾을 수 없는 object만
record 시작 전에 이미 L2에 있던 object로 간주합니다.

```text
선행 store 없음 → lookup(A) hit → load(A)
                  ^ A를 prepare

store(B) 성공 → lookup(B) hit → load(B)
                ^ B는 prepare하지 않음

선행 store 없음 → lookup(C) miss
                  ^ C도 prepare하지 않음
```

Prepare 대상에는 같은 key와 byte size의 dummy data를 저장합니다. Trace에
`store(B) → read(B)`가 있으면 replay 측정 구간에서 store를 실행하고, 완료 뒤에
read를 진행합니다.

```text
1. source lookup hit object 수집
2. 선행 successful store가 있는 object 제외
3. 남은 object의 key/size로 dummy data 저장
4. prepare 완료 확인
5. storage-node profiler 시작
6. L2 task replay 시작
```

Tracebench replayer는 이 prepare를 measured replay 전에 자동 실행합니다. 따라서
prepare write는 `l2_replay_stats.json`과 Tracebench가 시작하는 storage-node profile
구간에 포함되지 않습니다.

### 5.3 causal replay (`causal exact`)

쉽게 말하면 **반드시 순서가 필요한 작업만 기다리고, 관계없는 작업은 원래
시간표대로 계속 제출하는 방식**입니다.

```text
store(A) ───────── 완료
                    └─ lookup(A) ── 완료 ── load(A) ── 완료 ── unlock(A)

store(B) ───────────────── 완료       # A와 무관하므로 계속 진행
lookup(C) ─── 완료                    # A와 무관하므로 계속 진행
```

`store(A)`가 느리면 `lookup(A)`만 기다립니다. `store(B)`나 `lookup(C)`까지 모두
멈추는 global barrier는 두지 않습니다. Adapter I/O는 비동기이므로 관계없는 task는
target backend queue에서 서로 겹쳐 실행될 수 있습니다.

Replayer는 trace를 pre-scan하여 다음 dependency를 파생합니다.

1. Source에서 같은 key의 successful store 뒤 lookup hit가 발생했다면
   `store completion → lookup` dependency를 둡니다.
2. 같은 request의 load는 lookup completion을 기다립니다.
3. Unlock은 같은 request의 load completion을 기다립니다. Load가 없다면 lookup
   completion을 기다립니다.
4. Delete와 위 관계가 없는 다른 task에는 추가 dependency를 만들지 않습니다.

각 task는 timestamp 조건과 dependency 조건을 모두 만족해야 제출할 수 있습니다.

```text
timestamp_target = replay_start + record.t_mono / speedup
earliest_submit  = max(timestamp_target, dependency_completion_time)
```

Replay buffer가 부족하거나 dispatch loop가 밀리면 실제 제출은 이보다 더 늦을 수
있습니다. 이 지연은 schedule lag, dependency wait와 buffer wait 통계에 반영됩니다.

여기서 `exact`는 source의 전체 실행 순서나 latency를 똑같이 복원한다는 뜻이
아닙니다. Source의 store 성공 여부, lookup hit indices와 load success indices를
target 결과와 비교하고, 다르면 outcome mismatch로 표시한다는 제한된 의미입니다.

### 5.4 앞부분만 replay하기

`--trace-percent N`은 L2 submission 개수를 기준으로 앞에서 `N%`를 선택합니다.
선택 개수는 올림하며, 선택된 task를 검증하는 completion event는 trace 뒤쪽에 있어도
사용합니다. Prepare 역시 선택된 prefix에서 필요한 object만 대상으로 합니다.

## 6. 무엇을 재현하고 무엇을 재현하지 않는가

### 재현하는 것

- L2 adapter operation과 source 제출 timestamp
- object key reuse, batch와 byte size, layout
- source의 store/lookup/load 결과
- key와 request 정보에서 파생한 필수 dependency
- dependency가 없는 비동기 task overlap

### 재현하지 않는 것

- 원본 L1 capacity, eviction 및 lock 상태
- vLLM request latency, TTFT와 GPU compute
- 원본 backend latency 및 completion 순서
- 원본의 전체 thread/request scheduling
- 실제 KV payload 내용
- `ObjectKey.cache_salt`로 구분한 key namespace
- 여러 source/target adapter나 여러 replay node의 topology

따라서 L2 replay는 다음 질문에 사용합니다.

> 기록된 adapter-level I/O 요청률과 필요한 dependency를 target backend에 주었을 때
> 제출 지연, 처리량, drain과 storage-node 자원 사용량이 어떻게 달라지는가?

이 결과만으로 L1 hit ratio, request TTFT 또는 serving throughput을 판단할 수는
없습니다. 그런 질문에는 storage trace와 실제 serving 실험이 별도로 필요합니다.

## 7. 측정과 해석

현재 `l2_replay_stats.json`의 주요 값은 다음과 같습니다.

| 값 | 의미 |
| --- | --- |
| `source_submission_window_seconds` | speedup을 반영한 source 목표 제출 구간 |
| `actual_submission_window_seconds` | target에서 실제 제출에 걸린 구간 |
| `total_replay_seconds` | 모든 task completion까지 포함한 전체 시간 |
| `drain_seconds` | 마지막 submit 이후 남은 task가 끝날 때까지의 시간 |
| `max/mean_schedule_lag_seconds` | 목표 timestamp보다 늦게 제출된 정도 |
| `max/total_dependency_wait_seconds` | 선행 task completion 때문에 기다린 시간 |
| `max/total_buffer_wait_seconds` | dependency 충족 뒤 buffer/dispatch 때문에 늦어진 시간 |
| `operations_submitted` | operation별 제출 task 수 |
| `bytes_submitted` | store/load별 제출 bytes |
| `throughput_bytes_per_second` | 제출한 store/load bytes를 전체 replay 시간으로 나눈 값 |
| `outcome_mismatches` | source와 target의 store/lookup/load 결과 차이 |

현재 이 JSON은 per-task backend service latency나 p50/p90/p99 latency를 제공하지
않습니다. SSD와 network bandwidth는 `--profile`로 별도 수집한 storage-node
`profile_summary.json`에서 확인합니다.

현재 direct L2 replay는 source trace와 target config가 각각 L2 adapter 하나만
사용하는 경우를 지원합니다.

`--speedup`은 backend I/O latency를 줄이지 않고 목표 제출 간격만 압축합니다.
Backend가 요청률을 감당하지 못하면 actual submission window, wait, schedule lag와
drain이 커져 total replay time이 더 이상 줄지 않거나 늘어날 수 있습니다. 이것이
scaled-open 실험에서 관찰하려는 saturation 신호입니다.

Backend와 speedup을 비교할 때는 case마다 별도의 빈 L2 path와 output directory를
사용해야 이전 case의 warm state가 다음 결과에 섞이지 않습니다.

## 8. 사용 흐름

한 번의 recorder 실행은 `storage` 또는 `l2` 중 하나만 기록합니다. 두 trace가 모두
필요하면 같은 workload를 각각 실행해야 합니다. 두 실행의 비동기 timing과 cache
상태가 다를 수 있으므로, 두 파일을 동일 실행의 서로 다른 view로 해석하면 안 됩니다.

```bash
# 첫 번째 workload 실행: StorageManager lifecycle
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-gaia.yaml \
  --mountpoint /MNTPNT \
  --trace-kind storage \
  --output-dir outputs/gaia-storage

# 두 번째 workload 실행: L2 adapter task
python -m recorder.main \
  --config configs/recorder/qwen3-coder-480b-tp8-gaia.yaml \
  --mountpoint /MNTPNT \
  --trace-kind l2 \
  --output-dir outputs/gaia-l2
```

Tracebench replayer는 `l2.lct` header를 감지해 prepare를 먼저 끝낸 뒤 direct L2
replay를 실행합니다.

```bash
python -m replayer.main \
  --trace outputs/gaia-l2/l2.lct \
  --config configs/replayer/fs-native.yaml \
  --l2-path /MNTPNT/lmcache-l2-replay/gaia-x1 \
  --output-dir outputs/replay/gaia-l2-x1 \
  --speedup 1 \
  --trace-percent 100
```

정리하면 `storage.lct`는 StorageManager/L1 lifecycle workload를 살펴보는 용도이고,
`l2.lct`는 그 lifecycle을 다시 계산하지 않고 backend I/O를 비교하는 용도입니다.
두 trace는 서로 다른 측정 계층이며 서로 대체하지 않습니다.
