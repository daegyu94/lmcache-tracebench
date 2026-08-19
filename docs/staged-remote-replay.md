# Staged remote replay

격리망의 storage/replay host에서 L2 benchmark를 실행할 때는 **controller node**와
**isolated replay node**를 분리합니다. Controller node는 Hugging Face와 GitHub에
접속할 수 있고, SSH/`rsync` 또는 SSH/`scp`로 replay node에 필요한 파일을 보냅니다.
이 방식을 **staged remote replay**라고 부릅니다.

```text
                         controller ↔ cluster network
┌───────────────────────────────┐
│ controller node               │
│                               │
│ - HF trace download           │
│ - Git repository              │
│ - result collection           │
└───────────────┬───────────────┘
                │ SSH + rsync/scp
                │ trace archive, repository
                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ isolated storage cluster                                                    │
│                                                                             │
│  ┌───────────────────────────────┐                                          │
│  │ replay node                   │                                          │
│  │ - trace extraction            │                                          │
│  │ - runtime installation        │                                          │
│  │ - replay / sweep execution    │                                          │
│  │ - profiler control (optional) │                                          │
│  └───────────────┬───────────────┘                                          │
│                 │ L2 I/O / replay traffic                                   │
│                 ▼                                                           │
│  ┌──────────────────────────────────────────────────┐                       │
│  │ storage cluster network                          │                       │
│  └───────┬─────────────────┬─────────────────┬──────┘                       │
│          │                 │                 │                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │ storage-01  │  │ storage-02  │  │ storage-03  │                          │
│  │ L2 target   │  │ L2 target   │  │ L2 target   │                          │
│  └──────────────┘  └──────────────┘  └──────────────┘                       │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │ storage-04  │  │ storage-05  │  │ storage-06  │                          │
│  │ L2 target   │  │ L2 target   │  │ L2 target   │                          │
│  └──────────────┘  └──────────────┘  └──────────────┘                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                ▲
                │ output retrieval via SSH + rsync/scp
                └────────────────────────────────────────
```

Controller와 storage cluster는 서로 네트워크로 연결되어 있어 SSH/`rsync` 또는
SSH/`scp`로 trace, repository, 결과 파일을 전송합니다. replay node는 storage
cluster 내부에서 지정된 Python과 사내 package source를 사용해 runtime을 직접
구성합니다. storage cluster 내부에서는 외부망 접근이 차단되는 것을 전제로 하며,
replay 실행 중에는 전달받은 파일과 cluster 내부 경로만 사용합니다.
`storage-01`부터 `storage-06`의 host/device/interface 정보는 replay repository의
`configs/profiling/storage.yaml` 또는 별도 profiling config에 기록합니다.

이 가이드는 controller node에서
`benchmarks/replayer/staged_remote_replay.sh`를 실행하는 절차를 설명합니다.

## 1. 준비 조건

Controller node에 다음이 필요합니다.

- `git`, `ssh`, 그리고 topology에서 선택한 `rsync` 또는 `scp`
- GitHub/Hugging Face에 연결할 수 있는 네트워크
- replay node에 passwordless SSH가 되는 key와 `BatchMode` 설정
- topology에 지정할 replay node의 Python 경로와 package source 정보

Replay node에는 SSH server, topology에서 지정한 Python interpreter, `bash`, `tar`, `find`,
`sed`, `git`, trace/L2 저장 공간이 필요합니다. Replay node가
사내 package index 또는 사전 구성된 pip 설정을 사용해 runtime dependency를 설치하므로
외부망 접근은 필요하지 않습니다. `uv`는 검사·보고되지만 기본적으로 선택 사항이며,
Python의 `venv`/`pip` fallback을 사용할 수 있습니다.

SSH를 먼저 확인합니다.

```bash
ssh -o BatchMode=yes -p 22 benchmark@replay-node.example.com true
```

### Replay node prerequisite 검사

`prepare-replay`에도 같은 검사가 포함되지만, 설치 전에 단독으로 확인할 수 있습니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh check-prerequisites \
  --topology configs/replayer/staged-remote/topology.yaml
