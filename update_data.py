#!/usr/bin/env python3
"""
일일 시장 데이터 수집기 (8축 진단 대시보드용) v3.0

신규 기능 (v3.0):
- 금리 곡선 라이브 (FRED 10개 만기, 1M~30Y)
- Sahm Rule 자동 계산 (실업률 3M평균 vs 12M최저)
- 2s10s 스프레드 5년 시계열
- 11개 섹터 ETF 자동 분석 + 자금흐름 4패턴 매칭
- 변곡점 자동 감지 (이전 회차 대비 신호 변동)
- Mag7 종목 카드
- Stooq fallback (Yahoo 실패 시)
- 시계열 누적 (history/ 폴더)
"""
import json
import sys
import os
import urllib.request
import urllib.parse
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

# 거시 지표 (기존 9개)
FRED_SERIES = {
    "DFF":          {"label": "Fed 실효금리",    "unit": "%",   "transform": "latest"},
    "UNRATE":       {"label": "실업률",          "unit": "%",   "transform": "latest"},
    "PAYEMS":       {"label": "비농업 고용",      "unit": "K",   "transform": "diff_1m"},
    "CES0500000003":{"label": "시간당 임금",      "unit": "%",   "transform": "yoy"},
    "CPIAUCSL":     {"label": "CPI (YoY)",       "unit": "%",   "transform": "yoy"},
    "DRCCLACBS":    {"label": "카드 연체율",      "unit": "%",   "transform": "latest"},
    "DFII10":       {"label": "10Y 실질금리",     "unit": "%",   "transform": "latest"},
    "ICSA":         {"label": "주간 실업수당청구","unit": "K",   "transform": "latest_k"},
    "RSAFS":        {"label": "소매판매 (YoY)",   "unit": "%",   "transform": "yoy"},
}

# === NEW: 금리 곡선 (10개 만기) ===
YIELD_CURVE_SERIES = {
    "DGS1MO": "1M", "DGS3MO": "3M", "DGS6MO": "6M",
    "DGS1": "1Y", "DGS2": "2Y", "DGS5": "5Y", "DGS7": "7Y",
    "DGS10": "10Y", "DGS20": "20Y", "DGS30": "30Y",
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


def fred_fetch_range(series_id, start_date, end_date):
    """FRED 시계열 범위 fetch."""
    if not FRED_API_KEY:
        return []
    try:
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
               f"&observation_start={start_date}&observation_end={end_date}"
               f"&sort_order=asc")
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read())
        obs = data.get("observations", [])
        return [(o["date"], float(o["value"])) for o in obs if o["value"] != "."]
    except Exception as e:
        print(f"  ⚠️  FRED range {series_id}: {e}", file=sys.stderr)
        return []


def fred_transform(obs, transform):
    if not obs:
        return None
    try:
        latest = obs[0]
        if latest["value"] == ".":
            # 최신값이 결측이면 다음 사용 가능한 값 찾기
            for o in obs:
                if o["value"] != ".":
                    latest = o
                    break
            else:
                return None
        latest_val = float(latest["value"])
        latest_date = latest["date"]

        if transform == "latest":
            return {"value": round(latest_val, 2), "date": latest_date}
        if transform == "latest_k":
            return {"value": round(latest_val / 1000, 1), "date": latest_date, "unit_note": "천명"}
        if transform == "diff_1m":
            if len(obs) > 1:
                # 두 번째 유효값
                for o in obs[1:]:
                    if o["value"] != ".":
                        prev_val = float(o["value"])
                        diff = latest_val - prev_val
                        return {"value": round(diff, 0), "date": latest_date}
            return None
        if transform == "yoy":
            if len(obs) >= 13:
                year_ago = obs[12]
                if year_ago["value"] != ".":
                    year_ago_val = float(year_ago["value"])
                    if year_ago_val:
                        return {"value": round((latest_val - year_ago_val) / year_ago_val * 100, 2),
                                "date": latest_date, "level": round(latest_val, 2)}
            return None
    except Exception as e:
        print(f"  ⚠️  transform: {e}", file=sys.stderr)
        return None


