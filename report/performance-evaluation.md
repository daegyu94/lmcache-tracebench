# LMCache L2 분산 스토리지 backend 성능 평가

> **문서 상태:** 이 문서는 LMCache L2 backend 평가의 실험 설계와 결과 서술을 함께 관리하는 초안이다.
> 현재 `report/figures/*.png`의 값은 레이아웃 검토용 dummy dataset에서 생성되었으므로,
> 실측 결과로 교체하기 전에는 backend 비교나 병목에 대한 결론의 근거로 사용할 수 없다.
> 실측이 필요한 문장은 `[실측 결과 필요]`로 표시한다.

| 항목 | 값 |
| --- | --- |
| 문서 버전 | Draft |
| 실험 기간 | `[YYYY-MM-DD ~ YYYY-MM-DD]` |
| LMCache/Tracebench commit | `[commit SHA]` |
| 작성자 | `[이름]` |

## 1. 개요

### 1.1 평가 목적과 범위

동일한 adapter-level L2 trace를 인과 순서와 timestamp plan에 따라 재생하여
`fs-native`, `3FS`, `pNFS`의 throughput, latency, storage/network 병목과
storage-node 확장성을 비교한다. 평가는 L2 adapter와 replay host·storage node의
계측에 한정하며 application end-to-end 성능으로 일반화하지 않는다.

E1과 E2는 5개 workload, 3개 backend, replay speedup `x1`, `x1.25`, `x1.5`, `x2`를 비교한다.
`SWE-bench`(`tensormesh-swebench`), `mooncake-toolagent`, `mooncake-conversation`은 원본 trace가 너무 크므로 대표 subset으로 줄여 테스트한다.
축소 비율(`trace_percent`) 또는 고정 시간 구간, checksum, operation/byte 수를 기록하고 모든 backend와 speedup에 동일한 축소 trace를 사용한다.
E3~E5는 대표 workload를 사용한다. E5에서는 `SWE-bench`의 replay speedup `x2`를
고정하고 `3FS`와 `pNFS`의 storage node 수 `1..6`을 비교한다.

### 1.2 주장과 증거의 연결

| 주장 | 증거 |
| --- | --- |
| Workload/backend별 burst 처리 차이 | E1, 그림 1–2: interval throughput과 변동성 |
| Backend별 saturation knee | E2, 그림 3: throughput gain과 p99/lag |
| Tail latency의 지배 원인 | E3, 그림 4: task percentile과 replay delay |
| Storage/network 병목 | E4, 그림 5–6: throughput과 동시간대 utilization |
| Storage-node 확장성 | E5, 그림 7: throughput gain, p99, utilization, imbalance |

### 1.3 핵심 지표

| 범주 | 지표 |
| --- | --- |
| Throughput | 시간별 L2 read/write GB/s, x1 대비 gain |
| Latency/replay | Read/write task latency p50·p90·p99, schedule lag, dependency wait, buffer wait |
| Resource/scale-out | Storage/network utilization p95, node별 imbalance, scaling efficiency |

반복 실험은 case별 중앙값과 95% bootstrap confidence interval로 보고한다.
서로 다른 trace 길이의 결과를 하나의 평균으로 합치지 않으며, 입력 trace·축소 조건·backend 설정이
같은 case끼리만 비교한다. 입력 metric이 없으면 다른 metric으로 대체하지 않고 `N/A`로 표시한다.

### 1.4 분석 용어와 판정 규칙

이 문서에서 `throughput`은 replay가 제출한 L2 bytes를 측정 구간의 wall-clock으로
나눈 값이며, read/write throughput과 aggregate throughput을 구분해 기록한다.
`task latency`는 adapter task의 submission부터 completion까지다. `replay delay`는
target 제출 시각부터 실제 dispatch까지의 지연을 진단하며 task latency에 포함되는
adapter service time과 같은 값이 아니다. `queueing`은 offered load가 증가하는 동안
throughput 증가는 둔화되고 submission window, schedule lag 또는 tail latency가 증가하는
상태를 뜻한다. `saturation knee`는 이 변화가 처음 함께 관찰되는 speedup case로
정의하며, 단순히 가장 높은 throughput을 보인 case를 knee로 부르지 않는다. 상세 field
정의와 단위 변환은 [L2 replay metric guide](../docs/l2-replay-metrics.md)를 따른다.

## 2. 실험 맥락

### 2.1 L2 trace와 replay 모델

