# Staged remote report experiments

이 디렉터리는 report/performance-evaluation.md의 그림별 실험을 staged remote
storage에서 실행하는 진입점이다. 실제 LMCache replay 명령은
benchmarks/replayer/replay_speed_sweep.sh가 담당하고, 이 runner는 그래프별
workload/backend/speedup/repeat matrix와 원격 run directory만 관리한다.

## 준비

1. staged remote topology를 준비한다. 예시는
   configs/replayer/staged-remote/topology.example.yaml와 b300.yaml이다.
2. topology의 git_revision은 trace-percent 옵션을 포함한 artifacts 커밋 또는
   그 이후 커밋을 가리켜야 한다. 원격 replay repository가 오래된 main을
   가리키면 원격 speed sweep이 --trace-percent를 알지 못한다.
3. trace archive를 staged remote로 올릴 경우 --asset을 지정한다. 이미
   topology의 replay_trace_root에 trace가 있으면 --skip-prepare로 preparation을
   건너뛸 수 있다.
4. backend spec의 config와 L2 path는 remote command에 전달되는 문자열이다.
   topology placeholder가 들어갈 수 있으므로 CONFIG와 L2_ROOT 사이는 |로
   구분한다.

기본적으로 controller의 project virtual environment를 자동으로 활성화하는
shell entrypoint를 사용한다.

    bash benchmarks/report/run_report_experiments.sh --help

## 가장 작은 dry-run

실제 SSH나 replay를 시작하지 않고 matrix와 remote command만 확인한다.

    bash benchmarks/report/run_report_experiments.sh \
      --topology configs/replayer/staged-remote/topology.example.yaml \
      --graph speedup \
      --backend-spec 'fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native' \
      --workloads tensormesh-swebench \
      --speedups 1,1.25 \
      --trace-percent 10 \
      --repeats 1 \
      --skip-prepare \
      --dry-run

## 실제 speedup 실험

    bash benchmarks/report/run_report_experiments.sh \
      --topology configs/replayer/staged-remote/b300.yaml \
      --graph speedup \
      --asset tensormesh/swebench.tar.gz \
      --backend-spec 'fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native' \
      --backend-spec '3FS=@REPO_ROOT@/configs/replayer/nixl-hf3fs.yaml|@L2_ROOT@/3fs' \
      --backend-spec 'pNFS=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/pnfs' \
      --trace-percent 10 \
      --repeats 3

report workload label tensormesh-swebench는 staged archive의
tensormesh/swebench/l2.lct로, mooncake-toolagent와 mooncake-conversation은
각각 mooncake/toolagent/l2.lct와 mooncake/conversation/l2.lct로 매핑된다.
SWE-bench, mooncake-toolagent, mooncake-conversation처럼 원본 trace가 큰
workload는 --trace-percent로 동일한 prefix subset을 선택한다. 모든 backend,
speedup, repeat은 같은 trace-percent를 사용하고, case.json에 선택 비율을
기록한다. 원본 trace의 controller-side mirror가 있으면 --local-trace-root를
추가해 size_bytes와 sha256도 기록할 수 있다.

## 그래프 preset

| --graph | report 그림 | 기본 workload | 기본 speedup |
| --- | --- | --- | --- |
| throughput | 그림 1–2 | 5개 전체 | x1 |
| speedup | 그림 3 | 5개 전체 | x1, x1.25, x1.5, x2 |
| latency | 그림 4 | SWE-bench, Conversation | x1, x2 |
| resource | 그림 5 | SWE-bench | x1 |
| nodewise | 그림 6 | SWE-bench | x1, x2 |
| scaling | 그림 7 | SWE-bench | x2, node-count sweep |

--workloads와 --speedups를 주면 해당 graph preset을 덮어쓴다. --graph all은
위 preset을 모두 순서대로 실행한다. 각 matrix cell은 기본 3회 반복하며
--repeats로 변경한다. resource/nodewise/scaling에는 같은 remote profiler
설정을 --profile로 전달한다.

## Backend와 node scaling

일반 backend spec 형식은 다음과 같다.

    NAME=CONFIG|L2_ROOT

CONFIG/L2_ROOT에는 staged_remote_replay.sh가 이해하는
@REPO_ROOT@, @TRACE_ROOT@, @OUTPUT_ROOT@, @L2_ROOT@ placeholder를 사용할 수
있다. 예를 들어 fs-native는 다음과 같이 local XFS baseline mount를 가리킨다.

    fs-native=@REPO_ROOT@/configs/replayer/fs-native.yaml|@L2_ROOT@/fs-native

scaling graph에서 distributed backend를 node 수에 맞춰 바꾸려면 {nodes}를
config 또는 L2 path에 포함한다.

    3FS=@REPO_ROOT@/configs/replayer/nixl-hf3fs-{nodes}.yaml|@L2_ROOT@/3fs-{nodes}

--node-counts 1,2,3,4,5,6은 {nodes} backend를 여섯 개 case로 확장한다.
{nodes}가 없는 backend는 한 번만 실행되어 baseline으로 기록된다. placeholder
확장은 경로만 바꾸므로 실제 3FS/pNFS node activation, mount, striping,
replication 설정은 topology/config에서 별도로 확정해야 한다.

## Resume와 output

state root는 기본적으로 outputs/report-experiments-staged이며, 같은 명령을
다시 실행해도 성공한 case.json은 건너뛴다.

    outputs/report-experiments-staged/
    ├── run-config.json
    ├── matrix-plan.json
    ├── matrix-results.jsonl
    ├── matrix-summary.json
    └── cases/<graph>/<workload>/<backend>/n<node>/s<speedup>/r<repeat>/case.json

실제 replay 결과는 topology의 controller_output_root 아래에
report-<graph>-<workload>-<backend>-... run name으로 저장된다. case directory
안에는 staged remote output 경로와 실행 command가 함께 기록된다.

실행 도중 중단되면 현재 case marker가 running 또는 interrupted로 남는다.
다음 실행은 완료 case를 재사용하고 미완료 case만 다시 시도한다. runner는 재시도할 때
staged_remote_replay.sh의 --replace-existing를 사용해 그 run name의 remote와
controller output directory만 교체한다. 임의의 state directory나 symlink는
삭제하지 않는다. 실패 output을 보존하고 싶으면 --no-retry-incomplete를
사용하고, 전체 matrix를 처음부터 다시 하려면 --no-resume을 사용한다.

matrix-summary.json의 completed, failed, interrupted, pending, resume_skipped와
matrix-results.jsonl의 각 case status를 plot 단계의 입력 검증에 사용한다.
replay가 성공한 case의 실제 metric은 result_dir 아래 x<SPEEDUP>/의
l2_replay_stats.json, l2_io_interval.tsv, profile 결과에서 읽는다.

## 기존 replayer script와의 관계

replay_speed_sweep.sh는 하나의 trace와 speedup 목록을 실행하는 공통 primitive로
남겨 두었다. replay_backend_sweep.sh와 replay_workload_sweep.sh도 report 외
반복 작업에서 사용할 수 있으므로 삭제하지 않았다. report 실험에서는 staged
remote run 이름과 resume marker가 필요하기 때문에 이 runner를 사용한다.