def fred_fetch_all():
    print("\n🏛️  FRED 거시 데이터 수집...")
    results = {}
    for sid, meta in FRED_SERIES.items():
        obs = fred_fetch(sid, limit=14)
        if obs:
            val = fred_transform(obs, meta["transform"])
            if val:
                val.update({"series_id": sid, "label": meta["label"], "unit": meta["unit"]})
                results[sid] = val
                print(f"  ✓ {sid:18s} {meta['label']:15s} = {val['value']:.2f}{meta['unit']:2s} ({val['date']})")
            else:
                results[sid] = None
                print(f"  ✗ {sid:18s} 변환 실패")
        else:
            results[sid] = None
    return results


# === NEW: 금리 곡선 fetch ===
def fetch_yield_curve():
    """10개 만기 라이브 금리 + 역사 비교 데이터."""
    print("\n📉 금리 곡선 (FRED) 수집...")
    today = datetime.now(timezone.utc)
    curve = {}
    for sid, label in YIELD_CURVE_SERIES.items():
        obs = fred_fetch(sid, limit=5)
        if obs:
            val = fred_transform(obs, "latest")
            if val:
                curve[label] = val["value"]
                print(f"  ✓ {sid:7s} ({label:3s}) = {val['value']:.2f}%")
    return curve


def fetch_2s10s_history(years=5):
    """2s10s 스프레드 5년 시계열."""
    print(f"\n📊 2s10s 스프레드 시계열 ({years}년)...")
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=years * 365)).strftime("%Y-%m-%d")

    dgs2_data = fred_fetch_range("DGS2", start, end)
    dgs10_data = fred_fetch_range("DGS10", start, end)

    # 날짜 매칭
    dgs2_map = dict(dgs2_data)
    dgs10_map = dict(dgs10_data)
    common_dates = sorted(set(dgs2_map.keys()) & set(dgs10_map.keys()))

    # 월 1회 샘플링 (데이터 크기 절감)
    sampled = []
    last_month = None
    for d in common_dates:
        month = d[:7]
        if month != last_month:
            spread = round(dgs10_map[d] - dgs2_map[d], 3)
            sampled.append({"date": d, "spread": spread})
            last_month = month

    print(f"  ✓ {len(sampled)} 시점 (월 1회 샘플링)")
    return sampled


# === NEW: Sahm Rule 자동 계산 ===
def compute_sahm_rule():
    """실업률 3개월 이동평균 vs 12개월 최저점."""
    print("\n🚨 Sahm Rule 계산...")
    obs = fred_fetch("UNRATE", limit=20)
    if not obs or len(obs) < 13:
        return None
    try:
        values = []
        for o in obs:
            if o["value"] != ".":
                values.append((o["date"], float(o["value"])))
        if len(values) < 13:
            return None
        values.sort(key=lambda x: x[0])  # 오래된 순 정렬

        # 최근 3개월 평균
        recent_3 = values[-3:]
        avg_3m = sum(v[1] for v in recent_3) / 3

        # 지난 12개월 최저
        last_12 = values[-12:]
        min_12m = min(v[1] for v in last_12)

        diff = avg_3m - min_12m
        triggered = diff >= 0.5

        # 상태 분류
        if diff >= 0.5:
            status = "발동"
            severity = "neg"
        elif diff >= 0.3:
            status = "경계"
            severity = "neu"
        else:
            status = "안전"
            severity = "pos"

        result = {
            "avg_3m": round(avg_3m, 2),
            "min_12m": round(min_12m, 2),
            "diff_pp": round(diff, 3),
            "triggered": triggered,
            "status": status,
            "severity": severity,
            "last_date": values[-1][0]
        }
        print(f"  ✓ 3M평균: {avg_3m:.2f}% / 12M최저: {min_12m:.2f}% / 차이: {diff:+.2f}%p → {status}")
        return result
    except Exception as e:
        print(f"  ⚠️  Sahm 계산: {e}", file=sys.stderr)
        return None


# ============ Yahoo Finance (확장) ============
SYMBOLS = {
    # 지수
    "sp500":  "^GSPC",  "nasdaq": "^IXIC",  "dow":    "^DJI",
    "rut":    "^RUT",   "vix":    "^VIX",
    # 금리
    "us10y":  "^TNX",   "us2y":   "^FVX",   "us3m":   "^IRX",
    # 달러/원자재
    "dxy":    "DX-Y.NYB", "wti":   "CL=F",  "brent":  "BZ=F",
    "copper": "HG=F",  "gold":   "GC=F",  "silver": "SI=F",
    # 암호화폐
    "btc":    "BTC-USD",
    # 지수 ETF
    "qqq":    "QQQ", "spy":    "SPY", "iwm":    "IWM", "dia":    "DIA",
    # 11개 섹터 ETF (NEW: XLB, XLRE, XLC 추가)
    "xlk": "XLK", "xlf": "XLF", "xle": "XLE", "xlv": "XLV",
    "xly": "XLY", "xlp": "XLP", "xli": "XLI", "xlu": "XLU",
    "xlb": "XLB", "xlre": "XLRE", "xlc": "XLC",
    # 리스크
    "tlt": "TLT", "gld": "GLD", "sqqq": "SQQQ", "vxx": "VXX",
    # Mag7
    "aapl": "AAPL", "msft": "MSFT", "googl": "GOOGL", "amzn": "AMZN",
    "meta": "META", "nvda": "NVDA", "tsla": "TSLA",
}

