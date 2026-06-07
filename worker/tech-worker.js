/**
 * 기술적 4축 자동 분석 — Cloudflare Worker (임의 티커 즉석 계산)
 *
 * 정적 페이지(GitHub Pages)는 Yahoo API를 직접 못 부른다(CORS 차단).
 * 이 Worker가 서버에서 Yahoo OHLCV(1년)를 받아 4축을 계산하고 CORS 허용 헤더와 함께 반환한다.
 * tech_analysis.py 와 동일한 로직(구조·가격·시간·유동성).
 *
 * 사용: GET https://<your-worker>.workers.dev/?ticker=NVDA
 * 배포: worker/README.md 참고 (무료, 5분)
 */
export default {
  async fetch(request) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Cache-Control": "public, max-age=1800",
      "Content-Type": "application/json; charset=utf-8",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    const url = new URL(request.url);
    const ticker = (url.searchParams.get("ticker") || "").trim().toUpperCase().replace(/\./g, "-");
    if (!ticker || !/^[A-Z0-9.\-]{1,12}$/.test(ticker)) {
      return json({ error: "invalid ticker" }, cors, 400);
    }

    try {
      const y = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=1y&interval=1d`;
      const res = await fetch(y, { headers: { "User-Agent": "Mozilla/5.0" } });
      if (!res.ok) return json({ error: "yahoo " + res.status, ticker }, cors, 502);
      const data = await res.json();
      const r = data?.chart?.result?.[0];
      if (!r) return json({ error: "no data", ticker }, cors, 404);

      const q = r.indicators.quote[0];
      const adj = r.indicators.adjclose?.[0]?.adjclose;
      const closeRaw = adj || q.close;
      // null 제거(휴장 등)
      const idx = [];
      for (let i = 0; i < closeRaw.length; i++) if (closeRaw[i] != null && q.high[i] != null && q.low[i] != null && q.open[i] != null) idx.push(i);
      const close = idx.map((i) => closeRaw[i]);
      const high = idx.map((i) => q.high[i]);
      const low = idx.map((i) => q.low[i]);
      const open = idx.map((i) => q.open[i]);
      const name = r.meta?.shortName || r.meta?.longName || ticker;
      if (close.length < 60) return json({ error: "insufficient history", ticker }, cors, 404);

      const out = computeAxes({ close, high, low, open });
      out.ticker = ticker;
      out.name = name;
      return json(out, cors);
    } catch (e) {
      return json({ error: String(e), ticker }, cors, 500);
    }
  },
};

function json(obj, cors, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: cors });
}

// ---- 지표 ----
function sma(arr, p) {
  if (arr.length < p) return null;
  let s = 0;
  for (let i = arr.length - p; i < arr.length; i++) s += arr[i];
  return s / p;
}
function rsi(close, p = 14) {
  if (close.length < p + 1) return null;
  let gain = 0, loss = 0;
  for (let i = close.length - p; i < close.length; i++) {
    const d = close[i] - close[i - 1];
    if (d > 0) gain += d; else loss -= d;
  }
  gain /= p; loss /= p;
  if (loss === 0) return 100;
  const rs = gain / loss;
  return 100 - 100 / (1 + rs);
}

function computeAxes({ close, high, low, open }) {
  const c = close[close.length - 1];
  const ma50 = sma(close, 50);
  const ma200 = sma(close, 200);

  // 구조
  let s_struct, d_struct;
  if (ma200 != null && ma50 != null && c > ma50 && ma50 > ma200) { s_struct = 2; d_struct = "상승추세(정배열)"; }
  else if (ma200 != null && ma50 != null && c < ma50 && ma50 < ma200) { s_struct = 0; d_struct = "하락추세(역배열)"; }
  else {
    const recent = close.slice(-60);
    const h = Math.floor(recent.length / 2);
    const fh = recent.slice(0, h), sh = recent.slice(h);
    const mx = (a) => Math.max(...a), mn = (a) => Math.min(...a);
    if (sh.length && mx(sh) > mx(fh) && mn(sh) > mn(fh)) { s_struct = 2; d_struct = "상승추세(고점·저점 상승)"; }
    else if (sh.length && mx(sh) < mx(fh) && mn(sh) < mn(fh)) { s_struct = 0; d_struct = "하락추세(고점·저점 하락)"; }
    else { s_struct = 1; d_struct = "횡보"; }
  }

  // 가격 (52주 범위)
  const hi = Math.max(...close), lo = Math.min(...close);
  let s_price, d_price, range_pct = null;
  const rng = hi - lo;
  if (rng <= 0) { s_price = 1; d_price = "범위 산출 불가"; }
  else {
    const pct = ((c - lo) / rng) * 100; range_pct = Math.round(pct);
    if (pct < 33) { s_price = 2; d_price = `범위 하단(${range_pct}%)·싸다`; }
    else if (pct < 66) { s_price = 1; d_price = `범위 중간(${range_pct}%)`; }
    else { s_price = 0; d_price = `범위 상단(${range_pct}%)·비싸다`; }
  }

  // 시간 (RSI)
  const rv = rsi(close);
  let s_time, d_time;
  if (rv == null) { s_time = 1; d_time = "RSI 산출 불가"; }
  else if (rv < 35) { s_time = 2; d_time = `과매도(RSI ${Math.round(rv)})·에너지 축적`; }
  else if (rv <= 65) { s_time = 1; d_time = `중립(RSI ${Math.round(rv)})`; }
  else { s_time = 0; d_time = `과매수(RSI ${Math.round(rv)})·에너지 소진`; }

  // 유동성 (캔들 프록시)
  let swept = false;
  const n = close.length;
  for (let i = Math.max(0, n - 10); i < n; i++) {
    const r2 = high[i] - low[i];
    if (r2 <= 0) continue;
    const lower = (Math.min(open[i], close[i]) - low[i]) / r2;
    const cpos = (close[i] - low[i]) / r2;
    if (lower >= 0.45 && cpos >= 0.6) swept = true;
  }
  const high20 = Math.max(...high.slice(-20));
  const nearHigh = c >= high20 * 0.98;
  let s_liq, d_liq;
  if (swept) { s_liq = 2; d_liq = "긴 아래꼬리+회복(사냥 완료 가능)"; }
  else if (nearHigh) { s_liq = 0; d_liq = "최근 고점 근접(위에 유동성)"; }
  else { s_liq = 1; d_liq = "중립"; }

  const score = s_struct + s_price + s_time + s_liq;
  const verdict = score >= 7 ? "적극 진입" : score >= 5 ? "분할 진입" : score >= 3 ? "신중·관망" : "회피";
  return {
    struct: s_struct, price: s_price, time: s_time, liq: s_liq, score, verdict,
    d_struct, d_price, d_time, d_liq,
    close: Math.round(c * 100) / 100,
    rsi: rv == null ? null : Math.round(rv * 10) / 10,
    range_pct,
    source: "worker",
  };
}
