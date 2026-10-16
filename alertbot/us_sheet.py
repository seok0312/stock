# -*- coding: utf-8 -*-
"""미국 시장 일일 분석 → 구글시트 탭 (10-16 사용자 — KR 시트의 미국판).

매일 06:20 KST(화~토, 미국 마감 후): 대형주 유니버스(movers.US_UNIV)를 야후
chart 로 스캔해 두 표를 만든다 (미국은 상한가 제도가 없어 급등주 표는 상승률
표와 겹침 → 표 2개로 통합, 10-16 사용자 확인):
  ① 달러 거래대금 상위 10 (종가×거래량)
  ② 상승률 상위 10 (거래대금 $1B↑)
행 = [종목(한글명·티커), 섹터, 등락률, 거래대금($B), 이유]
시장 표 = S&P500·나스닥·다우·러셀2000·VIX·ES/NQ 선물 × 지수·등락률
         + 거래대금은 SPY/QQQ 달러대금 프록시(사용자 지정).
이유 체인 = ① 한글 뉴스 풀(네이버 월드뉴스+스탁허브, 있으면 우선)
           ② 야후 종목 뉴스 API(티커 정확 매칭, 당일 세션 창) ③ 빈칸.
탭 이름 = KST 오늘 "M/DD (요일) 미국" (아침에 보는 간밤 마감). 같은 웹훅·
범용 렌더러 사용, 상태 키는 날짜+"US" 로 분리. 멱등·자동 백필 동일.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STORE = os.path.join(DATA, "us_sheet.jsonl")
STATE = os.path.join(DATA, "sheet_push_state.json")      # KR 과 공유 (키 분리)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"}
# ES/NQ 삭제, WTI·금·미10Y 추가 (10-16 사용자). ^TNX 는 수익률×10 스케일.
IDX = [("S&P500", "^GSPC"), ("나스닥", "^IXIC"), ("VIX", "^VIX"),
       ("WTI", "CL=F"), ("금", "GC=F"), ("미국10Y", "^TNX")]
UNIV_CACHE = os.path.join(DATA, "us_univ.json")
GICS_KR = {"Information Technology": "기술", "Communication Services": "커뮤니케이션",
           "Consumer Discretionary": "경기소비", "Consumer Staples": "필수소비",
           "Health Care": "헬스케어", "Financials": "금융", "Industrials": "산업재",
           "Energy": "에너지", "Materials": "소재", "Utilities": "유틸리티",
           "Real Estate": "부동산"}


def _load_universe() -> list:
    """S&P500 + 나스닥100 전체(~530) [(sym, name, sec)] — 주 1회 캐시 (10-16
    사용자: 주요종목 118개 → 전체로). 한글명·섹터는 movers.US_UNIV 수동맵 우선,
    그 외 섹터는 GICS 한글 변환. 수집 실패 시 캐시 → US_UNIV 폴백."""
    from movers import US_UNIV
    manual = {s: (k, sec) for s, k, sec in US_UNIV}
    now = datetime.now(KST)
    try:
        doc = json.load(open(UNIV_CACHE, encoding="utf-8"))
        if (now - datetime.fromisoformat(doc["built"])).days < 7:
            return [tuple(x) for x in doc["univ"]]
    except Exception:
        doc = None
    univ: dict = {}
    try:
        import csv as _csv
        import io
        r = requests.get("https://raw.githubusercontent.com/datasets/"
                         "s-and-p-500-companies/main/data/constituents.csv",
                         headers=UA, timeout=20)
        for row in _csv.DictReader(io.StringIO(r.text)):
            sym = (row.get("Symbol") or "").strip().replace(".", "-")
            if sym:
                univ[sym] = [sym, (row.get("Security") or sym).strip()[:20],
                             GICS_KR.get((row.get("GICS Sector") or "").strip(), "")]
        h2 = dict(UA, Origin="https://www.nasdaq.com",
                  Referer="https://www.nasdaq.com/", Accept="application/json")
        j = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                         headers=h2, timeout=20).json()
        d = j.get("data") or {}
        rows = (d.get("data") or {}).get("rows") or d.get("rows") or []
        for x in rows:
            sym = (x.get("symbol") or "").strip().replace(".", "-")
            if sym and sym not in univ:
                nm = (x.get("companyName") or sym).replace(" Common Stock", "")
                univ[sym] = [sym, nm.strip()[:20], ""]
        for sym, (kr, sec) in manual.items():
            univ[sym] = [sym, kr, sec]
        if len(univ) >= 400:
            os.makedirs(DATA, exist_ok=True)
            json.dump({"built": now.isoformat(timespec="seconds"),
                       "univ": list(univ.values())},
                      open(UNIV_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
            return [tuple(x) for x in univ.values()]
    except Exception as e:
        print(f"[us_sheet] 유니버스 갱신 실패({type(e).__name__}) — 캐시/수동맵 폴백")
    if doc:
        return [tuple(x) for x in doc["univ"]]
    return US_UNIV


def _chart(sym: str):
    """야후 일봉 — (전일比%, 종가, 달러대금 $B, 마지막 봉 날짜) 또는 None."""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                         params={"range": "7d", "interval": "1d"}, headers=UA,
                         timeout=12)
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        pts = [(t, c, v) for t, c, v in zip(res.get("timestamp") or [],
                                            q.get("close") or [], q.get("volume") or [])
               if c]
        if len(pts) < 2:
            return None
        pct = (pts[-1][1] / pts[-2][1] - 1) * 100
        dvol = (pts[-1][1] * (pts[-1][2] or 0)) / 1e9
        d = datetime.fromtimestamp(pts[-1][0], tz=timezone(timedelta(hours=-5)))
        return pct, pts[-1][1], dvol, d.strftime("%m/%d")
    except Exception:
        return None


def _yahoo_news(sym: str, hours: int = 22) -> str:
    """야후 종목 뉴스 — 최근 세션 창 안의 첫 기사 제목(영어). 없으면 ''."""
    try:
        j = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                         params={"q": sym, "newsCount": 6, "quotesCount": 0},
                         headers=UA, timeout=12).json()
        cut = datetime.now(timezone.utc).timestamp() - hours * 3600
        for n in j.get("news") or []:
            if (n.get("providerPublishTime") or 0) >= cut and n.get("title"):
                return n["title"][:90], n.get("link") or ""
    except Exception:
        pass
    return "", ""


def _reason(sym: str, kr: str, now) -> str:
    try:
        import news
        # 2글자 티커(IP 등)는 풀 매칭 오염(IPO챗 등) — 한글명 키워드로만 (10-16)
        kw = (kr, sym) if len(sym) >= 3 else (kr,)
        tk = (sym,) if len(sym) >= 3 else ()
        hits = news._pool_match(keywords=kw, tickers=tk, limit=3,
                                start=now - timedelta(hours=22), end=now)
        for h in hits:
            if not any(w in h["title"] for w in ("시황", "마감", "요약", "다우",
                                                 "나스닥", "종목 알림", "3대 지수")):
                return h["title"][:90], h.get("url") or ""
    except Exception:
        pass
    return _yahoo_news(sym)


def collect(now=None) -> dict | None:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    univ = _load_universe()
    print(f"[us_sheet] 유니버스 {len(univ)}종목 스캔")
    rows, stale = [], 0
    for sym, kr, sec in univ:
        got = _chart(sym)
        time.sleep(0.08)
        if not got:
            continue
        pct, px, dvol, last_d = got
        rows.append({"sym": sym, "name": kr, "sec": sec, "chg": round(pct, 2),
                     "dvol": round(dvol, 1), "last": last_d})
    if len(rows) < 100:
        print(f"[us_sheet] 수신 {len(rows)} — 휴장/장애 추정, 생략")
        return None
    sess = max(r["last"] for r in rows)
    rows = [r for r in rows if r["last"] == sess]        # 묵은 종목 제외
    top = sorted(rows, key=lambda r: -r["dvol"])[:10]
    # 상승률 top10 — $1B 필터 표는 제거(10-16 사용자 상담): 유니버스 자체가
    # 유동성 필터라 두 표가 60~70% 중복, 필터는 정보만 깎음. 단일 표 확정.
    gain = sorted([r for r in rows if r["chg"] > 0],
                  key=lambda r: -r["chg"])[:10]
    for r in top + gain:
        if "issue" not in r:
            r["issue"], r["issue_url"] = _reason(r["sym"], r["name"], now)
            time.sleep(0.05)
            for o in top + gain:
                if o["sym"] == r["sym"]:
                    o["issue"] = r["issue"]
                    o["issue_url"] = r["issue_url"]
    market = []
    spy = _chart("SPY"); qqq = _chart("QQQ")
    for i, (lab, sym) in enumerate(IDX):
        g = _chart(sym)
        time.sleep(0.06)
        dv = None
        if lab == "S&P500" and spy:
            dv = round(spy[2], 1)
        elif lab == "나스닥" and qqq:
            dv = round(qqq[2], 1)
        if g and sym == "^TNX":
            # 변동은 %p(전일 대비 금리차). ^TNX 스케일은 데이터에 따라 ×10/×1
            # 혼재 → 값 크기로 자동 판별 (10-16 버그: /10 고정이 0.51 표시)
            raw = g[1]
            prev = raw / (1 + g[0] / 100)
            sc = 10 if raw > 20 else 1
            market.append([lab, round(raw / sc, 2),
                           round((raw - prev) / sc, 2), None])
        else:
            market.append([lab, round(g[1], 2) if g else None,
                           g[0] if g else None, dv])
    def pack(rs):
        return [{"name": f"{r['name']} ({r['sym']})", "sec": r["sec"],
                 "chg": r["chg"], "dvol": r["dvol"],
                 "issue": r.get("issue") or "",
                 "issue_url": r.get("issue_url") or ""} for r in rs]
    rec = {"date": today, "asof": f"{now:%m/%d %H:%M} · 미국 {sess} 세션",
           "market": market, "sections": [
               {"title": "거래대금 TOP 10", "rows": pack(top)},
               {"title": "상승률 TOP 10", "rows": pack(gain)}]}
    hist = []
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        pass
    hist = [h for h in hist if h["date"] != today] + [rec]
    os.makedirs(DATA, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        for h in hist[-60:]:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    # 존 적층 구조(10-16)에선 미국 존은 하루 1회만 — 재수집해도 재푸시하지
    # 않는다(이미 올라간 존을 중복 삽입하지 않기 위함).
    print(f"[us_sheet] {today} 수집 — 대금 {len(top)} · 상승 {len(gain)} "
          f"(세션 {sess}, 이유 {sum(1 for s in rec['sections'] for r in s['rows'] if r['issue'])}건)")
    return rec


def push_all() -> int:
    """통합 탭 푸시는 sheet_top20 이 담당 (10-16: 하루 한 탭, 미국 위·국내 아래)."""
    import sheet_top20
    return sheet_top20.push_all()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, HERE)
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")))
    if "--push-only" not in sys.argv:
        collect()
    push_all()
