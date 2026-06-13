#!/usr/bin/env python3
"""
종목별 기술적 4대 축 자동 분석 (기술적 분석 2강 기준) v2.0

유니버스: S&P 500 전체(동적 수집 + 폴백) — 미국 대형주 대부분 검색 가능
yfinance 일봉(1년)을 배치 다운로드해 4대 축을 0/1/2로 자동 채점 → tech.json
  구조  : 추세(정배열/역배열 + 고점·저점 진행)        2=상승추세 1=횡보 0=하락추세
  가격  : 52주 범위 내 위치(쌀수록 진입 우호)          2=하단 1=중간 0=상단
  시간  : RSI 에너지(과매도=축적, 과매수=소진)         2=조정충분 1=보통 0=과열
  유동성: 최근 긴 아래꼬리+회복(사냥 완료 프록시 ★)    2=사냥완료 1=중립 0=위에쌓임

진입 적합도 = 4축 합(0~8). 7~8 적극 / 5~6 분할 / 3~4 관망 / 0~2 회피
※ 목록에 없는 임의 티커는 서버리스 Worker(worker/tech-worker.js)가 동일 로직으로 즉석 계산.
"""
import csv
import io
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "yfinance", "pandas"])
    import yfinance as yf
    import pandas as pd

KST = timezone(timedelta(hours=9))
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"

# S&P500 외 인기 종목/ADR/ETF/신규상장 (리테일 검색 빈도 높음) — 유니버스에 병합
EXTRA = [
    # 인기 ETF
    ("SPY", "S&P500 ETF", "ETF"), ("QQQ", "Nasdaq100 ETF", "ETF"), ("IWM", "Russell2000 ETF", "ETF"),
    ("DIA", "Dow ETF", "ETF"), ("VTI", "Total Market ETF", "ETF"), ("VOO", "S&P500 ETF", "ETF"),
    ("ARKK", "ARK Innovation", "ETF"), ("SOXX", "Semiconductor ETF", "ETF"), ("SMH", "Semiconductor ETF", "ETF"),
    ("TQQQ", "Nasdaq 3x", "ETF"), ("SOXL", "Semi 3x", "ETF"), ("GLD", "Gold ETF", "ETF"),
    ("SLV", "Silver ETF", "ETF"), ("TLT", "20Y Treasury ETF", "ETF"), ("HYG", "High Yield ETF", "ETF"),
    ("XLK", "Tech Sector", "ETF"), ("XLF", "Financial Sector", "ETF"), ("XLE", "Energy Sector", "ETF"),
    # 성장/신규상장/밈
    ("SOFI", "SoFi", "Fin"), ("RBLX", "Roblox", "Comm"), ("RIVN", "Rivian", "Cons"),
    ("LCID", "Lucid", "Cons"), ("HOOD", "Robinhood", "Fin"), ("COIN", "Coinbase", "Fin"),
    ("SNOW", "Snowflake", "Tech"), ("NET", "Cloudflare", "Tech"), ("DDOG", "Datadog", "Tech"),
    ("ZS", "Zscaler", "Tech"), ("S", "SentinelOne", "Tech"), ("PATH", "UiPath", "Tech"),
    ("U", "Unity", "Tech"), ("AFRM", "Affirm", "Fin"), ("UPST", "Upstart", "Fin"),
    ("DKNG", "DraftKings", "Cons"), ("LYFT", "Lyft", "Tech"), ("PINS", "Pinterest", "Comm"),
    ("SNAP", "Snap", "Comm"), ("SHOP", "Shopify", "Tech"), ("ARM", "Arm Holdings", "Tech"),
    ("IONQ", "IonQ", "Tech"), ("RGTI", "Rigetti", "Tech"), ("QBTS", "D-Wave", "Tech"),
    ("RKLB", "Rocket Lab", "Industrials"), ("ASTS", "AST SpaceMobile", "Comm"),
    ("ACHR", "Archer", "Industrials"), ("JOBY", "Joby", "Industrials"), ("PLUG", "Plug Power", "Energy"),
    ("CHPT", "ChargePoint", "Cons"), ("RUN", "Sunrun", "Energy"), ("GME", "GameStop", "Cons"),
    ("AMC", "AMC", "Comm"), ("BBAI", "BigBear.ai", "Tech"), ("SMR", "NuScale", "Energy"),
    ("OKLO", "Oklo", "Energy"), ("RDDT", "Reddit", "Comm"), ("ONON", "On Holding", "Cons"),
    # 주요 ADR (해외)
    ("TSM", "TSMC", "Tech"), ("ASML", "ASML", "Tech"), ("BABA", "Alibaba", "Cons"),
    ("PDD", "PDD/Temu", "Cons"), ("JD", "JD.com", "Cons"), ("NIO", "NIO", "Cons"),
    ("XPEV", "XPeng", "Cons"), ("LI", "Li Auto", "Cons"), ("BIDU", "Baidu", "Comm"),
    ("SE", "Sea Ltd", "Cons"), ("GRAB", "Grab", "Tech"), ("NVO", "Novo Nordisk", "Health"),
    ("SAP", "SAP", "Tech"), ("TM", "Toyota", "Cons"), ("SONY", "Sony", "Cons"),
    ("SHEL", "Shell", "Energy"), ("BP", "BP", "Energy"), ("HSBC", "HSBC", "Fin"),
    ("UL", "Unilever", "Staples"), ("RIO", "Rio Tinto", "Materials"), ("BHP", "BHP", "Materials"),
    ("MSTR", "MicroStrategy", "Tech"), ("HIMS", "Hims & Hers", "Health"), ("CVNA", "Carvana", "Cons"),
]

