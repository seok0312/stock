# -*- coding: utf-8 -*-
"""일정 캘린더 — 대시보드용 calendar.json 생성 (09-30 사용자).

경제지표(fxstreet) + 미국 실적(us_earnings) + IPO(kr_ipo) + 휴장(holidays) +
선물·옵션 만기(kr_expiry) + 수동(custom)을 오늘~+14일 창으로 수집해
/var/www/html/calendar.json 으로 저장. closebet.html 이 읽는다.

표시 규칙: HIGH 는 전부, MEDIUM 은 KR/US/CN 만, LOW 제외 — 캘린더는 브리핑보다
넓게 보되(브리핑은 HIGH 만) 소음까지 싣지는 않는다.
크론: 07:30·17:30 KST 하루 2회.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = "/var/www/html"

TYPE = {"fxstreet": "지표", "us_earnings": "실적", "kr_ipo": "IPO",
        "holidays": "휴장", "kr_expiry": "만기", "custom": "기타"}


def build(now=None) -> str:
    now = now or datetime.now(KST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=14)
    import events
    evs = events.collect(start, end)
    days: dict = {}
    for e in evs:
        vol = e.get("vol")
        if vol == "LOW":
            continue
        if vol != "HIGH" and e.get("country") not in ("KR", "US", "CN"):
            continue
        if e.get("speech") and vol != "HIGH":
            continue          # 연준 위원 연설 도배 방지 — 파월급(HIGH)만
        w = e["when"]
        item = {
            "t": w.strftime("%H:%M"),
            "type": TYPE.get(e.get("src"), "기타"),
            "name": events.label(e),
            "hi": vol == "HIGH",
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
    print(f"[{now:%m/%d %H:%M}] 캘린더 {len(out['days'])}일 · {n}건 → {path}")
    return path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    build()
