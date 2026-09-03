# -*- coding: utf-8 -*-
"""네이버 선물 투자자별 순매수가 '언제 확정되는지'를 실측하는 일회성 로거.

배경:
  2026-09-03 관측에서 값이 두 번 달랐다.
    ~16:05, ~16:40  개인 -1,889 / 외국인 +6,658 / 기관 -3,557   (잠정으로 추정)
    ~17:35          개인 -3,768 / 외국인 +8,076 / 기관 -3,229   (0780 확정치와 일치)
  사용자 화면의 계약수는 16:07에 이미 확정값이었다. 즉 화면(계약)과
  API(억원)의 확정 시점이 다를 수 있다. 마감 후 알림을 몇 시에 보낼지가
  여기에 달려 있어 추정 대신 측정한다.

사용: 크론에서 5분마다 호출. 값이 바뀌지 않기 시작하는 시각이 확정 시각이다.
  */5 6-8 * * 1-5  /usr/bin/python3 /opt/alertbot/probe_fut.py >> /opt/alertbot/data/fut_probe.log 2>&1
확인이 끝나면 이 크론 줄과 파일을 지운다.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def get(url):
    try:
        r = requests.get(url, headers=UA, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def row(d):
    if not d:
        return "조회실패"
    return (f"개인 {d.get('personalValue','?'):>8} / "
            f"외국인 {d.get('foreignValue','?'):>8} / "
            f"기관 {d.get('institutionalValue','?'):>8}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(KST)
    t = get("https://m.stock.naver.com/api/index/FUT/trend")
    ig = get("https://m.stock.naver.com/api/index/FUT/integration") or {}
    d = ig.get("dealTrendInfo")
    info = {x.get("key"): x.get("value") for x in (ig.get("totalInfos") or [])}
    print(f"{now:%m-%d %H:%M}  /trend      {row(t)}")
    print(f"{now:%m-%d %H:%M}  dealTrend   {row(d)}   "
          f"거래량 {info.get('거래량','?')} 대금 {info.get('대금','?')}")


if __name__ == "__main__":
    main()