SECTOR_NAMES = {
    "xlk": "기술", "xlf": "금융", "xle": "에너지", "xlv": "헬스케어",
    "xly": "임의소비재", "xlp": "필수소비재", "xli": "산업재", "xlu": "유틸리티",
    "xlb": "소재", "xlre": "부동산", "xlc": "통신",
}

MAG7_NAMES = {
    "aapl": "Apple", "msft": "Microsoft", "googl": "Alphabet", "amzn": "Amazon",
    "meta": "Meta", "nvda": "NVIDIA", "tsla": "Tesla"
}


def stooq_fetch(sym):
    """Stooq fallback (Yahoo 실패 시)."""
    try:
        # Stooq 심볼 매핑
        stooq_map = {
            "^GSPC": "^spx", "^IXIC": "^ndx", "^DJI": "^dji",
            "^VIX": "^vix", "^TNX": "^tnx",
            "DX-Y.NYB": "dx.f", "CL=F": "cl.f",
            "GC=F": "gc.f", "HG=F": "hg.f",
        }
        ssym = stooq_map.get(sym, sym.lower().replace("=f", ".f"))
        url = f"https://stooq.com/q/d/l/?s={ssym}&i=d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            csv = resp.read().decode().strip().splitlines()
        if len(csv) < 3:
            return None
        # CSV: Date,Open,High,Low,Close,Volume
        latest = csv[-1].split(",")
        prev = csv[-2].split(",")
        close = float(latest[4])
        prev_close = float(prev[4])
        return {
            "value": round(close, 4),
            "prev": round(prev_close, 4),
            "change_pct": round((close - prev_close) / prev_close * 100, 2) if prev_close else 0,
            "change_5d_pct": 0, "change_1m_pct": 0,
            "high_1m": close, "low_1m": close,
            "trade_date": latest[0],
            "source": "Stooq (Yahoo fallback)"
        }
    except Exception as e:
        print(f"  ⚠️  Stooq {sym}: {e}", file=sys.stderr)
        return None


def fetch_one(sym, period="1mo", allow_fallback=True):
    try:
        ticker = yf.Ticker(sym)
        hist = ticker.history(period=period, interval="1d")
        if hist.empty or "Close" not in hist:
            if allow_fallback:
                return stooq_fetch(sym)
            return None
        close = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
        week_ago = float(hist["Close"].iloc[-6]) if len(hist) > 5 else close
        month_ago = float(hist["Close"].iloc[0]) if len(hist) > 0 else close
        last_trade_date = hist.index[-1].strftime("%Y-%m-%d")
        return {
            "value": round(close, 4),
            "prev": round(prev, 4),
            "change_pct": round((close - prev) / prev * 100, 2) if prev else 0,
            "change_5d_pct": round((close - week_ago) / week_ago * 100, 2) if week_ago else 0,
            "change_1m_pct": round((close - month_ago) / month_ago * 100, 2) if month_ago else 0,
            "high_1m": round(float(hist["High"].max()), 4),
            "low_1m": round(float(hist["Low"].min()), 4),
            "trade_date": last_trade_date,
            "source": "Yahoo"
        }
    except Exception as e:
        print(f"  ⚠️  {sym} (Yahoo): {e}", file=sys.stderr)
        if allow_fallback:
            print(f"  🔄  Stooq fallback 시도...")
            return stooq_fetch(sym)
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


