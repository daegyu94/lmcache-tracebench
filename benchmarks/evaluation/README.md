# Staged remote report experiments

이 디렉터리는 report/performance-evaluation.md의 그림별 실험을 staged remote
storage에서 실행하는 진입점이다. 실제 LMCache replay 명령은
benchmarks/replayer/replay_speed_sweep.sh가 담당하고, 이 runner는 그래프별
workload/backend/speedup/repeat matrix와 원격 run directory만 관리한다.

## 준비

Topology 작성, trace/repository 준비와 결과 회수 방식은
[Staged remote replay guide](../../docs/staged-remote-replay.md)를 따른다.
Runner는 기본적으로 필요한 preparation을 호출하며, 이미 준비된 환경에서만
`--skip-prepare`를 사용한다. Backend spec은 remote config와 L2 path를 전달하므로
placeholder가 포함된 `CONFIG`와 `L2_ROOT` 사이를 `|`로 구분한다.

기본적으로 controller의 project virtual environment를 자동으로 활성화하는
shell entrypoint를 사용한다.

    bash benchmarks/evaluation/run_report_experiments.sh --help

## 1. 가장 작은 dry-run

실제 SSH나 replay를 시작하지 않고 matrix와 remote command만 확인한다.

    bash benchmarks/evaluation/run_report_experiments.sh \
      --topology configs/replayer/staged-remote/example.yaml \
      --graph speedup \
      --backend-spec 'xfs=@REPO_ROOT@/configs/replayer/xfs.yaml|@L2_ROOT@/xfs' \
      --workloads tensormesh-swebench \
      --speedups 1,1.25 \
      --trace-percent 10 \
      --repeats 1 \
      --skip-prepare \
      --dry-run

`--workload-preset smoke`를 쓰면 5개 canonical workload 전체를 10 GB 이내의
trace prefix로 줄여 end-to-end pipeline 검증용 matrix를 빠르게 돌려볼 수 있다.

    bash benchmarks/evaluation/run_report_experiments.sh \
      --topology configs/replayer/staged-remote/example.yaml \
      --graph speedup \
      --backend-spec 'xfs=@REPO_ROOT@/configs/replayer/xfs.yaml|@L2_ROOT@/xfs' \
      --workload-preset smoke \
      --repeats 1 \
      --skip-prepare \
      --dry-run

## 2. Workload preset과 preflight estimate

[`preflight-estimates.md`](preflight-estimates.md)에는 workload별 preset의
`trace_percent`, source window, estimated peak가 정리되어 있다.
`workload-presets.json`은 이 preflight 분석에서 계산한 `smoke`, `full`과
`0.5tb`, `1tb`, `2tb`, `4tb` strict target preset을 제공한다.

`full`은 모든 workload를 full trace로 사용한다. 모든 workload를 target 안에 넣으려면
strict preset 중 하나를 선택한다. `smoke`는 10 GB target으로 각 workload의 trace
prefix를 줄여 end-to-end pipeline 검증용으로 빠르게 실행한다.

    bash benchmarks/evaluation/run_report_experiments.sh \
      --topology configs/replayer/staged-remote/b300.yaml \
      --graph speedup \
      --workload-preset full \
      --backend-spec 'xfs=@REPO_ROOT@/configs/replayer/xfs.yaml|@L2_ROOT@/xfs'

Preset을 쓰면 workload마다 서로 다른 `--trace-percent`가 replay command와
`case.json`에 기록된다. `--dry-run`은 preset에 기록된 첫/마지막 submission
timestamp 차이를 이용해 다음을 함께 출력한다.

- `one_case_min`: 한 backend/repeat/speedup case가 source schedule만 재생할 때의 최소 시간
- `Minimum sequential replay schedule`: 현재 matrix의 모든 case를 순차 실행한다고 가정한 최소 시간