LMCache의 `StorageManager` 수준 trace는 L1 reserve, lock, eviction과 async completion을
현재 target 상태에서 다시 계산한다. 따라서 source와 target의 L1 상태나 L2 latency가
달라지면 downstream L2 operation의 제출 여부와 순서가 달라질 수 있다. 이 방식은
StorageManager 동작을 평가하는 데는 유용하지만, backend마다 동일한 L2 부하를 제공해야
하는 본 비교에는 적합하지 않다.

본 평가는 adapter-level L2 trace인 `l2.lct`를 사용한다. 각 backend에는 같은 adapter
operation sequence와 timestamp plan을 전달하고, source에서 실제로 필요했던 causal
dependency만 보존한다. 독립 operation에는 global barrier를 추가하지 않는다. Trace에는
원본 KV payload가 포함되지 않으므로 trace 시작 전에 존재했던 read object는 기록된 key와
byte size에 맞는 synthetic object로 준비하고, preparation I/O는 측정에서 제외한다.

Event 범위, dependency, object preparation과 replay validity contract는
[L2 tracing guide](../docs/l2-tracing.md), 결과 field와 판정 기준은
[L2 replay metric guide](../docs/l2-replay-metrics.md)를 따른다.

### 2.2 L2 storage backend

본 보고서에서 비교하는 backend는 replay host의 local baseline과 distributed backend로
나눈다. 공정한 비교를 위해 최종 결과에는 adapter, filesystem 또는 storage system,
mount option, storage-node topology를 함께 기록한다.

| 보고서 표기 | 비교 구성 | 실험에 기록할 설정 |
| --- | --- | --- |
| `fs-native` | LMCache `fs_native` adapter와 replay host의 local storage를 사용하는 baseline | XFS device, mount option, capacity: `[실험 설정 필요]` |
| `3FS` | NIXL/HF3FS adapter로 접근하는 distributed filesystem | adapter/version, storage node, striping/replication, mount와 topology: `[실험 설정 필요]` |
| `pNFS` | `fs_native` 또는 NIXL `POSIX` adapter로 접근하는 pNFS data path | adapter/version, NFS/mount option, metadata/data server와 topology: `[실험 설정 필요]` |

`fs-native`는 local baseline이며 3FS·pNFS와 동일한 durability나 failure model을
제공한다고 해석하지 않는다. 따라서 backend 간 비교의 주장은 동일한 L2 operation과
offered load를 처리하는 비용·지연·확장성에 한정한다. 3FS와 pNFS의 구체적인 adapter,
버전, storage node/device 수, replication·striping 정책과 network topology가 확정되지
않으면 해당 비교 결과는 `[실측 결과 필요]`로 남긴다.

### 2.3 Workload와 trace replay

TensorMesh의 `GAIA`와 `WildClaw`, Mooncake의 `ToolAgent`와 `Conversation`은
workload phase와 request pattern의 차이를 비교하기 위해 사용한다. `SWE-bench`는
storage-node scaling case의 대표 workload로 선택한다. 각 workload의 object size,
read/write mix, request 수와 operation 수는 trace metadata를 기준으로 보고하며,
metadata가 삽입되기 전에는 workload 특성에 대한 정량적 해석을 하지 않는다.

| Suite | Workload | Trace 표기 | 결과에 기록할 trace 정보 |
| --- | --- | --- | --- |
| TensorMesh | GAIA | `tensormesh-gaia` | request/operation 수, object size, read/write mix: `[trace metadata 필요]` |
| TensorMesh | WildClaw | `tensormesh-wildclaw` | request/operation 수, object size, read/write mix: `[trace metadata 필요]` |
| TensorMesh | SWE-bench | `tensormesh-swebench` | trace 축소 비율 또는 시간 구간, checksum: `[trace metadata 필요]` |
| Mooncake | ToolAgent | `mooncake-toolagent` | trace 축소 비율 또는 시간 구간, checksum: `[trace metadata 필요]` |
| Mooncake | Conversation | `mooncake-conversation` | trace 축소 비율 또는 시간 구간, checksum: `[trace metadata 필요]` |

