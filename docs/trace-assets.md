# Trace release assets

기록한 LMCache trace를 GitHub Release asset 또는 Hugging Face Dataset으로 공유하거나 내려받는 방법입니다.
`tools/artifacts/release_asset.sh`는 GitHub Release 생성, trace upload, download를 제공합니다.
`--help`를 제외한 모든 command는 `gh` CLI 설치와 GitHub 인증이 필요합니다.

```bash
gh auth login
```

## Create a release

```bash
bash tools/artifacts/release_asset.sh release \
  --tag tensormesh-benchmark-20260805 \
  --title "Tensormesh benchmark traces (2026-08-05)"
```

## Upload trace assets

기록한 trace 하나를 기존 GitHub Release의 asset으로 등록하려면 다음을 실행합니다.
`--filename`은 Release에 표시할 asset 이름이고, `--filepath`은 로컬 파일 경로입니다.

```bash
bash tools/artifacts/release_asset.sh upload --tag tensormesh-benchmark-20260805 \
  --filename wildclaw_l2.lct \
  --filepath /MNTPNT/lmcache-tracebench/outputs/source-traces-20260804-082231/wildclaw/l2.lct
```

위 예시는 `wildclaw_l2.lct`를 해당 release에 업로드합니다. 같은 이름의 asset을
교체하려면 `--clobber`를 추가하고, 실제 업로드 없이 이름과 command를 확인하려면
`--dry-run`을 사용합니다. 같은 `--tag`에도 서로 다른 `--filename`을 지정하면 여러 trace
file을 추가할 수 있습니다. GitHub Release asset 하나는 2 GiB 미만이어야 하므로 2 GiB 이상의
file은 script가 자동으로 최대 1900 MiB 크기의 `NAME.part-001`, `NAME.part-002` 형식 asset으로
분할해 업로드합니다. Split part는 업로드가 끝나면 삭제됩니다.

## Download trace assets

Release에서 trace를 내려받으려면 다음을 실행합니다. split asset은 자동으로 결합해
`--output-dir`에 원본 파일을 만들며, `--keep-parts`를 지정하지 않으면 다운로드한 part를
정리합니다.

```bash
bash tools/artifacts/release_asset.sh download \
  --tag tensormesh-benchmark-20260805 \
  --filename swebench_l2.lct \
  --output-dir downloads
```

## Hugging Face Dataset

HF Dataset은 대형 LMCache trace를 workload별 archive로 보관하고 공유할 때
사용합니다. 현재 canonical branch는 `main`이며 저장소는
`daegyu94/lmcache-storage-traces`입니다.

```text
# revision: main
tensormesh/gaia.tar.gz
tensormesh/swebench.tar.gz
tensormesh/wildclaw.tar.gz
```

각 archive 안에는 `<workload>/l2.lct`와 해당 replay에 필요한 recorder 결과가
들어 있습니다. replay node에서는 archive를 받은 뒤 trace root 아래에 압축을
풀어 다음 구조를 만듭니다.

```text
/mnt/nvme/lmcache-l2-replay/traces/tensormesh/
├── gaia/l2.lct
├── swebench/l2.lct
└── wildclaw/l2.lct
```

### Authentication

업로드에는 `huggingface_hub`와 write 권한이 있는 `HF_TOKEN`이 필요합니다.
프로젝트 가상환경을 활성화한 뒤 다음처럼 준비합니다.

```bash
source .venv/bin/activate
python -m pip install 'huggingface_hub>=1.0,<2.0'
export HF_TOKEN=...
```

### Upload

HF script는 기존 파일을 덮어쓰지 않습니다. 의도적으로 교체할 때만 `--clobber`를
추가합니다.

```bash
bash tools/artifacts/hf_trace_asset.sh upload \
  --repo-id daegyu94/lmcache-storage-traces \
  --filepath /path/to/gaia.tar.gz \
  --path-in-repo tensormesh/gaia.tar.gz
```

### Download on a replay node

`--revision`은 Dataset의 branch, tag, 또는 commit이며 기본값은 `main`입니다.
재현 가능한 실험에서는 변경하지 않을 tag 또는 commit을 지정하는 것을 권장합니다.
현재 main의 asset은 archive이므로 다운로드 후 반드시 압축을 풉니다.

```bash
bash tools/artifacts/hf_trace_asset.sh download \
  --repo-id daegyu94/lmcache-storage-traces \
  --revision main \
  --path-in-repo tensormesh/wildclaw.tar.gz \
  --output-dir /mnt/nvme/lmcache-l2-replay/traces
mkdir -p /mnt/nvme/lmcache-l2-replay/traces/tensormesh
tar -xzf /mnt/nvme/lmcache-l2-replay/traces/tensormesh/wildclaw.tar.gz \
  -C /mnt/nvme/lmcache-l2-replay/traces/tensormesh
```

파일 목록은 다음처럼 확인합니다.

```bash
bash tools/artifacts/hf_trace_asset.sh list \
  --repo-id daegyu94/lmcache-storage-traces \
  --revision main
```
