#!/usr/bin/env python3
"""
일일 시장 데이터 수집기 (8축 진단 대시보드용) v2.0

데이터 소스:
- Yahoo Finance (yfinance): 시세 38개 심볼
- FRED API (St. Louis Fed): 거시 지표 9개

룰 기반 평가 (4축 → 6축으로 확장):
- 자동: 금리(1), 자금흐름(3), 고용(4), 소비(5), 달러/원자재(7), VIX(8)
- 정적: 실적(2), 마진(6) — 분기 발표
"""
import json
import sys
import os
import urllib.request
from datetime import datetime, timezone, timedelta

# ============ yfinance ============
try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "yfinance"])
    import yfinance as yf

# ============ FRED API ============
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
if not FRED_API_KEY:
    print("⚠️  FRED_API_KEY 환경변수 없음. FRED 데이터 건너뜀.", file=sys.stderr)

FRED_SERIES = {
    # series_id: (label, type, transform)
    "DFF":          {"label": "Fed 실효금리",    "unit": "%",   "transform": "latest"},
    "UNRATE":       {"label": "실업률",          "unit": "%",   "transform": "latest"},
    "PAYEMS":       {"label": "비농업 고용",      "unit": "K",   "transform": "diff_1m"},  # 전월 대비 변화
    "CES0500000003":{"label": "시간당 임금",      "unit": "%",   "transform": "yoy"},
    "CPIAUCSL":     {"label": "CPI (YoY)",       "unit": "%",   "transform": "yoy"},
    "DRCCLACBS":    {"label": "카드 연체율",      "unit": "%",   "transform": "latest"},
    "DFII10":       {"label": "10Y 실질금리",     "unit": "%",   "transform": "latest"},
    "ICSA":         {"label": "주간 실업수당청구","unit": "K",   "transform": "latest_k"},
    "RSAFS":        {"label": "소매판매 (YoY)",   "unit": "%",   "transform": "yoy"},
}

def fred_fetch(series_id, limit=14):
    """FRED API 호출 — 최근 N개 관측값."""
    if not FRED_API_KEY:
        return None
    try:
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
               f"&sort_order=desc&limit={limit}")
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("observations", [])
    except Exception as e:
        print(f"  ⚠️  FRED {series_id}: {e}", file=sys.stderr)
        return None

def fred_transform(obs, transform):
    """관측값 리스트 → 변환된 값."""
    if not obs:
        return None
    try:
        # 최신값
        latest = obs[0]
        latest_val = float(latest["value"])
        latest_date = latest["date"]

        if transform == "latest":
            return {"value": round(latest_val, 2), "date": latest_date}

        if transform == "latest_k":
            return {"value": round(latest_val / 1000, 1), "date": latest_date, "unit_note": "천명"}

        if transform == "diff_1m":
            # 전월 대비 변화 (천명 단위)
            if len(obs) > 1:
                prev_val = float(obs[1]["value"])
                diff = (latest_val - prev_val)  # PAYEMS는 천명 단위
                return {"value": round(diff, 0), "date": latest_date, "unit_note": "천명 (전월 대비)"}
            return None

        if transform == "yoy":
            # 1년 전 (12개월 전) 대비 변화율
            if len(obs) >= 13:
                year_ago_val = float(obs[12]["value"])
                yoy_pct = (latest_val - year_ago_val) / year_ago_val * 100
                return {"value": round(yoy_pct, 2), "date": latest_date, "level": round(latest_val, 2)}
            return None
    except (ValueError, KeyError, TypeError) as e:
        print(f"  ⚠️  transform error: {e}", file=sys.stderr)
        return None

def fred_fetch_all():
    """모든 FRED 시리즈 가져오기."""
    print("\n🏛️  FRED 거시 데이터 수집...")
    results = {}
    for sid, meta in FRED_SERIES.items():
        obs = fred_fetch(sid, limit=14)
        if obs:
            val = fred_transform(obs, meta["transform"])
            if val:
                val["series_id"] = sid
                val["label"] = meta["label"]
                val["unit"] = meta["unit"]
                results[sid] = val
                print(f"  ✓ {sid:18s} {meta['label']:15s} = {val['value']:.2f}{meta['unit']:2s} ({val['date']})")
            else:
                print(f"  ✗ {sid:18s} 변환 실패")
                results[sid] = None
        else:
            results[sid] = None
    return results


