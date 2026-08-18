# Configuration index

이 디렉터리는 실행 시 version과 함께 고정해야 하는 설정만 관리합니다.
설정 필드의 의미와 실행 명령은 복사하지 않고 각 기준 문서를 참고합니다.

| 경로 | 용도 | 기준 문서 |
| --- | --- | --- |
| `recorder/` | workload별 recorder와 smoke 설정 | [Recorder guide](../docs/recorder.md) |
| `replayer/` | L2 backend와 replay smoke 설정 | [Replayer guide](../docs/replayer.md) |
| `profiling/` | replay I/O profiling 설정 | [Replayer guide](../docs/replayer.md) |

실험 전에는 선택한 config를 artifact metadata와 함께 보관합니다. Host별 mount,
credential과 trace 경로처럼 환경에 종속된 값은 versioned config에 복사하지 않고
CLI 또는 staged remote topology에서 주입합니다.
