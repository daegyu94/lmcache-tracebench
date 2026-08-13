# Staged remote replay

격리망의 storage/replay host에서 L2 benchmark를 실행할 때는 **controller node**와
**isolated replay node**를 분리합니다. Controller node는 Hugging Face와 GitHub에
접속할 수 있고, SSH/`rsync` 또는 SSH/`scp`로 replay node에 필요한 파일을 보냅니다.
이 방식을 **staged remote replay**라고 부릅니다.

```text
controller node (connected) ── SSH + rsync/scp ──> isolated replay node
        │                                               │
        ├─ HF trace download                            ├─ trace extraction
        ├─ repository/.venv staging                     ├─ venv path repair
        └─ result retrieval <───────────────────────────└─ replay/sweep execution
```

이 가이드는 controller node에서
`benchmarks/replayer/staged_remote_replay.sh`를 실행하는 절차를 설명합니다.

## 1. 준비 조건

Controller node에 다음이 필요합니다.

- `git`, `ssh`, 그리고 topology에서 선택한 `rsync` 또는 `scp`
- GitHub/Hugging Face에 연결할 수 있는 네트워크
- replay node에 passwordless SSH가 되는 key와 `BatchMode` 설정
- replay node와 같은 OS/architecture 및 controller와 동일한 Python minor version

Replay node에는 SSH server, controller와 동일한 Python minor version, `bash`, `tar`, 그리고 trace/L2 저장 공간만
있으면 됩니다. Python package와 LMCache는 controller에서 준비한 `.venv`를 전송하므로
replay node의 package index 접속은 필요하지 않습니다.

SSH를 먼저 확인합니다.

```bash
ssh -o BatchMode=yes -p 22 benchmark@replay-node.example.com true
```

## 2. Topology 작성

기본값이 없는 예제를 복사해 실제 값을 모두 채웁니다.

```bash
cp configs/replayer/staged-remote/topology.example.yaml \
  configs/replayer/staged-remote/topology.yaml
```

`topology.yaml`은 단순한 top-level scalar YAML입니다. 다음 항목은 모두 필수입니다.

| 항목 | 의미 |
| --- | --- |
| `controller_repo_root` | controller에서 clone/stage할 tracebench 경로 |
| `controller_venv_root` | controller venv 경로 (`controller_repo_root/.venv`여야 함) |
| `controller_trace_root` | controller가 HF archive를 저장할 경로 |
| `controller_output_root` | 회수한 replay 결과를 저장할 경로 |
| `replay_host`, `replay_user`, `replay_port` | SSH 접속 정보 |
| `replay_repo_root` | replay node의 tracebench 경로 |
| `replay_venv_root` | replay venv 경로 (`replay_repo_root/.venv`여야 함) |
| `replay_trace_root` | replay node의 압축 해제 trace root |
| `replay_output_root` | replay node의 run별 결과 상위 경로 |
| `replay_l2_root` | L2 replay용 disposable base path |
| `git_repo_url`, `git_revision` | controller/replay에 보낼 repository source |
| `hf_repo_id`, `hf_revision` | trace archive source |
| `transfer_method` | `rsync` 또는 `scp` |

모든 경로는 혼동을 피하기 위해 absolute path를 사용합니다. `replay_l2_root`는
mount root나 다른 실험과 공유하지 않는 benchmark 전용 경로로 지정합니다.

### venv 경로와 호환성

venv는 완전히 relocatable하지 않습니다. console script의 shebang, editable package의
`.pth`, `pyvenv.cfg`에 절대 경로가 들어갈 수 있습니다. 그래서 이 workflow는 다음을
강제합니다.

- 양쪽 venv 경로는 각 repository의 `.venv`여야 합니다.
- 전송 후 replay node의 Python 위치와 controller의 Python minor version에 맞춰 `bin/python`,
  `pyvenv.cfg`, console script shebang을 보정합니다.
- controller repository 경로가 `.pth`에 남아 있으면 replay repository 경로로 치환합니다.
- OS, CPU architecture, Python minor version이 다르면 native wheel이 동작하지 않을 수
  있으므로 이 경우에는 venv 복사 방식을 사용하지 말고 별도 image/wheelhouse를 준비합니다.

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

Repository를 controller에 clone하고 `.venv`가 없으면 controller에서
`setup_runtime.sh --profile replayer`를 실행합니다. 준비된 repository와 `.venv`를
replay node로 전송한 뒤, 원격 venv 경로를 보정하고 import/pip 검사를 수행합니다.

```bash
bash benchmarks/replayer/staged_remote_replay.sh prepare-replay \
  --topology configs/replayer/staged-remote/topology.yaml
```

이 단계에서 controller에 이미 `.venv`가 있으면 경고만 출력하고 재설치하지 않습니다.
replay node에 같은 repository나 `.venv`가 이미 있으면 전송하지 않고, 기존 환경을
그대로 검증합니다.

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
  건너뜁니다.
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
