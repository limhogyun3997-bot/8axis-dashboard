#!/usr/bin/env python3
"""
종목별 기술적 4대 축 자동 분석 (기술적 분석 2강 기준) v1.0

yfinance 일봉 OHLCV(1년)로 4대 축을 0/1/2로 자동 채점 → tech.json
  구조  : 추세(정배열/역배열 + 고점·저점 진행)        2=상승추세 1=횡보 0=하락추세
  가격  : 52주 범위 내 위치(쌀수록 진입 우호)          2=하단 1=중간 0=상단
  시간  : RSI 에너지(과매도=축적, 과매수=소진)         2=조정충분 1=보통 0=과열
  유동성: 최근 긴 아래꼬리+회복(사냥 완료 프록시 ★)    2=사냥완료 1=중립 0=위에쌓임

진입 적합도 = 4축 합(0~8). 7~8 적극 / 5~6 분할 / 3~4 관망 / 0~2 회피
※ 유동성은 정성 영역이라 캔들 프록시로 근사 — ★ 표시.
"""
import json
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "yfinance", "pandas"])
    import yfinance as yf
    import pandas as pd

try:
    from score_stocks import WATCHLIST
except Exception:
    WATCHLIST = [
        ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"),
        ("AMZN", "Amazon"), ("META", "Meta"), ("NVDA", "NVIDIA"), ("TSLA", "Tesla"),
    ]

KST = timezone(timedelta(hours=9))


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def axis_structure(close, ma50, ma200, hi, lo):
    """구조: 정배열/역배열 + 추세 0/1/2"""
    c = close.iloc[-1]
    m50 = ma50.iloc[-1]
    m200 = ma200.iloc[-1] if not pd.isna(ma200.iloc[-1]) else None
    # 정배열 / 역배열
    if m200 is not None:
        if c > m50 > m200:
            return 2, "상승추세(정배열)"
        if c < m50 < m200:
            return 0, "하락추세(역배열)"
    # 보조: 최근 60일 고점·저점 진행
    recent = close.iloc[-60:]
    first_half = recent.iloc[:30]
    second_half = recent.iloc[30:]
    if second_half.max() > first_half.max() and second_half.min() > first_half.min():
        return 2, "상승추세(고점·저점 상승)"
    if second_half.max() < first_half.max() and second_half.min() < first_half.min():
        return 0, "하락추세(고점·저점 하락)"
    return 1, "횡보"


def axis_price(close, hi, lo):
    """가격: 52주 범위 내 위치 (낮을수록 우호) 0/1/2"""
    c = close.iloc[-1]
    rng = hi - lo
    if rng <= 0:
        return 1, "범위 산출 불가"
    pct = (c - lo) / rng * 100
    if pct < 33:
        return 2, f"범위 하단({pct:.0f}%)·싸다"
    if pct < 66:
        return 1, f"범위 중간({pct:.0f}%)"
    return 0, f"범위 상단({pct:.0f}%)·비싸다"


def axis_time(rsi_val):
    """시간: RSI 에너지 0/1/2"""
    if rsi_val is None or pd.isna(rsi_val):
        return 1, "RSI 산출 불가"
    if rsi_val < 35:
        return 2, f"과매도(RSI {rsi_val:.0f})·에너지 축적"
    if rsi_val <= 65:
        return 1, f"중립(RSI {rsi_val:.0f})"
    return 0, f"과매수(RSI {rsi_val:.0f})·에너지 소진"


def axis_liquidity(df):
    """유동성(프록시 ★): 최근 10봉 긴 아래꼬리+회복 → 사냥 완료 0/1/2"""
    recent = df.iloc[-10:]
    best_wick = 0.0
    swept = False
    for _, row in recent.iterrows():
        hi, lo, op, cl = row["High"], row["Low"], row["Open"], row["Close"]
        rng = hi - lo
        if rng <= 0:
            continue
        lower_wick = (min(op, cl) - lo) / rng  # 아래꼬리 비율
        close_pos = (cl - lo) / rng            # 종가가 범위 상단이면 회복
        if lower_wick > best_wick:
            best_wick = lower_wick
        if lower_wick >= 0.45 and close_pos >= 0.6:
            swept = True
    # 현재 위치가 최근 20봉 고점 근처면 위에 유동성(불리)
    near_high = df["Close"].iloc[-1] >= df["High"].iloc[-20:].max() * 0.98
    if swept:
        return 2, "긴 아래꼬리+회복(사냥 완료 가능)"
    if near_high:
        return 0, "최근 고점 근접(위에 유동성)"
    return 1, "중립"


def verdict(score):
    if score >= 7:
        return "적극 진입"
    if score >= 5:
        return "분할 진입"
    if score >= 3:
        return "신중·관망"
    return "회피"


def main():
    out = []
    for ticker, name in WATCHLIST:
        try:
            df = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
            if df is None or len(df) < 60:
                print(f"⚠️  {ticker}: 데이터 부족 — 건너뜀", file=sys.stderr)
                continue
            close = df["Close"]
            ma50 = close.rolling(50).mean()
            ma200 = close.rolling(200).mean()
            r = rsi(close)
            rsi_val = float(r.iloc[-1]) if not pd.isna(r.iloc[-1]) else None
            hi = float(close.max())
            lo = float(close.min())

            s_struct, d_struct = axis_structure(close, ma50, ma200, hi, lo)
            s_price, d_price = axis_price(close, hi, lo)
            s_time, d_time = axis_time(rsi_val)
            s_liq, d_liq = axis_liquidity(df)
            score = s_struct + s_price + s_time + s_liq

            out.append({
                "ticker": ticker, "name": name,
                "struct": s_struct, "price": s_price, "time": s_time, "liq": s_liq,
                "score": score, "verdict": verdict(score),
                "d_struct": d_struct, "d_price": d_price, "d_time": d_time, "d_liq": d_liq,
                "close": round(float(close.iloc[-1]), 2),
                "rsi": round(rsi_val, 1) if rsi_val is not None else None,
                "range_pct": round((float(close.iloc[-1]) - lo) / (hi - lo) * 100, 0) if hi > lo else None,
            })
            print(f"✅ {ticker}: {score}/8 ({verdict(score)}) — {d_struct}")
            time.sleep(0.3)
        except Exception as e:
            print(f"❌ {ticker}: {e}", file=sys.stderr)
            continue

    out.sort(key=lambda x: x["score"], reverse=True)
    now = datetime.now(KST)
    result = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_updated_kst": now.strftime("%Y-%m-%d %H:%M KST"),
        "version": "tech-1.0",
        "basis": "yfinance 일봉(1년) · 기술적 분석 2강 4대 축 · 유동성은 캔들 프록시(★)",
        "axes": {"구조": "MA정배열·추세", "가격": "52주 범위 위치", "시간": "RSI 에너지", "유동성": "꼬리 사냥 프록시"},
        "stocks": out,
    }
    with open("tech.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n📊 tech.json 저장 완료 — {len(out)}종목")


if __name__ == "__main__":
    main()
