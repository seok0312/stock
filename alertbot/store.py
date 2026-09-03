# -*- coding: utf-8 -*-
"""슬롯 스냅샷 누적 저장소.

왜 필요한가:
  장중 거래대금·순매수는 '그 시각까지의 누적'이다. 이걸 완결된 하루 평균과
  비교하면 14:30 값은 항상 작게 나와 늘 '한산'으로 보인다.
  같은 슬롯(같은 시각)끼리 비교해야 "지금 평소보다 돈이 몰리나"를 알 수 있다.
  키움·네이버 어디에도 시간대별 과거 누적이 없어 직접 쌓는다.

형식: JSON Lines. 한 줄 = 한 슬롯 실행.
경로: ALERTBOT_DATA 환경변수 > <모듈위치>/data/snapshots.jsonl
"""
from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("ALERTBOT_DATA") or os.path.join(HERE, "data", "snapshots.jsonl")


def _ensure_dir():
    os.makedirs(os.path.dirname(PATH), exist_ok=True)


def build_record(slot: str, fl: dict, now: datetime | None = None) -> dict:
    """flows.summary() 결과 → 저장 레코드."""
    now = now or datetime.now(KST)
    amount, flow, prog = {}, {}, {}
    for m in (fl or {}).get("rows", []):
        if m.get("error"):
            continue
        amount[m["label"]] = m.get("amount_won")
        flow[m["label"]] = m.get("flow_eok") or {}
        prog[m["label"]] = m.get("program_eok") or {}
    return {"ts": now.isoformat(timespec="seconds"),
            "date": now.strftime("%Y%m%d"), "slot": slot,
            "amount": amount, "flow": flow, "program": prog}


def append(rec: dict) -> None:
    """같은 (date, slot) 이 이미 있으면 최신으로 교체(수동 재실행 대비)."""
    _ensure_dir()
    rows = [r for r in load_all() if not (r.get("date") == rec["date"]
                                          and r.get("slot") == rec["slot"])]
    rows.append(rec)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("slot", "")))
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, PATH)


def load_all() -> list:
    if not os.path.exists(PATH):
        return []
    out = []
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _total_amount(rec) -> float | None:
    v = [x for x in (rec.get("amount") or {}).values() if x]
    return sum(v) if v else None


def _flow_of(rec, markets, key) -> float | None:
    f = rec.get("flow") or {}
    vals = [(f.get(m) or {}).get(key) for m in markets]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def _prog_of(rec, markets, key="비차익") -> float | None:
    p = rec.get("program") or {}
    vals = [(p.get(m) or {}).get(key) for m in markets]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def _amount_of(rec, market) -> float | None:
    return (rec.get("amount") or {}).get(market)


def _stats(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"n": len(vals), "avg": statistics.fmean(vals),
            "sd": statistics.pstdev(vals) if len(vals) > 1 else 0.0}


def slot_history(slot: str, days: int = 20, exclude_date: str | None = None) -> list:
    """같은 슬롯의 과거 기록(오늘 제외), 최근 days개."""
    rows = [r for r in load_all()
            if r.get("slot") == slot and r.get("date") != exclude_date]
    rows.sort(key=lambda r: r.get("date", ""))
    return rows[-days:]


def compare(slot: str, fl: dict, short: int = 5, long: int = 20,
            today: str | None = None) -> dict | None:
    """같은 시각(같은 슬롯) 과거 대비 비교.

    두 창을 함께 쓴다:
      short(기본 5일)  — 증감률(%) 용. 종가베팅은 1일 지평이라 최근 국면이 기준.
      long(기본 20일) — z-score 용. 표준편차를 5개 표본으로 추정하면 오차가 커
                        이상치 판정은 표본이 더 필요하다.
    표본이 없으면 None.
    """
    today = today or datetime.now(KST).strftime("%Y%m%d")
    h_long = slot_history(slot, long, exclude_date=today)
    if not h_long:
        return None
    h_short = h_long[-short:]
    cur = build_record(slot, fl)
    out = {"slot": slot, "n_short": len(h_short), "n_long": len(h_long)}

    a_now = _total_amount(cur)
    st_s = _stats([_total_amount(r) for r in h_short])
    st_l = _stats([_total_amount(r) for r in h_long])
    if a_now:
        amt = {"today": a_now}
        if st_s and st_s["avg"]:
            amt["avg_short"] = st_s["avg"]
            amt["pct_short"] = (a_now / st_s["avg"] - 1) * 100
            amt["n_short"] = st_s["n"]
        if st_l and st_l["avg"]:
            amt["avg_long"] = st_l["avg"]
            amt["pct_long"] = (a_now / st_l["avg"] - 1) * 100
            amt["n_long"] = st_l["n"]
        if len(amt) > 1:
            out["amount"] = amt

    per = {}
    for mk in ("선물", "코스피", "코스닥"):
        now_v = _amount_of(cur, mk)
        if not now_v:
            continue
        s = _stats([_amount_of(r, mk) for r in h_short])
        l = _stats([_amount_of(r, mk) for r in h_long])
        d = {"today": now_v}
        if s and s["avg"]:
            d["pct_short"] = (now_v / s["avg"] - 1) * 100
            d["n_short"] = s["n"]
        if l and l["avg"]:
            d["pct_long"] = (now_v / l["avg"] - 1) * 100
        if len(d) > 1:
            per[mk] = d
    if per:
        out["amount_market"] = per

    # 코스피 현물 기준. 순매수 소스가 키움(KRX+NXT 통합)이라 선물은 들어 있지 않다.
    SPOT = ["코스피"]
    items = [("외국인", "foreign", lambda r: _flow_of(r, SPOT, "외국인")),
             ("기관", "inst", lambda r: _flow_of(r, SPOT, "기관")),
             ("개인", "indiv", lambda r: _flow_of(r, SPOT, "개인")),
             ("기타법인", "etc", lambda r: _flow_of(r, SPOT, "기타법인")),
             ("비차익", "nonarb", lambda r: _prog_of(r, SPOT))]
    for _, name, fn in items:
        now_v = fn(cur)
        if now_v is None:
            continue
        s = _stats([fn(r) for r in h_short])
        l = _stats([fn(r) for r in h_long])
        d = {"today": now_v}
        if s:
            d["avg_short"] = s["avg"]; d["n_short"] = s["n"]
        if l:
            d["avg_long"] = l["avg"]; d["sd"] = l["sd"]; d["n_long"] = l["n"]
            # z-score 는 표본이 넉넉한 long 창으로만 계산한다
            d["z"] = (now_v - l["avg"]) / l["sd"] if l["sd"] else 0.0
        if len(d) > 1:
            out[name] = d
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    rows = load_all()
    print(f"저장 경로: {PATH}")
    print(f"레코드 {len(rows)}건")
    if rows:
        by = {}
        for r in rows:
            by.setdefault(r["slot"], []).append(r["date"])
        for s in sorted(by):
            d = sorted(by[s])
            print(f"   슬롯 {s}: {len(d)}건  {d[0]} ~ {d[-1]}")
