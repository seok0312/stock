# -*- coding: utf-8 -*-
"""종가베팅 수급 대시보드 생성기.

텔레그램은 요약, 상세는 여기로 — 라는 분담(사용자 결정)의 '상세' 쪽이다.
  입력: data/intraday/*.json (시간대별 순매수, 네이버)
        data/snapshots.jsonl (슬롯 스냅샷 → 세션별 집계, sessions.py)
  출력: /var/www/html/closebet_data.json + closebet.html (nginx 정적 서빙)

로컬에서 돌리면 out/ 아래에 쓴다(서버 경로가 없으므로) — 미리보기용.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import intraday
import sessions
import store

WEB_DIR = "/var/www/html"
OUT_DIR = WEB_DIR if os.path.isdir(WEB_DIR) else os.path.join(HERE, "out")

# 시계열로 내보낼 주체. 기관 하위(금융투자·연기금 등)는 v1 에서 제외 — 선이 많으면 못 읽는다.
SERIES = ("개인", "외국인", "기관계", "기타법인")
MAX_DAYS = 14


def _fut_to_eok(v, close):
    """선물 계약 → 억원. 1계약 = 지수 × 250,000원."""
    if v is None or not close:
        return None
    return round(v * close * 250_000 / 1e8, 1)


def build() -> dict:
    days = intraday.list_days()[-MAX_DAYS:]
    recs = store.load_all()

    sess = {}
    for market, key in (("코스피", "kospi"), ("코스닥", "kosdaq")):
        for d, v in sessions.by_date(recs, market).items():
            sess.setdefault(d, {})[key] = {
                "sessions": v["sessions"], "used": v["used"], "quality": v["quality"]}

    intra = {}
    for d in days:
        intra[d] = {}
        for mkt in ("kospi", "kosdaq", "fut"):
            doc = intraday.load_day(d, mkt)
            if not doc:
                continue
            idx = {c: i + 1 for i, c in enumerate(doc["cols"])}
            close = doc.get("fut_close")
            out = {"t": [r[0] for r in doc["rows"]], "unit": "억원"}
            for s in SERIES:
                col = [r[idx[s]] if idx[s] < len(r) else None for r in doc["rows"]]
                if mkt == "fut":
                    col = [_fut_to_eok(v, close) for v in col]
                out[s] = col
            if mkt == "fut":
                out["fut_close"] = close
                out["note"] = "계약수 × 지수 × 25만원 환산"
            intra[d][mkt] = out

    return {"generated": datetime.now(KST).isoformat(timespec="seconds"),
            "days": days, "sessions": sess, "intraday": intra,
            "labels": {"pre": "NXT 프리 08:00~08:50",
                       "main": "정규장 09:00~15:30",
                       "after": "NXT 애프터 15:40~20:00"}}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    os.makedirs(OUT_DIR, exist_ok=True)
    data = build()
    jpath = os.path.join(OUT_DIR, "closebet_data.json")
    with open(jpath + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(jpath + ".tmp", jpath)
    src = os.path.join(HERE, "closebet.html")
    if os.path.exists(src):
        shutil.copyfile(src, os.path.join(OUT_DIR, "closebet.html"))
    print(f"[{data['generated']}] 대시보드 생성 → {OUT_DIR}  "
          f"(일자 {len(data['days'])}개, 세션 {len(data['sessions'])}일)")


if __name__ == "__main__":
    main()
