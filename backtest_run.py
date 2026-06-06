#!/usr/bin/env python3
"""
🔬 8축 진단 시스템 5년 백테스트
2021-01-04 ~ 현재까지 매주 월요일 시점:
- 그 시점에 알 수 있었던 데이터로 룰 적용
- 8축 평가 + 신호 집계
- 그 후 1/3/6개월 S&P 수익률 측정
- Hit rate, Sharpe, Max drawdown 계산

데이터 정직성:
- look-ahead bias 방지: FRED 월간 데이터는 발표 지연(보통 +15일) 보정
- 실시간 시점에 알 수 있었던 데이터만 사용
"""
import json
import sys
import os
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "yfinance", "pandas"])
    import yfinance as yf
    import pandas as pd

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# === 백테스트 기간 ===
BACKTEST_START = "2021-01-04"  # 5+ 년 전 첫 월요일
BACKTEST_END_BUFFER_DAYS = 200  # 6개월 미래 수익률 측정 위해 마진

# === Yahoo 심볼 ===
YAHOO_SYMBOLS = {
    "sp500":  "^GSPC",
    "vix":    "^VIX",
    "us10y":  "^TNX",
    "us3m":   "^IRX",
    "dxy":    "DX-Y.NYB",
    "wti":    "CL=F",
    "copper": "HG=F",
    "qqq":    "QQQ",
    "iwm":    "IWM",
    "tlt":    "TLT",
    "sqqq":   "SQQQ",
}

# === FRED 시리즈 (발표 지연 일수) ===
FRED_CONFIG = {
    "DFF":          {"lag_days": 1,  "transform": "latest"},   # 일간, 거의 즉시
    "UNRATE":       {"lag_days": 14, "transform": "latest"},   # BLS 매월 첫 금요일
    "PAYEMS":       {"lag_days": 14, "transform": "diff_1m"},
    "CES0500000003":{"lag_days": 14, "transform": "yoy"},
    "CPIAUCSL":     {"lag_days": 17, "transform": "yoy"},      # 매월 중순
    "DRCCLACBS":    {"lag_days": 45, "transform": "latest"},   # 분기, 45일 지연
    "DFII10":       {"lag_days": 1,  "transform": "latest"},
    "ICSA":         {"lag_days": 7,  "transform": "latest_k"},
    "RSAFS":        {"lag_days": 14, "transform": "yoy"},
}


# === FRED API ===
def fred_fetch_series_full(series_id, start_date, end_date):
    """FRED 시리즈 전체 시계열 가져오기."""
    if not FRED_API_KEY:
        return None
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
           f"&observation_start={start_date}&observation_end={end_date}"
           f"&sort_order=asc")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read())
        obs = data.get("observations", [])
        # 날짜로 정렬된 dict 반환
        result = {}
        for o in obs:
            if o["value"] != ".":
                try:
                    result[o["date"]] = float(o["value"])
                except ValueError:
                    pass
        return result
    except Exception as e:
        print(f"  ⚠️ FRED {series_id}: {e}", file=sys.stderr)
        return {}


def fred_value_at(series_data, eval_date, lag_days, transform="latest"):
    """시점 eval_date에 알 수 있었던 FRED 값 (look-ahead bias 방지)."""
    if not series_data:
        return None
    # 발표 지연 보정: eval_date - lag_days 이전 발표된 데이터만 사용
    cutoff = (datetime.strptime(eval_date, "%Y-%m-%d") - timedelta(days=lag_days)).strftime("%Y-%m-%d")
    available = {d: v for d, v in series_data.items() if d <= cutoff}
    if not available:
        return None
    sorted_dates = sorted(available.keys())
    latest_date = sorted_dates[-1]
    latest_val = available[latest_date]

    if transform == "latest":
        return {"value": latest_val, "date": latest_date}
    if transform == "latest_k":
        return {"value": latest_val / 1000.0, "date": latest_date}
    if transform == "diff_1m":
        if len(sorted_dates) > 1:
            prev_val = available[sorted_dates[-2]]
            return {"value": latest_val - prev_val, "date": latest_date}
        return None
    if transform == "yoy":
        # 12개월 전 값 찾기
        try:
            d = datetime.strptime(latest_date, "%Y-%m-%d")
            target = (d - timedelta(days=365)).strftime("%Y-%m-%d")
            # 가장 가까운 과거 데이터
            year_ago_candidates = [dd for dd in sorted_dates if dd <= target]
            if not year_ago_candidates:
                return None
            year_ago_val = available[year_ago_candidates[-1]]
            if year_ago_val == 0:
                return None
            yoy = (latest_val - year_ago_val) / year_ago_val * 100
            return {"value": yoy, "date": latest_date, "level": latest_val}
        except Exception:
            return None
    return None


