# Workload preflight estimates

이 문서는 현재 workload별 replay preset과 `trace_percent`, source window, logical KV peak를 사전에 확인하기 위한 자료다.
실제 replay 전의 용량 계획에 사용하며, filesystem allocation이나 backend metadata 오버헤드는 포함하지 않는다.

- Generated: `2026-08-18`
- Trace root: `HF daegyu94/lmcache-storage-traces@main`
- Unit: decimal GB/TB (`1 TB = 1,000 GB`)
- Capacity column: prefix replay의 `peak_gb`
- Assumption: selected store가 성공하고 submission 순서대로 overwrite/delete가 반영됨
- Preset target mapping: conservative trace-prefix selection with five percent headroom
- Duration estimate: source first-to-last submission window divided by replay speedup; schedule lower bound only
- `source window s` is the raw timestamp difference between the first and last selected submission; for speedup S, the schedule lower bound is `source window / S`
- Target preset windows are interpolated from preflight analysis unless `--validate-targets` is used

## Presets

`full`은 모든 workload에서 full trace를 사용한다.
엄격하게 모든 workload를 target 안에 넣으려면 `0.5tb`, `1tb`, `2tb`, `4tb` preset을 사용한다.

| preset | workload | target GB | trace_percent | source window s | estimated peak GB |
| --- | --- | ---: | ---: | ---: | ---: |
| `full` | `tensormesh-gaia` | full | 100% | 1597.828 | 580.685 |
| `full` | `tensormesh-wildclaw` | full | 100% | 305.650 | 59.681 |
| `full` | `tensormesh-swebench` | full | 100% | 22883.430 | 5361.638 |
| `full` | `mooncake-toolagent` | full | 100% | 52621.881 | 23763.755 |
| `full` | `mooncake-conversation` | full | 100% | 51799.581 | 24831.687 |
| `0.5tb` | `tensormesh-gaia` | 500.000 | 77.96% | 1060.153 | 480.225 |
| `0.5tb` | `tensormesh-wildclaw` | 500.000 | 100% | 305.650 | 59.681 |
| `0.5tb` | `tensormesh-swebench` | 500.000 | 9.63% | 2060.152 | 474.587 |
| `0.5tb` | `mooncake-toolagent` | 500.000 | 1.76% | 1109.631 | 472.622 |
| `0.5tb` | `mooncake-conversation` | 500.000 | 1.66% | 991.277 | 472.861 |
| `1tb` | `tensormesh-gaia` | 1000.000 | 100% | 1597.828 | 580.685 |
| `1tb` | `tensormesh-wildclaw` | 1000.000 | 100% | 305.650 | 59.681 |
| `1tb` | `tensormesh-swebench` | 1000.000 | 19.25% | 4118.164 | 948.681 |
| `1tb` | `mooncake-toolagent` | 1000.000 | 3.53% | 2225.567 | 947.928 |
| `1tb` | `mooncake-conversation` | 1000.000 | 3.33% | 1988.525 | 948.571 |
| `2tb` | `tensormesh-gaia` | 2000.000 | 100% | 1597.828 | 580.685 |
| `2tb` | `tensormesh-wildclaw` | 2000.000 | 100% | 305.650 | 59.681 |
| `2tb` | `tensormesh-swebench` | 2000.000 | 37.15% | 7771.685 | 1895.749 |
| `2tb` | `mooncake-toolagent` | 2000.000 | 7.07% | 4457.438 | 1898.542 |
| `2tb` | `mooncake-conversation` | 2000.000 | 6.67% | 3983.021 | 1899.991 |
| `4tb` | `tensormesh-gaia` | 4000.000 | 100% | 1597.828 | 580.685 |
| `4tb` | `tensormesh-wildclaw` | 4000.000 | 100% | 305.650 | 59.681 |
| `4tb` | `tensormesh-swebench` | 4000.000 | 71.43% | 15179.611 | 3778.917 |
| `4tb` | `mooncake-toolagent` | 4000.000 | 14.15% | 8921.181 | 3799.770 |
| `4tb` | `mooncake-conversation` | 4000.000 | 13.34% | 7966.041 | 3799.981 |

### Usage

```bash
# Full-trace preset for all workloads
bash benchmarks/evaluation/run_report_experiments.sh \
  --topology configs/replayer/staged-remote/b300.yaml \
  --graph speedup \
  --workload-preset full \
  --backend-spec 'fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native'
```

Preset은 이 문서와 함께 생성된 `workload-presets.json`의 trace checksum을 기준으로 한다. trace archive가 바뀌면 이 generator를 다시 실행한다.