```

검사는 Python `>=3.10`, `venv`/`ensurepip`, `bash`, `tar`, `find`, `sed`, 기본 파일 도구,
`git`을 확인합니다. `uv`는 topology의 `replay_require_uv: true`일 때만 필수로 취급합니다.
Ubuntu/Debian 계열에서 기본 도구가 없다면 다음과 같이 준비할 수 있습니다.

```bash
sudo apt-get install -y bash coreutils findutils sed tar gzip git \
  python3.12 python3.12-venv
```

### Source build 의존성은 수동 설치

위 명령은 staged script가 확인하는 기본 OS 도구만 준비합니다. `setup_runtime.sh`가
설치하는 Python package와는 별개로, 사내 package index에 native wheel이 없으면
`pip install`이 package source를 replay node에서 직접 컴파일합니다. 이 경우
`prepare-replay`를 실행하기 전에 replay node 관리자(또는 node image)가 다음을
수동으로 준비해야 합니다.

- 기본 C/C++ 빌드 도구: `build-essential` (예: `gcc`, `g++`, `make`)
- 선택한 backend가 요구하는 compiler/toolchain과 개발 library
  (예: CUDA toolkit, `cmake`, Rust toolchain 또는 backend SDK)

Ubuntu/Debian에서 필요한 기본 도구를 설치하는 예시는 다음과 같습니다.

```bash
sudo apt-get update
sudo apt-get install -y build-essential
```

Backend별 추가 항목은 해당 backend와 OS 문서를 기준으로 설치합니다. 어떤 native
wheel을 제공하는지는 package index와 Python/CPU architecture에 따라 달라지므로,
setup script는 이 system package를 자동 설치하지 않습니다. 따라서 source build가
필요한 환경에서는 toolchain 설치를 먼저 완료한 뒤 `prepare-replay`를 실행하세요.

## 2. Topology 작성

기본값이 없는 예제를 복사해 실제 값을 모두 채웁니다.

```bash
cp configs/replayer/staged-remote/example.yaml \
  configs/replayer/staged-remote/topology.yaml
```

`topology.yaml`은 단순한 top-level scalar YAML입니다. 기본 항목은 모두 채워야 합니다.
Package index URL은 replay node에 pip 설정이 이미 있으면 생략할 수 있습니다.

| 항목 | 의미 |
| --- | --- |
| `controller_repo_root` | controller에서 clone/stage할 tracebench 경로 |
| `controller_trace_root` | controller가 HF archive를 저장할 경로 |
| `controller_output_root` | 회수한 replay 결과를 저장할 경로 |
| `replay_host`, `replay_user`, `replay_port` | SSH 접속 정보 |
| `replay_repo_root` | replay node의 tracebench 경로 |
| `replay_venv_root` | replay node가 만들 venv 경로 (`replay_repo_root/.venv`여야 함) |
| `replay_python` | replay node의 venv를 만들 때 사용할 승인된 Python |
| `replay_runtime_requirements` | replay repository 안의 runtime requirements 파일 |
| `replay_package_index_url` | 사내 Python package index URL; 생략하면 replay node의 pip 설정 사용 |
| `replay_extra_index_url` | 추가 package index URL(선택) |
| `replay_require_uv` | `true`이면 `uv`가 없을 때 prerequisite 검사 실패; 기본 예제는 `false` |
| `replay_trace_root` | replay node의 압축 해제 trace root |
| `replay_output_root` | replay node의 run별 결과 상위 경로 |
| `replay_l2_root` | L2 replay용 disposable base path |
| `git_repo_url`, `git_revision` | controller/replay에 보낼 repository source |
| `hf_repo_id`, `hf_revision` | trace archive source |
| `transfer_method` | `rsync` 또는 `scp` |

모든 경로는 혼동을 피하기 위해 absolute path를 사용합니다. `replay_l2_root`는
mount root나 다른 실험과 공유하지 않는 benchmark 전용 경로로 지정합니다.

### Runtime 설치

Repository를 replay node로 전송한 뒤 replay node에서 다음을 수행합니다.

- `replay_python`으로 `replay_repo_root/.venv`를 생성합니다.
- `replay_runtime_requirements`를 설치하고, 설정된 사내 package index를 사용합니다.
- `pip check`와 LMCache/replayer import 검사를 수행합니다.

Replay node의 OS, CPU architecture, Python 및 native wheel 환경을 그대로 사용합니다.
`requirements` 파일에 Git VCS URL이 남아 있고 replay cluster에서 GitHub가 차단되어 있으면,
내부 mirror를 가리키는 별도 requirements 파일을 repository에 넣고
`replay_runtime_requirements`로 지정해야 합니다.

## 3. 단계별 실행

### 3.1 Trace 준비

HF archive를 controller에 받고 replay node로 전송한 뒤, replay trace root에
압축을 풉니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh prepare-trace \
  --topology configs/replayer/staged-remote/topology.yaml \
  --asset tensormesh/wildclaw
```

