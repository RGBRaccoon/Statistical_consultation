# Task: 공유용 HTML 생성

- 원본 명령: “THML로 작성. 빠르게.”, “v2,2.1,2.3,2.4까지”
- Goal: 지정한 네 버전의 MD로부터 이미지가 내장된 공유용 HTML을 만든다.
- Reason: 별도 이미지 폴더 없이 HTML 파일을 공유하기 위함이다.
- Scope: 네 버전 HTML, 공통 변환 코드, 실행 기록. MD·CSV와 분석 결과는 변경하지 않는다.
- Acceptance Criteria: 네 파일 생성, MD의 PNG와 동일한 임베딩, 외부 렌더링 리소스 없음, MD 해시 불변.
- Tests: 저장한 HTML을 파싱하고 PNG 디코딩·MD 이미지 일치·외부 리소스·원본 해시를 확인한다.
- Dependencies: 기존 V2·V2.1·V2.3·V2.4 MD.
- 사용자 변경: 이번 명령은 이전 HTML 제외 지시를 네 버전 공유 파일에 한하여 변경한다.
- 근거: `html_export_log.json`. 브라우저 실제 표시 여부는 별도 미검증으로 남긴다.
