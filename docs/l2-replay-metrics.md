# L2 replay metric guide

`l2_replay_stats.json`은 기계 처리용 원본 결과입니다. JSON에는 주석을 넣지
않으며, 같은 directory의 `l2_replay_summary.md`가 주요 값을 사람이 읽기 쉬운
형태로 보여줍니다. Speedup sweep의 case 비교에는 상위 directory의
`sweep-summary.csv`를 사용하세요. 이 문서는 세 결과에서 사용하는 메트릭 정의의
단일 기준입니다.

## 먼저 볼 값

Backend와 speedup을 비교할 때 다음 순서로 확인합니다.

1. `pending`과 submitted/completed 수로 replay가 모두 drain됐는지 확인합니다.
2. `total_replay_seconds`와 `throughput_bytes_per_second`로 전체 replay 결과를
   비교합니다.
3. Read/write의 p50, p90, p99 latency로 task latency 분포를 비교합니다.
4. `mean_schedule_lag_seconds`, `max_schedule_lag_seconds`로 요청이 목표 시각에
   제출됐는지 확인합니다.
5. dependency/buffer wait와 `drain_seconds`로 lag의 원인과 마지막 I/O 완료
   시간을 확인합니다.
6. outcome mismatch는 별도의 진단 지표로 해석합니다.

서로 다른 case를 비교할 때는 trace, `trace_percent`, L1 설정, worker 수,
direct I/O, replay host와 storage 상태를 고정해야 합니다.

## 시간과 처리량

| JSON field | 단위 | 의미 |
| --- | --- | --- |
| `speedup` | 배수 | source submission 간격을 나눈 배수 |
| `source_submission_window_seconds` | s | 선택한 첫 operation부터 마지막 operation까지의 source timestamp 범위를 speedup으로 나눈 목표 제출 구간. 기존 field 이름에 `source`가 있지만 값은 scaled target 구간임 |
| `actual_submission_window_seconds` | s | target에서 첫 operation과 마지막 operation이 실제 제출된 시각 차이 |
| `total_replay_seconds` | s | replay loop 시작부터 모든 store/lookup/load task가 완료될 때까지의 wall-clock 시간 |
| `drain_seconds` | s | 마지막 operation 제출부터 모든 비동기 task 완료까지의 wall-clock 시간 |
| `total_bytes_submitted` | byte | store와 load task에 제출한 object byte의 합 |
| `throughput_bytes_per_second` | byte/s | `total_bytes_submitted / total_replay_seconds` |

`l2_replay_summary.md`와 `sweep-summary.csv`의 wall throughput은
`throughput_bytes_per_second / 1e9`인 decimal GB/s입니다. Read와 write를 합친
replay 전체 wall-clock 처리량이므로 backend 비교의 대표 처리량으로 사용합니다.

## Schedule lag과 wait

각 operation의 목표 제출 시각은 다음과 같습니다.

```text
target = replay_start + (source_timestamp - first_source_timestamp) / speedup
schedule_lag = max(0, actual_dispatch - target)
```

Schedule lag은 “원래 제출하려던 시각보다 target 제출이 얼마나 늦었는가”입니다.
Backend가 dependency를 늦게 완료했거나, replay buffer를 바로 얻지 못했거나,
replay loop 자체가 밀리면 증가합니다.

| JSON field | 의미 |
| --- | --- |
| `mean_schedule_lag_seconds` | 선택된 operation별 schedule lag의 평균 |
| `max_schedule_lag_seconds` | 단일 operation에서 관측한 가장 큰 schedule lag |
| `max_dependency_wait_seconds` | operation의 목표 시각 이후 causal dependency 완료를 기다린 최대 시간 |
| `total_dependency_wait_seconds` | operation별 dependency wait를 모두 더한 값 |
| `max_buffer_wait_seconds` | dependency가 준비된 뒤 실제 제출까지 기다린 최대 시간 |
| `total_buffer_wait_seconds` | operation별 buffer wait를 모두 더한 값 |

`total_dependency_wait_seconds`와 `total_buffer_wait_seconds`는 operation별 값을
합산하므로 서로 겹치는 대기 시간이 포함될 수 있고 `total_replay_seconds`보다 클
수 있습니다. Wall-clock breakdown으로 더해서 사용하지 마세요. Buffer wait에는
memory buffer 확보뿐 아니라 dependency 준비 후 다음 replay loop에서 제출될 때까지의
지연도 포함됩니다.

Schedule lag이 크면 latency와 throughput 비교 전에 replay가 의도한 offered load를
실제로 제출했는지 확인해야 합니다. 예를 들어 target submission window가 60초인데
actual submission window가 크게 늘었다면 지정한 speedup을 backend가 따라가지 못한
상태일 수 있습니다.

## Operation latency

`operations.read.<adapter>`는 load task, `operations.write.<adapter>`는 store
task 통계입니다. Lookup, unlock, delete latency는 이 표에 포함되지 않습니다.

