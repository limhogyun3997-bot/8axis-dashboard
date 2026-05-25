#!/usr/bin/env python3
"""
일일 시장 데이터 수집기 (8축 진단 대시보드용).

GitHub Actions에서 매일 cron으로 실행되어:
- Yahoo Finance에서 주요 시세 가져오기
- 룰 기반으로 4개 축 (금리, 자금흐름, 달러/원자재, VIX) 평가
- 분기성 4개 축 (실적, 고용, 소비, 마진)은 수동 데이터 유지
- data.json 출력
"""
import json
import sys
import os
from datetime import datetime, timezone, timedelta

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "yfinance"])
    import yfinance as yf

# === Yahoo Finance Symbols ===
SYMBOLS = {
    # 지수
    "sp500":  "^GSPC",
    "nasdaq": "^IXIC",
    "dow":    "^DJI",
    "rut":    "^RUT",   # Russell 2000
    "vix":    "^VIX",

    # 금리
    "us10y":  "^TNX",   # 10-Year Treasury yield (단위: %)
    "us2y":   "^FVX",   # 5Y - 2Y 대용 (Yahoo는 2Y 없음, 5Y로 대신)
    "us3m":   "^IRX",   # 13-week Treasury

    # 달러 / 원자재
    "dxy":    "DX-Y.NYB",
    "wti":    "CL=F",
    "brent":  "BZ=F",
    "copper": "HG=F",
    "gold":   "GC=F",
    "silver": "SI=F",

    # 암호화폐
    "btc":    "BTC-USD",

    # 지수 ETF
    "qqq":    "QQQ",
    "spy":    "SPY",
    "iwm":    "IWM",
    "dia":    "DIA",

    # 섹터 ETF
    "xlk":    "XLK",  # 기술
    "xlf":    "XLF",  # 금융
    "xle":    "XLE",  # 에너지
    "xlv":    "XLV",  # 헬스케어
    "xly":    "XLY",  # 임의소비재
    "xlp":    "XLP",  # 필수소비재
    "xli":    "XLI",  # 산업재
    "xlu":    "XLU",  # 유틸리티

    # 리스크
    "tlt":    "TLT",   # 장기국채
    "gld":    "GLD",   # 금
    "sqqq":   "SQQQ",  # 나스닥 인버스
    "vxx":    "VXX",   # VIX

    # Mag7
    "aapl":   "AAPL",
    "msft":   "MSFT",
    "googl":  "GOOGL",
    "amzn":   "AMZN",
    "meta":   "META",
    "nvda":   "NVDA",
    "tsla":   "TSLA",
}


def fetch_one(sym, period="1mo"):
    """단일 심볼 가져오기. 5d, 1m 추세 계산."""
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


def fetch_all():
    """모든 심볼 일괄 수집."""
    print("📊 시장 데이터 수집 시작...")
    prices = {}
    for key, sym in SYMBOLS.items():
        data = fetch_one(sym)
        if data:
            prices[key] = data
            print(f"  ✓ {key:8s} ({sym:12s}) = {data['value']:.2f}  ({data['change_pct']:+.2f}%)")
        else:
            prices[key] = None
            print(f"  ✗ {key:8s} ({sym:12s}) = FAILED")
    return prices


# ============ 룰 기반 평가 (4개 축) ============

