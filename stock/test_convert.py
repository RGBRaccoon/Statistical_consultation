import unittest

from stock.convert import extract, daily_row


class DailyTest(unittest.TestCase):
    def test_close_and_trade_date(self):
        html = """<dl class="blind"><dd>2026년 9월 2일 16시 10분 기준 장마감</dd>
        <dd>종목코드 000050 코스피</dd><dd>현재가 8,200 전일대비 하락 150</dd>
        <dd>고가 8,350</dd><dd>저가 8,100</dd><dd>거래량 15,778</dd>
        <dd>거래대금 129백만</dd></dl>"""
        self.assertEqual(
            daily_row(html, "000050"), ("2026-09-02", 8350, 8100, 8200, 15778, 129)
        )
        with self.assertRaises(ValueError):
            daily_row(html.replace("장마감", "장중"), "000050")


class ExtractionTest(unittest.TestCase):
    def test_investor_history_and_unknown_table(self):
        html = """<div class="wrap_company"><span class="code">000050</span></div>
        <table><caption>외국인 기관</caption><tr><th>09/01</th><td>8,350</td>
        <td>상향 90</td><td>-4,801</td><td>+202</td></tr></table>
        <table><caption>새 표</caption><tr><td>0</td></tr></table>"""
        data = extract(html, "000050")
        self.assertEqual(data["investor_flows"][0]["foreign_net"], "-4801")
        self.assertEqual(data["investor_flows"][0]["trade_date_raw"], "09/01")
        self.assertIn("새 표", data["unclassified"][0]["html"])

    def test_reject_wrong_instrument(self):
        with self.assertRaises(ValueError):
            extract(
                '<div class="wrap_company"><span class="code">000020</span></div>',
                "000050",
            )
