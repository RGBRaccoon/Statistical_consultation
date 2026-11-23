"""일별 자료를 이용한 사전 계획 기반 전략 비교."""

import csv
import hashlib
from pathlib import Path
import numpy as np
import pandas as pd

OUT = Path("stock/results")
NAMES = ["MA20", "RSI14", "Donchian", "Bollinger", "Volume", "BH"]
FIELDS = ["high", "low", "close", "volume", "amount"]
ACTIONS = {
    "010120": ("2026-04-13", 5),
    "278470": ("2024-10-31", 5),
    "000500": ("2026-06-30", 1.8),
}


def prepare():
    import exchange_calendars as xc
    import shutil

    OUT.mkdir(exist_ok=True)
    cache = Path(".tmp/study-prices")
    cache.mkdir(parents=True, exist_ok=True)
    dates = xc.get_calendar("XKRX").sessions_in_range("2024-03-27", "2026-09-02")
    dates = dates.strftime("%Y-%m-%d").tolist()
    market_dates = pd.read_csv(OUT / "kospi_sessions.csv").day.tolist()
    dates = [d for d in dates if d in market_dates]
    first = set(dates[:60])
    ranks, observed, inventory = [], set(), []
    for p in sorted(Path("stock_daily").glob("*.csv")):
        raw = p.read_bytes()
        rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))[1:]
        observed.update(r[0] for r in rows)
        amounts = [
            int(a)
            for d, h, low, c, v, a in rows
            if d in first and int(v) > 0 and 0 < int(low) <= int(c) <= int(h)
        ]
        if len(amounts) >= 50:
            ranks.append((p.stem, np.mean(amounts), len(amounts)))
        inventory.append((p.stem, len(rows), hashlib.sha256(raw).hexdigest()))
    selected = sorted(ranks, key=lambda r: (-r[1], r[0]))[:50]
    assert len(selected) == 50
    for code, *_ in selected:
        shutil.copyfile(Path("stock_daily") / (code + ".csv"), cache / (code + ".csv"))
    pd.DataFrame(inventory, columns=["code", "rows", "sha256"]).to_csv(
        OUT / "inventory.csv", index=False
    )
    pd.DataFrame(
        selected, columns=["code", "mean_amount_million", "valid_days"]
    ).to_csv(OUT / "universe.csv", index=False)
    pd.DataFrame({"day": sorted(set(dates) | observed)}).assign(
        calendar=lambda t: t.day.isin(dates), observed=lambda t: t.day.isin(observed)
    ).to_csv(OUT / "calendar.csv", index=False)


def rsi(s):
    arr = s.to_numpy()
    out = np.full(len(s), np.nan)
    n = 0
    for i in range(1, len(s)):
        diff = arr[i] - arr[i - 1]
        if not np.isfinite(diff):
            n = 0
            continue
        if not n:
            gain = loss = 0.0
        n += 1
        up, down = max(diff, 0), max(-diff, 0)
        if n <= 14:
            gain += up / 14
            loss += down / 14
        else:
            gain = (gain * 13 + up) / 14
            loss = (loss * 13 + down) / 14
        if n >= 14:
            out[i] = 100 * gain / (gain + loss) if gain + loss else 50
    return pd.Series(out, index=s.index)


def signals(frame):
    c, h, low, v = (frame[x] for x in ["close", "high", "low", "volume"])
    m = c.rolling(20).mean()
    lower = m - 2 * c.rolling(20).std(ddof=0)
    r = rsi(c)
    breakout = c > h.shift().rolling(20).max()
    exit_ = c < low.shift().rolling(10).min()
    # 오늘과 전일 지표가 모두 준비된 공통 관측.
    valid = c.rolling(22).count().eq(22)

    def edge(s):
        return s & ~s.shift(fill_value=False)

    b = edge(breakout)
    e = edge(exit_)
    ref = c >= m
    pairs = [
        ((c > m) & (c.shift() <= m.shift()), (c < m) & (c.shift() >= m.shift())),
        ((r > 30) & (r.shift() <= 30), (r < 70) & (r.shift() >= 70)),
        (b, e),
        (
            (c.shift() < lower.shift()) & (c >= lower) & (c < m),
            edge(ref) | (ref & ~valid.shift(fill_value=False)),
        ),
        (b & (v >= 1.5 * v.shift().rolling(20).mean()), e),
    ]
    return np.stack(
        [
            np.where(valid, np.select([buy, sell], [1, -1], 0), np.nan)
            for buy, sell in pairs
        ]
    )


