# 성능 평가 리포트 작업 안내

이 디렉터리는 LMCache L2 backend 성능 평가 본문과 figure 생성 코드를
관리합니다. 현재 figure 값은 모두 더미 데이터이며 성능 주장의 근거로 사용할 수
없습니다. 실험 조건, figure 정의와 주장-증거 연결은
[성능 평가 보고서](performance-evaluation.md)를 단일 기준으로 사용합니다.

## 파일 구성

| 파일 | 역할 |
| --- | --- |
| `performance-evaluation.md` | 실험 설계와 보고서 본문 |
| `plot_dummy_results.py` | 재현 가능한 더미 데이터와 multiplot 생성 |
| `requirements.txt` | Figure 생성용 Python package |
| `figures/*.png` | Markdown에 포함되는 렌더링 결과 |
| `../benchmarks/report/` | Staged remote figure별 experiment runner |

## Figure 다시 만들기

프로젝트 virtual environment에서 report dependency를 설치하고 plot을 생성합니다.

```bash
source .venv/bin/activate
python -m pip install -r report/requirements.txt
python report/plot_dummy_results.py
```

다른 출력 디렉터리는 `--output-dir`로 지정합니다.

```bash
python report/plot_dummy_results.py --output-dir /tmp/lmcache-report-figures
```

## 실험 matrix

Figure별 workload/backend/speedup/repeat case는
[Report runner](../benchmarks/report/README.md)를 사용합니다.

```bash
bash benchmarks/report/run_report_experiments.sh --help
```

Runner의 graph preset, backend template, trace subset, resume state와 output
schema는 runner 문서에서 관리합니다. 각 figure가 읽는 artifact와 metric은
[성능 평가 보고서](performance-evaluation.md)의 해당 실험 절과
[L2 replay metric guide](../docs/l2-replay-metrics.md)를 따릅니다.

## Plot 검토 기준

- 관련 panel은 하나의 figure로 렌더링하고, panel이 많아 text가 작아지면
  workload나 분석 목적별로 figure를 분리합니다.
- Axis label, tick, annotation과 legend가 graph box 또는 서로 겹치지 않는지
  실제 PNG 크기에서 확인합니다.
- 같은 비교의 단위, 색상, backend 순서와 axis 범위를 일관되게 유지합니다.
- 반복 결과는 보고서에서 정한 aggregation과 confidence interval을 사용합니다.
- Adapter interval data가 없는 case를 node-level physical I/O로 대체하지 않고
  `N/A`로 표시합니다.
- 더미 figure와 실측 figure를 파일명과 caption에서 명확하게 구분합니다.

실측 입력의 field, 단위와 계산식은 README에 다시 정의하지 않습니다. Figure별
입력을 바꿀 때는 성능 평가 보고서를, metric 의미를 바꿀 때는 L2 replay metric
guide를 수정합니다.

## 문서 형식

현재는 GitHub에서 바로 읽을 수 있고 plot layout을 Python에서 통제할 수 있는
Markdown + matplotlib PNG를 사용합니다. 자동 numbering, cross-reference,
bibliography 또는 HTML/PDF 동시 배포가 필요해질 때 Quarto 전환을 검토합니다.