def eval_interest(prices):
    """축 1: 금리."""
    p10 = prices.get("us10y")
    p3m = prices.get("us3m")
    if not p10:
        return None

    yld10 = p10["value"]
    yld3m = p3m["value"] if p3m else None

    # 역전 체크 (3M > 10Y이면 역전 = 침체 선행)
    inverted = yld3m and yld3m > yld10

    metrics = [
        {"k": "10Y 국채", "v": f"{yld10:.2f}%", "tone": "neg" if yld10 >= 4.5 else ("neu" if yld10 >= 4.0 else "pos")},
        {"k": "3M 국채", "v": f"{yld3m:.2f}%" if yld3m else "N/A", "tone": "neu"},
        {"k": "역전 여부", "v": "역전 ⚠️" if inverted else "정상", "tone": "neg" if inverted else "pos"},
    ]

    if inverted:
        rating = "부정"
        summary = f"3M-10Y 역전 — 12~18개월 후 침체 신호 가능"
    elif yld10 >= 5.0:
        rating = "부정"
        summary = f"10Y {yld10:.2f}% — 주식 가치 평가에 큰 부담"
    elif yld10 >= 4.5:
        rating = "중립"
        summary = f"10Y {yld10:.2f}% — 채권 매력 ↑, 주식 압박 시작"
    elif yld10 >= 4.0:
        rating = "중립"
        summary = f"10Y {yld10:.2f}% — 4% 기준선 위, 주식 약간 부담"
    elif yld10 >= 3.0:
        rating = "긍정"
        summary = f"10Y {yld10:.2f}% — 적정 수준, 주식 환경 양호"
    else:
        rating = "긍정"
        summary = f"10Y {yld10:.2f}% — 매우 낮음, 위험자산에 유리"

    pdf_tip = "실질금리(10Y - CPI)가 +2% 넘으면 주식 부담. CME FedWatch로 다음 회의 인하 확률 매주 체크."

    return {"rating": rating, "metrics": metrics, "summary": summary, "pdfTip": pdf_tip}


def eval_vix(prices):
    """축 8: VIX."""
    p = prices.get("vix")
    if not p:
        return None

    vix = p["value"]
    change = p["change_pct"]
    change_5d = p["change_5d_pct"]

    metrics = [
        {"k": "VIX", "v": f"{vix:.2f}", "tone": "neg" if vix >= 25 or vix <= 10 else ("pos" if 15 <= vix <= 20 else "neu")},
        {"k": "1일 변화", "v": f"{change:+.1f}%", "tone": "neg" if change >= 15 else ("pos" if change <= -5 else "neu")},
        {"k": "5일 변화", "v": f"{change_5d:+.1f}%", "tone": "neu"},
    ]

    if vix >= 40:
        rating = "부정"
        summary = f"VIX {vix:.1f} — 극도 패닉! 역사적 위기 수준 (역설적 매수 기회 검토)"
    elif vix >= 30:
        rating = "부정"
        summary = f"VIX {vix:.1f} — 공포 상태, 분할 매수 검토 (떨어지는 칼날 주의)"
    elif vix >= 20:
        rating = "중립"
        summary = f"VIX {vix:.1f} — 불안 증가, 방어 포지션 고려"
    elif vix >= 15:
        rating = "긍정"
        summary = f"VIX {vix:.1f} — 정상 범위, 시장 건강"
    elif vix >= 10:
        rating = "중립"
        summary = f"VIX {vix:.1f} — 안정적이지만 곡선 평탄화 주의"
    else:
        rating = "부정"
        summary = f"VIX {vix:.1f} — 과도한 자만, 충격에 취약 (일부 현금 보유)"

    if change >= 15:
        summary += f" · 하루 +{change:.0f}% 급등 (패닉셀 정점 가능)"

    pdf_tip = "VIX 너무 낮으면(<10) 자만, 너무 높으면(>30) 공포. 백워데이션 발생 시 즉시 방어 모드."

    return {"rating": rating, "metrics": metrics, "summary": summary, "pdfTip": pdf_tip}


def eval_dollar(prices):
    """축 7: 달러/원자재."""
    dxy = prices.get("dxy")
    wti = prices.get("wti")
    copper = prices.get("copper")
    gold = prices.get("gold")
    if not (dxy and wti and copper):
        return None

    dxy_v = dxy["value"]
    wti_v = wti["value"]
    cp_v = copper["value"]
    gold_v = gold["value"] if gold else None

    metrics = [
        {"k": "DXY", "v": f"{dxy_v:.2f}", "tone": "neu" if 90 <= dxy_v <= 105 else ("neg" if dxy_v > 110 else "pos")},
        {"k": "WTI", "v": f"${wti_v:.2f}", "tone": "neg" if wti_v >= 100 or wti_v <= 60 else ("pos" if 70 <= wti_v <= 85 else "neu")},
        {"k": "구리", "v": f"${cp_v:.2f}/lb", "tone": "pos" if cp_v >= 4.0 else "neg" if cp_v <= 3.5 else "neu"},
    ]

    # 시나리오 매칭 (PDF p.50)
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
        summary = f"공급 충격 시나리오 — WTI ${wti_v:.0f}, 구리 ${cp_v:.2f}, DXY {dxy_v:.1f} (지정학 리스크)"
    elif score >= 1:
        rating = "긍정"
        summary = f"DXY {dxy_v:.1f}, WTI ${wti_v:.0f}, 구리 ${cp_v:.2f} — 골디락스 영역"
    elif score <= -2:
        rating = "부정"
        summary = f"유가 ${wti_v:.0f} — 위협 수준, 인플레 압박 + 마진 압박"
    else:
        rating = "중립"
        summary = f"DXY {dxy_v:.1f}, WTI ${wti_v:.0f} — 혼재"

    pdf_tip = "유가 $100+ 지속 시 Fed 매파 회귀. 구리=닥터 코퍼(중국 50%). 금↑ = 불확실성."

    return {"rating": rating, "metrics": metrics, "summary": summary, "pdfTip": pdf_tip}


