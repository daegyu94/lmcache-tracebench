# Workload preflight estimates

이 문서는 현재 `l2.lct`를 `trace_percent` prefix로 선택했을 때의 operation 수와 logical KV payload를 사전에 확인하기 위한 자료다.
실제 replay 전의 용량 계획에 사용하며, filesystem allocation이나 backend metadata 오버헤드는 포함하지 않는다.

- Generated: `2026-08-18`
- Trace root: `HF daegyu94/lmcache-storage-traces@main`
- Unit: decimal GB/TB (`1 TB = 1,000 GB`)
- Capacity column: prefix replay의 `peak_gb`
- Assumption: selected store가 성공하고 submission 순서대로 overwrite/delete가 반영됨
- Preset target mapping: fixed-percent preflight row interpolation with five percent headroom
- Duration estimate: source first-to-last submission window divided by replay speedup; schedule lower bound only
- `source window s` is the raw timestamp difference between the first and last selected submission; for speedup S, the schedule lower bound is `source window / S`
- Target preset windows are interpolated from fixed-percent rows unless `--validate-targets` is used

## Fixed trace-percent estimates

### `tensormesh-gaia`

| trace_percent | selected operations | store | lookup | load | unlock | delete | source window s | peak GB | final GB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20% | 3,400 | 2,579 | 297 | 251 | 273 | 0 | 209.485 | 144.879 | 144.879 |
| 40% | 6,800 | 5,206 | 568 | 502 | 524 | 0 | 443.704 | 252.213 | 252.213 |
| 60% | 10,200 | 7,846 | 830 | 744 | 780 | 0 | 788.341 | 388.282 | 388.282 |
| 80% | 13,600 | 10,561 | 1,070 | 966 | 1,003 | 0 | 1091.027 | 490.668 | 490.668 |
| 100% | 16,999 | 13,276 | 1,302 | 1,192 | 1,229 | 0 | 1597.828 | 580.685 | 580.685 |

Trace SHA-256: `bc58456784fdb23a775b4763233e8fa77d10f028a937133e0bf67757314e7e76`

### `tensormesh-wildclaw`

| trace_percent | selected operations | store | lookup | load | unlock | delete | source window s | peak GB | final GB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20% | 411 | 298 | 42 | 22 | 49 | 0 | 14.664 | 9.947 | 9.947 |
| 40% | 821 | 597 | 74 | 45 | 105 | 0 | 39.264 | 15.367 | 15.367 |
| 60% | 1,232 | 911 | 100 | 68 | 153 | 0 | 79.877 | 26.476 | 26.476 |
| 80% | 1,642 | 1,234 | 127 | 85 | 196 | 0 | 172.122 | 36.927 | 36.927 |
| 100% | 2,052 | 1,561 | 147 | 106 | 238 | 0 | 305.650 | 59.681 | 59.681 |

Trace SHA-256: `2642f0ee5ee6b46ddaae52e3c8507a12927f2dbd0eac113a7a4cd5d3f59f82ee`

### `tensormesh-swebench`

| trace_percent | selected operations | store | lookup | load | unlock | delete | source window s | peak GB | final GB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20% | 61,551 | 48,509 | 4,436 | 4,286 | 4,320 | 0 | 4278.612 | 985.643 | 985.643 |
| 40% | 123,102 | 96,609 | 9,011 | 8,719 | 8,763 | 0 | 8352.166 | 2046.991 | 2046.991 |
| 60% | 184,653 | 144,244 | 13,728 | 13,290 | 13,391 | 0 | 12647.285 | 3107.917 | 3107.917 |
| 80% | 246,204 | 191,557 | 18,573 | 17,950 | 18,124 | 0 | 17078.302 | 4282.021 | 4282.021 |
| 100% | 307,754 | 238,895 | 23,354 | 22,551 | 22,954 | 0 | 22883.430 | 5361.638 | 5361.638 |

Trace SHA-256: `5526579e09dc3c7bfdd9a73b855d3165eadbfc22b9e287c6a19c6fdc2b57f448`

### `mooncake-toolagent`

| trace_percent | selected operations | store | lookup | load | unlock | delete | source window s | peak GB | final GB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20% | 77,853 | 68,119 | 4,213 | 2,248 | 3,273 | 0 | 12609.443 | 5370.699 | 5370.699 |
| 40% | 155,705 | 138,576 | 8,350 | 3,840 | 4,939 | 0 | 23143.344 | 10383.354 | 10383.354 |
| 60% | 233,558 | 208,633 | 12,528 | 5,466 | 6,931 | 0 | 33303.893 | 14876.898 | 14876.898 |
| 80% | 311,410 | 278,242 | 16,755 | 7,131 | 9,282 | 0 | 42921.453 | 19338.815 | 19338.815 |
| 100% | 389,262 | 347,962 | 20,989 | 8,760 | 11,551 | 0 | 52621.881 | 23763.755 | 23763.755 |

Trace SHA-256: `7f1744ec2384edd788506a3bb126bd77a2cc3df58eca2f759f07ba6d62bda13d`

### `mooncake-conversation`

| trace_percent | selected operations | store | lookup | load | unlock | delete | source window s | peak GB | final GB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20% | 62,227 | 57,637 | 2,181 | 912 | 1,497 | 0 | 11943.091 | 5697.123 | 5697.123 |
| 40% | 124,454 | 115,527 | 4,586 | 1,872 | 2,469 | 0 | 22616.606 | 10855.006 | 10855.006 |
| 60% | 186,680 | 173,421 | 7,017 | 2,814 | 3,428 | 0 | 32724.337 | 15562.975 | 15562.975 |
| 80% | 248,907 | 231,031 | 9,517 | 3,867 | 4,492 | 0 | 42161.936 | 20181.074 | 20181.074 |
| 100% | 311,133 | 288,678 | 12,027 | 4,890 | 5,538 | 0 | 51799.581 | 24831.687 | 24831.687 |

Trace SHA-256: `0d82f3a4abe02e28cf9892685f896f005efdfeb97e0763f593f3ad6dbc0f2914`

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
bash benchmarks/report/run_report_experiments.sh \
  --topology configs/replayer/staged-remote/b300.yaml \
  --graph speedup \
  --workload-preset full \
  --backend-spec 'fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native'
```

Preset은 이 문서와 함께 생성된 `workload-presets.json`의 trace checksum을 기준으로 한다. trace archive가 바뀌면 이 generator를 다시 실행한다.
