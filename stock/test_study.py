import numpy as np
import pandas as pd
from stock.study import simulate, signals, rsi


def test_close_signal_executes_next_session_and_costs_both_legs():
    dates = pd.bdate_range("2024-01-01", periods=65)
    prices = np.full((65, 1), 100.0)
    prices[61, 0] = 110
    prices[62, 0] = np.nan
    sig = np.zeros_like(prices)
    sig[60, 0], sig[61, 0] = 1, -1
    daily, trades = simulate(dates, ["TEST"], prices, sig, "MA20", 0.0015)
    (trade,) = trades
    shares = int(2e6 // (110 * 1.0015))
    assert trade[3:6] == (dates[61], dates[63], shares)
    assert np.isclose(trade[8], 100 * 0.9985 / (110 * 1.0015) - 1)
    assert np.isclose(daily[-1][3] - 2e6, trade[7] - trade[6])


def test_breakout_excludes_current_high_and_rsi_restarts():
    f = pd.DataFrame(
        {
            "close": [100.0] * 25 + [110.0],
            "high": [101.0] * 25 + [120.0],
            "low": 99.0,
            "volume": 100.0,
        }
    )
    sig = signals(f)
    assert sig[2, -1] == 1 and sig[4, -1] == 0
    f.loc[24, "close"] = np.nan
    assert np.isnan(signals(f)[2, -1])
    assert rsi(pd.Series(range(20))).iloc[-1] == 100
    assert rsi(pd.Series([1.0] * 20)).iloc[-1] == 50


def test_exported_quarters_reconcile_and_signals_have_three_outcomes():
    from stock.study import OUT

    r = pd.read_csv(OUT / "strategy_results.csv")
    for _, g in r.groupby(["strategy", "fee"]):
        periods = g[g.quarter != "ALL"]
        total = g[g.quarter == "ALL"].iloc[0]
        assert np.isclose((periods.ret + 1).prod() - 1, total.ret)
        assert np.isclose(periods.pnl.sum(), total.pnl)
        assert periods.trades.sum() == total.trades
    s = pd.read_csv(OUT / "signal_results.csv").query("n>0")
    assert np.allclose(s.up + s.down + s.flat, 1)