def eval_flow(prices):
    """축 3: 자금흐름 (ETF 가격 변화로 추정)."""
    qqq = prices.get("qqq")
    iwm = prices.get("iwm")
    tlt = prices.get("tlt")
    sqqq = prices.get("sqqq")
    gld = prices.get("gld")
    if not (qqq and iwm):
        return None

    qqq_5d = qqq["change_5d_pct"]
    iwm_5d = iwm["change_5d_pct"]
    tlt_5d = tlt["change_5d_pct"] if tlt else 0
    sqqq_5d = sqqq["change_5d_pct"] if sqqq else 0
    gld_5d = gld["change_5d_pct"] if gld else 0

    metrics = [
        {"k": "QQQ 5일", "v": f"{qqq_5d:+.1f}%", "tone": "pos" if qqq_5d > 1 else ("neg" if qqq_5d < -2 else "neu")},
        {"k": "IWM 5일", "v": f"{iwm_5d:+.1f}%", "tone": "pos" if iwm_5d > 1 else ("neg" if iwm_5d < -2 else "neu")},
        {"k": "TLT 5일", "v": f"{tlt_5d:+.1f}%", "tone": "neg" if tlt_5d > 1.5 else "neu"},
    ]

    # 4가지 자금흐름 패턴 (PDF p.23)
    pattern = None
    if sqqq_5d > 5:
        pattern = 4
        rating = "부정"
        summary = f"패턴 4 (SQQQ +{sqqq_5d:.0f}%) — 공포 매도 정점, 역발상 매수 기회일 수도"
    elif tlt_5d > 1 and qqq_5d < 0:
        pattern = 3
        rating = "부정"
        summary = f"패턴 3 (TLT↑ + QQQ↓) — 안전자산 도피, 위험 회피"
    elif iwm_5d > qqq_5d and iwm_5d > 0:
        pattern = 2
        rating = "긍정"
        summary = f"패턴 2 (IWM > QQQ) — 중소형 선호, 위험 선호↑"
    elif qqq_5d > 1 and iwm_5d > 0:
        pattern = 1
        rating = "긍정"
        summary = f"패턴 1 (QQQ↑) — 기술주 강세 로테이션"
    elif tlt_5d > 0.5 and qqq_5d > 0.5:
        rating = "중립"
        summary = f"위험·안전자산 동시 유입 — 강세 끝물 가능 (양극화)"
    else:
        rating = "중립"
        summary = f"혼재 — QQQ {qqq_5d:+.1f}%, IWM {iwm_5d:+.1f}%, TLT {tlt_5d:+.1f}%"

    pdf_tip = "SQQQ 유입 급증 = 역발상 매수 기회. 13F 분기 체크, 내부자 매수 신호 추적."

    return {"rating": rating, "metrics": metrics, "summary": summary, "pdfTip": pdf_tip}


# ============ 메인 ============

