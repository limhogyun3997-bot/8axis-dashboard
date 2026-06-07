# 임의 티커 기술분석 — Cloudflare Worker 배포 (무료, 약 5분)

대시보드의 "기술적 분석 → 통합 진단" 검색은 기본적으로 **S&P 500(약 500종목)** 을
미리 계산한 `tech.json`에서 즉시 보여줍니다.

그 목록에 **없는 종목(소형주·해외·신규상장 등)** 까지 검색하려면, 이 Worker를 배포하면 됩니다.
Worker가 서버에서 Yahoo 데이터를 받아 4축을 즉석 계산해 돌려줍니다.
(정적 GitHub Pages는 Yahoo를 직접 못 부르기 때문에 이 중계가 필요합니다.)

## 방법 A — 웹 대시보드로 배포 (가장 쉬움, 터미널 불필요)

1. https://dash.cloudflare.com 가입/로그인 (무료)
2. 좌측 메뉴 **Workers & Pages → Create → Create Worker**
3. 이름 입력(예: `tech-axis`) → **Deploy**
4. **Edit code** 클릭 → 기존 코드 전체 삭제 → `worker/tech-worker.js` 내용 붙여넣기 → **Deploy**
5. 배포되면 주소가 생깁니다: `https://tech-axis.<본인계정>.workers.dev`
6. 브라우저에서 `https://tech-axis.<...>.workers.dev/?ticker=NVDA` 열어 JSON이 나오면 성공
7. 그 주소를 알려주시면 대시보드 `index.html`의 `TECH_API` 값에 넣어 연결하겠습니다.
   (또는 직접: index.html에서 `const TECH_API = ""` → `const TECH_API = "https://tech-axis.<...>.workers.dev"`)

## 방법 B — 터미널(wrangler)로 배포

```bash
npm i -g wrangler
wrangler login
cd worker
wrangler deploy tech-worker.js --name tech-axis --compatibility-date 2024-01-01
```

배포 후 출력되는 `*.workers.dev` 주소를 `TECH_API`에 넣으면 됩니다.

## 무료 한도
- Cloudflare Workers 무료 플랜: **하루 100,000 요청** (개인 사용엔 충분)
- 응답은 30분 캐시되어 같은 종목 반복 조회는 요청 수를 거의 안 씁니다.

## 동작 방식
- 입력: `GET /?ticker=심볼` (예: `?ticker=PLTR`)
- 처리: Yahoo `chart` API에서 1년 일봉 → 구조/가격/시간/유동성 4축 계산 (tech_analysis.py와 동일)
- 출력: `{ ticker, name, struct, price, time, liq, score, verdict, d_* , close, rsi, range_pct }`

연결 후에는 검색창에 어떤 티커를 넣어도:
1) S&P500 목록에 있으면 → tech.json에서 즉시
2) 없으면 → Worker가 즉석 계산
순으로 자동 처리됩니다.
