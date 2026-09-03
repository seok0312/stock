# -*- coding: utf-8 -*-
"""세션별 순매수 — 스냅샷 누적값을 차분해 '어느 장에서 누가 샀나'를 만든다.

세 구간:
  프리    NXT  08:00~08:50   → 09시 정규장 개장을 미리 읽는 재료
  정규장  KRX  09:00~15:30 + NXT 메인 09:00~15:20
  애프터  NXT  15:40~20:00   → 다음날 개장을 미리 읽는 재료

키움 ka10051/ka90010 은 '당일 누적'만 주므로 구간값은 경계 스냅샷의 차로 얻는다.
  프리   = NXT(08:50)
  정규장 = KRX(15:35) + [NXT(15:35) - NXT(08:50)]
  애프터 = NXT(20:00) - NXT(15:35)

경계 스냅샷이 없으면 가장 가까운 대체 슬롯을 쓰고 quality 에 표시한다.
16:30 을 정규장 경계로 대신 쓰면 NXT 애프터마켓 50분이 정규장에 섞이므로
정확한 분리를 원하면 15:35 에 collect.py 를 돌려야 한다.
"""
from __future__ import annotations

ITEMS = ("개인", "외국인", "기관", "기타법인", "비차익")

# 경계별 슬롯 후보. 앞에 있을수록 정확하다.
BOUNDS = {
    "pre_end":  ("0850", "0930"),
    "main_end": ("1535", "1630", "1430"),
    "day_end":  ("2000", "1900", "1630"),
}
LABELS = {
    "pre":   "NXT 프리 08:00~08:50",
    "main":  "정규장 09:00~15:30",
    "after": "NXT 애프터 15:40~20:00",
}


def _ex(rec, market, exchange):
    """{개인, 외국인, 기관, 기타법인, 비차익} 또는 None."""
    d = ((rec.get("by_ex") or {}).get(market) or {}).get(exchange) or {}
    f = d.get("flow")
    if not f:
        return None
    out = {k: f.get(k) for k in ITEMS if k != "비차익"}
    out["비차익"] = (d.get("program") or {}).get("비차익")
    return out


def _sub(a, b):
    if a is None:
        return None
    if b is None:
        return dict(a)
    return {k: (None if a.get(k) is None or b.get(k) is None else a[k] - b[k])
            for k in ITEMS}


def _add(a, b):
    if a is None:
        return dict(b) if b else None
    if b is None:
        return dict(a)
    return {k: (None if a.get(k) is None and b.get(k) is None
                else (a.get(k) or 0) + (b.get(k) or 0)) for k in ITEMS}


def _pick(by_slot, names):
    for n in names:
        if n in by_slot:
            return n, by_slot[n]
    return None, None


def for_day(records, market="코스피") -> dict | None:
    """하루치 레코드 → {sessions: {...}, used: {...}, quality: [...]}"""
    by_slot = {r.get("slot"): r for r in records if r.get("by_ex")}
    if not by_slot:
        return None
    s_pre, r_pre = _pick(by_slot, BOUNDS["pre_end"])
    s_main, r_main = _pick(by_slot, BOUNDS["main_end"])
    s_day, r_day = _pick(by_slot, BOUNDS["day_end"])

    nxt_pre = _ex(r_pre, market, "NXT") if r_pre else None
    nxt_main = _ex(r_main, market, "NXT") if r_main else None
    nxt_day = _ex(r_day, market, "NXT") if r_day else None
    krx_main = _ex(r_main, market, "KRX") if r_main else None

    quality = []
    if s_main and s_main != "1535":
        quality.append(f"정규장 경계가 {s_main} 스냅샷 — NXT 애프터마켓 일부가 섞임")
    if not s_pre:
        quality.append("프리마켓 경계(08:50) 스냅샷 없음")
    if not s_day:
        quality.append("장 종료(20:00) 스냅샷 없음")

    sessions = {
        "pre":   nxt_pre,
        "main":  _add(krx_main, _sub(nxt_main, nxt_pre)),
        "after": _sub(nxt_day, nxt_main),
    }
    return {"market": market, "sessions": sessions, "labels": LABELS,
            "used": {"pre_end": s_pre, "main_end": s_main, "day_end": s_day},
            "quality": quality}


def by_date(records, market="코스피") -> dict:
    """날짜별 세션 집계. {date: for_day(...)}"""
    days = {}
    for r in records:
        days.setdefault(r.get("date"), []).append(r)
    out = {}
    for d, rows in sorted(days.items()):
        v = for_day(rows, market)
        if v:
            out[d] = v
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    import store
    rows = store.load_all()
    for market in ("코스피", "코스닥"):
        print(f"\n■ {market}  (억원)")
        res = by_date(rows, market)
        if not res:
            print("   거래소별 스냅샷 없음 — 수집 시작 후 축적됩니다")
            continue
        for d, v in res.items():
            print(f"  {d}  경계 {v['used']}")
            for key in ("pre", "main", "after"):
                s = v["sessions"].get(key)
                if not s:
                    print(f"    {LABELS[key]:<24} —")
                    continue
                print(f"    {LABELS[key]:<24} " +
                      "  ".join(f"{k} {('-' if s.get(k) is None else format(s[k], '+,.0f'))}"
                                for k in ITEMS))
            for q in v["quality"]:
                print(f"    ⚠ {q}")
