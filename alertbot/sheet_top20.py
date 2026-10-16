# -*- coding: utf-8 -*-
"""거래대금 상위 20 일일 분석 → 구글시트 자동 기록 (10-16 사용자).

매일 20:00 크론(평일): 마감 스냅샷에서 보통주(ETF 제외) 거래대금 상위 20 종목의
[날짜, 종목, 거래대금(억), 등락률(%), 이슈] 를 만들어
  ① data/sheet_top20.jsonl 에 저장(영구 원본, 멱등)
  ② 구글시트 Apps Script 웹훅(.env 의 SHEET_WEBHOOK_URL)으로 append
웹훅 URL 이 없거나 실패한 날은 상태파일에 미전송으로 남아 다음 실행 때 자동
재전송(백필)된다. 이슈 = 당일 뉴스 풀(네이버 증권뉴스+스탁허브) 제목 1건.

시트 쪽 스크립트는 HANDOFF 참고 — doPost 가 날짜 중복 삭제 후 append (멱등).
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
TOP_N = 20


def _load_state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def collect(now=None) -> dict | None:
    """당일 거래대금 상위 20 — [{name, amt_eok, chg, issue}]."""
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
    snap = market.get_snapshot()
    df = snap[(snap["종가"] > 0) & (snap["거래량"] > 0)]
    df = df[df.index.str.endswith("0")]
    df = df[~df.index.duplicated()]
    df = df[~df["종목명"].astype(str).str.contains(ETF_PAT, na=False)]
    df = df.sort_values("거래대금", ascending=False).head(TOP_N)
    import news
    n_start = now.replace(hour=0, minute=0)
    rows = []
    for code, x in df.iterrows():
        name = str(x["종목명"])
        issue = ""
        try:
            hits = news._pool_match(keywords=(name,), tickers=(code,), limit=3,
                                    start=n_start, end=now)
            for h in hits:   # 시황 랩업은 이슈가 아니다 (기존 규칙)
                if not any(w in h["title"] for w in ("시황", "마감", "요약", "코스피", "특징주 요약")):
                    issue = h["title"][:90]
                    break
        except Exception:
            pass
        rows.append({"code": code, "name": name,
                     "amt_eok": round(float(x["거래대금"]) / 1e8),
                     "chg": round(float(x["등락률"]), 2), "issue": issue})
        time.sleep(0.03)
    rec = {"date": today, "asof": now.strftime("%m/%d %H:%M"), "rows": rows}
    os.makedirs(DATA, exist_ok=True)
    hist = []
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        pass
    hist = [h for h in hist if h["date"] != today] + [rec]     # 재실행 멱등
    with open(STORE, "w", encoding="utf-8") as f:
        for h in hist:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    print(f"[top20] {today} 수집 {len(rows)}종목 (이슈 {sum(1 for r in rows if r['issue'])}건)")
    return rec


def push_all() -> int:
    """미전송 날짜 전부 웹훅으로 전송 — URL 미설정이면 수집만 하고 보류."""
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    token = os.environ.get("SHEET_WEBHOOK_TOKEN", "").strip()
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        return 0
    if not url:
        print("[top20] SHEET_WEBHOOK_URL 미설정 — 수집분은 쌓아두고 전송 보류"
              f" (미전송 {len([h for h in hist if not _load_state().get(h['date'])])}일)")
        return 0
    state = _load_state()
    n = 0
    for rec in sorted(hist, key=lambda h: h["date"]):
        if state.get(rec["date"]):
            continue
        wd = "월화수목금토일"[datetime.strptime(rec["date"], "%Y%m%d").weekday()]
        d_lab = f"{rec['date'][:4]}-{rec['date'][4:6]}-{rec['date'][6:]} ({wd})"
        payload = {"token": token, "date": d_lab,
                   "rows": [[d_lab, r["name"], r["amt_eok"], r["chg"], r["issue"]]
                            for r in rec["rows"]]}
        try:
            resp = requests.post(url, json=payload, timeout=30,
                                 allow_redirects=True)
            ok = resp.status_code == 200 and "ok" in resp.text[:50].lower()
        except Exception as e:
            print(f"[top20] {rec['date']} 전송 실패: {type(e).__name__}")
            ok = False
        if ok:
            state[rec["date"]] = datetime.now(KST).isoformat(timespec="seconds")
            n += 1
            print(f"[top20] {rec['date']} 시트 기록 완료")
        else:
            break            # 연속 실패 방지 — 다음 크론에서 재시도
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
