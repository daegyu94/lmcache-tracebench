# Trace release assets

기록한 storage trace를 GitHub Release asset으로 공유하거나 내려받는 방법입니다.
`scripts/release_asset.sh`는 GitHub Release 생성, trace upload, download를 제공합니다.
`--help`를 제외한 모든 command는 `gh` CLI 설치와 GitHub 인증이 필요합니다.

```bash
gh auth login
```

## Release 생성

```bash
bash scripts/release_asset.sh release \
  --tag tensormesh-benchmark-20260805 \
  --title "Tensormesh benchmark traces (2026-08-05)"
```

## Trace upload

기록한 trace 하나를 기존 GitHub Release의 asset으로 등록하려면 다음을 실행합니다.
`--filename`은 Release에 표시할 asset 이름이고, `--filepath`은 로컬 파일 경로입니다.

```bash
bash scripts/release_asset.sh upload --tag tensormesh-benchmark-20260805 \
  --filename wildclaw_storage.lct \
  --filepath /MNTPNT/lmcache-tracebench/outputs/source-traces-20260804-082231/wildclaw/storage.lct
```

위 예시는 `wildclaw_storage.lct`를 해당 release에 업로드합니다. 같은 이름의 asset을
교체하려면 `--clobber`를 추가하고, 실제 업로드 없이 이름과 command를 확인하려면
`--dry-run`을 사용합니다. 같은 `--tag`에도 서로 다른 `--filename`을 지정하면 여러 trace
file을 추가할 수 있습니다. GitHub Release asset 하나는 2 GiB 미만이어야 하므로 2 GiB 이상의
file은 script가 자동으로 최대 1900 MiB 크기의 `NAME.part-001`, `NAME.part-002` 형식 asset으로
분할해 업로드합니다. Split part는 업로드가 끝나면 삭제됩니다.

## Trace download

Release에서 trace를 내려받으려면 다음을 실행합니다. split asset은 자동으로 결합해
`--output-dir`에 원본 파일을 만들며, `--keep-parts`를 지정하지 않으면 다운로드한 part를
정리합니다.

```bash
bash scripts/release_asset.sh download \
  --tag tensormesh-benchmark-20260805 \
  --filename swebench_storage.lct \
  --output-dir downloads
```
