# -*- coding: utf-8 -*-
"""오늘의 종가베팅 의사결정 스냅샷 — 대시보드 결정 패널 데이터 (09-29 사용자).

3단 프레임: ① 할지 말지 & 비중(얼마나) ② 타이밍(15시/20시 → 빨리/천천히) ③ 종목.
cli 가 브리핑 슬롯마다 save() → /var/www/html/decision.json (closebet.html 이 읽음).

비중 제안 규칙(초안, 신호점수 → 비중%): +3↑=100 / +1~2=70 / 0=40 / -1~-2=20 / -3↓=0.
근거: 신호 상위/중립/하위 구간의 후보군 익일시가 수익(+2.2 / +1.0 / -1.6%)에 비례.
사용자 조정 대상 — 바꾸려면 WEIGHT 만 수정.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = "/var/www/html"

WEIGHT = ((3, 100), (1, 70), (0, 40), (-2, 20), (-99, 0))


def weight_of(score: int) -> int:
    return next(w for th, w in WEIGHT if score >= th)


def save(win, summary: dict | None, leaders: dict | None, now=None) -> str | None:
    """summary = render.LAST_SUMMARY ({score,label,timing}), leaders = fetch_leaders 결과."""
    if not summary:
        return None
    now = now or datetime.now(KST)
    rows = []
    for x in (leaders or {}).get("rows") or []:
        rows.append({"name": x.get("종목명"), "chg": x.get("등락률"),
                     "amt_eok": x.get("거래대금"), "tag": x.get("수급태그"),
                     "lead": x.get("주도일수")})
    kr = next((r for r in win.get("rows") or [] if r.get("name") == "코스피"), {})
    doc = {
        "updated": now.strftime("%m/%d %H:%M"),
        "slot": win.get("slot"), "slot_label": win.get("label"),
        "score": summary.get("score"), "score_label": summary.get("label"),
        "weight": weight_of(summary.get("score") or 0),
        "timing": summary.get("timing"),
        "leaders": rows,
    }
    out_dir = WEB_DIR if os.path.isdir(WEB_DIR) else os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "decision.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    # 전방검증 로그(09-29 사용자: 비중·타이밍·종목 실측 축적) — tag_track 이 채점.
    log = dict(doc)
    log["date"] = now.strftime("%Y%m%d")
    log["kr_flow"] = kr.get("chg_pct")        # 타이밍 판정 입력(코스피 흐름) 보존
    log.pop("leaders", None)                  # 종목 채점은 tag_picks 가 담당(중복 방지)
    with open(os.path.join(HERE, "data", "decision_log.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(json.dumps(log, ensure_ascii=False) + "\n")
    return path
