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
`sed`, `git`, trace/L2 저장 공간이 필요합니다. `remote_install` 모드에서는 replay node가
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

사내 package index에서 native wheel을 제공하지 않아 source build가 필요하면 `build-essential`
및 해당 backend의 compiler/toolchain도 replay node에 추가해야 합니다. 이 항목은 OS와
package source에 따라 달라지므로 script가 자동 설치하지 않습니다.

## 2. Topology 작성

기본값이 없는 예제를 복사해 실제 값을 모두 채웁니다.

```bash
cp configs/replayer/staged-remote/topology.example.yaml \
  configs/replayer/staged-remote/topology.yaml
```

`topology.yaml`은 단순한 top-level scalar YAML입니다. 기본 항목은 모두 채워야 하며,
`runtime_mode`에 따라 필요한 runtime 항목이 달라집니다. package index URL은 replay
node에 pip 설정이 이미 있으면 생략할 수 있습니다.

| 항목 | 의미 |
| --- | --- |
| `runtime_mode` | `remote_install`(권장) 또는 `copy_venv` fallback |
| `controller_repo_root` | controller에서 clone/stage할 tracebench 경로 |
| `controller_venv_root` | `copy_venv`에서만 필요하며 `controller_repo_root/.venv`여야 함 |
| `controller_trace_root` | controller가 HF archive를 저장할 경로 |
| `controller_output_root` | 회수한 replay 결과를 저장할 경로 |
| `replay_host`, `replay_user`, `replay_port` | SSH 접속 정보 |
| `replay_repo_root` | replay node의 tracebench 경로 |
| `replay_venv_root` | replay node가 만들 venv 경로 (`replay_repo_root/.venv`여야 함) |
| `replay_python` | `remote_install`에서 사용할 replay node의 승인된 Python |
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

### Runtime 설치 모드

`remote_install`이 기본 권장 모드입니다. Repository를 replay node로 전송한 뒤 replay
node에서 다음을 수행합니다.

- `replay_python`으로 `replay_repo_root/.venv`를 생성합니다.
- `replay_runtime_requirements`를 설치하고, 설정된 사내 package index를 사용합니다.
- `pip check`와 LMCache/replayer import 검사를 수행합니다.

이 방식은 replay node의 OS, CPU architecture, Python 및 native wheel 환경을 그대로
사용하므로 controller의 venv를 복사하고 경로를 보정할 필요가 없습니다. `requirements`
파일에 Git VCS URL이 남아 있고 replay cluster에서 GitHub가 차단되어 있으면, 내부 mirror를
가리키는 별도 requirements 파일을 repository에 넣고 `replay_runtime_requirements`로
지정해야 합니다.

`copy_venv`는 package index를 사용할 수 없을 때의 fallback입니다. 이 모드에서만
`controller_venv_root`를 전송하며, console script shebang, editable package의 `.pth`,
`pyvenv.cfg` 경로를 replay node에 맞춰 보정합니다. OS, CPU architecture, Python minor
version 또는 native library가 다르면 이 모드를 사용하지 마세요.

## 3. 단계별 실행

### 3.1 Trace 준비

HF archive를 controller에 받고 replay node로 전송한 뒤, replay trace root에
압축을 풉니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh prepare-trace \
  --topology configs/replayer/staged-remote/topology.yaml \
  --asset tensormesh/wildclaw.tar.gz
```

Mooncake archive가 main에 추가된 뒤에는 같은 명령에 다음 경로를 사용합니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh prepare-trace \
  --topology configs/replayer/staged-remote/topology.yaml \
  --asset mooncake/toolagent.tar.gz \
  --asset mooncake/conversation.tar.gz
```

스크립트는 archive를 `tar --keep-old-files`로 풀어 기존 파일을 덮어쓰지 않습니다.
상세한 archive 구조와 수동 `tar.gz` 명령은 [Trace assets guide](trace-assets.md)를
참고하세요.

### 3.2 Replay 준비

Repository를 controller에 clone한 뒤 replay node로 전송합니다. 기본 `remote_install`
모드에서는 replay node에서 `setup_runtime.sh --profile replayer-cpu --python ...`을 실행해
지정된 Python으로 `.venv`를 만들고, runtime requirements와 사내 package source를
사용해 설치한 뒤 import/pip 검사를 수행합니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh prepare-replay \
  --topology configs/replayer/staged-remote/topology.yaml
