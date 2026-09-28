# -*- coding: utf-8 -*-
"""태그 전략 전방검증 — 실전 데이터로 갭/손바뀜 룰의 유효성을 채점 (09-28 사용자).

record  (15:40 크론): 당일 과열 종목(등락률 +5%↑ · 거래대금 50억↑ · 대금 상위 60)
        전체의 3주체 태그를 기록한다. 태그 없는 종목도 기록 — 무태그가 대조군.
evaluate(16:10 크론): 기록 건에 결과를 붙인다 — r_o1(익일 시가/종가매수),
        r_c5(5거래일 후 종가). 태그와 무관하게 둘 다 채점해 두면 룰 변경 시 재활용.
report  (수동): 태그별 평균/승률 — 백테스트(갭 +0.83%/53%, 손바뀜 +1.71%/53%,
        무차별 +0.16%/46%, 비용 0.2% 차감)와 대조해 실전 유효성 판정.

저장: data/tag_picks.jsonl (한 줄 = 한 종목-일). 실전 잠정치(15:40)로 태그를
판정하므로 확정치 기반 백테스트와의 괴리 자체가 검증 대상이다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.abspath(os.path.join(HERE, "..")), HERE):
    if os.path.isdir(os.path.join(_p, "closebet")) and _p not in sys.path:
        sys.path.insert(0, _p)

PATH = os.path.join(HERE, "data", "tag_picks.jsonl")
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1",
      "Referer": "https://m.stock.naver.com/"}
COST = 0.20          # 왕복 비용(수수료+거래세+슬리피지) — report 에서 차감 표기


def record(now=None, force=False) -> int:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    if not force and os.path.exists(PATH):        # 재실행 멱등 — 같은 날 중복 기록 방지
        for line in open(PATH, encoding="utf-8"):
            if f'"date": "{today}"' in line:
                print(f"[record] {today} 이미 기록됨 — 생략")
                return 0
    from closebet import market
    import leaders
    leaders._load_keys()
    from closebet.kiwoom import KiwoomClient
    snap = market.get_snapshot()
    df = snap.reset_index()
    code_col = next(c for c in ("종목코드", "index") if c in df.columns)
    hot = df[(df["등락률"] >= 5) & (df["거래대금"] >= 50e8) & df["종목명"].notna()]
    hot = hot.sort_values("거래대금", ascending=False).head(60)
    kc = KiwoomClient()
    n = 0
    with open(PATH, "a", encoding="utf-8") as f:
        for _, x in hot.iterrows():
            code = str(x[code_col]).zfill(6)
            try:
                d, _ = kc.request("ka10059",
                                  {"dt": today, "stk_cd": code, "amt_qty_tp": "1",
                                   "trde_tp": "0", "unit_tp": "1"},
                                  endpoint="/api/dostk/stkinfo")
                rows = sorted((r for r in d.get("stk_invsr_orgn") or []),
                              key=lambda r: r.get("dt") or "", reverse=True)
                tag = leaders._flow_tag(rows, today)
            except Exception:
                tag = None
            f.write(json.dumps({
                "date": today, "code": code, "name": str(x["종목명"]),
                "chg": round(float(x["등락률"]), 2),
                "close": float(x["종가"]) if x.get("종가") == x.get("종가") else None,
                "amt_eok": round(float(x["거래대금"]) / 1e8),
                "tag": tag}, ensure_ascii=False) + "\n")
            n += 1
            time.sleep(0.25)
    print(f"[record] {today} 과열 {n}종목 기록 (태그 판정: 당일 잠정치)")
    return n


def _daily_px(code: str, pages: int = 1) -> list:
    """[(YYYYMMDD, open, close)] 최신순."""
    out = []
    try:
        for p in range(1, pages + 1):
            r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/price",
                             params={"pageSize": 20, "page": p}, headers=UA,
                             timeout=12).json()
            if not isinstance(r, list):
                break
            for x in r:
                dt = (x.get("localTradedAt") or "")[:10].replace("-", "")
                try:
                    out.append((dt, float(str(x["openPrice"]).replace(",", "")),
                                float(str(x["closePrice"]).replace(",", ""))))
                except (ValueError, KeyError):
                    continue
    except Exception:
        pass
    return out


def evaluate() -> int:
    import quotes
    dates, _ = quotes._load_trading_dates()
    tdays = [d.strftime("%Y%m%d") for d in sorted(dates)]
    recs = []
    try:
        for line in open(PATH, encoding="utf-8"):
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
    except FileNotFoundError:
        print("[evaluate] 기록 없음")
        return 0
    pend = [r for r in recs if r.get("close") and
            (r.get("r_o1") is None or r.get("r_c5") is None
             or r.get("r_c10") is None)]
    by_code: dict = {}
    for r in pend:
        by_code.setdefault(r["code"], []).append(r)
    n = 0
    for code, rs in by_code.items():
        px = {d: (o, c) for d, o, c in _daily_px(code, pages=2)}  # 20일 커버
        for r in rs:
            if r["date"] not in tdays:
                continue
            i = tdays.index(r["date"])
            if r.get("r_o1") is None and i + 1 < len(tdays):
                nx = px.get(tdays[i + 1])
                if nx:
                    r["r_o1"] = round((nx[0] / r["close"] - 1) * 100, 2)
                    n += 1
            if r.get("r_c5") is None and i + 5 < len(tdays):
                d5 = px.get(tdays[i + 5])
                if d5:
                    r["r_c5"] = round((d5[1] / r["close"] - 1) * 100, 2)
                    n += 1
            if r.get("r_c10") is None and i + 10 < len(tdays):
                dx = px.get(tdays[i + 10])
                if dx:
                    r["r_c10"] = round((dx[1] / r["close"] - 1) * 100, 2)
                    n += 1
        time.sleep(0.05)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, PATH)
    print(f"[evaluate] 결과 {n}건 채점 (누적 기록 {len(recs)}건)")
    return n


def report():
    recs = []
    for line in open(PATH, encoding="utf-8"):
        try:
            recs.append(json.loads(line))
        except ValueError:
            continue
    print(f"태그 전방검증 — 누적 {len(recs)}건 (비용 {COST}% 차감, 백테스트 대조:"
          " 갭 +0.83%/53% · 손바뀜 +1.71%/53% · 무차별 +0.16%/46%)")
    groups = {"갭": [], "손바뀜": [], "회피": [], None: []}
    for r in recs:
        groups.setdefault(r.get("tag"), []).append(r)
    for tag, rs in groups.items():
        label = tag or "무태그(대조군)"
        o1 = [r["r_o1"] - COST for r in rs if r.get("r_o1") is not None]
        c5 = [r["r_c5"] - COST for r in rs if r.get("r_c5") is not None]
        seg = f"  {label:<10} 기록 {len(rs):<4}"
        if o1:
            seg += (f" | 익일시가 n={len(o1)} 평균 {sum(o1)/len(o1):+.2f}%"
                    f" 승률 {sum(v > 0 for v in o1)/len(o1)*100:.0f}%")
        if c5:
            seg += (f" | D+5 n={len(c5)} 평균 {sum(c5)/len(c5):+.2f}%"
                    f" 승률 {sum(v > 0 for v in c5)/len(c5)*100:.0f}%")
        c10 = [r["r_c10"] - COST for r in rs if r.get("r_c10") is not None]
        if c10:
            seg += f" | D+10 n={len(c10)} 평균 {sum(c10)/len(c10):+.2f}%"
        print(seg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")),
                    "/opt/upbit_bot/.env")
    if a.record:
        record()
    if a.evaluate:
        evaluate()
    if a.report:
        report()
