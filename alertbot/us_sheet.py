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
IDX = [("S&P500", "^GSPC"), ("나스닥", "^IXIC"), ("다우", "^DJI"),
       ("러셀2000", "^RUT"), ("VIX", "^VIX"), ("ES선물", "ES=F"), ("NQ선물", "NQ=F")]
GAIN_MIN_B = 1.0          # 상승률 표 거래대금 하한 $1B (10-16 사용자 확정)


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
                return n["title"][:90]
    except Exception:
        pass
    return ""


def _reason(sym: str, kr: str, now) -> str:
    try:
        import news
        hits = news._pool_match(keywords=(kr, sym), tickers=(sym,), limit=3,
                                start=now - timedelta(hours=22), end=now)
        for h in hits:
            if not any(w in h["title"] for w in ("시황", "마감", "요약", "다우",
                                                 "나스닥", "종목 알림", "3대 지수")):
                return h["title"][:90]
    except Exception:
        pass
    return _yahoo_news(sym)


def collect(now=None) -> dict | None:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    from movers import US_UNIV
    rows, stale = [], 0
    for sym, kr, sec in US_UNIV:
        got = _chart(sym)
        time.sleep(0.08)
        if not got:
            continue
        pct, px, dvol, last_d = got
        rows.append({"sym": sym, "name": kr, "sec": sec, "chg": round(pct, 2),
                     "dvol": round(dvol, 1), "last": last_d})
    if len(rows) < 30:
        print(f"[us_sheet] 수신 {len(rows)} — 휴장/장애 추정, 생략")
        return None
    sess = max(r["last"] for r in rows)
    rows = [r for r in rows if r["last"] == sess]        # 묵은 종목 제외
    top = sorted(rows, key=lambda r: -r["dvol"])[:10]
    gain = [r for r in rows if r["dvol"] >= GAIN_MIN_B and r["chg"] > 0]
    gain = sorted(gain, key=lambda r: -r["chg"])[:10]
    for r in top + gain:
        if "issue" not in r:
            r["issue"] = _reason(r["sym"], r["name"], now)
            time.sleep(0.05)
            for o in top + gain:
                if o["sym"] == r["sym"]:
                    o["issue"] = r["issue"]
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
        market.append([lab, round(g[1], 2) if g else None,
                       g[0] if g else None, dv])
    def pack(rs):
        return [{"name": f"{r['name']} ({r['sym']})", "sec": r["sec"],
                 "chg": r["chg"], "dvol": r["dvol"],
                 "issue": r.get("issue") or ""} for r in rs]
    rec = {"date": today, "asof": f"{now:%m/%d %H:%M} · 미국 {sess} 세션",
           "market": market, "sections": [
               {"title": "달러 거래대금 상위 10", "rows": pack(top)},
               {"title": f"상승률 상위 10 (거래대금 ${GAIN_MIN_B:.0f}B↑)",
                "rows": pack(gain)}]}
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
    try:
        st = json.load(open(STATE, encoding="utf-8"))
        if st.pop(today + "US", None):
            json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    print(f"[us_sheet] {today} 수집 — 대금 {len(top)} · 상승 {len(gain)} "
          f"(세션 {sess}, 이유 {sum(1 for s in rec['sections'] for r in s['rows'] if r['issue'])}건)")
    return rec


def push_all() -> int:
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    token = os.environ.get("SHEET_WEBHOOK_TOKEN", "").strip()
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        return 0
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        state = {}
    todo = [h for h in hist if not state.get(h["date"] + "US")]
    if not url:
        print(f"[us_sheet] 웹훅 미설정 — 보류 {len(todo)}일")
        return 0
    n = 0
    for rec in sorted(todo, key=lambda h: h["date"]):
        d = datetime.strptime(rec["date"], "%Y%m%d")
        d_lab = f"{d.month}/{d.day:02d} ({'월화수목금토일'[d.weekday()]}) 미국"
        blocks = [{
            "title": "시장",
            "headers": ["", "지수", "등락률", "거래대금($B)"],
            "rows": [[m[0], m[1], (m[2] / 100 if m[2] is not None else None), m[3]]
                     for m in rec["market"]],
            "fmts": {"2": "#,##0.00", "3": "[Red]+0.0%;[Blue]-0.0%;0.0%",
                     "4": "0.0"},
            "boldCol": 1, "align": {"1": "center"}}]
        r_next = 7
        for s in rec["sections"]:
            blocks.append({
                "title": s["title"], "col": 7, "row": r_next,
                "headers": ["종목", "섹터", "등락률", "거래대금($B)", "이유"],
                "rows": [[r["name"], r["sec"], r["chg"] / 100, r["dvol"],
                          r["issue"]] for r in s["rows"]],
                "fmts": {"3": "[Red]+0.0%;[Blue]-0.0%;0.0%", "4": "0.0"},
                "align": {"1": "center", "2": "center"}})
            r_next += 2 + max(len(s["rows"]), 1) + 1
        payload = {"token": token, "date": d_lab, "asof": rec.get("asof"),
                   "widths": {"1": 80, "2": 85, "3": 80, "4": 100, "5": 30,
                              "6": 30, "7": 170, "8": 90, "9": 80, "10": 100,
                              "11": 430},
                   "blocks": blocks}
        try:
            resp = requests.post(url, json=payload, timeout=40, allow_redirects=True)
            ok = resp.status_code == 200 and "ok" in resp.text[:50].lower()
        except Exception as e:
            print(f"[us_sheet] {rec['date']} 전송 실패: {type(e).__name__}")
            ok = False
        if ok:
            state[rec["date"] + "US"] = datetime.now(KST).isoformat(timespec="seconds")
            n += 1
            print(f"[us_sheet] {rec['date']} 시트 기록 완료 ({d_lab})")
        else:
            print(f"[us_sheet] {rec['date']} 응답 비정상 — 다음 실행 재시도")
            break
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    return n


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, HERE)
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")))
    if "--push-only" not in sys.argv:
        collect()
    push_all()