`--asset`은 `.tar.gz` 확장자를 붙여도 되고 생략해도 됩니다. 반복 지정하면 여러
archive를 함께 준비할 수 있습니다. Archive layout과
수동 압축 해제 방법은 [Trace assets guide](trace-assets.md)를 기준으로 하며, staged
script는 기존 파일을 덮어쓰지 않도록 archive를 풉니다.

### 3.2 Replay 준비

Repository를 controller에 clone한 뒤 replay node로 전송합니다. `prepare-replay`는
위 Runtime 설치 절차에 따라 `.venv`와 runtime requirements를 준비하고 import/pip
검사를 수행합니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh prepare-replay \
  --topology configs/replayer/staged-remote/topology.yaml
```

Replay node에 `.venv`가 이미 있으면 경고하고 재설치하지 않습니다. 기존 venv가 지정된
Python과 맞지 않거나 import/pip 검증에 실패하면 중단합니다.

### 3.3 Replay 또는 원하는 sweep 실행

실험 모드는 orchestration script가 결정하지 않습니다. 기존 speedup/backend/workload
sweep launcher 또는 별도 replay command를 `--` 뒤에 전달하면 됩니다. 따라서 아직
실험 모드를 정하지 않았어도 동일한 staged workflow를 사용할 수 있습니다.

Placeholder는 script가 replay node의 topology 값으로 치환합니다. 각
`@...@` 자리표시자는 직접 경로를 적는 대신, `--topology` YAML에서 이미 읽어둔
기존 설정값(`replay_repo_root`, `replay_trace_root`, `replay_output_root`,
`replay_l2_root`)에 매핑됩니다. 따라서 replay command에는 고정 경로를 하드코딩하지
않고 placeholder를 써서, topology 한 곳을 바꾸면 path가 함께 바뀌도록 합니다.
치환은 replay node에 전달하기 직전에 각 command 인자에 대해 단순 문자열 치환으로
수행됩니다.

- `@REPO_ROOT@`: replay node의 tracebench 경로 (`topology.replay_repo_root`)
- `@TRACE_ROOT@`: replay node의 압축 해제 trace 경로 (`topology.replay_trace_root`)
- `@OUTPUT_ROOT@`: `replay_output_root/<run-name>`
- `@L2_ROOT@`: topology의 L2 base path (`topology.replay_l2_root`)
- `@RUN_NAME@`: 현재 run 이름

예를 들어 speedup sweep은 다음처럼 실행합니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh replay \
  --topology configs/replayer/staged-remote/topology.yaml \
  --run-name wildclaw-speedup-20260813 \
  -- bash benchmarks/replayer/replay_speed_sweep.sh \
    --trace @TRACE_ROOT@/tensormesh/wildclaw/l2.lct \
    --config @REPO_ROOT@/configs/replayer/fs-native.yaml \
    --l2-root @L2_ROOT@/wildclaw-speedup-20260813 \
    --output-root @OUTPUT_ROOT@ \
    --speedups 1,2,4,8
```

