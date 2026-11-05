"""class2_rottery 폴더 구조의 공통 경로.

- data/: 원본·전처리 CSV
- reports/<버전>/: 버전별 보고서와 분석 결과 폴더
- docs/: 피드백, 계획, task, 로그
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"