# === NEW: 섹터 자동 정렬 + 자금흐름 4패턴 ===
def analyze_sectors(prices):
    print("\n🏭 섹터 자동 정렬...")
    sectors = []
    for key, name in SECTOR_NAMES.items():
        p = prices.get(key)
        if p:
            sectors.append({
                "key": key.upper(), "name": name,
                "value": p["value"],
                "chg_1d": p["change_pct"],
                "chg_5d": p["change_5d_pct"],
                "chg_1m": p["change_1m_pct"],
            })
    # 5일 변화율로 정렬
    sectors_sorted = sorted(sectors, key=lambda x: x["chg_5d"], reverse=True)

    # 4패턴 자동 매칭
    qqq = prices.get("qqq", {}).get("change_5d_pct", 0) if prices.get("qqq") else 0
    iwm = prices.get("iwm", {}).get("change_5d_pct", 0) if prices.get("iwm") else 0
    tlt = prices.get("tlt", {}).get("change_5d_pct", 0) if prices.get("tlt") else 0
    sqqq = prices.get("sqqq", {}).get("change_5d_pct", 0) if prices.get("sqqq") else 0

    pattern = None
    if sqqq > 5:
        pattern = {"id": 4, "name": "공포 정점", "desc": "SQQQ 급증 → 역발상 매수 기회",
                   "rating": "neg_or_contrarian"}
    elif tlt > 1 and qqq < 0:
        pattern = {"id": 3, "name": "안전자산 도피", "desc": "TLT↑ + QQQ↓ → 위험 회피",
                   "rating": "neg"}
    elif iwm > qqq and iwm > 0:
        pattern = {"id": 2, "name": "중소형 선호", "desc": "IWM > QQQ → 위험 선호↑",
                   "rating": "pos"}
    elif qqq > 1 and iwm > 0:
        pattern = {"id": 1, "name": "기술주 강세", "desc": "QQQ↑ → 기술주 로테이션",
                   "rating": "pos"}
    else:
        pattern = {"id": 0, "name": "혼재", "desc": f"QQQ {qqq:+.1f}%, IWM {iwm:+.1f}%, TLT {tlt:+.1f}%",
                   "rating": "neu"}

    return {
        "sorted_by_5d": sectors_sorted,
        "top_3": sectors_sorted[:3],
        "bottom_3": sectors_sorted[-3:][::-1],
        "flow_pattern": pattern
    }


# === NEW: Mag7 카드 ===
def fetch_mag7(prices):
    mag7 = []
    for key, name in MAG7_NAMES.items():
        p = prices.get(key)
        if p:
            mag7.append({
                "ticker": key.upper(), "name": name,
                "value": p["value"],
                "chg_1d": p["change_pct"],
                "chg_5d": p["change_5d_pct"],
                "chg_1m": p["change_1m_pct"],
                "trade_date": p.get("trade_date", "-")
            })
    return mag7


# === 변곡점 감지 (이전 회차 비교) ===
def detect_inflection(current_axes, history_dir="history"):
    """가장 최근 history 파일과 비교해 변동된 축 감지."""
    print("\n🎯 변곡점 감지...")
    try:
        if not os.path.exists(history_dir):
            return {"changed": [], "summary": "이전 데이터 없음", "first_run": True}
        files = sorted([f for f in os.listdir(history_dir) if f.endswith(".json")])
        if not files:
            return {"changed": [], "summary": "이전 데이터 없음", "first_run": True}
        last_file = files[-1]
        with open(os.path.join(history_dir, last_file)) as f:
            prev = json.load(f)
        prev_axes = {**(prev.get("auto_axes") or {}), **(prev.get("manual_axes") or {})}

        changed = []
        for key, cur in current_axes.items():
            if not cur:
                continue
            old = prev_axes.get(key)
            if not old:
                continue
            old_rating = old.get("rating")
            new_rating = cur.get("rating")
            if old_rating and new_rating and old_rating != new_rating:
                changed.append({
                    "axis": key,
                    "from": old_rating,
                    "to": new_rating,
                    "direction": "up" if (old_rating == "부정" or (old_rating == "중립" and new_rating == "긍정"))
                                 else "down"
                })

        is_inflection = len(changed) >= 4  # PDF p.62: 절반 이상 동시 전환
        return {
            "compared_to": last_file.replace(".json", ""),
            "changed": changed,
            "count": len(changed),
            "is_inflection": is_inflection,
            "summary": f"{len(changed)}개 축 변동" + (" (변곡점!)" if is_inflection else ""),
            "first_run": False
        }
    except Exception as e:
        print(f"  ⚠️  변곡점 감지: {e}", file=sys.stderr)
        return {"changed": [], "summary": "감지 실패", "first_run": False}


# ============ 룰 기반 평가 (기존 6축) ============

