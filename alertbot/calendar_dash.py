# -*- coding: utf-8 -*-
"""일정 캘린더 — 대시보드용 calendar.json 생성 (09-30 사용자).

전체 목록: 오늘~+14일 (지표·실적·IPO·휴장·만기, HIGH 전부 + KR/US/CN MEDIUM,
          일반 연준연설 제외)
SSS 목록: 오늘~+92일(3개월) — FOMC 결정 · 미 고용보고서 · 미 CPI · BoJ 결정 ·
          빅테크/반도체 대형 실적 · 한/미 휴장 · 선물옵션 만기 · custom HIGH
          (중국·유럽 제외 — 09-30 사용자. BoJ 는 엔캐리 리스크로 예외 포함)
크론: 07:30·17:30 KST. closebet.html 의 'SSS만' 토글이 양쪽을 오간다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = "/var/www/html"

TYPE = {"fxstreet": "지표", "us_earnings": "실적", "kr_earnings": "실적", "kr_ipo": "IPO",
        "holidays": "휴장", "kr_expiry": "만기", "btc": "코인", "us_ipo": "IPO", "custom": "기타"}
_MOMYOY = re.compile(r"\((MOM|YOY|QOQ)\)")


def build(now=None) -> str:
    now = now or datetime.now(KST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    near_end = start + timedelta(days=14)
    far_end = start + timedelta(days=92)
    import events
    from events import is_sss          # 브리핑과 단일 원천 (09-30)
    evs = events.collect(start, far_end)
    # us_earnings 프로바이더는 브리핑 비용 제한으로 9일 캡 — 캘린더는 9일씩 이어붙임
    fe = events.PROVIDERS.get("us_earnings")
    d0 = start + timedelta(days=9)
    while fe and d0 < far_end:
        d1 = min(d0 + timedelta(days=9), far_end)
        try:
            evs.extend(fe(d0 + timedelta(seconds=1), d1) or [])
        except Exception:
            pass
        d0 = d1
    dedup, uniq = set(), []
    for e in evs:
        k = (e["when"], e.get("src"), e.get("name"))
        if k in dedup:
            continue
        dedup.add(k)
        uniq.append(e)
    evs = uniq
    days: dict = {}
    seen_sss = set()
    for e in evs:
        vol, w = e.get("vol"), e["when"]
        sss = is_sss(e)
        if sss:
            # CPI (MOM)/(YOY), FOMC 부속 행 등 같은 사건의 하위 행 dedup
            key = (w.strftime("%Y%m%d%H%M"), e.get("country"),
                   _MOMYOY.sub("", e.get("name_kr") or e.get("name") or "").strip())
            if key in seen_sss:
                continue
            seen_sss.add(key)
        else:
            if w >= near_end:
                continue
            if vol == "LOW":
                continue
            if vol != "HIGH" and e.get("country") not in ("KR", "US", "CN", "BTC"):
                continue
            if e.get("speech") and vol != "HIGH":
                continue          # 연준 위원 연설 도배 방지 — 파월급(HIGH)만
        item = {
            "t": w.strftime("%H:%M"),
            "type": TYPE.get(e.get("src"), "기타"),
            "name": events.label(e),
            "hi": vol == "HIGH",
            "sss": sss,
            "val": events.value_text(e) or (e.get("note") or ""),
        }
        days.setdefault(w.strftime("%Y%m%d"), []).append(item)
    out = {"generated": now.strftime("%m/%d %H:%M"),
           "days": [{"date": d,
                     "wd": "월화수목금토일"[datetime.strptime(d, "%Y%m%d").weekday()],
                     "items": sorted(v, key=lambda x: x["t"])}
                    for d, v in sorted(days.items())]}
    out_dir = WEB_DIR if os.path.isdir(WEB_DIR) else os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "calendar.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    n = sum(len(d["items"]) for d in out["days"])
    ns = sum(1 for d in out["days"] for x in d["items"] if x["sss"])
    print(f"[{now:%m/%d %H:%M}] 캘린더 {len(out['days'])}일 · {n}건 (SSS {ns}건) → {path}")
    return path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    build()
