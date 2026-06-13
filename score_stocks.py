#!/usr/bin/env python3
"""
매수 종목 자동 점수화 (자산네제곱 2·3·6단계 기준) v1.0

yfinance 펀더멘털을 받아 워치리스트 종목을 100점 만점으로 채점 → scores.json 출력.

배점(수동 채점기와 동일 스케일):
  재무 25 · 밸류 15 · 성장 20 · 해자 15 · 변곡점 10 · 컨센서스 10 · 타이밍 5 = 100

자동 채점 가능(정량): 재무·밸류·성장·컨센서스·타이밍
정성 항목(해자·변곡점)은 ROE/모멘텀 프록시로 근사 — ★ 표시, 수동 보정 권장.
판정: 80+ 강력매수 / 65~79 매수 / 50~64 관망 / <50 회피
"""
import csv
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "yfinance"])
    import yfinance as yf

KST = timezone(timedelta(hours=9))
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"


def load_sp500():
    """S&P500 구성종목 동적 수집 (ticker, name). 실패 시 빈 리스트."""
    try:
        req = urllib.request.Request(SP500_CSV, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
        out = []
        for r in csv.DictReader(io.StringIO(data)):
            sym = (r.get("Symbol") or "").strip().replace(".", "-")  # BRK.B → BRK-B
            nm = (r.get("Security") or "").strip()
            if sym:
                out.append((sym, nm))
        print(f"📋 S&P500 {len(out)}종목 수집")
        return out
    except Exception as e:
        print(f"⚠️ S&P500 수집 실패 ({e}) — 기본 워치리스트 사용", file=sys.stderr)
        return []

# 워치리스트 (Mag7 + 대형주 + 인기 성장주/ADR ~90종목)
WATCHLIST = [
    # Mag7 + 대형 기술
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"),
    ("AMZN", "Amazon"), ("META", "Meta"), ("NVDA", "NVIDIA"), ("TSLA", "Tesla"),
    ("AVGO", "Broadcom"), ("AMD", "AMD"), ("NFLX", "Netflix"), ("CRM", "Salesforce"),
    ("ADBE", "Adobe"), ("ORCL", "Oracle"), ("PLTR", "Palantir"), ("CSCO", "Cisco"),
    ("QCOM", "Qualcomm"), ("TXN", "Texas Instr"), ("INTC", "Intel"), ("IBM", "IBM"),
    ("NOW", "ServiceNow"), ("INTU", "Intuit"), ("AMAT", "Applied Materials"),
    ("MU", "Micron"), ("LRCX", "Lam Research"), ("KLAC", "KLA"),
    # 금융
    ("JPM", "JPMorgan"), ("V", "Visa"), ("MA", "Mastercard"), ("BAC", "Bank of America"),
    ("WFC", "Wells Fargo"), ("GS", "Goldman Sachs"), ("MS", "Morgan Stanley"),
    ("AXP", "American Express"), ("BLK", "BlackRock"), ("BRK-B", "Berkshire"),
    # 헬스케어
    ("UNH", "UnitedHealth"), ("LLY", "Eli Lilly"), ("JNJ", "J&J"), ("ABBV", "AbbVie"),
    ("MRK", "Merck"), ("PFE", "Pfizer"), ("TMO", "Thermo Fisher"), ("ABT", "Abbott"),
    # 소비
    ("WMT", "Walmart"), ("COST", "Costco"), ("HD", "Home Depot"), ("KO", "Coca-Cola"),
    ("PEP", "PepsiCo"), ("MCD", "McDonald's"), ("NKE", "Nike"), ("SBUX", "Starbucks"),
    ("DIS", "Disney"), ("PG", "P&G"), ("LOW", "Lowe's"),
    # 산업/에너지/기타
    ("CAT", "Caterpillar"), ("GE", "GE Aerospace"), ("BA", "Boeing"), ("HON", "Honeywell"),
    ("XOM", "Exxon"), ("CVX", "Chevron"), ("LIN", "Linde"), ("UNP", "Union Pacific"),
    # 인기 성장주/신규상장
    ("SOFI", "SoFi"), ("RBLX", "Roblox"), ("RIVN", "Rivian"), ("HOOD", "Robinhood"),
    ("COIN", "Coinbase"), ("SNOW", "Snowflake"), ("NET", "Cloudflare"), ("DDOG", "Datadog"),
    ("CRWD", "CrowdStrike"), ("ZS", "Zscaler"), ("PANW", "Palo Alto"), ("SHOP", "Shopify"),
    ("UBER", "Uber"), ("ABNB", "Airbnb"), ("ARM", "Arm"), ("SMCI", "Super Micro"),
    ("MSTR", "MicroStrategy"), ("DKNG", "DraftKings"), ("AFRM", "Affirm"), ("RDDT", "Reddit"),
    ("HIMS", "Hims & Hers"), ("CVNA", "Carvana"), ("IONQ", "IonQ"), ("RKLB", "Rocket Lab"),
    # 주요 ADR
    ("TSM", "TSMC"), ("ASML", "ASML"), ("BABA", "Alibaba"), ("PDD", "PDD/Temu"),
    ("NIO", "NIO"), ("SE", "Sea Ltd"), ("NVO", "Novo Nordisk"), ("SAP", "SAP"),
]


def g(info, key):
    v = info.get(key)
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def score_fin(info):
    """재무 건전성 max 25"""
    s = 0
    # 순부채/EBITDA (max 7)
    td, tc, eb = g(info, "totalDebt"), g(info, "totalCash"), g(info, "ebitda")
    if td is not None and eb and eb > 0:
        r = (td - (tc or 0)) / eb
        s += 7 if r < 0 else 6 if r < 1 else 4 if r < 2 else 2 if r < 3 else 0
    else:
        s += 3
    # 이자보상배율 프록시: 부채비율 (max 8)
    dte = g(info, "debtToEquity")
    if dte is not None:
        s += 8 if dte < 30 else 6 if dte < 70 else 4 if dte < 120 else 2 if dte < 200 else 0
    else:
        s += 4
    # FCF (max 5)
    fcf = g(info, "freeCashflow")
    s += (5 if fcf > 0 else 0) if fcf is not None else 2
    # 영업레버리지 프록시: 영업이익률 수준 (max 5)
    om = g(info, "operatingMargins")
    if om is not None:
        s += 5 if om >= 0.25 else 4 if om >= 0.15 else 2 if om >= 0.05 else 0
    else:
        s += 2
    return min(s, 25)


def score_val(info):
    """밸류에이션 max 15"""
    s = 0
    pe = g(info, "trailingPE") or g(info, "forwardPE")
    if pe is not None and pe > 0:
        s += 5 if pe < 15 else 4 if pe < 20 else 3 if pe < 25 else 1 if pe < 30 else 0
    else:
        s += 2
    peg = g(info, "trailingPegRatio") or g(info, "pegRatio")
    if peg is not None and peg > 0:
        s += 5 if peg < 1 else 4 if peg < 1.5 else 2 if peg < 2 else 0
    else:
        s += 2
    # 안전마진 프록시: 애널리스트 목표가 상승여력 (max 5)
    tgt = g(info, "targetMeanPrice")
    cur = g(info, "currentPrice") or g(info, "regularMarketPrice")
    if tgt and cur and cur > 0:
        up = (tgt - cur) / cur
        s += 5 if up > 0.4 else 4 if up > 0.3 else 3 if up > 0.2 else 1 if up > 0.1 else 0
    else:
        s += 2
    return min(s, 15)


def score_grw(info):
    """성장·수명주기 max 20"""
    s = 0
    rg = g(info, "revenueGrowth")
    if rg is not None:
        s += 8 if rg >= 0.2 else 6 if rg >= 0.1 else 4 if rg >= 0.05 else 2 if rg >= 0 else 0
    else:
        s += 3
    eg = g(info, "earningsGrowth") or g(info, "earningsQuarterlyGrowth")
    if eg is not None:
        s += 7 if eg >= 0.1 else 4 if eg > 0 else 0
    else:
        s += 3
    # 수명주기 프록시: 매출성장 단계 (max 5)
    if rg is not None:
        s += 5 if rg >= 0.15 else 3 if rg >= 0 else 0
    else:
        s += 3
    return min(s, 20)


def score_moat(info):
    """해자 max 15 — ROE/마진 프록시 (정성 ★)"""
    roe = g(info, "returnOnEquity")
    if roe is not None:
        s = 13 if roe >= 0.3 else 11 if roe >= 0.2 else 9 if roe >= 0.15 else 7 if roe >= 0.1 else 4
    else:
        s = 7
    gm = g(info, "grossMargins")
    if gm is not None and gm >= 0.5:
        s += 2
    return min(s, 15)


def score_inf(info):
    """변곡점 max 10 — 모멘텀 프록시 (정성 ★)"""
    s = 5  # 중립 기준
    eq = g(info, "earningsQuarterlyGrowth")
    if eq is not None and eq > 0:
        s += 2
    cur = g(info, "currentPrice") or g(info, "regularMarketPrice")
    d50 = g(info, "fiftyDayAverage")
    if cur and d50 and cur > d50:
        s += 3
    return min(s, 10)


def score_cons(info):
    """컨센서스 갭 max 10 (6단계)"""
    s = 0
    eq = g(info, "earningsQuarterlyGrowth")
    if eq is not None:
        s += 5 if eq > 0.1 else 3 if eq > 0 else 0
    else:
        s += 2
    rm = g(info, "recommendationMean")  # 1 strong buy ~ 5 sell
    if rm is not None and rm > 0:
        s += 5 if rm <= 1.8 else 4 if rm <= 2.2 else 2 if rm <= 2.7 else 0
    else:
        s += 2
    return min(s, 10)


def score_tim(info):
    """타이밍 max 5"""
    s = 0
    cur = g(info, "currentPrice") or g(info, "regularMarketPrice")
    d200 = g(info, "twoHundredDayAverage")
    d50 = g(info, "fiftyDayAverage")
    if cur and d200 and cur > d200:
        s += 2
    if cur and d50 and cur > d50:
        s += 2
    if cur and d50 and d200 and cur > d50 > d200:  # 골든 정배열 = 촉매
        s += 1
    return min(s, 5)


def verdict(score):
    if score >= 80:
        return "강력매수"
    if score >= 65:
        return "매수"
    if score >= 50:
        return "관망"
    return "회피"


def build_universe():
    """S&P500 + 인기 비S&P500/ADR(WATCHLIST) 병합, 중복 제거."""
    meta = {}
    for t, n in load_sp500() + WATCHLIST:
        meta.setdefault(t.upper(), n)
    return list(meta.items())


def main():
    universe = build_universe()
    print(f"🌐 펀더멘털 채점 유니버스: {len(universe)}종목")
    out = []
    for ticker, name in universe:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            if not info or len(info) < 5:
                continue
            fin = score_fin(info)
            val = score_val(info)
            grw = score_grw(info)
            moat = score_moat(info)
            inf = score_inf(info)
            cons = score_cons(info)
            tim = score_tim(info)
            total = fin + val + grw + moat + inf + cons + tim
            cur = g(info, "currentPrice") or g(info, "regularMarketPrice")
            tgt = g(info, "targetMeanPrice")
            upside = round((tgt - cur) / cur * 100, 1) if (tgt and cur and cur > 0) else None
            out.append({
                "ticker": ticker, "name": name, "score": total,
                "fin": fin, "val": val, "grw": grw, "moat": moat,
                "inf": inf, "cons": cons, "tim": tim,
                "verdict": verdict(total),
                "price": round(cur, 2) if cur else None,
                "upside": upside,
                "pe": round(g(info, "trailingPE"), 1) if g(info, "trailingPE") else None,
            })
            time.sleep(0.2)
        except Exception as e:
            print(f"❌ {ticker}: {e}", file=sys.stderr)
            continue

    out.sort(key=lambda x: x["score"], reverse=True)
    now = datetime.now(KST)
    result = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_updated_kst": now.strftime("%Y-%m-%d %H:%M KST"),
        "version": "scores-1.0",
        "basis": "yfinance 펀더멘털 · 자산네제곱 2·3·6단계 채점 · 해자/변곡점은 프록시(★)",
        "scoring": {"재무": 25, "밸류": 15, "성장": 20, "해자": 15, "변곡점": 10, "컨센서스": 10, "타이밍": 5},
        "stocks": out,
    }
    with open("scores.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n📊 scores.json 저장 완료 — {len(out)}종목")


if __name__ == "__main__":
    main()
