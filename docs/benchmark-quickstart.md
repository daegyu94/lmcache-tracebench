# L2 benchmark quickstart

이 문서는 이미 기록된 `l2.lct`로 L2 backend 실험을 시작할 때 따라갈 최소
순서만 제공합니다. CLI 옵션, archive 명령, metric 정의는 각각의 기준 문서로
연결하며 여기에서 반복하지 않습니다.

Trace를 직접 만들려면 [Recorder guide](recorder.md), report figure 전체 matrix를
실행하려면 [Report runner](../benchmarks/report/README.md)를 사용하세요.

## 실행 경로 선택

| 환경 | 시작 문서 |
| --- | --- |
| Replay host에서 repository와 storage에 직접 접근 가능 | 이 quickstart |
| Controller만 외부망에 연결되고 replay node는 격리됨 | [Staged remote replay](staged-remote-replay.md) |
| Figure별 workload/backend/repeat matrix가 필요함 | [Report runner](../benchmarks/report/README.md) |

## 1. Host와 target 확인

검증 기준은 Ubuntu 24.04와 Python 3.12입니다. Git, build toolchain, target
filesystem의 읽기·쓰기 권한과 trace/L2/output을 위한 공간을 확인합니다.

```bash
command -v git
python --version
df -h /path/to/trace-root /path/to/l2-root /path/to/output-root
```

> [!CAUTION]
> Sweep의 L2 root는 benchmark 전용 disposable directory여야 합니다. Launcher가
> case 전에 내용을 지울 수 있으므로 기존 데이터, mount root, symlink 또는 공유
> 경로를 지정하지 마세요.

## 2. Replayer 설치

Profile 정의와 설치 명령은 root [Prerequisites](../README.md#prerequisites)를
따릅니다. CPU replay의 기본 profile은 `replayer-cpu`입니다. 설치 후 같은
profile에 `--check`를 붙여 runtime을 검증합니다.

L2 trace replay만 수행할 때는 TensorMesh submodule이나 GPU가 필요하지 않습니다.
설치 출처와 package 상태를 더 확인해야 하면 다음 명령을 사용합니다.

```bash
source .venv/bin/activate
python -m pip check
lmcache trace replay --help
```

## 3. Trace 준비

Canonical archive 이름, 다운로드, 압축 해제와 directory layout은
[Trace assets](trace-assets.md)를 따릅니다. Replay할 파일은 workload별
`<trace-root>/<suite>/<workload>/l2.lct` 경로에 둡니다.

```bash
test -s <trace-root>/<suite>/<workload>/l2.lct
sha256sum <trace-root>/<suite>/<workload>/l2.lct
```

긴 trace의 prefix만 사용할 때는 파일을 별도로 자르지 않고 replay의
`--trace-percent`를 사용합니다. 선택 규칙은
[Replayer guide](replayer.md#l2-adapter-trace)에 정의되어 있습니다.

## 4. 단일 replay 검증

[Replayer guide의 Replay 절](replayer.md#replay)에 있는 단일 command를 먼저
`--dry-run`으로 실행합니다. 다음 값만 현재 host에 맞게 정합니다.

- `--trace`: 3단계에서 확인한 `l2.lct`
- `--config`: target adapter config
- `--l2-path`: benchmark 전용 disposable path
- `--output-dir`: L2 target과 분리된 새 결과 path
- `--trace-percent`: smoke test에는 작은 prefix

Dry-run의 경로와 command가 맞으면 `--dry-run`을 제거합니다. 성공 여부는
[Benchmark artifact layout](../benchmarks/README.md#artifact-layout)의 파일 존재와
[L2 replay metric guide](l2-replay-metrics.md)의 drain/validity 항목으로
확인합니다.

## 5. Experiment로 확장

단일 replay가 통과한 뒤 목적에 맞는 launcher 하나를 선택합니다.

| 목적 | 기준 절 |
| --- | --- |
| 동일 trace의 arrival-rate sweep | [Replay speedup sweep](replayer.md#replay-speedup-sweep) |
| 여러 workload 비교 | [Replay workload sweep](replayer.md#replay-workload-sweep) |
| Backend/config/mount 비교 | [Backend configuration](replayer.md#backend-configuration) |
| 동일 trace의 병렬 process 부하 | [Parallel replicated replay](replayer.md#parallel-replicated-replay) |
| Storage/network counter 수집 | [Profiling](replayer.md#profiling) |
| Figure별 staged remote matrix | [Report runner](../benchmarks/report/README.md) |

비교 case의 trace, subset, backend, mount와 반복 조건은
[Documentation guidelines](documentation-guidelines.md#tracereplay-실험-기록)의
실험 기록 목록을 따릅니다.

## 6. 결과 확인

Launcher별 summary와 case artifact 이름은
[Benchmark guide](../benchmarks/README.md#artifact-layout), 각 JSON/TSV field의
정의와 비교 순서는 [L2 replay metric guide](l2-replay-metrics.md)를 사용합니다.
Report figure와 artifact의 연결은
[Performance report](../report/performance-evaluation.md)의 각 실험 절에서 관리합니다.

## 실패 시 확인 순서

1. 선택한 runtime profile의 `--check`를 다시 실행합니다.
2. Trace가 존재하고 크기가 0보다 큰지 확인합니다.
3. `--dry-run`에서 config, L2 path와 output path를 확인합니다.
4. Case의 `lmcache-prepare.log`와 `lmcache-replay.log`를 확인합니다.
5. 상위 launcher summary와 log에서 실패 case를 찾습니다.
6. Trace validity 오류는 [L2 tracing guide](l2-tracing.md), backend/profile 오류는
   [Replayer guide](replayer.md)를 확인합니다.