# ============ Yahoo Finance ============

SYMBOLS = {
    # 지수
    "sp500":  "^GSPC",  "nasdaq": "^IXIC",  "dow":    "^DJI",
    "rut":    "^RUT",   "vix":    "^VIX",
    # 금리
    "us10y":  "^TNX",   "us2y":   "^FVX",   "us3m":   "^IRX",
    # 달러/원자재
    "dxy":    "DX-Y.NYB",   "wti":    "CL=F",  "brent":  "BZ=F",
    "copper": "HG=F",  "gold":   "GC=F",  "silver": "SI=F",
    # 암호화폐
    "btc":    "BTC-USD",
    # 지수 ETF
    "qqq":    "QQQ", "spy":    "SPY", "iwm":    "IWM", "dia":    "DIA",
    # 섹터 ETF
    "xlk": "XLK", "xlf": "XLF", "xle": "XLE", "xlv": "XLV",
    "xly": "XLY", "xlp": "XLP", "xli": "XLI", "xlu": "XLU",
    # 리스크
    "tlt": "TLT", "gld": "GLD", "sqqq": "SQQQ", "vxx": "VXX",
    # Mag7
    "aapl": "AAPL", "msft": "MSFT", "googl": "GOOGL", "amzn": "AMZN",
    "meta": "META", "nvda": "NVDA", "tsla": "TSLA",
}

def fetch_one(sym, period="1mo"):
    try:
        ticker = yf.Ticker(sym)
        hist = ticker.history(period=period, interval="1d")
        if hist.empty or "Close" not in hist:
            return None
        close = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
        week_ago = float(hist["Close"].iloc[-6]) if len(hist) > 5 else close
        month_ago = float(hist["Close"].iloc[0]) if len(hist) > 0 else close
        return {
            "value": round(close, 4),
            "prev": round(prev, 4),
            "change_pct": round((close - prev) / prev * 100, 2) if prev else 0,
            "change_5d_pct": round((close - week_ago) / week_ago * 100, 2) if week_ago else 0,
            "change_1m_pct": round((close - month_ago) / month_ago * 100, 2) if month_ago else 0,
            "high_1m": round(float(hist["High"].max()), 4),
            "low_1m": round(float(hist["Low"].min()), 4),
        }
    except Exception as e:
        print(f"  ⚠️  {sym}: {e}", file=sys.stderr)
        return None

def fetch_all_prices():
    print("📊 Yahoo Finance 시세 수집...")
    prices = {}
    for key, sym in SYMBOLS.items():
        data = fetch_one(sym)
        prices[key] = data
        if data:
            print(f"  ✓ {key:8s} = {data['value']:.2f}  ({data['change_pct']:+.2f}%)")
    return prices


# ============ 룰 기반 평가 (6축으로 확장) ============

def eval_interest(prices, fred):
    """축 1: 금리 (FRED 실질금리 활용)."""
    p10 = prices.get("us10y")
    if not p10:
        return None
    yld10 = p10["value"]
    yld3m = prices.get("us3m", {}).get("value") if prices.get("us3m") else None

    # FRED 데이터
    fed_rate = fred.get("DFF", {}).get("value") if fred.get("DFF") else None
    real_yld = fred.get("DFII10", {}).get("value") if fred.get("DFII10") else None

    inverted = yld3m and yld3m > yld10

    metrics = [
        {"k": "Fed 금리", "v": f"{fed_rate:.2f}%" if fed_rate else "N/A",
         "tone": "neu", "source": "FRED:DFF"},
        {"k": "10Y 국채", "v": f"{yld10:.2f}%",
         "tone": "neg" if yld10 >= 4.5 else ("neu" if yld10 >= 4.0 else "pos"),
         "source": "Yahoo:^TNX"},
        {"k": "실질금리", "v": f"{real_yld:.2f}%" if real_yld else "N/A",
         "tone": "neg" if real_yld and real_yld > 2.0 else ("pos" if real_yld and real_yld < 0 else "neu"),
         "source": "FRED:DFII10 (10Y TIPS)"},
    ]

    # 평가: 실질금리 기준 강화
    if real_yld and real_yld > 2.5:
        rating = "부정"
        summary = f"실질금리 {real_yld:.2f}% — 주식에 큰 부담 (2%+ 위험 영역)"
    elif inverted:
        rating = "부정"
        summary = f"3M-10Y 역전 — 12~18개월 후 침체 신호"
    elif yld10 >= 5.0:
        rating = "부정"
        summary = f"10Y {yld10:.2f}% — 채권 매력 압도, 주식 가치 평가 부담"
    elif real_yld and real_yld > 1.5:
        rating = "중립"
        summary = f"실질금리 {real_yld:.2f}% — 주식에 약간 부담"
    elif yld10 >= 4.0:
        rating = "중립"
        summary = f"10Y {yld10:.2f}%, 실질금리 {real_yld:.2f}% — 균형 상태"
    elif real_yld and real_yld < 0:
        rating = "긍정"
        summary = f"실질금리 마이너스 — 위험자산 최고 환경"
    else:
        rating = "긍정"
        summary = f"10Y {yld10:.2f}% — 주식 환경 양호"

    pdf_tip = "FRED DFII10 = 10Y TIPS = 실질금리. 2%+ 부담, 마이너스면 위험자산 폭등기."
    return {"rating": rating, "metrics": metrics, "summary": summary, "pdfTip": pdf_tip}


