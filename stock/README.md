# 종목별 일별 시세 CSV

Python 3.10 이상과 beautifulsoup4가 필요하다. 서버에 저장된 모든 YYYY-MM-DD/종목코드.html을 순서대로 읽는다.

```sh
python3 stock/convert.py /mnt/data/stock ~/stock_daily
```

아직 없는 출력 폴더를 지정한다. 종목당 한 파일(예: 000050.csv)에 거래일, 고가, 저가, 종가, 거래량, 거래대금_백만원을 거래일 순으로 저장한다. 날짜와 종가는 HTML의 장마감 요약을 기준으로 읽고, 같은 거래일의 동일 관측은 한 번만 쓴다. 다른 값과 충돌하면 먼저 읽은 값을 유지하고 해당 경로를 오류로 기록한다. 필요할 때만 --yearly 옵션을 붙인다.

시세 요약·장마감 표시·단위를 확인할 수 없는 파일은 표준 오류에 경로와 사유가 남는다. 마지막에 입력·실패 건수가 표시되며 실패가 있으면 종료 코드는 1이다. 중간 SQLite 파일과 결과 CSV를 만들 임시 공간이 필요하다. 작업이 끝나면 결과 폴더를 게시한다.

Windows 프로젝트 폴더에서 아래 명령을 실행하면 SSH로 원격 HTML의 시세 요약 태그만 전송하고 로컬에서 종목별 CSV를 만든다.

```cmd
python stock\convert.py --ssh bigstat /mnt/data/stock stock_daily
```

비밀번호는 SSH 프롬프트에 입력하고 완료될 때까지 터미널을 열어 둔다.
