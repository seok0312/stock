# -*- coding: utf-8 -*-
"""거래대금 상위 20 일일 분석 → 구글시트 '일별 탭' 자동 기록 (10-16 사용자, v2).

매일 20:00 크론(평일): 마감 스냅샷에서 세 섹션을 만들어
  ① 거래대금 상위 20 (보통주, ETF 제외)
  ② 상한가 전 종목 (등락률 +29.5%↑, 대금순)
  ③ 거래대금 1,000억↑ 중 상승률 상위 10
행 = [종목, 시장(코스피/코스닥), 거래대금(억), 등락률(%), 이슈(뉴스 제목)]
→ data/sheet_top20.jsonl 저장(멱등) → Apps Script 웹훅으로 전송.
시트 쪽은 날짜명("10/16 (금)") 탭을 지우고 새로 만들어 기록 = 재전송 멱등.
서식은 시트 스크립트가 적용: 거래대금 #,##0 · 등락률 0.0% (값은 /100 저장).

collect 가 같은 날짜를 다시 수집하면 push 상태를 풀어 재전송한다
(15:4x 조기 수집 후 20:00 정식 수집 재기록 — 10-16 사용자 ③).
웹훅 미설정·실패분은 다음 실행 때 자동 백필.
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
STORE = os.path.join(DATA, "sheet_top20.jsonl")
STATE = os.path.join(DATA, "sheet_push_state.json")
TOP_N = 10          # 10-16 사용자: 20은 많다 — 10으로 시작
MKT_KR = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "KONEX": "코넥스"}


def _load_state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def _save_state(s: dict):
    os.makedirs(DATA, exist_ok=True)
    json.dump(s, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


def _rows_of(df, news, now, n_start):
    out = []
    for code, x in df.iterrows():
        name = str(x["종목명"])
        issue = ""
        try:
            hits = news._pool_match(keywords=(name,), tickers=(code,), limit=3,
                                    start=n_start, end=now)
            for h in hits:   # 시황 랩업은 이슈가 아니다
                if not any(w in h["title"] for w in ("시황", "마감", "요약", "코스피", "특징주 요약")):
                    issue = h["title"][:90]
                    break
        except Exception:
            pass
        out.append({"name": name,
                    "mkt": MKT_KR.get(str(x.get("시장") or ""), str(x.get("시장") or "")),
                    "amt_eok": round(float(x["거래대금"]) / 1e8),
                    "chg": round(float(x["등락률"]), 2), "issue": issue})
        time.sleep(0.03)
    return out


def collect(now=None) -> dict | None:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    try:
        import quotes
        hol = quotes.kr_holiday(now)
        if hol:
            print(f"[top20] 휴장({hol}) — 생략")
            return None
    except Exception:
        pass
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
    sys.path.insert(0, HERE)
    from closebet import market
    from movers import ETF_PAT
    import news
    snap = market.get_snapshot()
    df = snap[(snap["종가"] > 0) & (snap["거래량"] > 0)]
    df = df[df.index.str.endswith("0")]
    df = df[~df.index.duplicated()]
    df = df[~df["종목명"].astype(str).str.contains(ETF_PAT, na=False)]
    n_start = now.replace(hour=0, minute=0)
    top20 = df.sort_values("거래대금", ascending=False).head(TOP_N)
    limup = df[df["등락률"] >= 29.5].sort_values("거래대금", ascending=False)
    gain = df[(df["거래대금"] >= 1e11) & (df["등락률"] > 0)]
    gain = gain.sort_values("등락률", ascending=False).head(10)
    rec = {"date": today, "asof": now.strftime("%m/%d %H:%M"), "v": 2, "sections": [
        {"title": f"거래대금 상위 {TOP_N}", "rows": _rows_of(top20, news, now, n_start)},
        {"title": "상승률 상위 10 (거래대금 1,000억↑)",
         "rows": _rows_of(gain, news, now, n_start)},
        {"title": "상한가", "rows": _rows_of(limup, news, now, n_start)},
    ]}
    hist = []
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        pass
    hist = [h for h in hist if h["date"] != today] + [rec]     # 재실행 멱등
    os.makedirs(DATA, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        for h in hist:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    st = _load_state()
    if st.pop(today, None):            # 재수집 → 재전송 대상으로 (20시 정식분 갱신)
        _save_state(st)
    print("[top20] " + today + " 수집 — "
          + " · ".join(f"{s['title']} {len(s['rows'])}" for s in rec["sections"]))
    return rec


def push_all() -> int:
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    token = os.environ.get("SHEET_WEBHOOK_TOKEN", "").strip()
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        return 0
    state = _load_state()
    todo = [h for h in hist if not state.get(h["date"])]
    if not url:
        print(f"[top20] SHEET_WEBHOOK_URL 미설정 — 전송 보류 (미전송 {len(todo)}일)")
        return 0
    n = 0
    for rec in sorted(todo, key=lambda h: h["date"]):
        if rec.get("v") != 2:            # 구버전 레코드는 건너뜀 (10-16 전환)
            state[rec["date"]] = "skip-v1"
            continue
        d = datetime.strptime(rec["date"], "%Y%m%d")
        d_lab = f"{d.month}/{d.day:02d} ({'월화수목금토일'[d.weekday()]})"
        payload = {"token": token, "date": d_lab, "asof": rec.get("asof"),
                   "sections": [{"title": s["title"],
                                 "rows": [[r["name"], r["mkt"], r["amt_eok"],
                                           r["chg"], r["issue"]] for r in s["rows"]]}
                                for s in rec["sections"]]}
        try:
            resp = requests.post(url, json=payload, timeout=40, allow_redirects=True)
            ok = resp.status_code == 200 and "ok" in resp.text[:50].lower()
        except Exception as e:
            print(f"[top20] {rec['date']} 전송 실패: {type(e).__name__}")
            ok = False
        if ok:
            state[rec["date"]] = datetime.now(KST).isoformat(timespec="seconds")
            n += 1
            print(f"[top20] {rec['date']} 시트 기록 완료 ({d_lab})")
        else:
            print(f"[top20] {rec['date']} 시트 응답 비정상 — 다음 실행 때 재시도"
                  " (Apps Script v2 교체 전이면 정상)")
            break
    _save_state(state)
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
