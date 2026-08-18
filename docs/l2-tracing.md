# [구현 명세] L2 어댑터 수준의 trace 기록 및 causal replay

## 상태

`implemented`

## 기준 버전

이 문서는 LMCache `v0.5.3` 태그 이후 7개 커밋이 추가된 현재 브랜치 `priv/dg/l2-tracing`의 구현을 기준으로 합니다.
정확한 기준 커밋은 `68551f01` (`feat(trace-replay): report L2 operation latency`)이며, `git describe` 기준 버전은 `v0.5.3-7-g68551f01`입니다.
따라서 LMCache `v0.5.3` 릴리스 자체와는 L2 trace/replay 동작이 다를 수 있습니다.

## 먼저 읽기

이 문서는 L2 backend의 동작을 기록하고 다른 backend에서 같은 요청 순서를 재생하는 방법을 설명합니다.
원본 KV 데이터나 source의 L1 상태를 복사하는 문서가 아닙니다. Tracebench의
실행 wrapper와 결과 경로는 [Recorder guide](recorder.md)와
[Replayer guide](replayer.md)를 참고하세요.

### 한눈에 보기

1. **Record:** source에서 L2 adapter 요청을 `l2.lct`에 기록합니다.
2. **Replay:** trace의 요청을 target L2 adapter 하나에 직접 제출합니다.
3. **Measure:** 제출 시간, 대기 시간, store/load task latency, byte throughput 및 outcome 차이를 JSON으로 확인합니다.
4. **판정:** outcome mismatch는 metric/warning으로 남고, trace 구조 오류는 replay를 중단합니다.

```text
source process
      |
      v
  l2.lct trace
      |
      v
target L2 adapter  --->  l2_replay_stats.json
```

### 용어

- **Source:** trace를 생성한 원래 실행 환경입니다.
- **Target:** trace를 재생하면서 성능을 측정할 L2 adapter입니다.
- **Submission:** adapter task를 제출한 시점입니다.
- **Completion:** store/lookup/load task의 완료와 결과가 기록된 시점입니다.
- **Dependency:** 앞선 task가 끝나야 다음 task를 제출할 수 있는 causal 관계입니다.

### 실행 가이드

Trace 생성 명령과 output은 [Recorder guide](recorder.md), target adapter와 replay
옵션은 [Replayer guide](replayer.md)를 사용합니다. 이 문서는 실행 절차를 반복하지
않고 event, dependency, initial object preparation과 validity contract만
정의합니다.

## 문제와 목적

StorageManager-level trace는 `StorageManager`의 상위 lifecycle event를
재생합니다. L1과 L2의 전체 상태나 실제 L2 adapter operation을 고정해 저장하는
trace가 아닙니다.

Replay에서는 L1 object reserve, lock, eviction, store와 prefetch 결정을 현재
cache 상태에서 다시 계산합니다. L2 backend latency가 async completion과 lock
해제 시점을 바꾸면 후속 lookup/load/store의 제출 여부와 순서도 달라질 수 있습니다.
따라서 같은 StorageManager trace라도 target별 L2 operation 수, byte와 순서를
동일하게 보장하기 어렵습니다.

이 방식은 StorageManager와 L1 semantics를 평가할 때는 유용하지만, backend에
동일한 L2 workload를 제공하는 통제 실험에는 맞지 않습니다. Tracebench는
LMCache `priv/dg/l2-tracing` branch에서 실제 adapter task를 `l2.lct`에 기록합니다.

## 구현 범위

실제 adapter task submission과 completion result를 기록하고, 기록된 task를 target L2 adapter에 직접 제출하는 L2 adapter-level trace mode를 구현합니다.
이 모드의 목적은 L2 backend 동작을 분리하여 평가하는 것이며, source의 L1 상태를 재현하거나 평가하지 않습니다.

구현된 record/replay 경계는 다음과 같습니다.

```text
vLLM -> StorageManager/L1 -> async controller -> L2 adapter -> backend
                                             ^ record/replay boundary
```

Trace level 선택과 실행 명령은 [Recorder guide](recorder.md)와
[Replayer guide](replayer.md)를 참고합니다.

### Event 및 metadata

`L2TraceRecorder`는 실제 adapter operation의 submission 및 completion event를 기록합니다.

submission event는 다음 operation에 대해 기록됩니다.

- store
- lookup-and-lock
- load
- unlock
- delete

Completion event는 store, lookup-and-lock, load에 대해서만 기록합니다.
Store completion은 batch 전체의 aggregate `succeeded_count`, `failed_count` 및 `bytes_transferred`를 기록하며, per-object store 성공/실패 index는
기록하지 않습니다.
`key_count_per_salt`는 store submission과 성공한 store completion에 포함되는 보조 metadata입니다.
Lookup completion은 `hit_indices`, load completion은 `success_indices`를 기록합니다.
Unlock과 delete는 submitted event만 기록하고 completion/result는 기록하지 않습니다.
두 operation은 replay에서 제출 횟수만 집계합니다.