Backend/workload sweep를 포함한 다른 launcher의 option은
[benchmark script index](../benchmarks/README.md)에서 해당 상세 가이드를 확인한 뒤
같은 방식으로 `--` 뒤에 전달합니다.

Remote command가 성공하든 실패하든 script는
`replay_output_root/<run-name>`을 controller의
`controller_output_root/<run-name>`으로 회수합니다. 회수된 디렉터리에는
`remote_exit_code`가 남아 원격 명령의 exit status를 확인할 수 있습니다.

## 4. 한 번에 실행

세 단계를 한 번에 실행할 때는 `all`을 사용합니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh all \
  --topology configs/replayer/staged-remote/topology.yaml \
  --asset tensormesh/wildclaw \
  --run-name wildclaw-speedup-20260813 \
  -- bash benchmarks/replayer/replay_speed_sweep.sh \
    --trace @TRACE_ROOT@/tensormesh/wildclaw/l2.lct \
    --config @REPO_ROOT@/configs/replayer/fs-native.yaml \
    --l2-root @L2_ROOT@/wildclaw-speedup-20260813 \
    --output-root @OUTPUT_ROOT@ \
    --speedups 1,2,4,8
```

## 5. 안전 동작과 재실행

- 원격 repository, `.venv`, archive, 추출 trace가 이미 있으면 경고하고 해당 준비 작업을
  건너뜁니다. 준비 phase는 기존 runtime이나 asset을 자동으로 덮어쓰지 않습니다.
- replay phase는 동일한 `run-name`의 remote와 controller output을 항상 교체합니다.
  symlink 대상이나 root 경로는 거부합니다.
- replay node 전체를 초기화해야 할 때는 별도 `reset` phase를 씁니다(아래).
- `--dry-run`은 HF/SSH/전송/replay command를 실행하지 않고 계획만 출력합니다.

### Replay node 리셋

`reset` phase는 replay node의 경로를 지워서 다음 `prepare-trace`/`prepare-replay`가
깨끗한 상태에서 시작하도록 합니다. Controller 쪽은 전혀 건드리지 않습니다.
`--target`은 반복 지정할 수 있고 `repo`, `trace`, `output`, `l2`, `all` 중 하나입니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh reset \
  --topology configs/replayer/staged-remote/topology.yaml \
  --target repo --target trace --target output --target l2
```

`repo`/`trace`/`output`은 디렉터리를 통째로 지우고 다시 만듭니다. `l2`
(`replay_l2_root`)는 다릅니다 — pNFS나 3FS가 실제로 연결되면 이 경로 자체가 mount
point가 되므로, 디렉터리를 지우고 다시 만드는 대신 **내용만** 지웁니다(mount된
디렉터리는 `rmdir`할 수 없어 "Device or resource busy"로 실패하기 때문입니다).
`repo`를 리셋하면 `replay_venv_root`가 `replay_repo_root` 하위이므로 `.venv`도 함께 사라집니다.
이후 `prepare-replay`를 다시 실행해야 합니다.
실행 전에 항상 `--dry-run`으로 대상 경로를 먼저 확인하세요.

```bash
bash benchmarks/replayer/staged_remote_replay.sh all \
  --topology configs/replayer/staged-remote/topology.yaml \
  --asset tensormesh/wildclaw \
  --run-name dry-run-example \
  --dry-run -- \
  bash benchmarks/replayer/replay_speed_sweep.sh \
  --trace @TRACE_ROOT@/tensormesh/wildclaw/l2.lct \
  --config @REPO_ROOT@/configs/replayer/fs-native.yaml \
  --l2-root @L2_ROOT@/dry-run-example \
  --output-root @OUTPUT_ROOT@ \
  --speedups 1
```

새 실험은 날짜나 조건을 포함한 새 `run-name`을 사용합니다. Report matrix runner는
`--overwrite-output` 정책으로 완료 case 건너뛰기와 미완료 case 재실행을 결정하며,
case를 실행할 때마다 해당 run name의 remote/controller output을 교체합니다.
자세한 재실행 기준은
[report runner guide](../benchmarks/evaluation/README.md)를 따릅니다.
