#!/usr/bin/env python3
"""
종목 점수 백테스트 검증 v1.0 — 4축 기술점수가 미래 수익을 예측하는가?

방법 (point-in-time, look-ahead 없음):
  · 유동성 높은 ~90종목, 최근 5년 일봉
  · 매월(21거래일) 시점마다 그 시점까지의 데이터로 4축 점수 계산 (tech_analysis와 동일 함수 재사용)
  · 그 시점 이후 1개월(21d)·3개월(63d) forward 수익률 측정
  · 점수 버킷별 평균 forward 수익 + IC(점수-수익 상관) + 축별 예측력

정직한 한계:
  · 기술 4축만 검증 — 펀더멘털은 과거 시점 데이터를 무료로 못 구해 검증 불가
  · 현재 구성종목 기반이라 생존편향(survivorship bias) 존재 → 절대 수익보다 '버킷 간 상대 비교'로 해석
출력: backtest_scores.json → 대시보드 백테스트 탭에 표시
"""
import json
import sys
from datetime import datetime, timezone, timedelta

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "yfinance", "pandas"])
    import yfinance as yf
    import pandas as pd

# tech_analysis의 실제 채점 함수 재사용 (라이브와 100% 동일 로직)
from tech_analysis import rsi, axis_structure, axis_price, axis_time, axis_liquidity

KST = timezone(timedelta(hours=9))

UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","AVGO","TSLA","JPM","V","UNH","LLY","XOM","JNJ","WMT",
    "MA","COST","HD","PG","ORCL","AMD","CRM","NFLX","ADBE","KO","PEP","MRK","ABBV","BAC","CVX","CSCO",
    "QCOM","TXN","INTC","IBM","NOW","INTU","AMAT","MU","LRCX","WFC","GS","MS","AXP","BLK","TMO","ABT",
    "MCD","NKE","SBUX","DIS","LOW","CAT","GE","BA","HON","UNP","LIN","PANW","CRWD","SHOP","UBER","ABNB",
    "MRVL","SNOW","DDOG","NET","SE","PYPL","SQ","ROKU","PLTR","SOFI","COIN","DKNG","RBLX","F","GM","T",
    "VZ","PFE","CMCSA","TGT","NEE","DE","MMM","GILD","ISRG","BKNG","ADP","MDT","C","SCHW","SPGI",
]


def score_at(window_df):
    """window_df(시점까지의 OHLCV)로 4축 점수 계산 — None이면 산출 불가"""
    c = window_df["Close"]
    if len(c) < 210:
        return None
    try:
        s_struct, _ = axis_structure(c)
        s_price, _, _ = axis_price(c)
        rv = rsi(c)
        rval = float(rv.iloc[-1]) if not pd.isna(rv.iloc[-1]) else None
        s_time, _ = axis_time(rval)
        s_liq, _ = axis_liquidity(window_df)
    except Exception:
        return None
    return (s_struct, s_price, s_time, s_liq, s_struct + s_price + s_time + s_liq)


def main():
    print(f"📥 {len(UNIVERSE)}종목 5년 일봉 다운로드…")
    data = yf.download(UNIVERSE, period="5y", interval="1d", group_by="ticker",
                       auto_adjust=True, threads=True, progress=False)
    recs = []  # dict per observation
    for t in UNIVERSE:
        try:
            sub = data[t].dropna(subset=["Close"]) if len(UNIVERSE) > 1 else data.dropna(subset=["Close"])
        except Exception:
            continue
        if len(sub) < 300:
            continue
        close = sub["Close"].reset_index(drop=True)
        sub = sub.reset_index(drop=True)
        n = len(sub)
        for i in range(210, n - 63, 21):  # 매월, 3개월 forward 여유
            sc = score_at(sub.iloc[:i + 1])
            if not sc:
                continue
            p0 = close.iloc[i]
            if p0 <= 0:
                continue
            f1 = (close.iloc[i + 21] / p0 - 1) * 100
            f3 = (close.iloc[i + 63] / p0 - 1) * 100
            recs.append({"struct": sc[0], "price": sc[1], "time": sc[2], "liq": sc[3],
                         "score": sc[4], "f1": f1, "f3": f3})
        print(f"  {t}: 누적 {len(recs)} 관측")

    if len(recs) < 100:
        print("⚠️ 관측치 부족 — 종료", file=sys.stderr)
        return
    df = pd.DataFrame(recs)

    def buckets(col):
        out = []
        for lo, hi, label in [(0, 2, "0-2 (약)"), (3, 4, "3-4 (중하)"), (5, 6, "5-6 (중상)"), (7, 8, "7-8 (강)")]:
            seg = df[(df["score"] >= lo) & (df["score"] <= hi)]
            if len(seg):
                out.append({"range": label, "n": int(len(seg)),
                            "mean_ret": round(float(seg[col].mean()), 2),
                            "win_rate": round(float((seg[col] > 0).mean() * 100), 1)})
        return out

    result = {
        "last_updated_kst": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "version": "score-bt-1.0",
        "universe_size": len(set(df.index) and UNIVERSE),
        "n_observations": int(len(df)),
        "period": "최근 5년 · 월간 리밸런스",
        "forward_1m": {
            "buckets": buckets("f1"),
            "ic": round(float(df["score"].corr(df["f1"])), 3),
            "spread": round(float(df[df.score >= 7]["f1"].mean() - df[df.score <= 2]["f1"].mean()), 2) if len(df[df.score >= 7]) and len(df[df.score <= 2]) else None,
        },
        "forward_3m": {
            "buckets": buckets("f3"),
            "ic": round(float(df["score"].corr(df["f3"])), 3),
            "spread": round(float(df[df.score >= 7]["f3"].mean() - df[df.score <= 2]["f3"].mean()), 2) if len(df[df.score >= 7]) and len(df[df.score <= 2]) else None,
        },
        "axis_ic_1m": {
            "구조": round(float(df["struct"].corr(df["f1"])), 3),
            "가격": round(float(df["price"].corr(df["f1"])), 3),
            "시간": round(float(df["time"].corr(df["f1"])), 3),
            "유동성": round(float(df["liq"].corr(df["f1"])), 3),
        },
        "note": "기술 4축만 검증(펀더는 과거 데이터 불가). 현재 구성종목 기반이라 생존편향 있음 — 절대수익보다 점수 버킷 간 상대 비교로 해석.",
    }
    with open("backtest_scores.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n📊 backtest_scores.json 저장")
    print(f"  관측 {result['n_observations']} · 1M IC {result['forward_1m']['ic']} · 3M IC {result['forward_3m']['ic']}")
    for b in result["forward_1m"]["buckets"]:
        print(f"  [{b['range']}] n={b['n']} 1M평균 {b['mean_ret']}% 승률 {b['win_rate']}%")


if __name__ == "__main__":
    main()