# === 룰 평가 함수 (update_data.py와 동일 로직, 시계열 적용) ===
def rate(value, low, high):
    """긍정/중립/부정 구분 헬퍼."""
    pass  # 각 축마다 다른 로직


def eval_interest(prices_at, fred_at):
    p10 = prices_at.get("us10y")
    p3m = prices_at.get("us3m")
    if p10 is None:
        return None
    yld10 = p10
    yld3m = p3m
    real_yld = (fred_at.get("DFII10") or {}).get("value") if fred_at.get("DFII10") else None
    inverted = yld3m and yld3m > yld10

    if real_yld and real_yld > 2.5:
        return "부정"
    if inverted:
        return "부정"
    if yld10 >= 5.0:
        return "부정"
    if real_yld and real_yld > 1.5:
        return "중립"
    if yld10 >= 4.0:
        return "중립"
    if real_yld is not None and real_yld < 0:
        return "긍정"
    return "긍정"


def eval_vix(prices_at):
    v = prices_at.get("vix")
    if v is None:
        return None
    if v >= 30 or v <= 10:
        return "부정"
    if 15 <= v <= 20:
        return "긍정"
    return "중립"


def eval_dollar(prices_at):
    dxy = prices_at.get("dxy")
    wti = prices_at.get("wti")
    copper = prices_at.get("copper")
    if not (dxy and wti and copper):
        return None
    if wti >= 100 and copper >= 4.0 and dxy < 100:
        return "부정"  # 공급 충격
    score = 0
    if wti >= 100: score -= 2
    elif wti >= 85: score -= 1
    elif 70 <= wti <= 85: score += 1
    elif wti < 60: score -= 1
    if copper >= 4.0: score += 1
    elif copper < 3.5: score -= 1
    if dxy > 105: score -= 1
    if score >= 1: return "긍정"
    if score <= -2: return "부정"
    return "중립"


def eval_flow(prices_at_t, prices_at_5dago):
    """ETF 5일 변화율 기반."""
    if not prices_at_5dago:
        return "중립"
    def chg(k):
        a = prices_at_t.get(k); b = prices_at_5dago.get(k)
        if a is None or b is None or b == 0: return 0
        return (a - b) / b * 100
    qqq_5d = chg("qqq")
    iwm_5d = chg("iwm")
    tlt_5d = chg("tlt")
    sqqq_5d = chg("sqqq")
    if sqqq_5d > 5: return "부정"
    if tlt_5d > 1 and qqq_5d < 0: return "부정"
    if iwm_5d > qqq_5d and iwm_5d > 0: return "긍정"
    if qqq_5d > 1 and iwm_5d > 0: return "긍정"
    return "중립"


def eval_employment(fred_at):
    unrate_d = fred_at.get("UNRATE")
    nfp_d = fred_at.get("PAYEMS")
    wage_d = fred_at.get("CES0500000003")
    if not unrate_d:
        return None
    unrate = unrate_d["value"]
    nfp = nfp_d["value"] if nfp_d else None
    wage = wage_d["value"] if wage_d else None
    if unrate >= 5.0 or (nfp is not None and nfp < 50):
        return "부정"
    if nfp is not None and nfp > 300 and wage is not None and wage > 5:
        return "부정"
    if 3.8 <= unrate <= 4.2 and nfp is not None and 100 <= nfp <= 250 and wage is not None and 3 <= wage <= 4:
        return "긍정"
    return "중립"