def eval_employment(fred):
    """축 4: 고용 (FRED 100% 자동)."""
    unrate = fred.get("UNRATE", {}).get("value") if fred.get("UNRATE") else None
    nfp = fred.get("PAYEMS", {}).get("value") if fred.get("PAYEMS") else None  # 천명 단위 변화
    wage_yoy = fred.get("CES0500000003", {}).get("value") if fred.get("CES0500000003") else None
    icsa = fred.get("ICSA", {}).get("value") if fred.get("ICSA") else None  # 천명

    if unrate is None:
        return None

    metrics = [
        {"k": "실업률", "v": f"{unrate:.1f}%",
         "tone": "pos" if 3.8 <= unrate <= 4.2 else ("neg" if unrate > 5.0 else "neu"),
         "source": "FRED:UNRATE"},
        {"k": "NFP", "v": f"{int(nfp):+d}K" if nfp is not None else "N/A",
         "tone": "pos" if nfp and 150 <= nfp <= 250 else ("neg" if nfp and nfp < 50 else "neu"),
         "source": "FRED:PAYEMS (MoM)"},
        {"k": "임금 YoY", "v": f"+{wage_yoy:.1f}%" if wage_yoy else "N/A",
         "tone": "pos" if wage_yoy and 3 <= wage_yoy <= 4 else ("neg" if wage_yoy and wage_yoy > 5 else "neu"),
         "source": "FRED:CES0500000003"},
    ]

    # 4맥락 매트릭스 (PDF p.28-29)
    if unrate >= 5.0 or (nfp and nfp < 50):
        rating = "부정"
        summary = f"실업률 {unrate}%, NFP {nfp:+.0f}K — ④ 침체 진입 신호"
    elif nfp and nfp > 300 and wage_yoy and wage_yoy > 5:
        rating = "부정"
        summary = f"NFP +{nfp:.0f}K, 임금 +{wage_yoy:.1f}% — ② 과열, Fed 인상 우려"
    elif 3.8 <= unrate <= 4.2 and nfp and 100 <= nfp <= 250 and wage_yoy and 3 <= wage_yoy <= 4:
        rating = "긍정"
        summary = f"실업률 {unrate}%, NFP {nfp:+.0f}K, 임금 +{wage_yoy:.1f}% — ③ 소프트 랜딩"
    else:
        rating = "중립"
        summary = f"실업률 {unrate}%, NFP {nfp:+.0f}K — 연착륙 중"

    # 주간 청구 추가 정보
    if icsa:
        icsa_k = icsa if isinstance(icsa, (int, float)) else 0
        if icsa_k >= 400:
            summary += f" · 주간청구 {icsa_k:.0f}K (침체 영역)"
        elif icsa_k >= 300:
            summary += f" · 주간청구 {icsa_k:.0f}K (둔화)"

    pdf_tip = "PDF p.28-29 4맥락 매트릭스. Sahm Rule: 실업률 3MA 12M 최저 대비 +0.5%p → 침체."
    return {"rating": rating, "metrics": metrics, "summary": summary, "pdfTip": pdf_tip}


