"""평가 결과에서 한국어 Markdown 보고서와 PNG 그림을 생성한다."""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

out = Path("stock/results")
names = {
    "MA20": "이동평균",
    "RSI14": "RSI 회복",
    "Donchian": "돈치안",
    "Bollinger": "볼린저",
    "Volume": "거래량 돌파",
    "BH": "단순 보유(BH)",
}
rules = {
    "MA20": "종가가 20일 평균 상향/하향 교차",
    "RSI14": "RSI(14)가 30 상향/70 하향 교차",
    "Donchian": "직전 20일 고가 돌파/10일 저가 이탈",
    "Bollinger": "20일 하단 재진입/중심선 회복",
    "Volume": "돈치안 진입에 거래량 1.5배 조건 추가",
    "BH": "최초 매수/평가 종료 시 청산",
}
colors = ["#28728b", "#b35234", "#297c51", "#73529d", "#ad7d1f", "#222b38"]
r = pd.read_csv(out / "strategy_results.csv")
base = r[r.fee == 0.0015]
s = pd.read_csv(out / "signal_results.csv")
eq = pd.read_csv(out / "daily_equity.csv", parse_dates=["day"]).query("fee==.0015")
plt.rcParams.update(
    {
        "font.family": "Malgun Gothic",
        "axes.unicode_minus": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(out / name, dpi=155, bbox_inches="tight")
    plt.close(fig)


fig, axes = plt.subplots(
    2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
)
for (k, label), col in zip(names.items(), colors):
    e = eq[eq.strategy == k]
    val = e.equity.to_numpy()
    axes[0].plot(e.day, val / 1e8, label=label, color=col, lw=2 if k == "BH" else 1.3)
    axes[1].plot(
        e.day,
        100 * (val / np.maximum.accumulate(np.r_[1e8, val])[1:] - 1),
        color=col,
        lw=1.2,
    )
axes[0].set(title="같은 1억원으로 시작한 전략별 평가자산", ylabel="평가자산 (억원)")
axes[0].legend(ncol=3, fontsize=9)
axes[1].set(ylabel="고점 대비 낙폭 (%)", xlabel="거래일")
save(fig, "equity_drawdown.png")
quarters = (
    base[base.quarter != "ALL"]
    .pivot(index="strategy", columns="quarter", values="ret")
    .loc[list(names)]
    * 100
)
fig, ax = plt.subplots(figsize=(12, 4))
im = ax.imshow(quarters, aspect="auto", cmap="RdBu", vmin=-45, vmax=45)
ax.set_xticks(
    range(len(quarters.columns)),
    [
        q + ("*" if i in (0, len(quarters.columns) - 1) else "")
        for i, q in enumerate(quarters.columns)
    ],
    rotation=30,
)
ax.set_yticks(range(6), names.values())
ax.set_title("분기 수익률 (%)   ·   * 부분 분기")
fig.colorbar(im, ax=ax, label="%")
for i, j in np.ndindex(quarters.shape):
    v = quarters.iloc[i, j]
    ax.text(
        j,
        i,
        f"{v:+.1f}",
        ha="center",
        va="center",
        color="white" if abs(v) > 25 else "black",
        fontsize=9,
    )
save(fig, "quarterly.png")
fig, axs = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
for ax, direction in zip(axs, [1, -1]):
    for i, (k, col) in enumerate(zip(list(names)[:5], colors)):
        rows = s.query(
            "quarter=='ALL' and direction==@direction and strategy==@k"
        ).sort_values("horizon")
        y = (rows["return"] - rows.control_return) * 100
        x = np.arange(3) + (i - 2) * 0.11
        ax.vlines(x, rows.return_lo * 100, rows.return_hi * 100, color=col)
        ax.scatter(x, y, label=names[k], color=col, s=24)
    ax.axhline(0, color="grey", lw=1)
    ax.set_xticks(range(3), ["1일", "5일", "20일"])
    ax.set_title("매수 신호" if direction == 1 else "매도 신호")
axs[0].set_ylabel("무신호 대비 평균수익률 차이 (%p)")
axs[1].legend(fontsize=8)
fig.suptitle("신호 이후 수익률 차이와 날짜 블록 재표집 95% 구간")
save(fig, "signal_effect.png")

lines = []


def add(v=""):
    lines.append(v)


def table(cols, rows):
    add("|" + "|".join(cols) + "|")
    add("|" + "|".join(["---"] * len(cols)) + "|")
    for row in rows:
        add("|" + "|".join(str(x) for x in row) + "|")
    add()


def pct(x):
    return "—" if pd.isna(x) else f"{x * 100:+.2f}%"


def result_table(f):
    table(
        [
            "전략",
            "진입/청산",
            "왕복 거래",
            "평균 이익",
            "평균 손실",
            "순손익(원)",
            "수익률",
            "BH 대비(%p)",
            "MDD",
        ],
        [
            [
                names[k],
                rules[k],
                int(a.trades),
                pct(a.win_mean),
                pct(a.loss_mean),
                f"{a.pnl:+,.0f}",
                pct(a.ret),
                f"{100 * a.excess:+.2f}",
                f"{100 * a.mdd:.2f}%",
            ]
            for k in names
            for a in f[f.strategy == k].itertuples()
        ],
    )


add("# 국내 50종목의 매매 신호와 모의매매 성과")
add(
    "\n**평가기간 2024-06-26~2026-09-02 · 532거래일 · 초기자금 전략당 1억원 · 편도 비용 0.15%**\n"
)
allr = base[base.quarter == "ALL"].set_index("strategy")
add(
    f"단순 보유가 {pct(allr.loc['BH', 'ret'])}로 전체 기간 수익률 1위였다. 능동 전략에서는 이동평균이 {pct(allr.loc['MA20', 'ret'])}로 가장 높았다. RSI의 최대 낙폭은 {100 * allr.loc['RSI14', 'mdd']:.2f}%였고, BH는 {100 * allr.loc['BH', 'mdd']:.2f}%였다. RSI의 평균 자금 투입률은 {100 * allr.loc['RSI14', 'exposure']:.1f}%, BH는 {100 * allr.loc['BH', 'exposure']:.1f}%다. 낙폭 차이에는 현금 보유 비중의 영향도 포함된다.\n"
)
add("## 전체 기간 결과\n")
result_table(base[base.quarter == "ALL"])
add("![누적 자산과 낙폭](equity_drawdown.png)\n")
add("## 신호가 방향을 구별했는가\n")
add(
    "아래 수치는 동일 날짜·동일 전략의 무신호 종목 평균에 대응시킨 값이다. 5일은 설명용 단면이고 그림에는 사전에 정한 세 기간을 모두 표시했다. 매도 신호는 음의 차이가 가설에 맞는다. 확률 열은 매수일 때 상승, 매도일 때 하락 확률이다. 보합은 별도 집계했다.\n"
)
ss = s[(s.quarter == "ALL") & (s.horizon == 5)]
table(
    [
        "전략",
        "신호",
        "건수",
        "방향 확률 / 무신호",
        "평균 수익률 / 무신호",
        "평균 차이(%p)",
        "95% 구간(%p)",
    ],
    [
        [
            names[a.strategy],
            "매수" if a.direction == 1 else "매도",
            a.n,
            f"{100 * (a.up if a.direction == 1 else a.down):.1f}% / {100 * (a.control_up if a.direction == 1 else a.control_down):.1f}%",
            f"{pct(a['return'])} / {pct(a.control_return)}",
            f"{100 * (a['return'] - a.control_return):+.2f}",
            f"[{100 * a.return_lo:+.2f}, {100 * a.return_hi:+.2f}]",
        ]
        for _, a in ss.iterrows()
    ],
)
add("![신호 효과](signal_effect.png)\n")
add(
    "구간이 0을 포함하면 방향 우위를 확정하기 어렵다. 신호 수익률은 당일 종가부터, 모의매매는 다음 거래일 종가부터 측정한다. 같은 날짜의 종목은 함께 움직이고 20일 수익률은 서로 겹치므로 날짜 블록 단위로 재표집했다. 구간은 개별 비교 기준이며 다중 검정 보정은 적용하지 않았다. 30건 또는 5개 종목 미만은 탐색용이고, 20거래일보다 짧은 분기의 구간은 해석에서 제외한다.\n"
)
add("## 분기별 성과\n![분기별 수익률](quarterly.png)\n")
add(
    "전체 기간 5거래일 기준으로 다섯 매수 신호와 다섯 매도 신호 모두 무신호 대비 평균 차이의 95% 구간이 0을 포함했다. 거래량 조건을 추가하면 5일 매수 신호의 수익률 차이는 +0.60%p에서 +0.88%p로 커졌지만, 매수 신호가 713건에서 417건으로 줄었다. 포트폴리오 전체 수익률도 돈치안 +69.23%에서 거래량 돌파 +43.69%로 낮아졌다.\n"
)
for q in quarters.columns:
    if q in (quarters.columns[0], quarters.columns[-1]):
        continue
    if (
        quarters.loc["BH", q]
        == quarters.loc["BH"].drop([quarters.columns[0], quarters.columns[-1]]).min()
    ):
        add(
            f"완전한 분기 중 BH 수익률이 가장 낮았던 {q}에는 BH {quarters.loc['BH', q]:+.2f}%, 이동평균 {quarters.loc['MA20', q]:+.2f}%, RSI {quarters.loc['RSI14', q]:+.2f}%였다. 아래 분기별 손익은 종목별 현금과 보유 주식을 다음 분기로 이월한 결과다.\n"
        )
for q in quarters.columns:
    f = base[base.quarter == q]
    a = f.iloc[0]
    add(
        f"### {q} · {a.start}~{a.end} · {a.days}거래일"
        + (" (부분 분기)" if q in (quarters.columns[0], quarters.columns[-1]) else "")
        + "\n"
    )
    result_table(f)
add(
    "분기 수익률에는 미실현 평가손익도 포함한다. 거래 수와 평균 이익·손실은 청산 분기에 귀속하므로 BH는 중간 분기에 거래가 없어도 수익률이 변한다. ‘—’는 해당 거래가 없다는 뜻이다.\n"
)
add("## 거래비용 민감도\n")
table(
    ["전략", "편도 0%", "편도 0.15%", "편도 0.30%", "기준 투입률", "기준 총비용(원)"],
    [
        [
            names[k],
            *[
                pct(x)
                for x in r[(r.strategy == k) & (r.quarter == "ALL")]
                .sort_values("fee")
                .ret
            ],
            f"{100 * allr.loc[k, 'exposure']:.1f}%",
            f"{allr.loc[k, 'cost']:,.0f}",
        ]
        for k in names
    ],
)
add("## 자료와 해석 범위\n")
add(
    "매수 가설은 이동평균의 추세 지속, RSI의 과매도 반등, 돈치안의 신고가 추세, 볼린저의 평균 회귀, 거래량 돌파의 거래량 확인이다. 거래비용은 실제 세율이 아닌 동일 조건의 실험값이다. 현재 파일에는 보통주·ETF 등 상품 유형 구분이나 수정주가 마스터가 없으므로 종목 유형별 해석은 유보한다.\n"
)
add(
    "- 전체 4,813개 코드 중 최초 60시장일에 유효 거래가 50일 이상인 코드에서 유효일 평균 거래대금 상위 50개를 고정했다. 상품 유형 혼합을 허용했다. 50개 종목은 각 200만원 독립 계정으로 운용했다. 성과는 이 표본의 가격수익이며 배당·현금이자는 제외했다."
)
add(
    "- exchange_calendars 4.13.3의 XKRX와 [코스피 일봉](https://fchart.stock.naver.com/sise.nhn?symbol=KOSPI&timeframe=day&count=1200&requestType=0)을 대조했다. 2026-07-17은 지수 관측이 없어 제외했다. 분석 캘린더 592일 중 원자료에 어떤 종목도 없는 날이 42일이다. 해당 날에는 거래하지 않고 보유가는 마지막 유효 종가로 평가했다. 가격 공백은 낙폭을 작게 보이게 할 수 있다."
)
add(
    "- 매수는 다음 시장일에 체결 불가하면 취소하고, 매도는 유효 종가까지 대기했다. 신호는 연속 22개 유효 관측이 있는 공통 표본에서 계산했다. 거래량 돌파는 직전 20일 평균 거래량의 1.5배 조건을 추가한다. 주 결과는 현금 보유를 허용하는 정수 주식 매매이며, 마지막 날 모든 전략의 잔여 포지션이 청산됐다."
)
add(
    "- 확인된 [LS ELECTRIC 액면분할](https://www.ls-electric.com/ko/company/invest/data/%EC%A3%BC%EC%8B%9D%EB%B6%84%ED%95%A0%EA%B3%B5%EA%B3%A0_260326.pdf)과 [에이피알 액면분할](https://kind.krx.co.kr/external/2024/10/30/000411/20241030000939/99322.htm)은 1→5주로 처리했다. [가온전선 무상증자](https://kind.krx.co.kr/external/2026/06/16/000492/20260616001141/11307.htm)는 권리락일부터 1주당 0.8주를 반영하고 단수는 상장일 종가로 정산했다. 전량 청산 규칙 때문에 신주 상장 전에는 권리가 있는 포지션 전체의 매도를 보류했다. 이는 보수적인 모형 가정이다."
)
add(
    "- 급변 점검 파일에는 두 유효 종가 사이에 결측일이 끼어 있는 경우와 부동소수점 오차로 정확히 30%인 경우도 들어 있다. 기업행동 전수 마스터와 공식 수정주가 전체를 대조한 결과는 아니므로 원화 금액은 모의 추정값이다. 실제 재현 수익, 일반 주식 전체의 성과, 향후 수익을 뜻하지 않는다."
)
add(
    "\n[신호별·분기별 상세](signal_results.csv) · [전체/분기 전략 지표](strategy_results.csv) · [종목 선정](universe.csv) · [거래 장부](trades.csv) · [일별 자산](daily_equity.csv) · [결측·급변](exclusions.csv) · [달력](calendar.csv) · [입력 해시](inventory.csv) · [사전 계획](../STRATEGY_STUDY_PLAN.md)"
)
(out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