동일 workload의 backend·speedup 비교에는 같은 `.lct` 파일과 같은 축소 조건
(`trace_percent` 또는 고정 시간 구간)을 사용한다. Replay의 `--speedup s`는 source
submission 간격을 `s`로 나누는 offered-load 조절이며, target adapter의 I/O latency를
인위적으로 줄이는 옵션이 아니다. 정확한 timestamp scheduling은
[Replayer guide](../docs/replayer.md#how-timestamp-scaling-works)를 따른다.

## 3. 실험

### 3.1 실험 설정 및 계측

재현 가능한 비교를 위해 다음 설정을 결과와 함께 기록한다.
Storage node 수와 network link rate가 비어 있으면 cluster aggregate bandwidth와 network utilization을 재현할 수 없으므로, 해당 분석은 `[실험 설정 필요]`로 남긴다.

#### Software

| 항목 | 설정값 |
| --- | --- |
| OS/kernel | `[실험 설정 필요: OS/kernel]` |
| LMCache/Tracebench | `[실험 설정 필요: version, commit]` |
| NIXL/3FS/NFS client | `[실험 설정 필요: version]` |

#### Replay host

| 항목 | 설정값 |
| --- | --- |
| CPU / memory / NUMA | `[실험 설정 필요]` |
| GPU model / count | `[실험 설정 필요]` |
| Producer server 수 | `[실험 설정 필요]` |
| NIC / link rate | `[실험 설정 필요: NIC/link rate]` |

#### Storage 및 filesystem

| 항목 | 설정값 |
| --- | --- |
| Storage node 수 / device 수 | `[실험 설정 필요]` |
| Device model / capacity | `[실험 설정 필요]` |
| Storage network topology | `[실험 설정 필요: switch/link rate/bonding]` |
| Local filesystem / mount option | `XFS / [실험 설정 필요: mount option]` |
| pNFS mount option | `[실험 설정 필요]` |

#### Replay 및 profiling

| 항목 | 설정값 |
| --- | --- |
| L1 size / worker 수 | `[실험 설정 필요]` |
| Direct I/O / alignment | `[실험 설정 필요]` |
| Profiling node roles | replay node + 모든 storage node |
| Profiling sample/report interval | `5 s / 5 s` |

#### Backend ceiling calibration (E0)

E0에서는 대표 trace인 `SWE-bench`와 `Conversation`의 object size와 read/write mix를
반영한 microbenchmark로 각 backend의 effective ceiling을 측정한다. 동일한 host와
network 경로에서 direct-I/O sequential, random, mixed read/write workload를 실행하고
queue depth를 단계적으로 높인다. Peak GB/s, IOPS, p99 latency, CPU utilization과
storage/network utilization을 기록한다.

Calibration은 application 성능을 주장하기 위한 실험이 아니라 두 가지 기준선을 제공한다.

- Replay throughput이 backend의 물리적 처리 범위를 벗어나지 않는지 확인한다.
- Resource utilization이 낮은데 throughput이 plateau에 도달한 경우 adapter, metadata,
  CPU 또는 submission path 병목을 구분한다.

분산 backend의 replication과 durability 설정은 replay 실험과 동일하게 유지한다.
Local `fs-native` ceiling은 distributed backend와 동등한 기능의 비교값이 아니라
local reference로 해석한다. 본문에는 peak 값과 측정 조건을 요약하고, 전체 curve는
appendix에 둔다.

> **[실측 결과 필요]** E0의 backend별 ceiling, 측정 조건과 replay throughput 대비
> headroom을 삽입한다. 이 값이 없으면 이후의 plateau 원인을 물리적 한계와 software
> path 중 하나로 단정하지 않는다.

논리적인 측정 경로는 다음과 같다.

```text
L2 trace -> replay host -> LMCache L2 adapter -> local FS / 3FS / pNFS
                    |                         |
                    |                         +-> storage node disk.tsv
                    +-> l2_replay_stats.json  +-> storage node network.tsv
                    +-> l2_io_interval.tsv
```

### 3.2 실험 E1: 시간에 따른 L2 read/write throughput

**연구 질문:** 동일한 replay speedup에서 workload의 burst와 read/write phase가 backend별
throughput 시계열에 어떻게 나타나는가?

**측정 방법:** 다섯 workload를 동일 trace와 replay speedup `x1`에서 실행하고,
case별 `l2_io_interval.tsv`를 5초 interval로 집계한다. x축은 `elapsed_seconds`,
y축은 decimal GB/s 단위의 `read_gb_per_second`와 `write_gb_per_second`로 둔다.
비교값은 interval median, peak와 p95 throughput, 그리고 변동계수다. Adapter가
interval log를 제공하지 않는 case는 node-level disk/network rate로 대체하지 않고
`N/A`로 표시한다.

그림 1과 2는 workload를 행, backend를 열로 배치한 throughput 시계열의 구성 예시다.
Read와 write는 각각 실선과 점선으로 표시하며, backend 간 높이를 비교할 때는 모든 panel에
동일한 y축 범위를 사용한다.

![TensorMesh workload의 시간별 L2 read/write throughput 구성 예시](figures/l2-throughput-tensormesh.png)

**그림 1 (구성 예시).** TensorMesh workload별 시간 구간 L2 throughput.
실선은 read, 점선은 write이며 현재 값은 dummy data다.

![Mooncake workload의 시간별 L2 read/write throughput 구성 예시](figures/l2-throughput-mooncake.png)

**그림 2 (구성 예시).** Mooncake workload별 시간 구간 L2 throughput.
현재 값은 dummy data다.

#### 결과

> **[실측 결과 필요]** 각 workload/backend의 burst 구간, read/write peak와 p95,
> 동일 interval에서의 backend 차이를 삽입한다. Peak throughput만으로 backend 우열이나
> 병목을 결론내리지 않고, 같은 구간의 latency·schedule lag·resource metric과 함께 해석한다.

Adapter가 interval log를 제공하지 않아 `l2_io_interval.tsv`가 비어 있는 case는
node-level disk/network rate로 대체하지 않는다. 해당 L2 panel은 `N/A`로 남기고,
물리 자원 시계열은 실험 E4에서 별도로 보고한다.

### 3.3 실험 E2: Replay speedup 영향

**연구 질문:** replay speedup을 높였을 때 offered load와 throughput이 어떻게 변하며, 어느 지점에서 queueing이 관찰되는가?

#### Replay speedup의 의미

Replay speedup `s`는 GPU를 `s`배 빠르게 만드는 옵션이 아니라, trace의 submission
간격을 `s`로 줄여 offered L2 load를 조절하는 timestamp scaling factor다. 따라서
speedup 변화와 backend의 실제 I/O latency를 분리해서 해석해야 한다.

~~~text
submission_gap(s) = submission_gap(1) / s
offered_L2_rate(s) = s * offered_L2_rate(1)
~~~

따라서 `x2`는 같은 trace의 store/lookup 요청을 두 배 빠른 간격으로 제출하는
offered-load case다. 이는 producer가 빨라져 L2 요청이 더 자주 발생하는 상황을
모사할 뿐이며, 실제 GPU의 end-to-end speedup을 보장하지 않는다. Batching, sequence
length와 cache policy가 달라지는 경우는 별도 실험으로 분리한다.

#### Speedup sweep 범위

본 평가의 기본 sweep은 `1.0 ≤ s ≤ 2.0`으로 둔다. 이 값은 통제된 offered-load
범위이며, 실제 하드웨어의 end-to-end speedup으로 해석하지 않는다. GPU 수나 producer
server 수는 speedup과 별도의 실험 변수로 기록한다.

| Speedup | 역할 |
| --- | --- |
| x1 | baseline offered load |
| x1.25 | baseline 대비 25% 부하 증가 |
| x1.5 | baseline 대비 50% 부하 증가 |
| x2 | baseline 대비 2배 offered load |

H100/B300 system을 실제 비교 대상으로 포함하는 경우에는 아래 공개 사양을
sweep 범위의 참고 근거로 사용할 수 있다. 이는 본 replay의 측정 결과가 아니며, 실제 system configuration과
workload-specific compute fraction이 확정되기 전에는 end-to-end 성능을 추정하는 근거로
사용하지 않는다. 서로 다른 precision이나 sparsity 조건을 섞지 않기 위해 두 시스템 모두
제조사가 표기한 FP8 system 성능을 사용한다.

| 8-GPU system 지표 | H100 × 8 | B300 × 8 | B300/H100 | 해석 |
| --- | ---: | ---: | ---: | --- |
| FP8 성능 | 32 PFLOPS | 72 PFLOPS | 2.25× | Compute-only 낙관적 상한 |
| GPU memory 용량 | 640 GB | 2,304 GB | 3.60× | Cache residency가 달라질 수 있어 L2 요청량 자체가 바뀔 수 있음 |
| GPU memory bandwidth | 26.8 TB/s | 최대 64 TB/s | 최대 2.39× | Memory-bound phase의 참고 상한 |
| GPU당 NVLink bandwidth | 900 GB/s | 1,800 GB/s | 2.00× | GPU 간 통신 상한이며 L2 storage throughput과는 별개 |

H100 수치는 [NVIDIA H100 제품 사양](https://www.nvidia.com/en-us/data-center/h100/)과
[DGX H100 datasheet](https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/nvidia-dgx-h100-datasheet.pdf),
B300 수치는 [DGX B300 user guide](https://docs.nvidia.com/dgx/dgxb300-user-guide/introduction-to-dgxb300.html)와
[NVIDIA HGX AI Factory reference architecture](https://docs.nvidia.com/enterprise-reference-architectures/hgx-ai-factory/latest/components.html)를 기준으로 계산했다.
이는 peak theoretical specification의 비교일 뿐, 본 평가의 application benchmark
결과가 아니다.

FP8 system 성능 비율 `compute_rate_ratio = 72 / 32 = 2.25`를 사용하고,
H100 실행 시간 중 B300 compute 향상의 영향을 받는 비율을 `compute_fraction`으로 두면 다음과 같이 근사할 수 있다.

~~~text
effective_speedup =
    1 / ((1 - compute_fraction) + compute_fraction / compute_rate_ratio)
~~~

| Compute fraction 가정 | 근사 effective speedup |
| ---: | ---: |
| 0.5 | 1.38× |
| 0.7 | 1.64× |
| 0.8 | 1.80× |
| 1.0 | 2.25× |

이 계산에서 `x1.25`, `x1.5`, `x2`는 compute와 non-compute 구간을 함께 포함하는
부하 증가점으로 사용한다. `x2.25`는 필요할 때만 별도 stress point로 추가하며,
어떤 speedup도 B300의 실제 end-to-end speedup이라고 주장하지 않는다. 특히 HBM 용량
차이로 cache residency가 달라지면 동일 trace의 L2 요청 빈도를 단순 배율로 환산할 수
없다. 실제 분석에서는 throughput 증가가 actual submission window, pending, p99 latency와
함께 유지되는지 확인하고, submission window와 lag이 급증하는 첫 지점을 saturation knee로
보고한다.

#### 다중 producer server에서의 offered load

Producer server가 `N_g`대이면 이상적인 offered load는 `N_g × effective_speedup`에
비례한다. 실제 offered load는 shared L2와 network capacity에 의해 제한되므로 다음
상한을 참고한다.

~~~text
speedup(N_g) <= min(
    N_g * effective_speedup,
    L2_capacity / H100_trace_rate,
    network_capacity / H100_trace_rate
)
~~~

다중 server 실험에서는 `N_g`를 storage node 수와 독립적으로 기록한다. 동일 trace를
여러 server에서 복제한 경우 이를 새로운 workload로 부르지 않고 의도적인 offered-load
stress로 표시한다.

**분석 방법:** 다섯 workload를 동일 trace와 backend 설정으로 replay하고
speedup `x1`, `x1.25`, `x1.5`, `x2`를 비교한다. 주요 원본은
`sweep-summary.csv`와 case별 `l2_replay_stats.json`이다. 각 case에서 throughput,
read/write p99 latency, maximum schedule lag, target/actual submission window, pending과
drain time을 함께 확인한다. Speedup gain은 다음과 같이 계산한다.

```text
throughput_gain(s) = throughput(s) / throughput(x1)
```

그림 3에서는 throughput, read p99 latency와 maximum schedule lag을 같은 workload/backend
축에서 비교하고, 반복 결과는 95% confidence interval로 표시한다. 포화 여부는 throughput
증가가 멈추는 시점만으로 정하지 않고 actual submission window, pending, p99 latency와
schedule lag의 동시 변화를 기준으로 판정한다.

![Replay speedup 영향 구성 예시](figures/replay-speedup-impact.png)

**그림 3 (구성 예시).** 권장 speedup `x1`, `x1.25`, `x1.5`, `x2`에서
workload/backend별 throughput, read p99 latency와 maximum schedule lag을 비교한다.
현재 값과 band는 dummy data다.

#### 결과

> **[실측 결과 필요]** workload/backend별 throughput gain과 saturation knee를 삽입한다.
> 포화점은 throughput 증가가 둔화되면서 actual submission window, p99 latency 또는
> schedule lag이 증가하는 첫 speedup으로 보고한다. Read와 write 중 하나만 악화되는
> 경우에는 aggregate throughput으로 가리지 않고 operation별 결과를 별도로 서술한다.

### 3.4 실험 E3: Latency breakdown과 queueing 진단

**연구 질문:** tail latency 증가는 adapter task 자체의 service time에서 비롯되는가,
아니면 target submission 전에 발생한 dependency·buffer·dispatch 지연에서 비롯되는가?

**측정 방법:** 대표 workload `SWE-bench`와 `Conversation`을 대상으로 `x1` 및
각 backend의 saturation knee 직전·직후 case를 비교한다. Adapter task latency는
`operations.read.<adapter>`와 `operations.write.<adapter>`의 submission부터
completion까지 측정하고, p50·p90·p99를 보고한다. Replay delay는
`l2_replay_stats.json`의 dependency wait, buffer wait와 schedule lag을 사용한다.
Task latency는 microseconds를 milliseconds로, replay delay는 seconds를 milliseconds로
변환한다.

두 종류의 latency는 서로 다른 구간을 측정한다. 시간 흐름은 다음과 같다.

```text
target 제출 시각
  -> dependency 대기
  -> buffer 또는 dispatch 대기
  -> 실제 dispatch
  -> adapter task
  -> completion
```

`dep. max`는 target 제출 시각 이후 causal dependency를 기다린 최대 시간이다.
`buffer max`는 dependency가 준비된 뒤 buffer를 확보하거나 다음 replay loop에서
제출될 때까지의 최대 지연이다. 현재 aggregate metric의 buffer wait에는 memory buffer
확보와 다음 replay loop까지의 지연이 함께 포함될 수 있다. `schedule max`는 target
제출 시각부터 실제 dispatch까지의 전체 지연이므로 앞의 두 값과 더해지는 독립적인 세
번째 구간이 아니다. 세 값의 공식 field 정의는
[L2 replay metrics guide](../docs/l2-replay-metrics.md#schedule-lag과-wait)를 따른다.

따라서 `total_dependency_wait_seconds`와 `total_buffer_wait_seconds`도 wall-clock
breakdown처럼 합산하지 않는다. 두 값은 operation별 합계이며 서로 다른 operation의
대기와 겹칠 수 있다. 그림 4의 replay-delay 행은 task latency 행과 별도의 diagnostic
축으로 표시한다.

![Task latency 분포와 replay delay 진단 구성 예시](figures/latency-breakdown-x2.png)

**그림 4 (구성 예시).** 대표 workload(`SWE-bench`, `Conversation`)의 `x2`
read/write task percentile과 replay-delay diagnostic을 함께 표시한다. 현재 값은
dummy data이며, dependency·buffer·schedule 값은 비가산적이다.

#### 결과

> **[실측 결과 필요]** workload/backend/speedup별 read·write p99와
> `dep. max`, `buffer max`, `schedule max`를 삽입한다. Task p99가 증가한
> case에서 replay delay도 함께 증가하면 해당 delay와의 시간적 연관성을 보고하고,
> replay delay가 안정적인데 task p99만 증가하면 adapter service time의 변화로
> 구분한다. 실제 병목의 인과를 주장하려면 resource 또는 queue metric을 함께 제시한다.

현재 aggregate field만으로는 완전히 additive한 latency breakdown을 만들 수 없다.
정확한 분해가 필요하면 operation별로 같은 clock에서
`t_target`, `t_dependency_ready`, `t_buffer_ready`, `t_submit`,
`t_complete`를 수집하고 다음 component를 검증한다.

```text
dependency_wait = max(0, t_dependency_ready - t_target)
buffer_wait = max(0, t_buffer_ready - max(t_target, t_dependency_ready))
dispatch_wait = t_submit - max(t_target, t_dependency_ready, t_buffer_ready)
adapter_task_latency = t_complete - t_submit
target_to_completion = t_complete - t_target
```

### 3.5 실험 E4: Storage/network utilization

**연구 질문:** throughput plateau가 storage device, network 또는 replay/adapter software
path 중 어느 자원의 포화와 함께 나타나는가?

**측정 방법:** 전체 workload의 aggregate utilization은 overview로 사용하고, node-level
hotspot과 load imbalance는 `SWE-bench`의 replay speedup `x1`과 `x2`에서
분석한다. Storage 원본은 각 storage node의 `profile/<node>/disk.tsv`, network
원본은 replay node와 각 storage node의 `profile/<node>/network.tsv`다. Storage의
대표값은 interval별 physical device 최대 `io_util_percent`의 측정 구간 p95이며,
network의 대표값은 role별 directional utilization p95다. Disk read/write MiB/s,
IOPS, network RX/TX MiB/s와 error/drop은 원인 해석을 위한 보조 metric으로 기록한다.

Network는 full-duplex이므로 RX와 TX를 합쳐 단일 link rate로 나누지 않는다. 같은 role의
node를 집계할 때 각 interval에서 다음 directional utilization을 계산한다.

```text
network_utilization =
    max(sum(rx_bytes_per_second), sum(tx_bytes_per_second))
    / sum(directional_link_bytes_per_second) * 100
```

Replay role과 storage role이 같은 traffic을 양쪽에서 관측할 수 있으므로 두 role의
traffic은 합산하지 않고 별도로 보고한다. Bond interface와 slave interface를 동시에
집계하지 않으며, 같은 physical I/O를 나타내는 partition과 parent device도 중복 합산하지
않는다.

Aggregate metric은 [Report data contract](data/README.md)의 provenance와
집계 방법을 함께 기록한다. 기본 importer는 선택된 node p95의 equal-weight mean을
사용한다. Device 또는 link capacity가 서로 다른 cluster에서는 capacity-weighted 값을
다시 계산하고 그 방법을 manifest에 남긴다. `fs-native` aggregate는 distributed
backend와 합산하지 않고 replay host의 local device와 replay-role network에서 별도로
계산한다. Node-level storage 값은 node 내 physical device 중 최대 utilization,
node-level network 값은 해당 node interface의 directional utilization을 사용한다.

Node별 imbalance는 다음 지표로 요약한다.

```text
imbalance_max_mean = max(node_utilization) / mean(node_utilization)
imbalance_cv = std(node_utilization) / mean(node_utilization)
```

`imbalance_max_mean`이 1에 가깝고 `imbalance_cv`가 작으면 node 간 부하가 균등한
것으로 해석한다. Aggregate가 낮더라도 특정 node만 높으면 hotspot 후보로 분류하고,
해당 node의 device·interface·metadata 경로를 추가로 확인한다.

![Storage와 network utilization 구성 예시](figures/resource-utilization.png)

**그림 5 (구성 예시).** `x1`에서 workload/backend별 aggregate storage I/O utilization
p95와 network directional utilization p95를 비교한다. 현재 값은 dummy data다.

![Storage node별 utilization 구성 예시](figures/resource-utilization-nodewise.png)

**그림 6 (구성 예시).** `SWE-bench`에서 replay speedup `x1`과 `x2`의 node-wise
utilization을 비교한다. 왼쪽 열은 storage device, 오른쪽 열은 network utilization이며,
현재 값은 dummy data다. `fs-native`는 local baseline이므로 storage-node 행은
`N/A`로 표시하고 aggregate에 replay host 값을 기록한다.

#### 결과

> **[실측 결과 필요]** throughput plateau와 같은 구간의 storage/network utilization,
> headroom과 node별 imbalance를 삽입한다. Resource utilization이 증가하면서 throughput이
> plateau에 도달하면 해당 자원을 병목 후보로 보고, 두 utilization이 낮은데 latency나
> schedule lag만 증가하면 adapter queue, metadata server, CPU/NUMA, lock contention과
> single-thread submission path를 추가로 확인한다. `io_util_percent` 100%만으로
> cluster 전체 포화를 주장하지 않는다.

### 3.6 실험 E5: Storage node 수에 따른 scale-out

**연구 질문:** distributed backend의 storage node 수를 늘렸을 때 aggregate throughput이
얼마나 확장되며, scale-out의 한계가 latency, resource utilization 또는 node imbalance
중 어디에서 나타나는가?

**측정 방법:** workload는 `SWE-bench`로 고정하고 replay speedup은 `x2`로 둔다.
`3FS`와 `pNFS`를 storage-node scaling 대상으로 삼고, `fs-native`는 local
baseline으로 함께 기록한다. Storage node 수 `N_s`는 `1, 2, 3, 4, 5, 6`으로
변경하며 trace, L1 size, worker 수, direct I/O 설정, device 종류와 capacity,
network link rate와 replication 정책은 가능한 한 고정한다. 각 node-count case는
최소 3회 반복하고, `N_s=1,3,6`은 5회 반복한다. 실제 반복 수가 다르면 결과 metadata에
기록한다.

이 절의 `N_s`는 storage node 수이며, 3.3절(E2)의 producer server 수 `N_g`와 다르다.
두 축을 동시에 바꾸는 실험은 `N_g × N_s` matrix로 별도 표시한다. `N_s < 6`인
case에서는 가능한 node subset을 순환하거나 assignment를 반복마다 바꾸고, 실제 활성
node 목록을 metadata에 남긴다. 단순히 replay client concurrency만 변경한 case는
storage-node scale-out 결과로 취급하지 않는다.

`fs-native`는 replay host의 local/direct-attached storage이므로 storage node 수에
따라 확장되는 backend가 아니다. 따라서 그림에서 `fs-native`는 distributed backend와
같은 scaling efficiency를 비교하는 점이 아니라 local reference로 해석한다.

주요 metric은 aggregate throughput, `N_s=1` 대비 throughput gain, read p99 latency,
drain/schedule lag, storage device utilization p95, network directional utilization p95와
node imbalance다. Node 수에 따른 scaling은 다음 비율로 요약한다.

```text
throughput_gain(N_s) = throughput(N_s) / throughput(1)
scaling_efficiency(N_s) = throughput(N_s) / (N_s * throughput(1))
```

Throughput이 node 수에 따라 증가하는지뿐 아니라, 증가율이 감소하는 지점에서 latency와
utilization이 어떻게 변하는지 함께 확인한다. `imbalance_max_mean`과 `imbalance_cv`는
각 `N_s`에서 특정 node로 부하가 몰리는지 판단하는 보조 지표다.

![Storage node 수에 따른 scaling 구성 예시](figures/storage-node-scaling.png)

**그림 7 (구성 예시).** `SWE-bench`, replay speedup `x2`에서 storage node 수를
`1`개부터 `6`개까지 늘린다. 왼쪽 위부터 aggregate throughput, read p99 latency,
storage utilization과 network directional utilization을 표시하며, 현재 값은 dummy data다.
`fs-native`는 node-count scaling 대상이 아니므로 점선 local baseline으로 표시한다.

#### 결과

> **[실측 결과 필요]** `N_s`별 throughput gain과 scaling efficiency, read p99,
> storage/network utilization과 imbalance를 삽입한다. Throughput gain이 감소하는
> 첫 node count에서 latency와 resource evidence를 함께 제시하고, scale-out 한계를
> network, storage, metadata 또는 software path 중 하나로 판단할 근거를 명시한다.

## 4. 해석의 범위와 한계

본 평가는 동일한 adapter-level L2 trace를 replay하는 통제 실험이다. 따라서 결과는 해당
trace와 replay 조건에서의 L2 adapter throughput·latency·resource behavior를 설명하며,
application end-to-end 성능이나 다른 L1 policy로 일반화하지 않는다. Trace가 원본 KV
payload와 source L1 상태를 복원하지 않는다는 점도 함께 기록한다.

Backend 비교에서는 adapter, filesystem 또는 storage system, mount option, replication과
network topology를 함께 고정하거나 결과에 명시해야 한다. `fs-native`는 local baseline이고
distributed backend와 failure model이나 durability가 같지 않으므로, 단순히 throughput이
높다는 이유만으로 backend 우열을 단정하지 않는다.

Throughput plateau, latency 증가와 resource utilization의 동시성은 병목 해석의 근거지만,
단일 metric만으로 인과를 확정하지 않는다. 특히 `schedule lag`, dependency wait와
buffer wait는 operation별 diagnostic metric이므로 wall-clock latency component처럼
합산하지 않는다. Resource utilization이 낮은 경우에는 adapter queue, metadata, CPU/NUMA,
lock contention과 submission path를 추가로 확인한다.

현재 figure와 결과 dataset이 dummy 상태인 동안에는 다음 주장에 대한 결론을 유보한다.

- workload/backend별 throughput 우열과 burst 처리 차이
- backend별 saturation knee와 유효한 offered-load 범위
- task tail latency의 지배 원인
- storage/network/software path 중 주된 병목
- storage-node scale-out의 효율과 한계

## 5. 결론

> **[실측 결과 필요]** measured dataset과 provenance가 삽입된 뒤, E1부터 E5의 결과를
> 같은 trace·축소 조건·backend 설정 기준으로 비교하여 throughput, saturation, latency
> 원인, resource bottleneck과 scale-out 한계를 순서대로 요약한다. 최종 결론에는 각
> 주장에 대응하는 figure/table과 metric field를 함께 인용하고, L2 adapter replay의
> 범위를 넘어서는 해석은 제외한다.

L1 size sweep, replay instance 수에 따른 aggregate contention, read/write 비율별
synthetic trace, warm-cache와 cold-cache 분리 평가는 본 평가의 결과가 확정된 뒤
후속 분석으로 분리한다.
