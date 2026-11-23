import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stock.convert import REMOTE_READER, convert_daily
from unittest.mock import patch


class RemoteInputTest(unittest.TestCase):
    def test_named_and_plain_files_share_one_csv_over_ssh(self):
        html = "<dl><dd>2026년 9월 2일 장마감</dd><dd>종목코드 095570</dd><dd>현재가 5</dd><dd>고가 6</dd><dd>저가 4</dd><dd>거래량 100</dd><dd>거래대금 1백만</dd></dl>"
        with tempfile.TemporaryDirectory() as folder:
            day = Path(folder) / "2026-09-02"
            day.mkdir()
            for stem in ["AJ네트웍스_095570", "095570", "알수없음"]:
                (day / (stem + ".html")).write_text(html, encoding="utf8")
            popen = subprocess.Popen

            def fake_ssh(command, **kwargs):
                return popen([sys.executable, "-c", REMOTE_READER, folder], **kwargs)

            output = Path(folder) / "result"
            with patch("subprocess.Popen", side_effect=fake_ssh):
                self.assertEqual(convert_daily(folder, output, host="test"), (3, 1))
            self.assertEqual([p.name for p in output.iterdir()], ["095570.csv"])
            self.assertEqual(
                len(
                    (output / "095570.csv").read_text(encoding="utf-8-sig").splitlines()
                ),
                2,
            )

    def test_only_quote_markup_crosses_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            day = Path(folder) / "2026-09-02"
            day.mkdir()
            block = "<dl><dd>종목코드 000050 코스피</dd></dl>".encode()
            (day / "000050.html").write_bytes(b"<h1>news</h1>" + block)
            result = subprocess.check_output(
                [sys.executable, "-c", REMOTE_READER, folder]
            )
            record, end = map(json.loads, result.splitlines())
            self.assertEqual(record[:2], [day.name, "000050"])
            self.assertEqual(base64.b64decode(record[2]), block)
            self.assertEqual(end, {"complete": 1})