Workload shape을 재현하고 dependency를 도출하는 정보는 다음과 같이 기록합니다.

- trace 시작 시점 기준의 monotonic submission 및 completion timestamp
- submission/completion의 adapter ID 및 task ID(해당 operation에 존재하는 경우)
- lookup/load/unlock을 연결하기 위한 request ID
- `cache_salt`를 포함한 완전한 object-key identity
- key list, object byte size 및 lookup layout metadata
- store completion의 aggregate 성공/실패 개수 및 전송 byte 수
- lookup의 `hit_indices` 및 load의 `success_indices`
- unlock/delete의 submitted event와 key 목록(별도 completion/result 없음)

L2 trace는 원본 KV payload를 기록하지 않으므로, 기록된 object size에 맞는 replay buffer를 사용해야 합니다.

#### `cache_salt` 보존

`cache_salt`는 별도의 trace-wide 값이 아니라 각 `ObjectKey` identity의 일부로 기록합니다.
따라서 store, lookup, load, unlock 및 delete의 `keys`에 포함된 모든 `ObjectKey`는 `cache_salt`까지 직렬화하며,
replay와 초기 read object 준비에서도 복원된 key를 변경 없이 target adapter에 전달합니다.
나머지 필드가 같더라도 `cache_salt`가 다른 object는 서로 다른 key이므로 dependency 도출과 준비 object 분류에서도
합쳐져서는 안 됩니다.

`cache_salt` 필드가 없는 기존 trace는 하위 호환을 위해 빈 문자열(`""`)로 해석합니다.
다만 기존 trace에서 원래 salt를 복구할 수는 없으므로, `cache_salt`를 사용하는 workload는 이 필드를 지원하는 버전으로 새로 기록해야 tenant isolation을 보존할 수 있습니다.

Trace 마지막에는 recorder 및 event queue의 drop count를 포함하는 completeness marker가 있어야 합니다.
Marker가 없거나 중복 marker가 있거나 drop count가 0이 아니면 replay를 거부합니다.
L2 plan은 trace header가 `level="l2"`인지와 source의 store/lookup/load task가 둘 이상의 adapter를 사용하지 않는지도 확인하며, replay target에는 정확히 하나의 adapter가 있어야 합니다.

### 초기 read object 준비

Read object는 모두 미리 만들어 두지 않습니다. 각 object의 첫 lookup 결과와 trace에 기록된 store 시점을 보고 다음처럼 처리합니다.

1. **Lookup 전에 store가 끝난 hit**

   Source에서 lookup이 hit였고, 같은 object를 저장한 store가 lookup 제출 전에 끝났다면, replay lookup이 그 store를 기다리도록 합니다.
   따라서 object를 미리 만들지 않고, replay에서 store를 먼저 실행합니다.

2. **Lookup 중에 store가 끝난 hit**

   Store가 lookup 제출 후, lookup 완료 전에 끝났다면 source에서도 두 작업이 겹쳐 실행된 것입니다. |
   이 겹침을 재현하기 위해 lookup에 store dependency를 추가하지 않으며, object도 미리 만들지 않습니다.

3. **Trace 시작 전부터 있었던 것으로 보이는 hit**

   Lookup은 hit였지만 lookup이 끝나기 전까지 해당 object를 성공적으로 저장한 store completion이 trace에 없다면, object가 trace 시작 전에 이미 존재했다고
   봅니다.
   이 경우 측정 전에 같은 key와 byte size의 synthetic object를 target에 저장합니다.
   원본 KV payload를 복사하는 것은 아닙니다.
   Lookup에는 trace의 layout metadata를 사용하지만, preparation store는 byte-size replay buffer로 수행합니다.

4. **Lookup miss**

   Source에서 miss였던 object는 미리 만들지 않습니다.

Store completion은 batch 전체의 성공 개수만 기록하고 object별 성공 여부는 기록하지 않습니다.
따라서 `succeeded_count > 0`인 store task는 그 task의 모든 key가 성공했을 가능성이 있는 것으로 취급합니다.
한 batch에서 일부 key만 성공했는지는 trace만으로 알 수 없습니다.

준비 I/O는 측정 replay 전에 끝내며 replay 통계에는 포함하지 않습니다.
현재 L2 replay CLI는 별도의 storage-node profiling을 수행하지 않습니다.

### Causal, timestamp-scaled replay

Replay는 기록된 key 및 request lifecycle을 보존하는 데 필요한 dependency만 기다려야 합니다.
모든 task 뒤에 global completion barrier를 두어서는 안됩니다.

```text
store(A) -------- completes
                  `-> lookup(A) -> load(A) -> unlock(A)

