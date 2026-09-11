# HANDOFF — alertbot (종가베팅 브리핑 + 수급 대시보드)

마지막 갱신: **2026-09-11 저녁**

---

## 0. 현재 상태 — 전부 자동으로 돌고 있다

- **텔레그램 브리핑**: 평일 9슬롯(+일 18:00). 채널: 종가베팅(`ALERTBOT_CHAT_ID`) /
  업비트(`TELEGRAM_CHAT_ID`) / **LOG(`LOG_CHAT_ID`) 개인 전용**(remind.py, 테마스캔).
- **대시보드**: http://165.22.108.193/closebet.html (15:45 잠정 · 18:40 확정 갱신)
- **데이터 축적** (서버 `/opt/alertbot/data/`): intraday/ · snapshots.jsonl · reactions.jsonl ·
  predictions.jsonl · sector_map.json · **attention/**(인기검색 5분, 09-09~) ·
  **closebet_picks.jsonl**(픽 전방검증, 09-09~) · factor_bt_panel(2).json ·
  sigma_cache.json(★용 σ, 일1회) · flow_close_ref.json(1530↔1630 대조)

## 1. 미결

1. **수급 통념 재설명** — 사용자가 "이따가 다시" 요청 후 아직. 요지: 통념(외인/기관
   따라사기)이 사는 유일한 구간 = 조용한 종목(-1~+3%) + 유입 1~2일차 + D+1 (+0.40%).
   과열 후보군에선 역효과(연속일수 IC -0.147), 3일차 이후는 어디서나 손실.
2. 픽 전방검증 판독 (4주 뒤 `python3 closebet_picks.py`) / 예측 적중률 판독(predictions.py).
3. 관심도(attention) 4주 쌓이면 4번째 팩터로 백테스트 재실행.
4. 테마스캔(theme_scout) LOG 채널 오탐 관찰 → 공개 브리핑 편입 여부 결정.
5. 휴장일 퍼프 가격 vs 다음날 NYSE 시초가 괴리 측정(구현 전 검증 항목).

## 2. 확정된 사실 (재조사 불필요)

- **시황/주요종목 표시 체계(09-11 확정)**: 본장 시세+전일比가 본문, 괄호는 15:30 앵커
  퍼프 창변동(= 마감 후 괴리 프록시 — 사용자 확인: NXT 실체결 표시는 불필요, 되돌림).
  ★=z(자산별 60일 σ, sigma_cache) 1.2/1.5/2.0 → ★/★★/★★★. 창 변동엔 sqrt(창/24h) 보정.
  화살표 🔺상승/🔽하락. 본장 소스: 오일 marketindex/energy/CLcv1 · 금 metals/GCcv1 ·
  나스닥 index/.IXIC · SOX index/.SOX · DRAM(라운드힐 ETF) stock/DRAM.K — 전부
  api.stock.naver.com. 미국주 시간외 = basic 의 overMarketPriceInfo(한국주는 NXT 체결).
- **네이버 테마 페이지 사망**(JS 개편, read_html 0개) → 주도 테마는 키움 ka90001/ka90002
  (1+10콜, 구성종목 stk_cd 는 '005930_AL' 꼴). 동명 업종(코스피/코스닥)이 상승·하락
  양쪽에 뜨면 "(코스피)/(코스닥)" 라벨.
- **뉴스 풀(news.py)**: 네이버 front-api(mainnews/flashnews/worldnews, pageSize≤60,
  링크는 n.news.naver.com/mnews/article/{oid}/{aid} 조립) + 스탁허브 /api/news
  (tab 11종, limit≤200, 해외속보 link 는 소스홈뿐 → stockhub.kr/news/{id} 상세로).
  풀 매칭 우선, 구글 RSS 폴백. 시황 랩업 기사는 클러스터링·특이종목 뉴스에서 배제.
- **미국 실적 실제치**: 나스닥 earnings-surprise API 는 발표 후 수 시간 지연 —
  발표 직후 슬롯은 뉴스 속보 파싱(EPS 우선, 없으면 매출)으로 잠정 판정, 이후 EPS 확정
  교체. 반응줄은 나스닥 + 해당 종목 퍼프(발표시각 이후). ORCL 로 전 과정 실전 검증.
- **미국 특이종목(us_movers)**: 워치 12종 퍼프 vs QQQ, |자체|≥1.5% & |격차|≥2%p.
  실증: AAPL 폴더블 아이폰(+2.76% vs 지수 -1.05%), MU 급락. 아침 슬롯에 표시.
- **종가베팅 픽 기준(09-11 개편)**: 후보 = 시총구간 등락률(10조↑3%/1~10조 5%/1조↓7%)
  & 거래대금 ≥ 시장 전체의 0.5%. 픽 = 과열 합성점수(배율+등락률+연속순매수일수) 하위 K.
  근거: 후보군 진입이 알파(기준선 +1.01%/일), 후보 내 과열 하위 우위, 연속일수 IC -0.147.
- ka10059 flu_rt = %×100 / 금액 백만원. 종목별 차익/비차익 분해 TR 없음(시장 단위만).
- api-manager.upbit.com 은 서버 IP 429 (업비트 공지 스윕은 로컬에서).

## 3. 크론 (서버 UTC = KST-9. diff 후 수정 — upbit_bot 동거)

```
알림:   21:00(0600) 22:50(0750) 23:50(0850) 00:30(0930) 05:30(1430)
        06:36(1530 마감 본편) 07:31(1630 확정갱신·조건부) 10:00(1900) 11:00(2000)
        일 09:00(1800 --force)
수집:   06:35 collect 1535 / 06:45·09:40 intraday+dashboard(+predictions --evaluate)
        일 08:00 nxt --build-map / */5 attention --collect(창 자체판정)