계산식은 `source_submission_window_seconds / speedup`이다. 따라서 L2 preparation,
backend startup/mount와 filesystem metadata 작업, SSH/trace 전송, async drain과
schedule lag는 포함하지 않는 하한(lower bound)이다. 이 값은
`run-config.json`, `matrix-plan.json`, 각 `case.json`에도 기록된다. Target preset의
window는 fixed-percent preflight row에서 보간한 값이므로 실제 trace를 target prefix로
재분석하지 않은 경우에는 대략적인 계획값이다. Trace archive가 바뀌면 replay node
또는 controller에 같은 trace root를 준비하고 다음 generator를 다시 실행한다.

    python benchmarks/evaluation/generate_preflight_estimates.py \
      --trace-root /path/to/trace-root \
      --source-revision 'HF daegyu94/lmcache-storage-traces@main'

Generator의 target mapping은 fixed-percent preflight row를 보수적으로
interpolate한다. 대용량 trace를 target별로 다시 읽어 검증하려면
`--validate-targets`를 추가한다.

## 3. Replay 실행

먼저 단일 case로 topology와 backend를 검증한 뒤, 여러 workload/backend/speedup을 한 번에 실행한다.

### 단일 실행

trace와 replay repository가 replay node에 이미 준비된 환경에서 단일 workload,
backend, repeat만 실행해 command와 결과 수집 경로를 확인한다. 준비가 끝나지 않은
환경에서는 `--skip-prepare`를 제거한다.

    bash benchmarks/evaluation/run_report_experiments.sh \
      --topology <topology.yaml> \
      --graph throughput \
      --workloads tensormesh-wildclaw \
      --backend-spec 'xfs=@REPO_ROOT@/configs/replayer/xfs.yaml|@L2_ROOT@/xfs' \
      --trace-percent 1 \
      --repeats 1 \
      --skip-prepare

실행이 완료되면 controller의 state root와 topology의 `controller_output_root`에서
상태와 결과를 확인한다.

    cat <state_root>/matrix-summary.json
    cat <state_root>/cases/throughput/tensormesh-wildclaw/xfs/nbaseline/s1/r1/case.json
    python -m json.tool \
      <controller_output_root>/<run-name>/x1/l2_replay_stats.json

`<state_root>`는 `--state-root` 값이며 기본값은 `outputs/report-experiments-staged`다.
`matrix-summary.json`의 `completed: 1`, `failed: 0`과 case.json의 `"status": "ok"`를
확인하면 단일 replay case가 정상적으로 완료된 것이다.


### 여러 backend/speedup 실행

아래는 여러 backend와 speedup/repeat을 한 번에 실행하는 예시다.

    bash benchmarks/evaluation/run_report_experiments.sh \
      --topology configs/replayer/staged-remote/b300.yaml \
      --graph speedup \
      --backend-spec 'xfs=@REPO_ROOT@/configs/replayer/xfs.yaml|@L2_ROOT@/xfs' \
      --backend-spec '3FS=@REPO_ROOT@/configs/replayer/3fs.yaml|@L2_ROOT@/3fs' \
      --backend-spec 'pNFS=@REPO_ROOT@/configs/replayer/pnfs.yaml|@L2_ROOT@/pnfs' \
      --profile @REPO_ROOT@/configs/profiling/storage02-07.yaml \
      --workload-preset 1tb \
      --repeats 3