# NASDAQ-100 구성종목 (연 1회 재구성 — 하드코딩, dedup으로 S&P500과 병합)
NASDAQ100 = [
    ("AAPL", "Apple", "Tech"), ("MSFT", "Microsoft", "Tech"), ("NVDA", "NVIDIA", "Tech"),
    ("AMZN", "Amazon", "Cons"), ("AVGO", "Broadcom", "Tech"), ("META", "Meta", "Comm"),
    ("GOOGL", "Alphabet A", "Comm"), ("GOOG", "Alphabet C", "Comm"), ("TSLA", "Tesla", "Cons"),
    ("COST", "Costco", "Staples"), ("NFLX", "Netflix", "Comm"), ("TMUS", "T-Mobile", "Comm"),
    ("CSCO", "Cisco", "Tech"), ("PEP", "PepsiCo", "Staples"), ("ADBE", "Adobe", "Tech"),
    ("LIN", "Linde", "Materials"), ("AMD", "AMD", "Tech"), ("QCOM", "Qualcomm", "Tech"),
    ("TXN", "Texas Instr", "Tech"), ("INTU", "Intuit", "Tech"), ("AMGN", "Amgen", "Health"),
    ("ISRG", "Intuitive Surgical", "Health"), ("AMAT", "Applied Materials", "Tech"),
    ("BKNG", "Booking", "Cons"), ("CMCSA", "Comcast", "Comm"), ("HON", "Honeywell", "Industrials"),
    ("VRTX", "Vertex", "Health"), ("ADP", "ADP", "Industrials"), ("PANW", "Palo Alto", "Tech"),
    ("GILD", "Gilead", "Health"), ("ADI", "Analog Devices", "Tech"), ("MU", "Micron", "Tech"),
    ("REGN", "Regeneron", "Health"), ("LRCX", "Lam Research", "Tech"), ("MELI", "MercadoLibre", "Cons"),
    ("PYPL", "PayPal", "Fin"), ("SBUX", "Starbucks", "Cons"), ("KLAC", "KLA", "Tech"),
    ("SNPS", "Synopsys", "Tech"), ("CDNS", "Cadence", "Tech"), ("CRWD", "CrowdStrike", "Tech"),
    ("MAR", "Marriott", "Cons"), ("CEG", "Constellation Energy", "Energy"), ("ORLY", "O'Reilly", "Cons"),
    ("CSX", "CSX", "Industrials"), ("ASML", "ASML", "Tech"), ("ABNB", "Airbnb", "Cons"),
    ("FTNT", "Fortinet", "Tech"), ("ADSK", "Autodesk", "Tech"), ("PCAR", "PACCAR", "Industrials"),
    ("ROP", "Roper", "Tech"), ("MNST", "Monster", "Staples"), ("NXPI", "NXP Semi", "Tech"),
    ("WDAY", "Workday", "Tech"), ("AEP", "American Electric", "Utilities"), ("CPRT", "Copart", "Industrials"),
    ("PAYX", "Paychex", "Industrials"), ("TTD", "Trade Desk", "Tech"), ("ROST", "Ross Stores", "Cons"),
    ("KDP", "Keurig Dr Pepper", "Staples"), ("CHTR", "Charter", "Comm"), ("FAST", "Fastenal", "Industrials"),
    ("DDOG", "Datadog", "Tech"), ("EA", "Electronic Arts", "Comm"), ("VRSK", "Verisk", "Industrials"),
    ("EXC", "Exelon", "Utilities"), ("CTAS", "Cintas", "Industrials"), ("GEHC", "GE HealthCare", "Health"),
    ("KHC", "Kraft Heinz", "Staples"), ("CCEP", "Coca-Cola Europacific", "Staples"), ("LULU", "Lululemon", "Cons"),
    ("BKR", "Baker Hughes", "Energy"), ("XEL", "Xcel Energy", "Utilities"), ("FANG", "Diamondback", "Energy"),
    ("TEAM", "Atlassian", "Tech"), ("IDXX", "Idexx", "Health"), ("CSGP", "CoStar", "Industrials"),
    ("ON", "ON Semi", "Tech"), ("ANSS", "Ansys", "Tech"), ("DXCM", "Dexcom", "Health"),
    ("ZS", "Zscaler", "Tech"), ("TTWO", "Take-Two", "Comm"), ("WBD", "Warner Bros", "Comm"),
    ("GFS", "GlobalFoundries", "Tech"), ("MDB", "MongoDB", "Tech"), ("ARM", "Arm", "Tech"),
    ("MRVL", "Marvell", "Tech"), ("CDW", "CDW", "Tech"), ("PDD", "PDD", "Cons"),
    ("BIIB", "Biogen", "Health"), ("DASH", "DoorDash", "Cons"), ("SMCI", "Super Micro", "Tech"),
    ("MSTR", "MicroStrategy", "Tech"), ("PLTR", "Palantir", "Tech"), ("AXON", "Axon", "Industrials"),
    ("APP", "AppLovin", "Tech"), ("MCHP", "Microchip", "Tech"), ("ODFL", "Old Dominion", "Industrials"),
    ("MDLZ", "Mondelez", "Staples"), ("ABNB", "Airbnb", "Cons"), ("LIN", "Linde", "Materials"),
]

