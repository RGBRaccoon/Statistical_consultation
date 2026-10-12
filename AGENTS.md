<!--
AUTO-GENERATED / DO NOT EDIT DIRECTLY
Source: RGBRaccoon/dev-agent-standard@f7ff8557e7407039d9c1506e04f2e178cdddbc67
Project-specific rules belong in PROJECT_RULES.md.
-->

# Agent Development Standard

이 문서는 이 저장소에서 작업하는 Coding Agent가 항상 따라야 하는 핵심 개발 규칙이다.
Codex는 이 파일을 프로젝트 지침으로 사용한다. Claude Code는 `CLAUDE.md`를 통해 이 파일을 읽는다.

## 0. 최우선 원칙

1. 모든 개발은 **Task 단위**로 수행한다.
2. 기능 추가와 버그 수정은 기본적으로 **TDD: RED -> GREEN -> REFACTOR** 순서를 따른다.
3. 테스트를 삭제하거나 약화시켜 실패를 숨기지 않는다.
4. Task의 Scope를 임의로 확장하지 않는다. 새 요구가 생기면 별도 Task로 분리한다.
5. Acceptance Criteria와 자동 검증을 모두 만족하기 전에는 완료로 선언하지 않는다.
6. 하나의 Task는 가능한 한 하나의 논리적 Commit으로 종료한다.
7. 관리 파일에 `AUTO-GENERATED / DO NOT EDIT DIRECTLY`가 있으면 직접 수정하지 않는다.
8. 프로젝트별 추가 지침은 `PROJECT_RULES.md`를 확인하고 함께 준수한다.

## 1. 언어 및 산출물 규칙

기본 작업 언어는 **한국어**다. 사용자가 별도로 다른 언어를 요구하거나 프로젝트의 기존 규칙이 명시적으로 다른 언어를 요구하지 않는 한 다음 규칙을 따른다.

- 사용자에게 전달하는 설명, 작업 결과 요약, 진행 보고는 한국어로 작성한다.
- 새로 작성하는 Markdown 문서, README, Spec, Plan, Task, ADR 등 개발 문서는 한국어를 기본으로 한다.
- GitHub Issue, Pull Request 제목/본문, 코드 리뷰 의견 등 Agent가 작성하는 협업용 텍스트는 한국어를 기본으로 한다.
- Commit message는 Conventional Commit prefix(`feat:`, `fix:` 등)는 유지하되 설명 부분은 한국어를 기본으로 한다. 예: `feat: 주문 체결 로직 추가`.
- 코드 주석과 docstring은 프로젝트 기존 스타일과 일관성을 우선하되, 별도 기준이 없으면 한국어로 작성한다.
- 변수명, 함수명, 클래스명, 파일명, API/라이브러리/프로토콜/표준 명칭, CLI 명령어 등 기술 식별자는 관례에 따라 영어 원문을 유지한다.
- 외부 공개 프로젝트의 기여 가이드나 기존 문서 언어가 영어를 요구하면 해당 프로젝트 규칙을 우선한다.
- 번역으로 의미가 불명확해지는 전문용어는 영어 원문을 함께 표기하거나 그대로 사용한다.

## 2. Task 정의

Task는 **하나의 독립적으로 검증 가능한 변경 단위**다.
각 Task에는 반드시 다음이 있어야 한다.

- Goal: 무엇을 변경하는가
- Reason: 왜 필요한가
- Scope: 수정 가능한 범위와 수정하지 않을 범위
- Acceptance Criteria: 완료 여부를 Yes/No로 판단할 수 있는 조건
- Tests: 완료를 검증할 테스트
- Dependencies: 선행 Task가 있다면 명시

다음 중 하나에 해당하면 Task를 분리한다.

- 서로 다른 기능이 포함됨
- 서로 독립적으로 검증 가능함
- 하나의 Commit으로 설명하기 어려움
- 구현 중 변경 범위가 예상보다 커짐
- 서로 다른 책임의 모듈을 동시에 크게 변경해야 함

## 3. 작업 순서

각 Task는 아래 순서를 따른다.

1. Task와 `PROJECT_RULES.md` 확인
2. 관련 코드, 테스트, 인터페이스, 의존성 분석
3. Acceptance Criteria를 검증하는 테스트 작성
4. RED: 새 테스트가 의도한 이유로 실패하는지 확인
5. GREEN: 테스트를 통과시키는 최소 구현 작성
6. REFACTOR: 동작을 유지하며 필요한 구조 개선
7. 관련 테스트 및 전체 회귀 테스트 수행
8. Lint / Format / Type Check 수행
9. 변경 diff를 확인하고 Scope 밖 변경 제거
10. Acceptance Criteria 재확인
11. 모든 검증이 성공한 경우 Commit

## 4. 테스트 규칙

- 기능 변경에는 가능한 한 Unit Test를 추가한다.
- 모듈 간 상호작용이 바뀌면 Integration Test를 추가한다.
- Web UI의 주요 사용자 흐름은 필요 시 Playwright E2E Test로 검증한다.
- 버그 수정은 가능하면 먼저 버그를 재현하는 Regression Test를 작성한다.
- 실패한 테스트를 skip, 삭제, 조건 완화하여 해결하지 않는다.
- 기존 테스트 변경이 필요하면 요구사항 변경으로 설명 가능해야 한다.

프로젝트가 별도로 지정하지 않았다면 Python 프로젝트의 기본 검증 후보는 다음과 같다.

```bash
pytest
ruff check .
ruff format --check .
pyright
```

실제 저장소에 정의된 명령이 있다면 그 명령을 우선한다.

## 5. Commit 규칙

- 기본 단위는 **완료된 Task 1개 = Commit 1개**다.
- RED/GREEN/REFACTOR 단계마다 기계적으로 Commit하지 않는다.
- 서로 다른 논리적 변경을 하나의 Commit에 섞지 않는다.
- 검증 실패 상태를 완료 Commit으로 남기지 않는다.
- Commit 전에 `git diff`와 `git status`를 확인한다.

권장 prefix:

- `feat:` 기능 추가
- `fix:` 버그 수정
- `refactor:` 동작 변경 없는 구조 개선
- `test:` 테스트 추가/개선
- `docs:` 문서 변경
- `chore:` 개발환경/자동화/기타 관리 작업

## 6. 안전 및 변경 범위

- 사용자가 요구하지 않은 destructive operation을 수행하지 않는다.
- secret, credential, token을 코드나 로그에 기록하지 않는다.
- 관련 없는 파일을 정리한다는 이유로 대규모 수정하지 않는다.
- 기존 public API나 데이터 구조를 바꿔야 한다면 영향 범위를 먼저 확인한다.
- 불확실한 요구사항을 임의로 확정하여 큰 구조 변경을 하지 않는다.

## 7. 완료 조건

Task는 다음을 모두 만족할 때만 완료다.

- Acceptance Criteria 충족
- 신규/변경 기능 테스트 통과
- 기존 관련 테스트 통과
- 프로젝트가 요구하는 전체 검증 통과
- Lint / Format / Type Check 통과(구성된 경우)
- Scope 밖 의도하지 않은 변경 없음
- Commit 완료

상세 정책은 `rules/` 문서를 참고한다. 단, 이 파일의 핵심 규칙은 선택사항이 아니다.