Runner는 report workload label을 `<suite>/<workload>/l2.lct` archive layout에
매핑한다. `--workload-preset`은 workload별 percent를 backend, speedup과 repeat에
동일하게 전달해 `case.json`에 기록한다. `--local-trace-root`를 지정하면 size와
checksum도 남긴다. Subset 선택 원칙은
[Documentation guidelines](../../docs/documentation-guidelines.md#tracereplay-실험-기록)를
따른다.

### Case iteration 순서

`--asset PATH`는 선택값이며 반복 지정할 수 있다. 지정하면 해당 HF trace
archive(예: `tensormesh/swebench`)를 matrix 실행 전에 staging(`prepare-trace`)만
하고, case 자체는 만들지 않는다. 어느 trace를 어떤 workload에 쓸지는
`--workloads`/`--workload-preset`과 `--trace-root`/`--trace-name`이 결정한다.

case는 `--workloads`/`--workload-preset`, `--backend-spec`,
`--speedups`, `--node-counts`, `--repeats`로 형성되며, 다음 순서로 중첩해 생성된다.

    graph → workload → backend → node_count → speedup → repeat

`--workloads`를 생략하면 graph preset의 기본 workload 전체가 실행되고, 콤마로
지정하면 그 부분집합만 실행된다. `--workload-preset`은 workload 목록을 정하는
대신 선택된 workload들의 `trace_percent`/source window를 채워주는 역할이다. 예를
들어 `--workloads tensormesh-swebench,mooncake-conversation --workload-preset 1tb`는
두 workload만 쓰되 1tb preset의 percent를 적용한다.

즉 가장 바깥쪽이 graph, 가장 안쪽이 repeat이다. 예를 들어
`--graph speedup --workloads tensormesh-swebench,tensormesh-gaia
--backend-spec 'xfs=...|...' --backend-spec '3FS=...|...' --repeats 2`를 주면
생성 순서는 다음과 같다.

    speedup/tensormesh-swebench/xfs/nbaseline/s1/r1
    speedup/tensormesh-swebench/xfs/nbaseline/s1/r2
    speedup/tensormesh-swebench/3FS/nbaseline/s1/r1
    speedup/tensormesh-swebench/3FS/nbaseline/s1/r2
    speedup/tensormesh-gaia/xfs/nbaseline/s1/r1
    speedup/tensormesh-gaia/xfs/nbaseline/s1/r2
    speedup/tensormesh-gaia/3FS/nbaseline/s1/r1
    speedup/tensormesh-gaia/3FS/nbaseline/s1/r2

speedup이 여러 개면 repeat 안쪽이 아니라 speedup 아래 repeat이므로, 각
workload/backend/node마다 `s<speedup>`이 오름차순으로 이어지고 그 안에서
`r1..rN`이 반복된다. scaling graph에서는 backend가 `{nodes}`를 쓰면 `node_count`가
`--node-counts`로 확장되어 repeat보다 한 단계 더 바깥으로 끼어든다. 상태 저장
경로도 이 `cases/<graph>/<workload>/<backend>/n<node>/s<speedup>/r<repeat>`를
그대로 따른다.

## 4. 그래프 preset

| --graph | report 그림 | 기본 workload | 기본 speedup |
| --- | --- | --- | --- |
| throughput | 그림 1–2 | 5개 전체 | x1 |
| speedup | 그림 3 | 5개 전체 | x1, x1.25, x1.5, x2 |
| latency | 그림 4 | SWE-bench, Conversation | x1, x2 |
| resource | 그림 5 | 5개 전체 | x1 |
| nodewise | 그림 6 | SWE-bench | x1, x2 |
| scaling | 그림 7 | SWE-bench | x2, node-count sweep |

--workloads와 --speedups를 주면 해당 graph preset을 덮어쓴다. --graph all은
위 preset을 모두 순서대로 실행한다. 각 matrix cell은 기본 3회 반복하며
--repeats로 변경한다. resource/nodewise/scaling에는 같은 remote profiler
설정을 --profile로 전달한다. profiler YAML은 `configs/profiling/` 아래
예제(예: `storage.yaml`, `storage02-07.yaml`)를 참고해 작성하고,
`@REPO_ROOT@` placeholder와 함께 전달한다.

    --profile @REPO_ROOT@/configs/profiling/storage02-07.yaml

## 5. Backend와 node scaling

일반 backend spec 형식은 다음과 같다.

    NAME=CONFIG|L2_ROOT

CONFIG/L2_ROOT에는 staged_remote_replay.sh가 이해하는
@REPO_ROOT@, @TRACE_ROOT@, @OUTPUT_ROOT@, @L2_ROOT@ placeholder를 사용할 수
있다. 예를 들어 xfs는 다음과 같이 local XFS baseline mount를 가리킨다.

    xfs=@REPO_ROOT@/configs/replayer/xfs.yaml|@L2_ROOT@/xfs

fs-native.yaml은 filesystem backend들이 공통으로 상속하는 템플릿이다.
실제 로컬 파일시스템 baseline은 xfs.yaml을 사용하고, 3FS fuse mount와
pNFS도 각각 3fs.yaml, pnfs.yaml을 쓴다. 자세한 backend config 목록은
[replayer guide](../../docs/replayer.md#backend-configuration)를 따른다.

3FS FUSE 경로가 아닌 NIXL usrbio 경로로 동일 storage에 접근하는 HF3FS backend는
`hf3fs.yaml`을 쓴다. 이 backend는 pip NIXL wheel에 없는 `libplugin_HF3FS.so`를
요구하므로 별도 빌드/배포가 필요하다.

    HF3FS=@REPO_ROOT@/configs/replayer/hf3fs.yaml|@L2_ROOT@/3fs/nixl

빌드 절차와 mount_point 설정, backend 비교는
[HF3FS backend guide](../../docs/hf3fs-backend.md)를 따른다.

scaling graph에서 distributed backend를 node 수에 맞춰 바꾸려면 {nodes}를
config 또는 L2 path에 포함한다.

    3FS=@REPO_ROOT@/configs/replayer/3fs-{nodes}.yaml|@L2_ROOT@/3fs-{nodes}

--node-counts 1,2,3,4,5,6은 {nodes} backend를 여섯 개 case로 확장한다.
{nodes}가 없는 backend는 한 번만 실행되어 baseline으로 기록된다. placeholder
확장은 경로만 바꾸므로 실제 3FS/pNFS node activation, mount, striping,
replication 설정은 topology/config에서 별도로 확정해야 한다.

case_id와 상태 경로의 `n<node>` 부분은 node-count 세그먼트다. `{nodes}`를 쓰지
않는(즉 node scaling이 아닌) backend는 node 수가 없으므로 `nbaseline`으로
기록된다. node scaling을 하는 backend(`--node-counts`로 확장)는 `n1`, `n2`,
`n3`처럼 실제 node 수가 들어간다. 따라서 `nbaseline`은 "node scaling이 아닌
단일/기본 실행"을 뜻하는 라벨이며, report 데이터에서는 node 수 `None`으로
해석된다(`import_artifacts.py`가 `"baseline"`을 node 수 미지정으로 정규화한다).

## 6. Resume와 output

state root는 기본적으로 outputs/report-experiments-staged이며, 같은 명령을
다시 실행해도 성공한 case.json은 건너뛴다.

    outputs/report-experiments-staged/
    ├── run-config.json
    ├── matrix-plan.json
    ├── matrix-results.jsonl
    ├── matrix-summary.json
    └── cases/<graph>/<workload>/<backend>/n<node>/s<speedup>/r<repeat>/case.json

실제 replay 결과는 topology의 controller_output_root 아래에
<graph>-<workload>-<backend>-... run name으로 저장된다. case directory
안에는 staged remote output 경로와 실행 command가 함께 기록된다.

실행 도중 중단되면 현재 case marker가 running 또는 interrupted로 남는다.
`--overwrite-output` 정책에 따라 marker가 있는 case를 건너뛸지 결정한다.
runner는 case를 실행할 때 항상 그 run name의 remote와 controller output
directory를 교체한다. 임의의 state directory나 symlink는 삭제하지 않는다.

- `--overwrite-output failed` (기본): 성공한 case는 건너뛰고 실패/중단
  case만 재실행해 원격 run directory를 교체한다.
- `--overwrite-output all`: 성공/실패 관계없이 모든 case를 재실행하고
  원격 run directory를 교체한다. 이전 결과를 유지하려면 별도 state-root를
  사용하거나 원격 directory를 직접 백업한다.
- `--overwrite-output none`: marker가 있는 case는 모두 건너뛴다. 실패
  output도 보존한다.

matrix-summary.json의 completed, failed, interrupted, pending, resume_skipped와
matrix-results.jsonl의 각 case status를 plot 단계의 입력 검증에 사용한다.
replay가 성공한 case의 실제 metric은 result_dir 아래 x<SPEEDUP>/의
l2_replay_stats.json, l2_io_interval.tsv, profile 결과에서 읽는다.
Artifact를 figure 입력으로 정규화하는 명령과 schema는
[Report data contract](../../report/data/README.md)를 따른다.
