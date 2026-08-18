# Benchmark guide

이 디렉터리는 recorder와 replayer의 반복 실행 launcher를 제공합니다. 각 script의
옵션·실행 순서와 artifact layout만 이 문서에서 관리합니다. Workload 설정은
[Recorder guide](../docs/recorder.md), replay 의미론과 backend 설정은
[Replayer guide](../docs/replayer.md), metric 정의는
[L2 replay metric guide](../docs/l2-replay-metrics.md)를 기준으로 합니다.

```text
benchmarks/
├── recorder/   # workload를 실행해 l2.lct 생성
├── replayer/   # trace를 workload/backend/speedup별로 replay
└── report/     # staged remote report matrix와 resume state 관리
```

## 실행 전 확인

Runtime profile과 설치 명령은 root [README](../README.md#prerequisites)를
사용합니다. 각 launcher는 다음 두 명령으로 현재 옵션과 최종 경로를 먼저
확인할 수 있습니다.

```bash
bash <script> --help
bash <script> ... --dry-run
```

> [!CAUTION]
> Replay launcher의 L2 target은 benchmark 전용 disposable directory여야 합니다.
> 기본 동작은 case 전에 해당 L2 경로를 비우거나 다시 만드는 것입니다. 기존 데이터,
> mount root, symlink 또는 다른 실험과 공유하는 경로를 지정하지 마세요.

공통 동작은 다음과 같습니다.

- `--l2-root`는 target storage mount 아래의 absolute path를 사용합니다.
- `--output-root`는 결과와 launcher log를 저장하며 L2 target과 분리합니다.
- 기존 output case를 덮어쓰지 않습니다. 새 output root를 사용하거나 해당
  launcher의 resume 정책을 따릅니다.
- `--keep-l2`를 지원하는 launcher에서는 case 사이의 L2 content를 유지하지만,
  시작 시 target path가 비어 있어야 합니다.
- Timestamp가 없는 output root에는 UTC suffix가 자동으로 추가됩니다.

## Script catalog

### Recorder

| Script | 역할 | 상세 기준 |
| --- | --- | --- |
| `recorder/record_source_traces.sh` | 선택한 source별 baseline trace 생성 | [Recorder guide](../docs/recorder.md) |
| `recorder/record_speed_sweep.sh` | workload speed별 독립 trace 생성 | [Recorder speed sweep](../docs/recorder.md#speed-sweep) |

지원 source와 `--dataset-percent`, Mooncake 입력, recorder output은 Recorder
guide에서 관리합니다.

### Replayer

| Script | Sweep unit | Case 실행 |
| --- | --- | --- |
| `replayer/replay_speed_sweep.sh` | trace × speedup | speedup 순서대로 실행 |
| `replayer/replay_workload_sweep.sh` | workload × speedup | workload 안에서 speedup 순서 |
| `replayer/replay_backend_sweep.sh` | backend × speedup | backend 안에서 speedup 순서 |
| `replayer/replay_instances.sh` | 동일 trace × process | 독립 process를 병렬 실행 |
| `replayer/replay_l1_size_sweep.sh` | replay buffer size | L1 hit/miss 실험이 아닌 buffer 민감도 |

각 case는 별도의 `python -m replayer.main` 실행입니다. Launcher는 case matrix,
L2 reset, output 보호와 summary 생성을 담당하고, 실제 adapter replay는
`lmcache trace replay`가 수행합니다. 명령 예시는
[Replayer guide](../docs/replayer.md)를 사용합니다.

### Staged remote와 report

| Script | 역할 | 기준 문서 |
| --- | --- | --- |
| `replayer/staged_remote_replay.sh` | Trace/repository 준비, 원격 실행과 결과 회수 | [Staged remote replay](../docs/staged-remote-replay.md) |
| `report/run_report_experiments.sh` | Figure별 matrix, resume와 retry | [Report runner](report/README.md) |

## 실행 계층

```text
report runner 또는 backend/workload launcher
└── replay_speed_sweep.sh
    └── python -m replayer.main
        └── lmcache trace replay
```

상위 launcher가 실패를 기록하고 다음 case를 계속할 수 있으므로, 최종 exit code와
상위 summary를 함께 확인합니다. Report runner만 case별 state marker를 사용해
완료 case를 건너뛰고 미완료 case를 재시도합니다.

## Artifact layout

단일 L2 replay case의 기본 결과는 다음과 같습니다.

```text
<case>/
├── l2_preflight.json
├── l2_prepare_manifest.json
├── l2_replay_stats.json
├── l2_replay_summary.md
├── l2_usage.json
├── lmcache-prepare.log
├── lmcache-replay.log
└── profile/                    # --io-profile 사용 시
```

각 파일의 field와 유효성 판정은
[L2 replay metric guide](../docs/l2-replay-metrics.md)를 참고합니다. Launcher가
추가하는 상위 artifact는 다음과 같습니다.

| Launcher | 상위 artifact |
| --- | --- |
| Speedup | `sweep-summary.json`, `sweep-summary.csv`, `sweep-results.jsonl`, `sweep.log` |
| Workload | `workload-summary.json`, `workload-results.jsonl`, `workload-sweep.log` |
| Backend | `backend-summary.json`, `backend-results.jsonl`, `backend-sweep.log` |
| Instances | `instances-summary.json`, instance별 `launcher.log` |
| Report | `run-config.json`, `matrix-plan.json`, `matrix-results.jsonl`, `matrix-summary.json` |

Trace archive의 canonical layout은 [Trace assets](../docs/trace-assets.md), report
figure와 artifact의 연결은 [Performance report](../report/performance-evaluation.md)를
따릅니다.