def main():
    print(f"\n{'='*60}")
    print(f"🚀 8축 대시보드 일일 데이터 갱신")
    print(f"   실행 시각 (UTC): {datetime.now(timezone.utc).isoformat()}")
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    print(f"   실행 시각 (KST): {kst.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    prices = fetch_all()
    print()

    print("⚙️  룰 기반 축 평가...")
    auto_axes = {
        "interest": eval_interest(prices),
        "flow": eval_flow(prices),
        "dollar": eval_dollar(prices),
        "vix": eval_vix(prices),
    }
    for k, v in auto_axes.items():
        if v:
            print(f"  ✓ {k:10s} [{v['rating']}] {v['summary'][:60]}")
        else:
            print(f"  ✗ {k:10s} 평가 실패")

    # 분기성 데이터 (수동, 마지막 분석 시점)
    manual_axes = {
        "earnings": {
            "rating": "긍정",
            "metrics": [
                {"k": "Beat Rate", "v": "84%", "tone": "pos"},
                {"k": "EPS YoY", "v": "+15.1%", "tone": "pos"},
                {"k": "Mag7 CAPEX", "v": "$725B", "tone": "pos"}
            ],
            "summary": "기업들은 여전히 돈을 잘 번다. AI 투자에 돈을 쏟아붓는데 실적까지 좋다.",
            "pdfTip": "실적보다 가이던스가 중요. NVDA 2023 — 실적 좋았으나 가이던스 부족으로 조정.",
            "last_manual": "2026-05-24"
        },
        "employment": {
            "rating": "중립",
            "metrics": [
                {"k": "4월 NFP", "v": "+115K", "tone": "neu"},
                {"k": "실업률", "v": "4.3%", "tone": "neu"},
                {"k": "임금 YoY", "v": "+3.6%", "tone": "pos"}
            ],
            "summary": "고용시장이 '연착륙' 중. 식고 있지만 망가지진 않았다.",
            "pdfTip": "4맥락 매트릭스 중 ③ 소프트 랜딩. Sahm Rule 0.3%p (경계 단계).",
            "last_manual": "2026-05-08"
        },
        "consumption": {
            "rating": "중립",
            "metrics": [
                {"k": "카드 연체율", "v": "3.3%", "tone": "neg"},
                {"k": "Walmart", "v": "+7.1%", "tone": "pos"},
                {"k": "Starbucks", "v": "+7.1%", "tone": "pos"}
            ],
            "summary": "부자 소비자는 잘 쓰는데, 저소득층은 신용카드로 버티는 'K자 양극화'.",
            "pdfTip": "경기 사이클 '둔화 초입' — 재량 ↓, 필수 유지.",
            "last_manual": "2026-05-22"
        },
        "margin": {
            "rating": "긍정",
            "metrics": [
                {"k": "순이익률", "v": "13.4%", "tone": "pos"},
                {"k": "기록", "v": "2009년래 최고", "tone": "pos"},
                {"k": "IT 섹터", "v": "선두", "tone": "pos"}
            ],
            "summary": "비용 압박을 뚫고 사상 최고 마진. '효율의 끝판왕' 모드.",
            "pdfTip": "4대 비용 압박 중 유가만 압박. 에너지 빼고 마진 견조.",
            "last_manual": "2026-05-22"
        }
    }

    # 신호 집계
    all_axes = {**auto_axes, **manual_axes}
    pos = sum(1 for v in all_axes.values() if v and v.get("rating") == "긍정")
    neu = sum(1 for v in all_axes.values() if v and v.get("rating") == "중립")
    neg = sum(1 for v in all_axes.values() if v and v.get("rating") == "부정")

    # 시장 국면 판단 (4-4-반반 규칙)
    if pos >= 4:
        phase = "강세장"
        strategy = "공격적"
    elif neg >= 4:
        phase = "약세장"
        strategy = "방어적"
    else:
        phase = "강세 우위 횡보" if pos > neg else ("약세 우위 횡보" if neg > pos else "변동성 장세")
        strategy = "중립 + 방어 가미"

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_updated_kst": kst.strftime("%Y-%m-%d %H:%M KST"),
        "prices": prices,
        "auto_axes": auto_axes,
        "manual_axes": manual_axes,
        "summary": {
            "positive": pos,
            "neutral": neu,
            "negative": neg,
            "score": pos - neg,
            "phase": phase,
            "strategy": strategy
        }
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"✅ data.json 생성 완료")
    print(f"   가격 수집: {sum(1 for v in prices.values() if v)}/{len(prices)}")
    print(f"   자동 축 평가: {sum(1 for v in auto_axes.values() if v)}/4")
    print(f"   신호 집계: 긍정 {pos} / 중립 {neu} / 부정 {neg}")
    print(f"   시장 국면: {phase}")
    print(f"   권장 전략: {strategy}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