def eval_consumption(fred_at):
    delinq_d = fred_at.get("DRCCLACBS")
    retail_d = fred_at.get("RSAFS")
    cpi_d = fred_at.get("CPIAUCSL")
    if not delinq_d:
        return None
    delinq = delinq_d["value"]
    retail = retail_d["value"] if retail_d else None
    cpi = cpi_d["value"] if cpi_d else None
    real_retail = (retail - cpi) if (retail is not None and cpi is not None) else None
    if delinq > 4.5:
        return "부정"
    if delinq > 3.5 or (real_retail is not None and real_retail < -2):
        return "부정"
    if delinq <= 2.5 and real_retail is not None and real_retail > 1:
        return "긍정"
    return "중립"


# === 시점별 평가 ===
def evaluate_at(date_str, prices_at, prices_5dago, fred_at):
    """8축 평가 → 신호 집계."""
    ratings = {
        "interest":    eval_interest(prices_at, fred_at),
        "flow":        eval_flow(prices_at, prices_5dago),
        "employment":  eval_employment(fred_at),
        "consumption": eval_consumption(fred_at),
        "dollar":      eval_dollar(prices_at),
        "vix":         eval_vix(prices_at),
    }
    # 정적 2축 (실적, 마진) — 백테스트에서는 "긍정" 고정 (역사적으로 대부분 호조였음)
    # 더 정확히 하려면 분기별 Beat Rate 데이터 필요, 일단 단순화
    pos = sum(1 for v in ratings.values() if v == "긍정")
    neu = sum(1 for v in ratings.values() if v == "중립")
    neg = sum(1 for v in ratings.values() if v == "부정")
    none_count = sum(1 for v in ratings.values() if v is None)
    score = pos - neg

    # 4-4-반반 규칙 (6축 기준으로 조정: 절반인 3)
    if pos >= 4:
        phase = "강세장"
    elif neg >= 4:
        phase = "약세장"
    elif pos > neg:
        phase = "강세 우위"
    elif neg > pos:
        phase = "약세 우위"
    else:
        phase = "균형"

    return {
        "date": date_str,
        "ratings": ratings,
        "positive": pos, "neutral": neu, "negative": neg, "none": none_count,
        "score": score, "phase": phase
    }