def eval_interest(prices, fred):
    p10 = prices.get("us10y")
    if not p10:
        return None
    yld10 = p10["value"]
    yld3m = prices.get("us3m", {}).get("value") if prices.get("us3m") else None
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
         "source": "FRED:DFII10"},
    ]

    if real_yld and real_yld > 2.5:
        rating, summary = "부정", f"실질금리 {real_yld:.2f}% — 주식에 큰 부담"
    elif inverted:
        rating, summary = "부정", f"3M-10Y 역전 — 12~18개월 후 침체 신호"
    elif yld10 >= 5.0:
        rating, summary = "부정", f"10Y {yld10:.2f}% — 채권 매력 압도"
    elif real_yld and real_yld > 1.5:
        rating, summary = "중립", f"실질금리 {real_yld:.2f}% — 주식 약간 부담"
    elif yld10 >= 4.0:
        rating, summary = "중립", f"10Y {yld10:.2f}%, 실질금리 {real_yld:.2f}% — 균형" if real_yld else f"10Y {yld10:.2f}% — 균형"
    elif real_yld and real_yld < 0:
        rating, summary = "긍정", f"실질금리 마이너스 — 위험자산 호재"
    else:
        rating, summary = "긍정", f"10Y {yld10:.2f}% — 주식 환경 양호"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "FRED DFII10 = 10Y TIPS = 실질금리. 2%+ 부담, 마이너스면 위험자산 폭등기."}


def eval_employment(fred, sahm=None):
    unrate = fred.get("UNRATE", {}).get("value") if fred.get("UNRATE") else None
    nfp = fred.get("PAYEMS", {}).get("value") if fred.get("PAYEMS") else None
    wage_yoy = fred.get("CES0500000003", {}).get("value") if fred.get("CES0500000003") else None
    icsa = fred.get("ICSA", {}).get("value") if fred.get("ICSA") else None
    if unrate is None:
        return None

    metrics = [
        {"k": "실업률", "v": f"{unrate:.1f}%",
         "tone": "pos" if 3.8 <= unrate <= 4.2 else ("neg" if unrate > 5.0 else "neu"),
         "source": "FRED:UNRATE"},
        {"k": "NFP", "v": f"{int(nfp):+d}K" if nfp is not None else "N/A",
         "tone": "pos" if nfp and 150 <= nfp <= 250 else ("neg" if nfp and nfp < 50 else "neu"),
         "source": "FRED:PAYEMS"},
        {"k": "임금 YoY", "v": f"+{wage_yoy:.1f}%" if wage_yoy else "N/A",
         "tone": "pos" if wage_yoy and 3 <= wage_yoy <= 4 else ("neg" if wage_yoy and wage_yoy > 5 else "neu"),
         "source": "FRED:CES"},
    ]

    # Sahm Rule 우선 적용
    if sahm and sahm.get("triggered"):
        rating = "부정"
        summary = f"Sahm Rule 발동 (+{sahm['diff_pp']:.2f}%p) — 침체 시작 신호"
    elif sahm and sahm.get("diff_pp", 0) >= 0.3:
        rating = "중립"
        summary = f"Sahm 경계 (+{sahm['diff_pp']:.2f}%p), 실업률 {unrate}%"
    elif unrate >= 5.0 or (nfp and nfp < 50):
        rating = "부정"
        summary = f"실업률 {unrate}%, NFP {nfp:+.0f}K — 침체 신호"
    elif nfp and nfp > 300 and wage_yoy and wage_yoy > 5:
        rating = "부정"
        summary = f"NFP +{nfp:.0f}K, 임금 +{wage_yoy:.1f}% — 과열 (Fed 인상 우려)"
    elif 3.8 <= unrate <= 4.2 and nfp and 100 <= nfp <= 250 and wage_yoy and 3 <= wage_yoy <= 4:
        rating = "긍정"
        summary = f"실업률 {unrate}%, NFP {nfp:+.0f}K, 임금 +{wage_yoy:.1f}% — 소프트 랜딩"
    else:
        rating = "중립"
        summary = f"실업률 {unrate}%, NFP {nfp:+.0f}K — 연착륙 중"

    if icsa:
        icsa_k = icsa if isinstance(icsa, (int, float)) else 0
        if icsa_k >= 400:
            summary += f" · 주간청구 {icsa_k:.0f}K (침체 영역)"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "Sahm Rule: 실업률 3MA 12M 최저 대비 +0.5%p → 침체. PDF p.30."}


