# 성능 평가 리포트 작업 안내

이 디렉터리는 LMCache L2 backend 성능 평가의 실험 설계, 보고서 본문 초안, 그림 생성 코드를 모아 둡니다.
현재 그림의 값은 모두 **더미 데이터**이며 성능 주장을 위한 근거로 사용하면 안 됩니다.

## 파일 구성

| 파일 | 역할 |
| --- | --- |
| `performance-evaluation.md` | 한국어 성능 평가 보고서 초안 |
| `plot_dummy_results.py` | 재현 가능한 더미 데이터와 matplotlib multiplot 생성 |
| `requirements.txt` | 보고서 그림 생성에만 필요한 Python package |
| `figures/*.png` | Markdown 본문에 포함되는 사전 렌더링 그림 |
| `../benchmarks/report/` | staged remote 그래프별 실험 matrix runner |

## 그림 다시 만들기

프로젝트 virtual environment를 사용합니다.

```bash
source .venv/bin/activate
python -m pip install -r report/requirements.txt
python report/plot_dummy_results.py
```

다른 출력 디렉터리를 쓰려면 다음과 같이 실행합니다.

```bash
python report/plot_dummy_results.py --output-dir /tmp/lmcache-report-figures
```

## 실험 matrix 실행

실측 그림을 만들 때는 local replay wrapper를 직접 조합하지 말고
[staged remote runner](../benchmarks/report/README.md)를 사용합니다.

    bash benchmarks/report/run_report_experiments.sh --help

runner는 그림별 workload/backend/speedup/repeat case를 하나의 staged remote
run으로 만들고, outputs/report-experiments-staged/matrix-summary.json과
matrix-results.jsonl에 상태를 기록합니다. 기본 실행은 trace가 큰
SWE-bench, mooncake-toolagent, mooncake-conversation에 사용할
--trace-percent를 모든 backend와 speedup에 동일하게 전달합니다. 중단 후 같은
state root로 다시 실행하면 완료 case는 건너뛰고 미완료 case만 재시작합니다.

## Markdown과 Quarto 선택

현재 단계에는 **Markdown + matplotlib multiplot PNG**를 권장합니다.
GitHub에서 바로 읽을 수 있고, 여러 panel의 축·범례·크기를 Python 코드 한 곳에서 통제할 수 있으며, 별도 렌더링 도구가 필요하지 않습니다.
Markdown에서 여러 독립 이미지를 HTML table로 억지로 배치하기보다 관련 panel을 한 figure로 렌더링하면 문서 뷰어별 레이아웃 차이도 줄어듭니다.

다만 하나의 그림에 panel을 지나치게 많이 넣으면 글자가 작아집니다.
그래서 시간별 L2 throughput은 TensorMesh와 Mooncake을 각각 한 그림으로 분리했고, 각 그림 안에서 workload를 행, backend를 열로 배치했습니다.
Storage/network utilization은 전체 aggregate 요약과 `SWE-bench`의 replay speedup `x1`/`x2` node-wise drill-down을 분리해 network panel의 가독성을 유지했습니다.

다음 요구가 생기면 `.qmd` 전환을 고려할 수 있습니다.

- 실측 CSV/TSV를 읽어 본문, 표, 그림을 한 번에 다시 계산해야 할 때
- 자동 figure numbering, cross-reference, bibliography가 필요할 때
- 동일 원본에서 HTML과 PDF를 모두 배포해야 할 때

Quarto로 전환하더라도 `plot_dummy_results.py`의 plotting 함수를 재사용할 수 있습니다.
현재 초안에는 Quarto toolchain 의존성을 추가하지 않았습니다.

## 실측 데이터로 교체할 때의 입력 매핑

| 그림 | 권장 원본 | 사용할 열 또는 field |
| --- | --- | --- |
| 시간별 L2 read/write throughput | case별 `l2_io_interval.tsv` | `elapsed_seconds`, `read_gb_per_second`, `write_gb_per_second` |
| Replay speedup 영향 | `sweep-summary.csv`, case별 `l2_replay_stats.json` | `speedup`, wall throughput, read p99 latency, maximum schedule lag |
| Latency breakdown | case별 `l2_replay_stats.json` | read/write p50·p90·p99, dependency/buffer/schedule delay |
| Storage utilization | `profile/<node>/disk.tsv` | `elapsed_s`, `read_mibps`, `write_mibps`, `io_util_percent` |
| Network utilization | `profile/<node>/network.tsv` | `elapsed_s`, `rx_mibps`, `tx_mibps`; link rate와 node 수로 utilization 계산 |
| Node-wise utilization / imbalance | `profile/<node>/{disk,network}.tsv` | SWE-bench `x1`/`x2`의 node별 p95, aggregate, `max/mean`, coefficient of variation |
| Storage node scaling | `profile/<node>/{disk,network}.tsv`, `sweep-summary.csv` | SWE-bench `x2`에서 node 수 `1..6`별 throughput, p99, utilization, imbalance |

`l2_io_interval.tsv`는 adapter가 interval log를 제공할 때만 생성되는 유효한 시계열로 간주합니다.
이 파일이 없는 backend의 node-level `disk.tsv`를 L2 throughput인 것처럼 대체하지 말고, 해당 panel을 `N/A`로 표시한 뒤 physical storage I/O를 별도 지표로 보고해야 합니다.

실측 변환 코드는 다음과 같은 tidy schema를 만드는 방식이 관리하기 쉽습니다.

```text
throughput: workload, backend, repeat, elapsed_s, direction, gb_per_s
speedup:    workload, backend, repeat, speedup, wall_gb_per_s,
            read_p99_ms, max_schedule_lag_s
latency:    workload, backend, repeat, speedup, operation, percentile,
            task_latency_ms, delay_kind, replay_delay_ms
resource:   workload, backend, repeat, speedup, storage_node_count,
            elapsed_s, node, resource, direction, rate_mib_per_s, utilization_percent
```

최종 그림을 만들 때는 반복 실험의 중앙값을 선 또는 막대로, 반복 간 95% bootstrap confidence interval을 band 또는 error bar로 표시합니다.
Dependency, buffer, schedule wait의 aggregate 합계는 서로 겹칠 수 있으므로 stacked latency로 합산하지 않습니다.

## Submission speedup 설정

권장 범위와 H100 × 8 대비 B300 × 8의 근거는
[`performance-evaluation.md`의 실험 B](performance-evaluation.md#33-실험-b-replay-speedup-영향)를 단일 기준으로 사용합니다.