def eval_consumption(fred):
    """축 5: 소비 (FRED 카드연체율 + 소매판매)."""
    delinq = fred.get("DRCCLACBS", {}).get("value") if fred.get("DRCCLACBS") else None
    retail_yoy = fred.get("RSAFS", {}).get("value") if fred.get("RSAFS") else None
    cpi_yoy = fred.get("CPIAUCSL", {}).get("value") if fred.get("CPIAUCSL") else None

    if delinq is None:
        return None

    # 소매판매 실질 = 명목 - 인플레
    real_retail = (retail_yoy - cpi_yoy) if (retail_yoy is not None and cpi_yoy is not None) else None

    metrics = [
        {"k": "카드 연체율", "v": f"{delinq:.2f}%",
         "tone": "pos" if delinq < 2.5 else ("neg" if delinq > 4.0 else "neu"),
         "source": "FRED:DRCCLACBS"},
        {"k": "소매판매 YoY", "v": f"{retail_yoy:+.1f}%" if retail_yoy is not None else "N/A",
         "tone": "pos" if retail_yoy and retail_yoy > 3 else ("neg" if retail_yoy and retail_yoy < 0 else "neu"),
         "source": "FRED:RSAFS"},
        {"k": "실질 소매 (-CPI)", "v": f"{real_retail:+.1f}%" if real_retail is not None else "N/A",
         "tone": "pos" if real_retail and real_retail > 0 else "neg",
         "source": "계산값"},
    ]

    if delinq > 4.5:
        rating = "부정"
        summary = f"연체율 {delinq:.2f}% — 침체 영역, 소비 위기"
    elif delinq > 3.5 or (real_retail is not None and real_retail < -2):
        rating = "부정"
        summary = f"연체율 {delinq:.2f}%, 실질소매 {real_retail:+.1f}% — K자 양극화 심화"
    elif delinq <= 2.5 and real_retail is not None and real_retail > 1:
        rating = "긍정"
        summary = f"연체율 {delinq:.2f}% (정상), 실질소매 {real_retail:+.1f}% — 견조"
    else:
        rating = "중립"
        summary = f"연체율 {delinq:.2f}%, 실질소매 {real_retail:+.1f}% — 혼재"

    pdf_tip = "연체율 4%+ = 침체 임박. 실질소매(명목-CPI) > 0이면 진짜 성장."
    return {"rating": rating, "metrics": metrics, "summary": summary, "pdfTip": pdf_tip}


def eval_vix(prices):
    """축 8: VIX."""
    p = prices.get("vix")
    if not p:
        return None
    vix = p["value"]
    change = p["change_pct"]

    metrics = [
        {"k": "VIX", "v": f"{vix:.2f}",
         "tone": "neg" if vix >= 25 or vix <= 10 else ("pos" if 15 <= vix <= 20 else "neu"),
         "source": "Yahoo:^VIX"},
        {"k": "1일 변화", "v": f"{change:+.1f}%",
         "tone": "neg" if change >= 15 else "neu",
         "source": "계산값"},
        {"k": "5일 변화", "v": f"{p['change_5d_pct']:+.1f}%", "tone": "neu", "source": "계산값"},
    ]

    if vix >= 40:
        rating = "부정"; summary = f"VIX {vix:.1f} — 극도 패닉 (역사적 위기, 역설적 매수 검토)"
    elif vix >= 30:
        rating = "부정"; summary = f"VIX {vix:.1f} — 공포 상태, 분할 매수 검토"
    elif vix >= 20:
        rating = "중립"; summary = f"VIX {vix:.1f} — 불안 증가, 방어 포지션"
    elif vix >= 15:
        rating = "긍정"; summary = f"VIX {vix:.1f} — 정상 범위, 시장 건강"
    elif vix >= 10:
        rating = "중립"; summary = f"VIX {vix:.1f} — 안정적이지만 평탄화 주의"
    else:
        rating = "부정"; summary = f"VIX {vix:.1f} — 과도한 자만, 충격 취약"

    if change >= 15:
        summary += f" · 하루 +{change:.0f}% 급등 (패닉셀 정점)"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "VIX <10 자만, >30 공포. 백워데이션 발생 시 즉시 방어."}