def load():
    cal = pd.read_csv(OUT / "calendar.csv")
    days = pd.DatetimeIndex(cal.loc[cal.calendar, "day"])
    codes = pd.read_csv(OUT / "universe.csv", dtype={"code": str}).code.tolist()
    prices = []
    sig = []
    issues = []
    for code in codes:
        f = (
            pd.read_csv(
                Path(".tmp/study-prices") / (code + ".csv"),
                header=0,
                names=["day", *FIELDS],
                parse_dates=["day"],
            )
            .set_index("day")
            .reindex(days)
        )
        ok = (f.volume > 0) & (f.low > 0) & (f.low <= f.close) & (f.close <= f.high)
        reasons = np.select(
            [f.close.isna(), f.volume.eq(0), ~ok],
            ["missing", "no_trade", "bad_price"],
            "",
        )
        for d, reason in zip(days, reasons):
            if reason:
                issues.append((code, d.date(), reason))
        f = f.where(ok)
        for d in f.close.dropna().pct_change().abs().loc[lambda x: x > 0.30].index:
            issues.append((code, d.date(), "jump_gt_30pct"))
        prices.append(f.close.to_numpy().copy())
        if code in ACTIONS:
            day, ratio = ACTIONS[code]
            f.loc[day:, ["close", "high", "low"]] *= ratio
            f.loc[day:, "volume"] /= ratio
        sig.append(signals(f))
    pd.DataFrame(issues, columns=["code", "day", "reason"]).to_csv(
        OUT / "exclusions.csv", index=False
    )
    return days, codes, np.array(prices).T, np.array(sig).transpose(1, 2, 0)


def simulate(days, codes, px, signals_, name, fee):
    cash = np.full(len(codes), 2e6)
    qty = np.zeros(len(codes), int)
    last = np.zeros(len(codes))
    basis = np.zeros(len(codes))
    payouts = np.zeros(len(codes))
    entered = np.zeros(len(codes), int)
    pending = np.zeros(len(codes), bool)
    bought = np.zeros(len(codes), bool)
    trades = []
    daily = []
    for t in range(60, len(days)):
        p = px[t]
        ok = np.isfinite(p)
        last[ok] = p[ok]
        locked = np.zeros(len(codes), bool)
        for code, (day, ratio) in ACTIONS.items():
            if code not in codes:
                continue
            j = codes.index(code)
            if days[t] == pd.Timestamp(day):
                entitlement = round(qty[j] * ratio, 8)
                qty[j] = int(entitlement)
                if code == "000500":
                    fraction = entitlement - qty[j]
                if not ok[j]:
                    last[j] /= ratio
            if (
                code == "000500"
                and pd.Timestamp(day) <= days[t] < pd.Timestamp("2026-07-23")
                and qty[j] > 0
                and entered[j] < days.get_loc(day)
            ):
                locked[j] = True
            if code == "000500" and days[t] == pd.Timestamp("2026-07-23") and fraction:
                cash[j] += fraction * p[j]
                payouts[j] += fraction * p[j]
        sig = signals_[t - 1] if name != "BH" else np.zeros(len(codes))
        want = (~bought if name == "BH" else sig == 1) & (qty == 0)
        pending |= (sig == -1) & (qty > 0)
        if t == len(days) - 1:
            pending |= qty > 0
            want[:] = False
        sells = np.flatnonzero(pending & ok & ~locked)
        cost = 0.0
        for j in sells:
            proceeds = qty[j] * p[j] * (1 - fee)
            cost += qty[j] * p[j] * fee
            realized = proceeds + payouts[j]
            trades.append(
                (
                    name,
                    fee,
                    codes[j],
                    days[entered[j]],
                    days[t],
                    qty[j],
                    basis[j],
                    realized,
                    realized / basis[j] - 1,
                    t - entered[j],
                )
            )
            payouts[j] = 0
            cash[j] += proceeds
            qty[j] = 0
            pending[j] = False
        buy = want & ok
        shares = np.floor(cash[buy] / (p[buy] * (1 + fee))).astype(int)
        inds = np.flatnonzero(buy)[shares > 0]
        shares = shares[shares > 0]
        basis[inds] = shares * p[inds] * (1 + fee)
        cash[inds] -= basis[inds]
        qty[inds] = shares
        entered[inds] = t
        bought[inds] = True
        cost += sum(shares * p[inds] * fee)
        entitlement_value = 0.0
        if "000500" in codes and pd.Timestamp("2026-06-30") <= days[t] < pd.Timestamp(
            "2026-07-23"
        ):
            entitlement_value = fraction * last[codes.index("000500")]
        exposure = qty @ last + entitlement_value
        equity = cash.sum() + exposure
        assert cash.min() > -1e-6 and equity > 0
        daily.append(
            (
                name,
                fee,
                days[t],
                equity,
                exposure,
                cost,
                int(sum(want & ~ok)),
                int(sum(pending & (~ok | locked))),
            )
        )
    return daily, trades


