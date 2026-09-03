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


def compare(slot: str, fl: dict, days: int = 20, today: str | None = None) -> dict | None:
    """같은 시각 과거 대비 비교. 표본이 없으면 None.

    반환: {n, amount:{today,avg,pct}, foreign:{today,avg,sd,z}, ...}
    """
    today = today or datetime.now(KST).strftime("%Y%m%d")
    hist = slot_history(slot, days, exclude_date=today)
    if not hist:
        return None
    cur = build_record(slot, fl)
    out = {"n": len(hist), "slot": slot}

    a_now, a_st = _total_amount(cur), _stats([_total_amount(r) for r in hist])
    if a_now and a_st:
        out["amount"] = {"today": a_now, "avg": a_st["avg"], "n": a_st["n"],
                         "pct": (a_now / a_st["avg"] - 1) * 100 if a_st["avg"] else None}

    # 시장별 거래대금
    per = {}
    for mk in ("선물", "코스피", "코스닥"):
        now_v = _amount_of(cur, mk)
        st = _stats([_amount_of(r, mk) for r in hist])
        if now_v and st and st["avg"]:
            per[mk] = {"today": now_v, "avg": st["avg"], "n": st["n"],
                       "pct": (now_v / st["avg"] - 1) * 100}
    if per:
        out["amount_market"] = per

    SPOT = ["코스피", "선물"]
    # 비차익 프로그램도 같은 방식으로 이례성 판정
    np_now = _prog_of(cur, SPOT)
    np_st = _stats([_prog_of(r, SPOT) for r in hist])
    if np_now is not None and np_st:
        z = (np_now - np_st["avg"]) / np_st["sd"] if np_st["sd"] else 0.0
        out["nonarb"] = {"today": np_now, "avg": np_st["avg"], "sd": np_st["sd"],
                         "z": z, "n": np_st["n"]}
    for key, name in (("외국인", "foreign"), ("기관", "inst"), ("개인", "indiv")):
        now_v = _flow_of(cur, SPOT, key)
        st = _stats([_flow_of(r, SPOT, key) for r in hist])
        if now_v is None or not st:
            continue
        z = (now_v - st["avg"]) / st["sd"] if st["sd"] else 0.0
        out[name] = {"today": now_v, "avg": st["avg"], "sd": st["sd"],
                     "z": z, "n": st["n"]}
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
