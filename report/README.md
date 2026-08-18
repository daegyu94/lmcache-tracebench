# 성능 평가 리포트 작업 안내

이 디렉터리는 LMCache L2 backend 성능 평가의 본문, 정규화된 figure 입력과
renderer를 관리합니다. 실험 matrix 실행과 원격 artifact 회수는
[Report runner](../benchmarks/evaluation/README.md)가 담당합니다.

Report module은 versioned data와 figure에 종속된 저장소 전용 도구입니다. 따라서
`src/`의 설치 package에 포함하지 않고 이 디렉터리에 함께 두며, 아래 명령은
저장소 root에서 실행합니다.

현재 `report/figures/*.png`는 더미 데이터로 만든 placeholder이며 실측 근거로
사용할 수 없습니다. 실험 조건과 주장-증거 연결은
[성능 평가 보고서](performance-evaluation.md)를 기준으로 합니다.

## 디렉터리 구조

```text
report/
├── __init__.py                  # explicit repository-local Python package
├── performance-evaluation.md    # 실험 설계와 보고서 본문
├── data/
│   ├── README.md                # 공통 dataset contract
│   ├── dummy/                   # versioned placeholder metrics
│   └── measured/                # artifact import 결과; gitignore
├── generate_dummy_data.py       # deterministic dummy dataset 생성
├── import_artifacts.py          # matrix artifact -> 공통 dataset
├── report_data.py               # schema, validation과 query
├── plot_results.py              # dummy/measured 공통 renderer
└── figures/
    ├── *.png                    # 본문 placeholder
    └── measured/                # 실측 검토용 output; gitignore
```

`plot_dummy_results.py`는 기존 command 호환용 wrapper입니다. 새 작업은
`plot_results.py`만 사용합니다. Column과 metric 이름은
[Report data contract](data/README.md)에 한 번만 정의합니다.

## 더미 figure 재현

프로젝트 virtual environment에서 report dependency를 설치한 뒤 versioned dummy
CSV와 figure를 재생성합니다.

```bash
source .venv/bin/activate
python -m pip install -r report/requirements.txt
python -m report.generate_dummy_data
python -m report.plot_results
```

기존 workload/backend/case matrix를 유지할 때는
`report/data/dummy/metrics.csv`의 `value`만 바꾼 뒤 renderer를 다시 실행하면
됩니다. `manifest.json`의 `kind: dummy`인 dataset에는 모든 figure에
`DUMMY DATA` watermark가 자동으로 들어갑니다.

## 실측 artifact로 교체

Report runner가 만든 `matrix-results.jsonl`을 공통 dataset으로 변환합니다.

```bash
python -m report.import_artifacts \
  --state-root outputs/report-experiments-staged \
  --network-link-gbps fs-native=100 \
  --network-link-gbps 3FS=100 \
  --network-link-gbps pNFS=100

python -m report.plot_results \
  --data-dir report/data/measured
```

첫 명령은 각 완료 case의 `result_dir`에서 replay stats, interval I/O와 profiler
TSV를 읽어 `report/data/measured/`에 정규화합니다. 두 번째 명령은 원본
placeholder를 건드리지 않고 `report/figures/measured/`에 실측 figure를 만듭니다.

Coverage, provenance와 layout을 검토한 뒤에만 다음처럼 본문 figure를 명시적으로
교체합니다.

```bash
python -m report.plot_results \
  --data-dir report/data/measured \
  --output-dir report/figures
```

특정 figure만 갱신하려면 `--figure throughput`, `--figure speedup`처럼
`--figure`를 사용합니다. 입력이 없는 조합은 다른 metric으로 대체하지 않고
`N/A`로 표시합니다.

## Plot 검토 기준

- 실제 PNG 크기에서 title, axis label, tick, annotation과 legend가 graph box 또는
  서로 겹치지 않는지 확인합니다. Renderer도 figure-level overlap과 clipping을
  검사하고 문제가 있으면 실패합니다.
- 같은 비교의 단위, 색상, backend 순서와 axis 범위를 유지합니다.
- 반복 결과는 중앙값과 deterministic 95% bootstrap confidence interval로
  표시합니다.
- Adapter interval data가 없는 case를 node-level physical I/O로 대체하지 않습니다.
- `manifest.json`의 source, warning과 row count를 figure와 함께 보관합니다.
- Placeholder를 실측으로 교체할 때 본문의 draft/dummy 문구와 caption도 함께
  갱신합니다.

## 문서 형식

현재는 GitHub에서 바로 읽을 수 있고 layout을 Python에서 통제할 수 있는 Markdown +
matplotlib PNG를 사용합니다. 자동 numbering, cross-reference, bibliography 또는
HTML/PDF 동시 배포가 필요해질 때 Quarto 전환을 검토합니다.