```

`remote_install`에서 replay node에 `.venv`가 이미 있으면 경고하고 재설치하지 않습니다.
기존 venv가 지정된 Python과 맞지 않거나 import/pip 검증에 실패하면 중단합니다.
`copy_venv`에서는 controller에 이미 `.venv`가 있으면 재설치하지 않고, replay node에
같은 repository나 venv가 있으면 전송하지 않고 기존 환경을 검증합니다.

### 3.3 Replay 또는 원하는 sweep 실행

실험 모드는 orchestration script가 결정하지 않습니다. 기존 speedup/backend/workload
sweep launcher 또는 별도 replay command를 `--` 뒤에 전달하면 됩니다. 따라서 아직
실험 모드를 정하지 않았어도 동일한 staged workflow를 사용할 수 있습니다.

Placeholder는 script가 replay node의 topology 값으로 치환합니다.

- `@REPO_ROOT@`: replay node의 tracebench 경로
- `@TRACE_ROOT@`: replay node의 압축 해제 trace 경로
- `@OUTPUT_ROOT@`: `replay_output_root/<run-name>`
- `@L2_ROOT@`: topology의 L2 base path
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

다른 launcher도 같은 방식으로 바꿔 전달할 수 있습니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh replay \
  --topology configs/replayer/staged-remote/topology.yaml \
  --run-name backend-wildclaw-20260813 \
  -- bash benchmarks/replayer/replay_backend_sweep.sh \
    --trace @TRACE_ROOT@/tensormesh/wildclaw/l2.lct \
    --config @REPO_ROOT@/configs/replayer/fs-native.yaml \
    --output-root @OUTPUT_ROOT@ \
    --l2-root @L2_ROOT@/backend-wildclaw-20260813 \
    --speedups 1,2,4
```

Remote command가 성공하든 실패하든 script는
`replay_output_root/<run-name>`을 controller의
`controller_output_root/<run-name>`으로 회수합니다. 회수된 디렉터리에는
`remote_exit_code`가 남아 원격 명령의 exit status를 확인할 수 있습니다.

## 4. 한 번에 실행

세 단계를 한 번에 실행할 때는 `all`을 사용합니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh all \
  --topology configs/replayer/staged-remote/topology.yaml \
  --asset tensormesh/wildclaw.tar.gz \
  --run-name wildclaw-speedup-20260813 \
  -- bash benchmarks/replayer/replay_speed_sweep.sh \
    --trace @TRACE_ROOT@/tensormesh/wildclaw/l2.lct \
    --config @REPO_ROOT@/configs/replayer/fs-native.yaml \
    --l2-root @L2_ROOT@/wildclaw-speedup-20260813 \
    --output-root @OUTPUT_ROOT@ \
    --speedups 1,2,4,8
```

## 5. 안전 동작과 재실행

- 원격 repository, `.venv`, archive, 추출 trace가 이미 있으면 경고하고 해당 전송/추출을
  건너뜁니다. 기존 runtime을 자동으로 삭제하거나 덮어쓰지 않습니다.
- 동일한 `run-name`의 remote 또는 controller output이 이미 있으면 replay를 시작하지
  않습니다. 새 run-name을 사용하세요.
- 기존 파일을 지우거나 `--clobber`하는 옵션은 staged script에 없습니다.
- `--dry-run`은 HF/SSH/전송/replay command를 실행하지 않고 계획만 출력합니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh all \
  --topology configs/replayer/staged-remote/topology.yaml \
  --asset tensormesh/wildclaw.tar.gz \
  --run-name dry-run-example \
  --dry-run -- \
  bash benchmarks/replayer/replay_speed_sweep.sh \
  --trace @TRACE_ROOT@/tensormesh/wildclaw/l2.lct \
  --config @REPO_ROOT@/configs/replayer/fs-native.yaml \
  --l2-root @L2_ROOT@/dry-run-example \
  --output-root @OUTPUT_ROOT@ \
  --speedups 1
```

재실행이 필요하면 기존 run-name을 재사용하지 말고 날짜나 실험 조건을 포함한
새 이름을 지정하세요.