def eval_consumption(fred):
    delinq = fred.get("DRCCLACBS", {}).get("value") if fred.get("DRCCLACBS") else None
    retail_yoy = fred.get("RSAFS", {}).get("value") if fred.get("RSAFS") else None
    cpi_yoy = fred.get("CPIAUCSL", {}).get("value") if fred.get("CPIAUCSL") else None
    if delinq is None:
        return None
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
        rating, summary = "부정", f"연체율 {delinq:.2f}% — 침체 영역"
    elif delinq > 3.5 or (real_retail is not None and real_retail < -2):
        rating, summary = "부정", f"연체율 {delinq:.2f}%, 실질소매 {real_retail:+.1f}% — K자 양극화"
    elif delinq <= 2.5 and real_retail is not None and real_retail > 1:
        rating, summary = "긍정", f"연체율 {delinq:.2f}% (정상), 실질소매 {real_retail:+.1f}% — 견조"
    else:
        rating, summary = "중립", f"연체율 {delinq:.2f}%, 실질소매 {real_retail:+.1f}% — 혼재"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "연체율 4%+ = 침체 임박. 실질소매(명목-CPI) > 0이면 진짜 성장."}


def eval_vix(prices):
    p = prices.get("vix")
    if not p:
        return None
    vix = p["value"]
    change = p["change_pct"]

    metrics = [
        {"k": "VIX", "v": f"{vix:.2f}",
         "tone": "neg" if vix >= 25 or vix <= 10 else ("pos" if 15 <= vix <= 20 else "neu"),
         "source": "Yahoo:^VIX"},
        {"k": "1일 변화", "v": f"{change:+.1f}%", "tone": "neg" if change >= 15 else "neu", "source": "계산값"},
        {"k": "5일 변화", "v": f"{p['change_5d_pct']:+.1f}%", "tone": "neu", "source": "계산값"},
    ]

    if vix >= 40:
        rating, summary = "부정", f"VIX {vix:.1f} — 극도 패닉 (역설적 매수 검토)"
    elif vix >= 30:
        rating, summary = "부정", f"VIX {vix:.1f} — 공포 상태"
    elif vix >= 20:
        rating, summary = "중립", f"VIX {vix:.1f} — 불안 증가"
    elif vix >= 15:
        rating, summary = "긍정", f"VIX {vix:.1f} — 정상 범위, 시장 건강"
    elif vix >= 10:
        rating, summary = "중립", f"VIX {vix:.1f} — 안정적이지만 평탄화 주의"
    else:
        rating, summary = "부정", f"VIX {vix:.1f} — 과도한 자만, 충격 취약"

    if change >= 15:
        summary += f" · 하루 +{change:.0f}% 급등"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "VIX <10 자만, >30 공포. 백워데이션 발생 시 즉시 방어."}


def eval_dollar(prices):
    dxy = prices.get("dxy"); wti = prices.get("wti"); copper = prices.get("copper")
    if not (dxy and wti and copper):
        return None
    dxy_v = dxy["value"]; wti_v = wti["value"]; cp_v = copper["value"]

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
        rating = "부정"; summary = f"공급 충격 시나리오 — WTI ${wti_v:.0f}, 구리 ${cp_v:.2f}"
    elif score >= 1:
        rating = "긍정"; summary = f"DXY {dxy_v:.1f}, WTI ${wti_v:.0f}, 구리 ${cp_v:.2f} — 양호"
    elif score <= -2:
        rating = "부정"; summary = f"유가 ${wti_v:.0f} — 위협 수준"
    else:
        rating = "중립"; summary = f"DXY {dxy_v:.1f}, WTI ${wti_v:.0f} — 혼재"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "유가 $100+ 지속 시 Fed 매파 회귀. 구리=닥터 코퍼."}


