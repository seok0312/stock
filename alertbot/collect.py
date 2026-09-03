# -*- coding: utf-8 -*-
"""세션 경계 스냅샷 수집기 — 알림 없이 수급 데이터만 찍는다.

왜 따로 있나:
  세션별 순매수는 '누적값의 차분'으로 만든다. 그러려면 경계 시각의 스냅샷이 필요한데,
  알림 슬롯(08:50 / 16:30 / 20:00)만으로는 정규장 종료 경계가 비어 있다.
  16:30 시점의 NXT 누적에는 이미 애프터마켓(15:40~)이 섞여 있어 정규장 몫을 못 뗀다.
  그래서 15:35 처럼 경계 시각에 이 스크립트만 돌려 스냅샷을 남긴다.

브리핑 전체 파이프라인(시황·뉴스·일정·주도주)을 돌지 않아 수 초면 끝난다.

사용: python3 collect.py 1535
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    argv = argv or sys.argv[1:]
    tag = argv[0] if argv else datetime.now(KST).strftime("%H%M")

    sys.path.insert(0, HERE)
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")),
                    "/opt/upbit_bot/.env")
    import flows
    import store

    now = datetime.now(KST)
    fl = flows.summary()
    if not fl or not fl.get("rows"):
        print(f"[{now:%Y-%m-%d %H:%M}] 수집 실패 — 저장 안 함")
        return 1
    rec = store.build_record(tag, fl, now)
    store.append(rec)
    ex = rec.get("by_ex", {}).get("코스피", {})
    seg = " / ".join(f"{k} 외국인 {(v.get('flow') or {}).get('외국인', 0):+,.0f}"
                     for k, v in ex.items()) or "거래소별 없음"
    print(f"[{now:%Y-%m-%d %H:%M}] slot={tag} 저장 · 코스피 {seg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