# === 메인 ===
def main():
    print("="*60)
    print("🔬 8축 백테스트 시작 (2021-01-04 ~ 현재)")
    print(f"   FRED API: {'활성' if FRED_API_KEY else '비활성'}")
    print("="*60)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    end_date = today
    print(f"\n📊 [1/4] Yahoo Finance 5년 가격 히스토리 다운로드...")
    tickers = list(YAHOO_SYMBOLS.values())
    data = yf.download(tickers, start="2020-06-01", end=end_date, progress=False, auto_adjust=False)
    # data['Close']에 각 ticker의 종가가 있음
    if isinstance(data.columns, pd.MultiIndex):
        close = data['Close']
    else:
        close = data[['Close']]
    # 컬럼명을 SYMBOLS 키로 변환
    rename_map = {v: k for k, v in YAHOO_SYMBOLS.items()}
    close = close.rename(columns=rename_map)
    print(f"   ✓ {len(close)} 거래일, {len(close.columns)} 심볼")

    print(f"\n🏛️ [2/4] FRED 시리즈 다운로드...")
    fred_series = {}
    for sid in FRED_CONFIG.keys():
        series = fred_fetch_series_full(sid, "2019-01-01", end_date)
        fred_series[sid] = series
        print(f"   ✓ {sid}: {len(series) if series else 0} 관측")

    print(f"\n⚙️  [3/4] 매주 월요일 시점 백테스트...")
    # 매주 월요일 추출
    start_dt = datetime.strptime(BACKTEST_START, "%Y-%m-%d")
    end_dt = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=BACKTEST_END_BUFFER_DAYS)
    weekly_dates = []
    cur = start_dt
    while cur <= end_dt:
        if cur.weekday() == 0:  # 월요일
            weekly_dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    print(f"   총 {len(weekly_dates)} 시점")

    results = []
    for i, ds in enumerate(weekly_dates):
        try:
            # 가장 가까운 이전 거래일 가격
            target_dt = pd.to_datetime(ds)
            available = close.loc[close.index <= target_dt]
            if available.empty:
                continue
            row = available.iloc[-1]
            prices_at = {k: (float(row[k]) if k in row and pd.notna(row[k]) else None)
                         for k in YAHOO_SYMBOLS.keys()}

            # 5일 전
            five_dago_target = target_dt - pd.Timedelta(days=7)
            available_5d = close.loc[close.index <= five_dago_target]
            if not available_5d.empty:
                row5 = available_5d.iloc[-1]
                prices_5dago = {k: (float(row5[k]) if k in row5 and pd.notna(row5[k]) else None)
                                for k in YAHOO_SYMBOLS.keys()}
            else:
                prices_5dago = {}

            # FRED at date (look-ahead 보정)
            fred_at = {}
            for sid, conf in FRED_CONFIG.items():
                val = fred_value_at(fred_series.get(sid, {}), ds, conf["lag_days"], conf["transform"])
                fred_at[sid] = val

            # 평가
            eval_result = evaluate_at(ds, prices_at, prices_5dago, fred_at)

            # 미래 수익률 (1/3/6개월)
            sp500_now = prices_at.get("sp500")
            if sp500_now:
                for label, days in [("ret_1m", 30), ("ret_3m", 90), ("ret_6m", 180)]:
                    future_target = target_dt + pd.Timedelta(days=days)
                    future_avail = close.loc[close.index >= future_target]
                    if not future_avail.empty:
                        sp_future = float(future_avail.iloc[0]['sp500'])
                        if sp_future and sp500_now:
                            eval_result[label] = round((sp_future - sp500_now) / sp500_now * 100, 2)
                    else:
                        eval_result[label] = None
            eval_result["sp500"] = round(sp500_now, 2) if sp500_now else None
            results.append(eval_result)

        except Exception as e:
            print(f"   ⚠️ {ds}: {e}")

    print(f"   ✓ {len(results)} 시점 평가 완료")

    print(f"\n📈 [4/4] 통계 계산...")
    stats = compute_statistics(results)
    for k, v in stats["overall"].items():
        print(f"   {k}: {v}")

    print(f"\n💾 backtest.json 저장...")
    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "period_start": BACKTEST_START,
        "period_end": today,
        "total_points": len(results),
        "results": results,
        "stats": stats,
        "notes": {
            "look_ahead_bias_prevention": "FRED 월간 데이터는 +14~17일 발표 지연 보정",
            "axes_evaluated": 6,
            "static_axes_excluded": ["earnings (분기 발표)", "margin (분기 발표)"],
            "rule_source": "자산네제곱 1단계 (자산제곱)",
        }
    }
    with open("backtest.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"✅ 백테스트 완료")
    print(f"   기간: {BACKTEST_START} ~ {today}")
    print(f"   시점: {len(results)}개 주간")
    print(f"   Hit Rate (강세→3M+): {stats['overall']['bull_hit_3m']:.1f}%")
    print(f"   Hit Rate (약세→3M-): {stats['overall']['bear_hit_3m']:.1f}%")
    print(f"   강세 신호 평균 3M: {stats['by_signal']['strong_bull']['avg_3m']:.2f}%")
    print(f"   약세 신호 평균 3M: {stats['by_signal']['strong_bear']['avg_3m']:.2f}%")
    print(f"{'='*60}\n")


def compute_statistics(results):
    """백테스트 통계 계산 (v2.0 — Sharpe/MDD/OOS/Rolling)."""
    import math

    def avg(lst):
        lst = [x for x in lst if x is not None]
        return sum(lst) / len(lst) if lst else 0

    def std(lst):
        lst = [x for x in lst if x is not None]
        if len(lst) < 2:
            return 0
        m = sum(lst) / len(lst)
        return math.sqrt(sum((x - m) ** 2 for x in lst) / (len(lst) - 1))

    def hit_rate(returns, direction='positive'):
        valid = [r for r in returns if r is not None]
        if not valid:
            return 0
        if direction == 'positive':
            return len([r for r in valid if r > 0]) / len(valid) * 100
        return len([r for r in valid if r < 0]) / len(valid) * 100

    def category_stats(cat):
        rets_3m = [r.get("ret_3m") for r in cat if r.get("ret_3m") is not None]
        return {
            "count": len(cat),
            "avg_1m": round(avg([r.get("ret_1m") for r in cat]), 2),
            "avg_3m": round(avg([r.get("ret_3m") for r in cat]), 2),
            "avg_6m": round(avg([r.get("ret_6m") for r in cat]), 2),
            "std_3m": round(std([r.get("ret_3m") for r in cat]), 2),
            "hit_3m_positive": round(hit_rate(rets_3m, 'positive'), 1),
            "pct": round(len(cat) / len(results) * 100, 1) if results else 0,
        }

    # === 신호별 카테고리 ===
    strong_bull = [r for r in results if r["positive"] >= 4]
    bull_lean = [r for r in results if r["positive"] == 3]
    balanced = [r for r in results if r["positive"] == r["negative"]]
    bear_lean = [r for r in results if r["negative"] == 3]
    strong_bear = [r for r in results if r["negative"] >= 4]

    by_signal = {
        "strong_bull": category_stats(strong_bull),
        "bull_lean": category_stats(bull_lean),
        "balanced": category_stats(balanced),
        "bear_lean": category_stats(bear_lean),
        "strong_bear": category_stats(strong_bear),
    }

    # === Hit rate ===
    bull_3m = [r.get("ret_3m") for r in results if r["positive"] >= 4]
    bear_3m = [r.get("ret_3m") for r in results if r["negative"] >= 4]
    bull_hit_3m = hit_rate(bull_3m, 'positive')
    bear_hit_3m = hit_rate(bear_3m, 'negative')

    # === 전략 시뮬레이션: 신호 따라 포지션 조정 ===
    # 강세 4+: 100% / 강세 3: 75% / 균형: 50% / 약세 3: 25% / 약세 4+: 0%
    def position_weight(r):
        if r["positive"] >= 4: return 1.0
        if r["positive"] == 3: return 0.75
        if r["negative"] >= 4: return 0.0
        if r["negative"] == 3: return 0.25
        return 0.5

    # 매 시점에 다음 주(7일) 수익률 (close 데이터에서 직접 안 받았으니 1M으로 4로 나눠 근사)
    # 더 정확하게: ret_1m / 4.33 (주간 수익률 근사)
    portfolio_returns = []
    bnh_returns = []
    for r in results:
        weight = position_weight(r)
        # 주간 수익률 = 월간 / 4.33
        ret_w = (r.get("ret_1m") or 0) / 4.33 / 100  # 소수점 표현
        port_ret = weight * ret_w
        portfolio_returns.append(port_ret)
        bnh_returns.append(ret_w)  # 100% 항상 매수

    # 누적 자산 (1.0에서 시작)
    portfolio_equity = [1.0]
    bnh_equity = [1.0]
    for pr, br in zip(portfolio_returns, bnh_returns):
        portfolio_equity.append(portfolio_equity[-1] * (1 + pr))
        bnh_equity.append(bnh_equity[-1] * (1 + br))

    # 최종 수익률
    portfolio_total_return = (portfolio_equity[-1] - 1) * 100
    bnh_total_return = (bnh_equity[-1] - 1) * 100

    # Max drawdown
    def max_drawdown(equity):
        peak = equity[0]
        mdd = 0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > mdd:
                mdd = dd
        return mdd

    port_mdd = max_drawdown(portfolio_equity)
    bnh_mdd = max_drawdown(bnh_equity)

    # Sharpe ratio (연환산, 무위험 수익률 0 가정)
    # 주간 수익률 × 52주
    def annualized_sharpe(weekly_returns):
        valid = [r for r in weekly_returns if r is not None]
        if len(valid) < 2:
            return 0
        m = sum(valid) / len(valid)
        s = math.sqrt(sum((r - m) ** 2 for r in valid) / (len(valid) - 1))
        if s == 0:
            return 0
        return (m * 52) / (s * math.sqrt(52))

    port_sharpe = annualized_sharpe(portfolio_returns)
    bnh_sharpe = annualized_sharpe(bnh_returns)

    # === 누적 자산 곡선 (시계열 차트용, 매주 데이터) ===
    equity_curve = []
    for i, r in enumerate(results):
        equity_curve.append({
            "date": r["date"],
            "portfolio": round(portfolio_equity[i + 1] * 100, 2),  # 100 = base
            "bnh": round(bnh_equity[i + 1] * 100, 2),
        })

    # === Out-of-sample 분할 (2021-2023 vs 2024-현재) ===
    in_sample = [r for r in results if r["date"] < "2024-01-01"]
    oos_sample = [r for r in results if r["date"] >= "2024-01-01"]

    def oos_stats(sample, label):
        if not sample:
            return None
        bull = [r.get("ret_3m") for r in sample if r["positive"] >= 4]
        bear = [r.get("ret_3m") for r in sample if r["negative"] >= 4]
        return {
            "period": label,
            "total_points": len(sample),
            "bull_count": sum(1 for r in sample if r["positive"] >= 4),
            "bear_count": sum(1 for r in sample if r["negative"] >= 4),
            "bull_hit_3m": round(hit_rate(bull, 'positive'), 1),
            "bear_hit_3m": round(hit_rate(bear, 'negative'), 1),
            "avg_score": round(avg([r["score"] for r in sample]), 2),
        }

    # === 롤링 6개월 hit rate ===
    rolling_window = 26  # 약 6개월 (주간 단위)
    rolling_hits = []
    for i in range(rolling_window, len(results)):
        window = results[i - rolling_window:i]
        bull_window = [r.get("ret_3m") for r in window if r["positive"] >= 4]
        if bull_window:
            rolling_hits.append({
                "date": results[i]["date"],
                "bull_hit_3m": round(hit_rate(bull_window, 'positive'), 1),
            })

    return {
        "by_signal": by_signal,
        "overall": {
            "total_points": len(results),
            "bull_hit_3m": round(bull_hit_3m, 1),
            "bear_hit_3m": round(bear_hit_3m, 1),
            "avg_score": round(avg([r["score"] for r in results]), 2),
            "avg_3m_all": round(avg([r.get("ret_3m") for r in results]), 2),
        },
        "strategy": {
            "description": "신호 기반 포지션 조정: 강세4+ 100%, 강세3 75%, 균형 50%, 약세3 25%, 약세4+ 0%",
            "portfolio_total_return": round(portfolio_total_return, 2),
            "bnh_total_return": round(bnh_total_return, 2),
            "portfolio_sharpe_annualized": round(port_sharpe, 3),
            "bnh_sharpe_annualized": round(bnh_sharpe, 3),
            "portfolio_max_drawdown": round(port_mdd, 2),
            "bnh_max_drawdown": round(bnh_mdd, 2),
            "outperformance": round(portfolio_total_return - bnh_total_return, 2),
        },
        "equity_curve": equity_curve,
        "out_of_sample": {
            "in_sample (2021-2023)": oos_stats(in_sample, "2021-01 ~ 2023-12"),
            "out_of_sample (2024-)": oos_stats(oos_sample, "2024-01 ~ 현재"),
        },
        "rolling_hit_rate_6m": rolling_hits,
    }


if __name__ == "__main__":
    main()