픽:     06:20(15:20 기록) 00:05(09:05 채점) closebet_picks
테마:   02:00·05:00(11:00·14:00) theme_scout --send → LOG 채널
```

## 4. 마감 알림 이원화 (09-11)

- **1530(15:36, 🏁)**: 마감 본편. 발송 후 순매수를 flow_close_ref.json 에 저장.
- **1630(16:31, 🔁)**: 확정 재집계 → 투자자별 순매수·비차익 **100억 이상** 변화 시에만
  재발송(거래대금은 NXT 애프터로 항상 늘어 대조 제외). cli.py 게이트.

## 5. 메시지 구성 (09-11 대개편 반영)

헤더 3줄 → 발표완료(국기 앞, 실적 EPS/매출 판정+종목반응) → 시황(Perp.)(본장+괄호퍼프,
★z단계) → 주요종목(SOX/DRAM/삼전/하이닉스) → 관련뉴스(★기준 %) → 거래대금(5일/20일)
→ 순매수(리스트형: 코스피+선물/코스닥, 비차익 구분선, ⚡z≥1) → 주도 섹터 → 주도 테마
→ 주도주(변동률/거래대금만) → [0850 NXT 프리] → [아침: 미국섹터+특이종목+한국영향
(상승/하락 예상)] → 예정(국기 앞, ⚠). ※ 텔레그램 <pre> 표는 PC 폰트 문제로 전면 폐기.

## 6. 함정 (반복해서 걸린 것들)

- Bash 히어독 `\\n` 붕괴 / **ssh 단일따옴표 안 단일따옴표·escaped 따옴표 금지** →
  파일 scp 실행이 정석. cp949 콘솔 → UTF-8 래퍼.
- `py -3.11`(로컬) / 서버 SSH `~/.ssh/do_key_home` / pip `--break-system-packages`
- 텔레그램 ReadTimeout 재시도 금지 / crontab diff 필수 / 주말은 빈 날 아님
- deploy.sh 는 최상위 `*.py *.json *.html README.md run.sh` — closebet/ 은 수동 scp
- 수동 재실행 시 슬롯시각 전이면 window_bounds 가 '어제 창'을 잡는다(퍼프% 오해 주의).
- naver `*Raw` 필드는 원 단위(콤마 필드가 백만/억 축약). dramexchange 는 풀 크롬 UA 필수.
