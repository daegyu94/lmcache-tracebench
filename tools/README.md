# Tools index

`tools/`는 benchmark 실행과 분리된 asset·dependency 관리 helper를 둡니다.
실험 launcher와 재실행 state는 [Benchmark guide](../benchmarks/README.md), trace
archive의 이름과 배포 절차는 [Trace assets](../docs/trace-assets.md)를 기준으로 합니다.

| Script | 역할 | 주요 dependency |
| --- | --- | --- |
| `artifacts/release_asset.sh` | GitHub Release 생성과 trace upload/download | authenticated `gh` CLI |
| `artifacts/hf_trace_asset.sh` | Hugging Face Dataset file list/upload/download | project environment의 `huggingface_hub` |
| `artifacts/uv_binary.sh` | 격리 replay node로 전달할 pinned `uv` binary 준비 | `curl`, `tar`, `sha256sum` |

각 script의 현재 option은 `bash SCRIPT --help`로 확인합니다. 이 문서에는 command
예시를 중복하지 않습니다.
