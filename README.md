# Statistical consultation

## 로또 6/45 기대수익 분석

- 원본 데이터: `class2_rottery/lotto_rank_1_1245.csv`
- 분석 코드: `class2_rottery/lotto_analysis.py`
- 자체 포함형 보고서: `class2_rottery/lotto_rank_report.md`
- 영문 자체 포함형 보고서: `class2_rottery/lotto_rank_report_en.md`
- 실험 결과: `class2_rottery/analysis_results/`

다음 명령은 모든 CSV 표와 PNG 그림을 다시 만들고, 그림을 base64 data URI로
내장한 Markdown 보고서를 갱신한다.

```bash
python class2_rottery/lotto_analysis.py
```

검증 명령은 다음과 같다.

```bash
python -m pytest class2_rottery/tests -q -p no:cacheprovider
python -m ruff check class2_rottery/lotto_analysis.py class2_rottery/tests/test_lotto_analysis.py
python -m ruff format --check class2_rottery/lotto_analysis.py class2_rottery/tests/test_lotto_analysis.py
```
