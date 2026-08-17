# Documentation guidelines

이 문서는 Tracebench의 문서를 추가하거나 수정할 때 문서 간 중복과
설정 불일치를 줄이기 위한 기준을 정의한다. 세부 기술 동작은 이 문서에
복사하지 않고 아래의 authoritative source를 참조한다.

## 문서 역할과 기준 위치

| 주제 | 기준 문서 | 이 문서에 기록할 내용 |
| --- | --- | --- |
| L2 event, dependency, object preparation, trace validity | [l2-tracing.md](l2-tracing.md) | event contract의 요약과 링크 |
| Replay CLI, speedup, profiling, output | [replayer.md](replayer.md) | 실행 목적과 대표 명령 |
| Staged remote topology와 준비/회수 | [staged-remote-replay.md](staged-remote-replay.md) | 실행 순서와 topology 링크 |
| Trace asset과 workload archive | [trace-assets.md](trace-assets.md) | asset 이름과 다운로드 방법 |
| Metric 정의와 유효성 판정 | [l2-replay-metrics.md](l2-replay-metrics.md) | 사용할 metric과 결과 파일 |
| 실험 matrix와 보고서 주장 | [../report/performance-evaluation.md](../report/performance-evaluation.md) | 실험 조건과 증거 |

README와 benchmark README는 빠른 진입점이다. 상세한 contract나 설정값을
여러 문서에 반복해서 쓰지 말고 기준 문서에 한 번만 기록한다.

## 작성 원칙

1. **범위를 먼저 적는다.** 문서가 recorder, replayer, staged remote,
   report 중 무엇을 설명하는지 첫 문단에서 밝힌다.
2. **한 가지 사실은 한 곳에서 관리한다.** 다른 문서에서는 핵심만 요약하고
   기준 문서로 링크한다. 동일한 명령, 경로, metric 정의를 복사하지 않는다.
3. **용어를 일관되게 쓴다.** source는 trace를 생성한 환경,
   target은 trace를 재생할 환경, L2 adapter는 backend 앞의 LMCache
   interface를 의미한다.
4. **가정과 미확정 값을 구분한다.** 아직 정하지 않은 device, mount option,
   node 수, replication/striping은 TBD로 표시하고 확정된 값처럼 쓰지 않는다.
5. **결과보다 조건을 먼저 기록한다.** 실험 결론에는 trace, backend,
   replay speedup, repeat 수와 유효성 기준을 함께 적는다.

## Trace/replay 실험 기록

실험 문서나 report에 case를 추가할 때 다음 정보를 남긴다.

- LMCache/Tracebench branch와 commit SHA
- workload 이름과 trace archive/file 이름
- trace subset을 사용하면 trace_percent 또는 time range
- subset의 checksum, operation 수와 byte 수
- backend별 adapter + filesystem/storage + mount option
- replay speedup, repeat 수, node 수와 profiler 설정
- output directory와 주요 결과 파일
- malformed trace, drop count, completion mismatch 등 유효성 판정

큰 trace를 줄여 실행할 때는 모든 backend와 speedup에 같은 subset을 사용한다.
subset 비율만 적지 말고 case metadata에 실제 선택 범위와 operation/byte 수를
함께 기록한다.

## Report와 figure 문서

각 figure는 다음 연결을 유지한다.

    실험 조건 -> output artifact -> metric/plot -> 보고서 주장

Figure 설명에는 workload, backend, speedup, 단위와 aggregation 방법을
명시한다. plot을 추가하거나 수정할 때는 axis label과 legend가 graph box와
겹치지 않는지 확인하고, 여러 panel의 단위와 색상 의미를 일관되게 유지한다.
더미 figure는 실측 결과가 아님을 명시한다.

## 명령과 경로 예시

- 문서의 Python 예시는 python ... 형식을 사용한다. 개인 환경의 .venv
  절대 경로를 문서에 넣지 않는다.
- 경로는 저장소 root를 기준으로 쓰고, 다른 문서로 연결할 때는 상대 링크를
  사용한다.
- staged remote 실행은 topology 파일, git_revision, --asset,
  --trace-percent, output/state root를 함께 보여준다.
- backend spec은 NAME=CONFIG|L2_ROOT 형식을 사용하고, @PLACEHOLDER@
  때문에 | 구분자가 필요한 이유를 설명한다.

## 문서 변경 전 확인

- 새 설명이 기존 authoritative source와 중복되거나 충돌하지 않는가?
- 명령의 옵션, config 경로, output 파일 이름이 현재 script와 일치하는가?
- trace subset, backend mount, commit SHA가 재현에 필요한 만큼 기록되었는가?
- report의 주장이 실제 metric 또는 artifact를 가리키는가?
- 내부 링크와 Markdown code block이 유효한가?
- git diff --check와 관련 테스트를 실행했는가?

문서 구조나 기준 위치가 바뀌면 이 파일의 표와 root [README.md](../README.md)의
Guides 목록을 함께 업데이트한다.
