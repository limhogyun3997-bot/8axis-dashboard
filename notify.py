#!/usr/bin/env python3
"""
능동 알림 — 시장 신호 전환 시 Discord/Slack 웹훅으로 푸시 v1.0

data.json을 읽어 다음 변화가 있으면 웹훅으로 알림:
  · 종합 액션 신호 레벨 변경 (예: 매수권장 → 방어)
  · 변곡점 발생 (inflection.is_inflection)
  · VIX 28 이상 진입 (공포 구간 — 한 번만)

웹훅 주소는 GitHub Secret ALERT_WEBHOOK 으로 주입. 없으면 조용히 스킵(워크플로우 영향 없음).
상태는 alert_state.json에 저장해 같은 알림 반복을 막음. Discord/Slack 둘 다 호환(content+text).
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

DASH_URL = "https://limhogyun3997-bot.github.io/8axis-dashboard/"
STATE_FILE = "alert_state.json"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def send(webhook, text):
    payload = json.dumps({"content": text, "text": text}).encode("utf-8")
    req = urllib.request.Request(webhook, data=payload,
                                 headers={"Content-Type": "application/json", "User-Agent": "8axis-bot"})
    try:
        urllib.request.urlopen(req, timeout=20)
        print("✅ 알림 전송 완료")
        return True
    except Exception as e:
        print(f"⚠️ 알림 전송 실패: {e}", file=sys.stderr)
        return False


def main():
    webhook = os.environ.get("ALERT_WEBHOOK", "").strip()
    data = load_json("data.json", None)
    if not data:
        print("data.json 없음 — 스킵")
        return
    state = load_json(STATE_FILE, {})

    action = data.get("action", {}) or {}
    level = action.get("level")
    signal = action.get("signal", "-")
    inflection = data.get("inflection", {}) or {}
    summary = data.get("summary", {}) or {}
    vix = ((data.get("prices", {}) or {}).get("vix") or {}).get("value")
    today = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")

    msgs = []

    # 1) 액션 신호 레벨 전환
    prev_level = state.get("last_action_level")
    if prev_level and level and level != prev_level:
        msgs.append(f"📊 종합 신호 전환: **{state.get('last_signal','?')} → {signal}**\n"
                    f"• 국면: {summary.get('phase','-')} (긍정 {summary.get('positive')}/부정 {summary.get('negative')})\n"
                    f"• 권장 현금: {action.get('cash_ratio','-')}")

    # 2) 변곡점 발생
    if inflection.get("is_inflection"):
        changed = inflection.get("changed", [])
        det = ", ".join(f"{c.get('axis')} {c.get('from')}→{c.get('to')}" for c in changed) or inflection.get("summary", "")
        msgs.append(f"⚡ 변곡점 감지: {det}")

    # 3) VIX 공포 진입 (28+, 한 번만)
    vix_alerted = state.get("vix_alerted", False)
    if vix is not None and vix >= 28 and not vix_alerted:
        msgs.append(f"😱 VIX {vix:.1f} — 공포 구간 진입. 신규매수 보류 검토.")
        vix_alerted = True
    if vix is not None and vix < 24:
        vix_alerted = False  # 정상 복귀 시 재무장

    # 4) 관심종목 알림 (ALERT_TICKERS 시크릿 — 쉼표구분) : 기술점수 매수권 진입/이탈
    ticker_state = dict(state.get("tickers", {}))
    watch = [t.strip().upper() for t in os.environ.get("ALERT_TICKERS", "").split(",") if t.strip()]
    if watch:
        tech = load_json("tech.json", {})
        tmap = {s.get("ticker", "").upper(): s for s in (tech.get("stocks") or [])}
        for t in watch:
            s = tmap.get(t) or tmap.get(t.replace("-", "."))
            if not s:
                continue
            sc = s.get("score")
            prev = ticker_state.get(t)
            if sc is None:
                continue
            if prev is not None:
                if prev < 6 <= sc:
                    msgs.append(f"📈 관심종목 **{t}** 기술점수 {sc}/8 — 매수권 진입 ({s.get('verdict','')})")
                elif prev >= 6 and sc <= 4:
                    msgs.append(f"📉 관심종목 **{t}** 기술점수 {sc}/8 — 약화 (이탈)")
            ticker_state[t] = sc

    # 상태 저장 (항상)
    new_state = {
        "last_action_level": level,
        "last_signal": signal,
        "last_date": today,
        "vix_alerted": vix_alerted,
        "tickers": ticker_state,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False, indent=2)

    if not msgs:
        print("📭 알림 조건 없음 (변화 없음)")
        return
    body = "🚨 **[8축 대시보드] 시장 알림** · " + today + "\n\n" + "\n\n".join(msgs) + f"\n\n👉 {DASH_URL}"
    print(body)
    if not webhook:
        print("ℹ️ ALERT_WEBHOOK 미설정 — 메시지 출력만 (전송 스킵)")
        return
    send(webhook, body)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"⚠️ notify 오류(무시): {e}", file=sys.stderr)