# S&P500 수집 실패 시 폴백(대표 종목)
FALLBACK = [
    ("AAPL", "Apple", "Tech"), ("MSFT", "Microsoft", "Tech"), ("GOOGL", "Alphabet", "Comm"),
    ("AMZN", "Amazon", "Cons"), ("META", "Meta", "Comm"), ("NVDA", "NVIDIA", "Tech"),
    ("TSLA", "Tesla", "Cons"), ("AVGO", "Broadcom", "Tech"), ("AMD", "AMD", "Tech"),
    ("NFLX", "Netflix", "Comm"), ("JPM", "JPMorgan", "Fin"), ("V", "Visa", "Fin"),
    ("UNH", "UnitedHealth", "Health"), ("LLY", "Eli Lilly", "Health"), ("XOM", "Exxon", "Energy"),
    ("WMT", "Walmart", "Staples"), ("COST", "Costco", "Staples"), ("HD", "Home Depot", "Cons"),
    ("KO", "Coca-Cola", "Staples"), ("CAT", "Caterpillar", "Industrials"),
]


def load_universe():
    try:
        req = urllib.request.Request(SP500_CSV, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(data)))
        uni = []
        for r in rows:
            sym = (r.get("Symbol") or "").strip()
            name = (r.get("Security") or "").strip()
            sector = (r.get("GICS Sector") or "").strip()
            if sym:
                uni.append((sym, name, sector))
        if len(uni) >= 100:
            print(f"📋 S&P500 유니버스 수집: {len(uni)}종목")
            return uni
    except Exception as e:
        print(f"⚠️ S&P500 수집 실패 ({e}) — 폴백 사용", file=sys.stderr)
    return FALLBACK