def eval_flow(prices, sector_analysis=None):
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

    if sector_analysis and sector_analysis.get("flow_pattern"):
        p = sector_analysis["flow_pattern"]
        rating_map = {"pos": "긍정", "neg": "부정", "neu": "중립", "neg_or_contrarian": "부정"}
        rating = rating_map.get(p["rating"], "중립")
        summary = f"패턴 {p['id']} {p['name']} — {p['desc']}"
    elif sqqq_5d > 5:
        rating = "부정"; summary = f"패턴 4 (SQQQ +{sqqq_5d:.0f}%) — 공포 정점"
    elif tlt_5d > 1 and qqq_5d < 0:
        rating = "부정"; summary = f"패턴 3 (TLT↑ + QQQ↓) — 안전자산 도피"
    elif iwm_5d > qqq_5d and iwm_5d > 0:
        rating = "긍정"; summary = f"패턴 2 (IWM > QQQ) — 중소형 선호"
    elif qqq_5d > 1 and iwm_5d > 0:
        rating = "긍정"; summary = f"패턴 1 (QQQ↑) — 기술주 강세"
    else:
        rating = "중립"; summary = f"혼재 — QQQ {qqq_5d:+.1f}%, IWM {iwm_5d:+.1f}%"

    return {"rating": rating, "metrics": metrics, "summary": summary,
            "pdfTip": "SQQQ 유입 급증 = 역발상 매수. 4패턴 (PDF p.23)."}


