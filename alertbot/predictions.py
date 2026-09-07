# -*- coding: utf-8 -*-
"""'전일 미국 섹터 → 한국 섹터' 예측 적중률 수집.

아침 알림의 '한국시장 영향 예상'(sectors.kr_impact)은 지금까지 검증 없이
나가기만 했다. 예측을 기록해 두고 저녁에 실제 등락과 대조해 적중률을 쌓는다.
표본이 모이면 US_TO_KR 매핑에서 안 맞는 조합을 걷어내거나 가중치를 조정한다.

기록(JSONL, append-only):
  {"type":"pred", "date","slot","kr_sector","driver","direction","tickers":[{name,code}]}
  {"type":"eval", "date","kr_sector","avg_chg","kospi_chg","hit_abs","hit_rel"}

판정 기준 두 가지를 다 저장한다(하나로 단정하지 않는다):
  hit_abs — 예측 방향과 대표종목 평균 등락률의 부호가 같은가
  hit_rel — 대표종목 평균이 코스피 대비 초과수익인가(방향 반영)
장이 전체로 오르면 hit_abs 는 다 맞아 보이므로, 매핑의 진짜 값어치는 hit_rel 쪽이다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PATH = os.environ.get("ALERTBOT_PREDICTIONS") or os.path.join(HERE, "data", "predictions.jsonl")

_SIGN = re.compile(r"([+-]\d+(?:\.\d+)?)\s*%")


def load_all() -> list:
    if not os.path.exists(PATH):
        return []
    out = []
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def _append(rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def record(kr_impact, slot: str, now=None) -> int:
    """아침 예측을 기록. 같은 날 같은 kr_sector 는 첫 슬롯 것만 남긴다."""
    now = now or datetime.now(KST)
    date = now.strftime("%Y%m%d")
    seen = {(r.get("date"), r.get("kr_sector")) for r in load_all()
            if r.get("type") == "pred"}
    rows = []
    for im in kr_impact or []:
        key = (date, im.get("kr_sector"))
        if key in seen or not im.get("kr_sector"):
            continue
        seen.add(key)
        m = _SIGN.search(im.get("driver") or "")
        direction = 0
        if m:
            direction = 1 if float(m.group(1)) > 0 else -1
        rows.append({"type": "pred", "date": date, "slot": slot,
                     "kr_sector": im["kr_sector"], "driver": im.get("driver"),
                     "direction": direction,
                     "tickers": [{"name": t.get("name"), "code": t.get("code")}
                                 for t in (im.get("tickers") or [])[:5]]})
    _append(rows)
    return len(rows)


def evaluate(now=None) -> int:
    """오늘 예측을 실제 등락과 대조해 eval 레코드를 남긴다. 반환: 평가한 수."""
    now = now or datetime.now(KST)
    date = now.strftime("%Y%m%d")
    rows = load_all()
    done = {(r["date"], r["kr_sector"]) for r in rows if r.get("type") == "eval"}
    todo = [r for r in rows if r.get("type") == "pred" and r["date"] == date
            and (date, r["kr_sector"]) not in done]
    if not todo:
        return 0

    from closebet import market as cbm
    frame = cbm.get_snapshot("KRX")
    import quotes
    k = quotes._poll_index("KOSPI") or {}
    kospi_chg = quotes._num(k.get("fluctuationsRatio"))

    out = []
    for p in todo:
        codes = [t["code"] for t in p.get("tickers") or [] if t.get("code")]
        codes = [c for c in codes if c in frame.index]
        if not codes or p.get("direction", 0) == 0 or kospi_chg is None:
            continue
        avg = float(frame.loc[codes, "등락률"].mean())
        d = p["direction"]
        out.append({"type": "eval", "date": date, "kr_sector": p["kr_sector"],
                    "n_tickers": len(codes), "avg_chg": round(avg, 2),
                    "kospi_chg": round(kospi_chg, 2), "direction": d,
                    "hit_abs": (avg > 0) == (d > 0),
                    "hit_rel": ((avg - kospi_chg) > 0) == (d > 0)})
    _append(out)
    return len(out)


def stats():
    rows = load_all()
    evs = [r for r in rows if r.get("type") == "eval"]
    preds = {(r["date"], r["kr_sector"]): r for r in rows if r.get("type") == "pred"}
    if not evs:
        print("평가 표본 없음 — 아침 예측이 기록되고 18:40 평가가 돌면 쌓입니다.")
        return
    n = len(evs)
    a = sum(1 for e in evs if e["hit_abs"])
    r_ = sum(1 for e in evs if e["hit_rel"])
    print(f"전체 {n}건 · 방향적중 {a}/{n} ({a/n*100:.0f}%) · "
          f"초과수익적중 {r_}/{n} ({r_/n*100:.0f}%)")
    by = {}
    for e in evs:
        p = preds.get((e["date"], e["kr_sector"])) or {}
        drv = re.sub(r"\s*[+-][\d.]+%", "", p.get("driver") or "?")
        d = by.setdefault((drv, e["kr_sector"]), [0, 0, 0])
        d[0] += 1
        d[1] += e["hit_abs"]
        d[2] += e["hit_rel"]
    print(f"\n{'드라이버 → 한국섹터':<40}{'n':>4}{'방향':>7}{'초과':>7}")
    for (drv, sec), (cnt, ha, hr) in sorted(by.items(), key=lambda x: -x[1][0]):
        print(f"{(drv + ' → ' + sec)[:40]:<40}{cnt:>4}{ha/cnt*100:>6.0f}%{hr/cnt*100:>6.0f}%")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "--evaluate":
        n = evaluate()
        print(f"[{datetime.now(KST):%Y-%m-%d %H:%M}] 예측 평가 {n}건 기록")
    else:
        stats()