def eval_dollar(prices):
    """축 7: 달러/원자재."""
    dxy = prices.get("dxy"); wti = prices.get("wti")
    copper = prices.get("copper"); gold = prices.get("gold")
    if not (dxy and wti and copper):
        return None

    dxy_v = dxy["value"]; wti_v = wti["value"]; cp_v = copper["value"]
    gold_v = gold["value"] if gold else None

    metrics = [
        {"k": "DXY", "v": f"{dxy_v:.2f}",
         "tone": "neu" if 90 <= dxy_v <= 105 else ("neg" if dxy_v > 110 else "pos"),
         "source": "Yahoo:DX-Y.NYB"},
        {"k": "WTI", "v": f"${wti_v:.2f}",
         "tone": "neg" if wti_v >= 100 or wti_v <= 60 else ("pos" if 70 <= wti_v <= 85 else "neu"),
         "source": "Yahoo:CL=F"},
        {"k": "구리", "v": f"${cp_v:.2f}/lb",
         "tone": "pos" if cp_v >= 4.0 else ("neg" if cp_v <= 3.5 else "neu"),
         "source": "Yahoo:HG=F"},
    ]

    score = 0
    if wti_v >= 100: score -= 2
    elif wti_v >= 85: score -= 1
    elif 70 <= wti_v <= 85: score += 1
    elif wti_v < 60: score -= 1
    if cp_v >= 4.0: score += 1
    elif cp_v < 3.5: score -= 1
    if dxy_v > 105: score -= 1

    if wti_v >= 100 and cp_v >= 4.0 and dxy_v < 100:
        rating = "부정"
        summary = f"공급 충격 시나리오 — WTI ${wti_v:.0f}, 구리 ${cp_v:.2f}, DXY {dxy_v:.1f}"
    elif score >= 1:
        rating = "긍정"
        summary = f"DXY {dxy_v:.1f}, WTI ${wti_v:.0f}, 구리 ${cp_v:.2f} — 양호"
    elif score <= -2:
        rating = "부정"
        summary = f"유가 ${wti_v:.0f} — 위협 수준, 인플레/마진 압박"
    else:
        rating = "중립"
        summary = f"DXY {dxy_v:.1f}, WTI ${wti_v:.0f} — 혼재"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "유가 $100+ 지속 시 Fed 매파 회귀. 구리=닥터 코퍼."}


def eval_flow(prices):
    """축 3: 자금흐름."""
    qqq = prices.get("qqq"); iwm = prices.get("iwm")
    tlt = prices.get("tlt"); sqqq = prices.get("sqqq")
    if not (qqq and iwm):
        return None

    qqq_5d = qqq["change_5d_pct"]; iwm_5d = iwm["change_5d_pct"]
    tlt_5d = tlt["change_5d_pct"] if tlt else 0
    sqqq_5d = sqqq["change_5d_pct"] if sqqq else 0

    metrics = [
        {"k": "QQQ 5일", "v": f"{qqq_5d:+.1f}%",
         "tone": "pos" if qqq_5d > 1 else ("neg" if qqq_5d < -2 else "neu"),
         "source": "Yahoo:QQQ"},
        {"k": "IWM 5일", "v": f"{iwm_5d:+.1f}%",
         "tone": "pos" if iwm_5d > 1 else ("neg" if iwm_5d < -2 else "neu"),
         "source": "Yahoo:IWM"},
        {"k": "TLT 5일", "v": f"{tlt_5d:+.1f}%",
         "tone": "neg" if tlt_5d > 1.5 else "neu",
         "source": "Yahoo:TLT"},
    ]

    if sqqq_5d > 5:
        rating = "부정"; summary = f"패턴 4 (SQQQ +{sqqq_5d:.0f}%) — 공포 정점, 역발상 기회"
    elif tlt_5d > 1 and qqq_5d < 0:
        rating = "부정"; summary = f"패턴 3 (TLT↑ + QQQ↓) — 안전자산 도피"
    elif iwm_5d > qqq_5d and iwm_5d > 0:
        rating = "긍정"; summary = f"패턴 2 (IWM > QQQ) — 중소형 선호"
    elif qqq_5d > 1 and iwm_5d > 0:
        rating = "긍정"; summary = f"패턴 1 (QQQ↑) — 기술주 강세"
    else:
        rating = "중립"; summary = f"혼재 — QQQ {qqq_5d:+.1f}%, IWM {iwm_5d:+.1f}%"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "SQQQ 유입 급증 = 역발상 매수. 13F 분기 체크."}


# ============ 메인 ============