def load_smallmid():
    """S&P MidCap 400 + SmallCap 600 = 중소형주 1000개 (Wikipedia wikitext 동적 수집)"""
    pages = [("List_of_S%26P_400_companies", "MidCap"), ("List_of_S%26P_600_companies", "SmallCap")]
    out = []
    for title, tag in pages:
        try:
            url = f"https://en.wikipedia.org/wiki/{title}?action=raw"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            txt = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
            cnt = 0
            for row in txt.split("\n|-"):
                tm = re.search(r"\{\{\s*(?:Nyse|Nasdaq|NYSE|NASDAQ)Symbol\s*\|\s*([A-Z][A-Z.\-]{0,5})", row)
                if not tm:
                    continue
                tk = tm.group(1).strip()
                nm = re.search(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", row)
                name = (nm.group(1) if nm else tk).strip()[:40]
                out.append((tk, name, tag))
                cnt += 1
            print(f"📋 {tag} 수집: {cnt}종목")
        except Exception as e:
            print(f"⚠️ {title} 수집 실패 ({e})", file=sys.stderr)
    return out


def yf_symbol(sym):
    # Yahoo는 점(.)을 하이픈(-)으로 사용 (예: BRK.B → BRK-B)
    return sym.replace(".", "-")


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def axis_structure(close):
    c = close.iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]
    ma200s = close.rolling(200).mean()
    ma200 = ma200s.iloc[-1] if not pd.isna(ma200s.iloc[-1]) else None
    if ma200 is not None and not pd.isna(ma50):
        if c > ma50 > ma200:
            return 2, "상승추세(정배열)"
        if c < ma50 < ma200:
            return 0, "하락추세(역배열)"
    recent = close.iloc[-60:]
    if len(recent) >= 40:
        fh, sh = recent.iloc[:len(recent) // 2], recent.iloc[len(recent) // 2:]
        if sh.max() > fh.max() and sh.min() > fh.min():
            return 2, "상승추세(고점·저점 상승)"
        if sh.max() < fh.max() and sh.min() < fh.min():
            return 0, "하락추세(고점·저점 하락)"
    return 1, "횡보"


def axis_price(close):
    c = close.iloc[-1]
    hi, lo = float(close.max()), float(close.min())
    rng = hi - lo
    if rng <= 0:
        return 1, "범위 산출 불가", None
    pct = (c - lo) / rng * 100
    if pct < 33:
        return 2, f"범위 하단({pct:.0f}%)·싸다", round(pct)
    if pct < 66:
        return 1, f"범위 중간({pct:.0f}%)", round(pct)
    return 0, f"범위 상단({pct:.0f}%)·비싸다", round(pct)


def axis_time(rsi_val):
    if rsi_val is None or pd.isna(rsi_val):
        return 1, "RSI 산출 불가"
    if rsi_val < 35:
        return 2, f"과매도(RSI {rsi_val:.0f})·에너지 축적"
    if rsi_val <= 65:
        return 1, f"중립(RSI {rsi_val:.0f})"
    return 0, f"과매수(RSI {rsi_val:.0f})·에너지 소진"


def axis_liquidity(df):
    recent = df.iloc[-10:]
    swept = False
    for _, row in recent.iterrows():
        hi, lo, op, cl = row["High"], row["Low"], row["Open"], row["Close"]
        if any(pd.isna(x) for x in (hi, lo, op, cl)):
            continue
        rng = hi - lo
        if rng <= 0:
            continue
        lower_wick = (min(op, cl) - lo) / rng
        close_pos = (cl - lo) / rng
        if lower_wick >= 0.45 and close_pos >= 0.6:
            swept = True
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


def analyze_df(df):
    """단일 종목 OHLCV DataFrame → 축 결과 dict (없으면 None)"""
    if df is None or len(df) < 60:
        return None
    df = df.dropna(subset=["Close"])
    if len(df) < 60:
        return None
    close = df["Close"]
    r = rsi(close)
    rsi_val = float(r.iloc[-1]) if not pd.isna(r.iloc[-1]) else None
    s_struct, d_struct = axis_structure(close)
    s_price, d_price, range_pct = axis_price(close)
    s_time, d_time = axis_time(rsi_val)
    s_liq, d_liq = axis_liquidity(df)
    score = s_struct + s_price + s_time + s_liq
    return {
        "struct": s_struct, "price": s_price, "time": s_time, "liq": s_liq,
        "score": score, "verdict": verdict(score),
        "d_struct": d_struct, "d_price": d_price, "d_time": d_time, "d_liq": d_liq,
        "close": round(float(close.iloc[-1]), 2),
        "rsi": round(rsi_val, 1) if rsi_val is not None else None,
        "range_pct": range_pct,
    }


def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def main():
    universe = load_universe()       # S&P 500 (대형)
    smallmid = load_smallmid()       # S&P MidCap400 + SmallCap600 (중소형 1000)
    # 병합 (yf 심볼 기준 중복 제거) — 대형 먼저 등록되어 sector 우선
    meta = {}
    for s, name, sector in list(universe) + NASDAQ100 + smallmid + EXTRA:
        meta.setdefault(yf_symbol(s), (s, name, sector))
    symbols = list(meta.keys())
    print(f"🌐 분석 유니버스: {len(symbols)}종목 (S&P500 + 중소형1000 + NASDAQ100 + 인기주/ADR/ETF)")
    out = []
    CHUNK = 50
    for ci, chunk in enumerate(chunked(symbols, CHUNK)):
        try:
            data = yf.download(chunk, period="1y", interval="1d", group_by="ticker",
                               threads=True, auto_adjust=True, progress=False)
        except Exception as e:
            print(f"❌ chunk {ci+1} 다운로드 실패: {e}", file=sys.stderr)
            continue
        for ysym in chunk:
            try:
                # 단일/다중 종목 컬럼 구조 처리
                if len(chunk) == 1:
                    sub = data
                else:
                    if ysym not in data.columns.get_level_values(0):
                        continue
                    sub = data[ysym]
                res = analyze_df(sub)
                if not res:
                    continue
                orig, name, sector = meta[ysym]
                res.update({"ticker": orig, "name": name, "sector": sector})
                out.append(res)
            except Exception:
                continue
        print(f"  chunk {ci+1}/{(len(symbols)+CHUNK-1)//CHUNK} 완료 (누적 {len(out)})")
        time.sleep(1)

    out.sort(key=lambda x: x["score"], reverse=True)
    now = datetime.now(KST)
    result = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_updated_kst": now.strftime("%Y-%m-%d %H:%M KST"),
        "version": "tech-2.0",
        "universe": "S&P 500",
        "basis": "yfinance 일봉(1년) · 기술적 분석 2강 4대 축 · 유동성은 캔들 프록시(★)",
        "axes": {"구조": "MA정배열·추세", "가격": "52주 범위 위치", "시간": "RSI 에너지", "유동성": "꼬리 사냥 프록시"},
        "count": len(out),
        "stocks": out,
    }
    with open("tech.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n📊 tech.json 저장 완료 — {len(out)}종목")


if __name__ == "__main__":
    main()