# ============ history 저장 ============
def save_history(data, history_dir="history"):
    """data.json 일일 스냅샷을 history/YYYY-MM-DD.json으로 저장."""
    try:
        os.makedirs(history_dir, exist_ok=True)
        today_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")
        path = os.path.join(history_dir, f"{today_kst}.json")
        # 압축: summary + auto/manual ratings만 저장 (전체 prices 제외)
        snapshot = {
            "date": today_kst,
            "last_updated": data["last_updated"],
            "summary": data["summary"],
            "auto_axes": {k: {"rating": v["rating"], "summary": v.get("summary")}
                          for k, v in (data.get("auto_axes") or {}).items() if v},
            "manual_axes": {k: {"rating": v["rating"]} for k, v in (data.get("manual_axes") or {}).items() if v},
            "sahm": data.get("sahm"),
            "sectors_top_3": [s["key"] for s in (data.get("sectors") or {}).get("top_3", [])],
            "yield_10y": (data.get("prices", {}).get("us10y") or {}).get("value"),
            "vix": (data.get("prices", {}).get("vix") or {}).get("value"),
            "spx": (data.get("prices", {}).get("sp500") or {}).get("value"),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        print(f"💾 history/{today_kst}.json 저장")
        # 정리: 365일 이전 파일 삭제
        cleanup_old_history(history_dir, days=365)
    except Exception as e:
        print(f"  ⚠️  history 저장: {e}", file=sys.stderr)


def cleanup_old_history(history_dir, days=365):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    for f in os.listdir(history_dir):
        if f.endswith(".json") and f.replace(".json", "") < cutoff:
            os.remove(os.path.join(history_dir, f))


def load_history_for_chart(history_dir="history", days=90):
    """history 최근 N일 데이터 → 시계열 차트용."""
    if not os.path.exists(history_dir):
        return []
    files = sorted([f for f in os.listdir(history_dir) if f.endswith(".json")])
    files = files[-days:]
    timeline = []
    for fn in files:
        try:
            with open(os.path.join(history_dir, fn)) as f:
                d = json.load(f)
            timeline.append({
                "date": d["date"],
                "score": d["summary"]["score"],
                "positive": d["summary"]["positive"],
                "negative": d["summary"]["negative"],
                "spx": d.get("spx"),
                "vix": d.get("vix"),
                "yield_10y": d.get("yield_10y"),
            })
        except Exception:
            continue
    return timeline


# ============ 메인 ============
def main():
    now = datetime.now(timezone.utc)
    kst = now + timedelta(hours=9)
    print(f"\n{'='*60}")
    print(f"🚀 8축 대시보드 일일 데이터 갱신 v3.0")
    print(f"   UTC: {now.isoformat()}")
    print(f"   KST: {kst.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    prices = fetch_all_prices()
    fred = fred_fetch_all() if FRED_API_KEY else {}

    # NEW: 금리 곡선, Sahm Rule, 2s10s 시계열
    yield_curve = fetch_yield_curve() if FRED_API_KEY else {}
    sahm = compute_sahm_rule() if FRED_API_KEY else None
    spread_2s10s = fetch_2s10s_history(years=5) if FRED_API_KEY else []

    # NEW: 섹터 분석
    sectors = analyze_sectors(prices)

    # NEW: Mag7 카드
    mag7 = fetch_mag7(prices)

    print("\n⚙️  룰 기반 축 평가 (6축 자동)...")
    auto_axes = {
        "interest":     eval_interest(prices, fred),
        "flow":         eval_flow(prices, sectors),
        "employment":   eval_employment(fred, sahm),
        "consumption":  eval_consumption(fred),
        "dollar":       eval_dollar(prices),
        "vix":          eval_vix(prices),
    }
    for k, v in auto_axes.items():
        if v:
            print(f"  ✓ {k:13s} [{v['rating']}] {v['summary'][:55]}")

    manual_axes = {
        "earnings": {
            "rating": "긍정",
            "metrics": [
                {"k": "Beat Rate", "v": "84%", "tone": "pos", "source": "FactSet (Q1)"},
                {"k": "EPS YoY", "v": "+15.1%", "tone": "pos", "source": "FactSet"},
                {"k": "Mag7 CAPEX", "v": "$725B", "tone": "pos", "source": "분기 합산"}
            ],
            "summary": "Q1 84% Beat, 6분기 연속 두자리 EPS.",
            "pdfTip": "실적보다 가이던스. NVDA 2023 사례.",
            "last_manual": "2026-05-24"
        },
        "margin": {
            "rating": "긍정",
            "metrics": [
                {"k": "순이익률", "v": "13.4%", "tone": "pos", "source": "FactSet (Q1)"},
                {"k": "기록", "v": "2009년래 최고", "tone": "pos", "source": "FactSet"},
                {"k": "IT 섹터", "v": "선두", "tone": "pos", "source": "FactSet"}
            ],
            "summary": "S&P500 Q1 순이익률 13.4% — FactSet 추적 이래 최고.",
            "pdfTip": "4대 비용 압박 중 유가만. 마진 견조.",
            "last_manual": "2026-05-22"
        }
    }

    # 신호 집계
    all_axes = {**auto_axes, **manual_axes}
    pos = sum(1 for v in all_axes.values() if v and v.get("rating") == "긍정")
    neu = sum(1 for v in all_axes.values() if v and v.get("rating") == "중립")
    neg = sum(1 for v in all_axes.values() if v and v.get("rating") == "부정")

    if pos >= 4: phase, strategy = "강세장", "공격적"
    elif neg >= 4: phase, strategy = "약세장", "방어적"
    elif pos > neg: phase, strategy = "강세 우위 횡보", "중립 + 약공격"
    elif neg > pos: phase, strategy = "약세 우위 횡보", "방어 + 일부 매수"
    else: phase, strategy = "변동성 장세", "신중"

    # 변곡점 감지
    inflection = detect_inflection(all_axes)

    # 시계열 차트용 히스토리
    history_timeline = load_history_for_chart(days=90)

    output = {
        "last_updated": now.isoformat(),
        "last_updated_kst": kst.strftime("%Y-%m-%d %H:%M KST"),
        "version": "v3.0",
        "prices": prices,
        "fred": fred,
        "yield_curve": yield_curve,
        "spread_2s10s_history": spread_2s10s,
        "sahm": sahm,
        "sectors": sectors,
        "mag7": mag7,
        "auto_axes": auto_axes,
        "manual_axes": manual_axes,
        "inflection": inflection,
        "history_timeline": history_timeline,
        "summary": {
            "positive": pos, "neutral": neu, "negative": neg,
            "score": pos - neg, "phase": phase, "strategy": strategy
        },
        "data_sources": {
            "yahoo": "yfinance + Stooq fallback — 시세 38개",
            "fred": "FRED St. Louis Fed — 거시 9개 + 금리곡선 10개 + Sahm Rule",
            "history": "매일 압축 스냅샷 history/YYYY-MM-DD.json",
        }
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # NEW: 일일 스냅샷 저장
    save_history(output)

    print(f"\n{'='*60}")
    print(f"✅ data.json 생성 완료 (v3.0)")
    print(f"   가격: {sum(1 for v in prices.values() if v)}/{len(prices)}")
    print(f"   FRED: {sum(1 for v in fred.values() if v)}/{len(fred)}")
    print(f"   금리 곡선: {len(yield_curve)}개 만기")
    print(f"   2s10s 시계열: {len(spread_2s10s)} 시점")
    print(f"   섹터: {len(sectors['sorted_by_5d'])}개 정렬 / 자금흐름 패턴: {sectors['flow_pattern']['name']}")
    print(f"   Mag7: {len(mag7)}/7")
    print(f"   Sahm Rule: {sahm['status'] if sahm else 'N/A'}")
    print(f"   변곡점: {inflection['summary']}")
    print(f"   신호: 긍정 {pos} / 중립 {neu} / 부정 {neg} → {phase}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