def run():
    days, codes, px, sigs = load()
    daily = []
    ledger = []
    for fee in [0, 0.0015, 0.003]:
        for i, name in enumerate(NAMES):
            d, t = simulate(days, codes, px, sigs[min(i, 4)], name, fee)
            daily.extend(d)
            ledger.extend(t)
    eq = pd.DataFrame(
        daily,
        columns="strategy fee day equity exposure cost missed_buy delayed_sell_days".split(),
    )
    tr = pd.DataFrame(
        ledger,
        columns="strategy fee code entry exit qty basis proceeds ret holding".split(),
    )
    eq.to_csv(OUT / "daily_equity.csv", index=False)
    tr.to_csv(OUT / "trades.csv", index=False)
    res = []
    for (name, fee), f in eq.groupby(["strategy", "fee"]):
        df = tr[(tr.strategy == name) & (tr.fee == fee)]
        first = 1e8
        total = 0
        for q, g in [*f.groupby(f.day.dt.to_period("Q")), ("ALL", f)]:
            if q == "ALL":
                first = 1e8
            a = np.r_[first, g.equity]
            ret = a[-1] / first - 1
            pnl = a[-1] - first
            t = df[df.exit.between(g.day.iloc[0], g.day.iloc[-1])]
            win = t.ret[t.ret > 0]
            loss = t.ret[t.ret < 0]
            res.append(
                dict(
                    strategy=name,
                    fee=fee,
                    quarter=str(q),
                    start=g.day.iloc[0],
                    end=g.day.iloc[-1],
                    days=len(g),
                    trades=len(t),
                    wins=len(win),
                    losses=len(loss),
                    win_mean=win.mean(),
                    loss_mean=loss.mean(),
                    pnl=pnl,
                    ret=ret,
                    mdd=max(1 - a / np.maximum.accumulate(a)),
                    cost=g.cost.sum(),
                    exposure=(g.exposure / g.equity).mean(),
                    holding=t.holding.mean(),
                    remaining=g.exposure.iloc[-1],
                )
            )
            if q == "ALL":
                assert abs(pnl - total) < 0.01
            else:
                total += pnl
            first = a[-1]
        if f.exposure.iloc[-1] == 0:
            assert (
                abs((df.proceeds - df.basis).sum() - (f.equity.iloc[-1] - 1e8)) < 0.01
            )
    result = pd.DataFrame(res)
    result = result.merge(
        result[result.strategy == "BH"][["fee", "quarter", "ret"]].rename(
            columns={"ret": "bh_ret"}
        ),
        on=["fee", "quarter"],
    )
    result["excess"] = result.ret - result.bh_ret
    result.to_csv(OUT / "strategy_results.csv", index=False)
    for code, (day, ratio) in ACTIONS.items():
        if code in codes:
            px[days >= day, codes.index(code)] *= ratio
    signal_eval(days, px, sigs)


def signal_eval(days, px, sigs):
    rng = np.random.default_rng(42)
    results = []
    quarters = days.to_period("Q")
    for i, name in enumerate(NAMES[:5]):
        for horizon in [1, 5, 20]:
            returns = np.full_like(px, np.nan)
            returns[:-horizon] = px[horizon:] / px[:-horizon] - 1
            neutral = (sigs[i] == 0) & np.isfinite(returns)
            den = neutral.sum(axis=1)
            metrics = [
                returns,
                (returns > 0).astype(float),
                (returns < 0).astype(float),
                (returns == 0).astype(float),
            ]
            controls = [
                np.where(neutral, m, 0).sum(axis=1) / np.where(den, den, np.nan)
                for m in metrics
            ]
            for sign in [1, -1]:
                for q in [*quarters[60:].unique(), "ALL"]:
                    ids = np.flatnonzero(
                        (np.arange(len(days)) >= 60)
                        & ((quarters == q) if q != "ALL" else True)
                    )
                    # 미래 가격이 없거나 대조군이 전무한 날짜는 분모에서 제외.
                    events = sigs[i, ids] == sign
                    mask = events & np.isfinite(returns[ids]) & (den[ids, None] > 0)
                    w = mask.sum(axis=1)
                    n = w.sum()
                    row = dict(
                        strategy=name,
                        quarter=str(q),
                        direction=sign,
                        horizon=horizon,
                        n=n,
                        excluded=events.sum() - n,
                        symbols=mask.any(axis=0).sum(),
                    )
                    if n:
                        row["median"] = np.median(returns[ids][mask])
                        L = min(20, len(ids))
                        starts = rng.integers(
                            0, len(ids) - L + 1, (2000, int(np.ceil(len(ids) / L)))
                        )
                        ix = (starts[:, :, None] + np.arange(L)).reshape(2000, -1)[
                            :, : len(ids)
                        ]
                        for label, m, ctrl in zip(
                            ["return", "up", "down", "flat"], metrics, controls
                        ):
                            sums = np.where(mask, m[ids], 0).sum(axis=1)
                            bench = w * np.nan_to_num(ctrl[ids])
                            diff = sums - bench
                            row[label] = sums.sum() / n
                            row["control_" + label] = bench.sum() / n
                            weights = w[ix].sum(axis=1)
                            boot = (
                                diff[ix].sum(axis=1)[weights > 0] / weights[weights > 0]
                            )
                            if len(boot):
                                row[label + "_lo"], row[label + "_hi"] = np.quantile(
                                    boot, [0.025, 0.975]
                                )
                    results.append(row)
    pd.DataFrame(results).to_csv(OUT / "signal_results.csv", index=False)


if __name__ == "__main__":
    import sys

    if "prepare" in sys.argv:
        prepare()
    else:
        run()