store(B) ---------------- completes    # independent; continues concurrently
lookup(C) ---- completes               # independent; continues concurrently
```

Dependency는 source에서 실제로 필요했던 순서를 replay에서도 지키기 위한 최소한의 연결입니다.
핵심은 **store가 lookup보다 먼저 끝났는지**, 아니면 **lookup이 진행 중일 때 store가 끝났는지**를 구분하는 것입니다.

### Store와 lookup의 관계

#### Store가 lookup보다 먼저 끝난 경우

```text
Source
time ──────────────────────────────────────────────>
      store(A) 제출 ── store(A) 완료 ── lookup(A) 제출 ── lookup(A) 완료
                                      └──── source hit

Replay
      store(A) 제출 ── store(A) 완료 ── lookup(A) 제출
                                      └──── dependency
```

Source lookup이 hit이고, lookup completion 전에 완료된 successful store가 있다면 후보로 수집합니다.
그중 lookup submission보다 먼저 완료된 store가 있을 때는 가장 최근 store를 선택합니다.
Replay lookup은 이 store의 target completion을 기다린 뒤 제출합니다.

#### Lookup 중에 store가 끝난 경우

```text
Source
time ──────────────────────────────────────────────>
      store(A) 제출 ── lookup(A) 제출 ── store(A) 완료 ── lookup(A) 완료
                       └──── 두 작업이 겹쳐 실행됨

Replay
      store(A) 제출 ── lookup(A) 제출
                       └──── store 완료를 기다리지 않음
```

Store가 lookup submission 뒤, lookup completion 전에 완료되었다면 source의 동시성을 보존하기 위해 lookup에 store dependency를 추가하지 않습니다.
Replay lookup은 timestamp schedule에 도달하면 store completion을 기다리지 않고 제출합니다.

### 하나의 read request 안의 관계

```text
lookup(request A) 완료 ──> load(request A) 완료 ──> unlock(request A) 제출
          │                         │
          └─────────────────────────┘
             load가 없으면 lookup 완료 후 unlock
```

- Load는 같은 request의 lookup task completion을 기다립니다.
- Unlock은 같은 request의 load completion을 기다립니다. Load가 제출되지 않았다면 lookup completion을 기다립니다.
- Delete에는 추가 dependency를 만들지 않습니다. 이 관계에 포함되지 않는 다른 task도 원래 timestamp schedule에 따라 계속 제출합니다.

Store dependency는 target store의 성공 여부가 아니라 completion 여부로 해제됩니다.
Target lookup의 hit/miss는 제출 전에 알 수 없으므로 dependency 파생과 prepare 판단에는 source trace의 `hit_indices`를 사용합니다.
Overlap을 유지한 결과 target lookup이 miss가 되더라도 outcome 차이로 기록하며 replay 실패로 처리하지 않습니다.

Timestamp normalization과 `speedup` 목표 시각 계산은
[Replayer guide의 timestamp scaling](replayer.md#how-timestamp-scaling-works)을
기준으로 합니다. 이 절의 causal dependency는 timestamp target보다 늦게
operation을 제출하게 만들 수 있지만, 관계없는 task에 global barrier를 만들지 않습니다.

### Replay 정확성, outcome 및 출력

Replay 성공 여부는 source/target outcome 일치가 아니라 다음 조건으로 판단합니다.

- trace header와 completeness marker가 유효한가
- 선택된 모든 operation이 제출되었는가
- 기록된 causal dependency를 지키면서 target async task가 drain되었는가
- replay 중 내부 오류나 drain timeout이 발생하지 않았는가

Source operation outcome과의 불일치는 필수 실패 조건이 아닙니다.
Backend가 다르면 서로 다른 결과가 나올 수 있으며, 이 차이는 비교 metric으로만 기록합니다.
`outcome_matches_source`가 `false`여도 replay command는 성공 상태로 반환하고 warning을 출력합니다.

Outcome comparison은 현재 다음 범위에서만 수행합니다.

- `store`: source `succeeded_count > 0`와 target store task 성공 여부 비교
- `lookup_task`: source `hit_indices`와 target hit index list 비교
- `load_task`: source `success_indices`와 target success index list 비교
- `unlock`, `delete`: completion/result가 없으므로 비교하지 않고 제출 횟수만 기록

Source와 target의 상세 per-object 결과를 별도 출력하지 않습니다.
결과 차이는 replay stats의 outcome metric으로 집계합니다.

`outcome_matches_source`는 비교 결과 요약이며, mismatch가 있어도 replay를 무효화하지 않습니다.

Replay stats와 prepare manifest의 field, 처리량 계산과 `trace_percent` 선택 수는
[L2 replay metric guide](l2-replay-metrics.md)를 기준으로 합니다. 이 specification은
trace validity, dependency와 outcome comparison contract만 정의합니다.