| JSON field | 의미 |
| --- | --- |
| `submitted`, `completed` | 제출 및 완료된 task 수 |
| `unmatched_completed` | 대응하는 submission sample을 찾지 못한 completion 수 |
| `samples` | percentile 계산에 사용한 latency sample 수 |
| `total_bytes` | 해당 operation이 처리한 object byte 합 |
| `average_latency_us` | task별 submission-to-completion latency 평균 |
| `p50_latency_us`, `p90_latency_us`, `p99_latency_us` | task latency percentile |
| `min_latency_us`, `max_latency_us` | 최소 및 최대 task latency |
| `aggregate_throughput_gbps` | `total_bytes / sum(task_latency)`로 계산한 decimal GB/s |

`aggregate_throughput_gbps`는 JSON schema의 기존 이름을 유지하지만 bit/s가 아니라
decimal GB/s입니다. 또한 여러 task가 비동기로 겹쳐 실행되어도 task latency를 모두
합한 값을 분모로 사용하므로 wall-clock throughput이 아닙니다.
`l2_replay_summary.md`에서는 혼동을 줄이기 위해 이를
“Task-latency throughput”으로, CSV에서는
`*_bytes_over_sum_task_latency_gb_per_second`로 표시합니다.

## Operation과 outcome

| JSON field | 의미 |
| --- | --- |
| `source_operations_total` | trace에 기록된 전체 submission 수 |
| `operations_selected` | `trace_percent` 적용 후 replay 대상으로 선택한 수 |
| `operations_submitted` | operation 종류별 target 제출 수 |
| `bytes_submitted` | store/load 종류별 제출 byte |
| `pending` | 종료 시 latency collector에 남은 read/write task |
| `outcome_comparisons` | source와 결과를 비교한 store/lookup/load 수 |
| `outcome_mismatch_count` | source와 결과가 다른 operation 수 |
| `outcome_mismatch_rate` | mismatch 수를 comparison 수로 나눈 값 |
| `outcome_mismatch_counts` | operation 종류별 mismatch 수 |
| `outcome_mismatch_samples` | 원인 조사에 사용할 제한된 task 식별자 sample |
| `operations_without_outcome_comparison` | 결과 비교가 없는 unlock/delete 제출 수 |

Outcome mismatch는 source와 target의 backend 상태, concurrency와 비동기 delete
timing 차이 등을 관찰하는 진단 지표입니다. Mismatch가 0이 아니어도 replay 자체의
실패를 뜻하지 않습니다. 반면 malformed trace, missing/duplicate end marker,
dispatch 오류와 drain timeout은 replay 실패입니다.

## L2 namespace 사용량

`l2_usage.json`은 replay client에서 접근하는 L2 directory를 `du -sb`로 측정한
apparent byte를 기록합니다. `fs_native`의 `base_path`와 file-based NIXL
adapter의 `backend_params.file_path`를 대상으로 하며, 다음 snapshot을 포함합니다.

| JSON field | 의미 |
| --- | --- |
| `scope` | 항상 `client_visible_namespace` |
| `adapter_type`, `path` | 측정한 adapter와 client path |
| `measurement_method` | 현재 `du -sb` |
| `after_prepare` | L2 preparation process가 종료된 직후의 namespace 크기 |
| `after_replay` | measured replay process가 종료된 직후의 namespace 크기 |
| `bytes`, `gb`, `gib` | snapshot 시점의 apparent size |
| `measurement_status` | `ok`, `missing`, `unsupported_adapter` 또는 `measurement_failed` |
| `command_exit_code` | 해당 snapshot 직전 prepare/replay process의 exit code |

이 값은 client namespace에서 보이는 논리적 크기입니다. Filesystem allocation,
distributed storage replication과 storage node의 physical usage를 의미하지
않습니다. 측정 실패는 replay exit code를 변경하지 않으며 status와 error를
`l2_usage.json`에 남깁니다. 원격 filesystem의 namespace scan이 300초를 넘으면
`measurement_failed`로 기록합니다.

## 보조 field와 생성 결과

- `schema_version`: stats JSON schema version
- `trace_percent`: trace에서 선택한 submission 비율
- `latency_unit`: operation latency field의 단위
- `sample_encoding`: percentile sample의 내부 저장 형식

정상 L2 replay는 output directory에 다음 분석 파일을 생성합니다.

- `l2_replay_stats.json`: 모든 원본 field를 보존한 기계 처리용 결과
- `l2_replay_summary.md`: 주요 값과 주의사항을 담은 사람용 요약
- `l2_io_interval.tsv`: adapter가 interval log를 지원할 때의 시간 구간별 I/O
- `l2_prepare_manifest.json`: 측정 전에 준비한 object와 byte
- `l2_usage.json`: prepare/replay 뒤의 client-visible L2 namespace 크기

Speedup sweep은 성공한 case의 핵심 field를 한 행씩 펼친
`sweep-summary.csv`를 추가합니다. 실패하거나 stats JSON이 없는 case는 CSV에서
제외되며, 실행 상태는 `sweep-results.jsonl`에서 확인합니다.