def main():
    now = datetime.now(timezone.utc)
    kst = now + timedelta(hours=9)
    print(f"\n{'='*60}")
    print(f"🚀 8축 대시보드 일일 데이터 갱신 v2.0")
    print(f"   UTC: {now.isoformat()}")
    print(f"   KST: {kst.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    prices = fetch_all_prices()
    fred = fred_fetch_all() if FRED_API_KEY else {}

    print("\n⚙️  룰 기반 축 평가 (6축 자동)...")
    auto_axes = {
        "interest":     eval_interest(prices, fred),
        "flow":         eval_flow(prices),
        "employment":   eval_employment(fred),
        "consumption":  eval_consumption(fred),
        "dollar":       eval_dollar(prices),
        "vix":          eval_vix(prices),
    }
    for k, v in auto_axes.items():
        if v:
            print(f"  ✓ {k:13s} [{v['rating']}] {v['summary'][:55]}")
        else:
            print(f"  ✗ {k:13s} 평가 실패")

    # 분기 데이터만 정적
    manual_axes = {
        "earnings": {
            "rating": "긍정",
            "metrics": [
                {"k": "Beat Rate", "v": "84%", "tone": "pos", "source": "FactSet (Q1)"},
                {"k": "EPS YoY", "v": "+15.1%", "tone": "pos", "source": "FactSet (Q1)"},
                {"k": "Mag7 CAPEX", "v": "$725B", "tone": "pos", "source": "분기 발표 합산"}
            ],
            "summary": "Q1 84% Beat, 6분기 연속 두자리 EPS. AI 캐펙스 +77%.",
            "pdfTip": "실적보다 가이던스. NVDA 2023 — Beat에도 가이던스 부족으로 조정.",
            "last_manual": "2026-05-24",
            "last_source": "FactSet Earnings Insight (Q1 잠정치)"
        },
        "margin": {
            "rating": "긍정",
            "metrics": [
                {"k": "순이익률", "v": "13.4%", "tone": "pos", "source": "FactSet (Q1)"},
                {"k": "기록", "v": "2009년래 최고", "tone": "pos", "source": "FactSet"},
                {"k": "IT 섹터", "v": "선두", "tone": "pos", "source": "FactSet"}
            ],
            "summary": "S&P500 Q1 순이익률 13.4% — FactSet 추적 이래 최고.",
            "pdfTip": "4대 비용 압박 중 유가만. 에너지 빼고 마진 견조.",
            "last_manual": "2026-05-22",
            "last_source": "FactSet (Q1 분기 발표)"
        }
    }

    # 신호 집계
    all_axes = {**auto_axes, **manual_axes}
    pos = sum(1 for v in all_axes.values() if v and v.get("rating") == "긍정")
    neu = sum(1 for v in all_axes.values() if v and v.get("rating") == "중립")
    neg = sum(1 for v in all_axes.values() if v and v.get("rating") == "부정")

    if pos >= 4:
        phase, strategy = "강세장", "공격적"
    elif neg >= 4:
        phase, strategy = "약세장", "방어적"
    elif pos > neg:
        phase, strategy = "강세 우위 횡보", "중립 + 약공격"
    elif neg > pos:
        phase, strategy = "약세 우위 횡보", "방어 + 일부 매수"
    else:
        phase, strategy = "변동성 장세", "신중"

    output = {
        "last_updated": now.isoformat(),
        "last_updated_kst": kst.strftime("%Y-%m-%d %H:%M KST"),
        "prices": prices,
        "fred": fred,
        "auto_axes": auto_axes,
        "manual_axes": manual_axes,
        "summary": {
            "positive": pos, "neutral": neu, "negative": neg,
            "score": pos - neg, "phase": phase, "strategy": strategy
        },
        "data_sources": {
            "yahoo_finance": "yfinance (비공식 Yahoo API) — 시세 38개",
            "fred": "FRED St. Louis Fed (공식) — 거시 9개" if FRED_API_KEY else "미사용",
            "factset": "FactSet (정적, 분기 발표 시 수동)",
        }
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"✅ data.json 생성 완료")
    print(f"   가격: {sum(1 for v in prices.values() if v)}/{len(prices)}")
    print(f"   FRED: {sum(1 for v in fred.values() if v)}/{len(fred)}")
    print(f"   자동 축 평가: {sum(1 for v in auto_axes.values() if v)}/6")
    print(f"   신호 집계: 긍정 {pos} / 중립 {neu} / 부정 {neg}")
    print(f"   시장 국면: {phase}")
    print(f"   권장 전략: {strategy}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
