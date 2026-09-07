# -*- coding: utf-8 -*-
"""시간대별 투자자 순매수 수집 — 네이버 investorDealTrendTime (약 1.5분 해상도).

왜 매일 저장하나:
  네이버는 이 표를 **최근 1~2주만** 보관한다(2026-09-07 확인: 08-28 조회됨,
  08-14 는 빈 페이지). 지나가면 사라지므로 매일 장 마감·정산 후 그날 치를
  통째로 받아 파일로 남긴다. 대시보드의 시계열은 전부 여기서 나온다.

저장: data/intraday/YYYYMMDD_{kospi|kosdaq|fut}.json  (하루 = 파일 하나, 재수집 시 교체)
  {"date", "market", "unit", "fut_close", "cols", "rows": [[시각, ...수치], ...]}

단위: 코스피·코스닥 = 억원, 선물 = **계약** (원자료 그대로 저장하고,
  환산용 코스피200선물 종가를 fut_close 에 같이 둔다. 1계약 = 지수 x 25만원)

주의(2026-09-03 실측): 장중 값은 잠정치이고 드물게 저녁에 정정된다
  (09-03 은 18:04→18:06 사이 기타법인 +1.6조가 기관/외국인으로 재배분됐다.
   09-04 는 정정 없음 — 상시 정산이 아니라 간헐적 정정이다).
  그래서 수집 크론은 18:30 이후에 돌린다 — 정정까지 포함한 그날의 최종 시계열이 남는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("ALERTBOT_INTRADAY") or os.path.join(HERE, "data", "intraday")

URL = ("https://finance.naver.com/sise/investorDealTrendTime.naver"
       "?bizdate={d}&sosok={so}&page={p}")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      "Referer": "https://finance.naver.com/sise/"}

MARKETS = (("kospi", "01"), ("kosdaq", "02"), ("fut", "03"))
COLS = ["개인", "외국인", "기관계", "금융투자", "보험", "투신", "은행",
        "기타금융", "연기금등", "기타법인"]
MAX_PAGES = 80          # 하루 ≈ 40~50페이지. 폭주 방지 상한.


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def fetch_day(bizdate: str, sosok: str) -> list:
    """[[HH:MM, v1..v10], ...] 시간 오름차순. 빈 날이면 []."""
    rows = []
    for p in range(1, MAX_PAGES + 1):
        try:
            r = requests.get(URL.format(d=bizdate, so=sosok, p=p),
                             headers=UA, timeout=20)
            r.encoding = "euc-kr"
        except Exception:
            break
        page = re.findall(r'<td class="date2">([^<]+)</td>(.*?)</tr>', r.text, re.S)
        if not page:
            break
        for t, body in page:
            vals = [_num(v) for v in re.findall(r">([-+]?[\d,]+)</td>", body)]
            rows.append([t.strip()] + vals[:len(COLS)])
        time.sleep(0.15)
    rows.reverse()          # 페이지는 최신순 → 시간 오름차순으로
    return rows


def _fut_close(bizdate: str):
    """코스피200선물 해당일 종가 — 계약→금액 환산용. 실패 시 None."""
    try:
        r = requests.get("https://api.stock.naver.com/chart/domestic/index/FUT",
                         headers={"User-Agent": UA["User-Agent"],
                                  "Referer": "https://m.stock.naver.com/"},
                         params={"periodType": "dayCandle", "count": 20}, timeout=15)
        for x in (r.json().get("priceInfos") or []):
            if str(x.get("localDate")) == bizdate:
                return _num(x.get("closePrice"))
    except Exception:
        pass
    return None


def collect_day(bizdate: str | None = None, verbose: bool = True) -> int:
    """하루치 3개 시장을 파일로 저장. 반환: 저장한 파일 수."""
    bizdate = bizdate or datetime.now(KST).strftime("%Y%m%d")
    os.makedirs(DIR, exist_ok=True)
    saved = 0
    fut_close = None
    for mkt, so in MARKETS:
        rows = fetch_day(bizdate, so)
        if not rows:
            if verbose:
                print(f"  {bizdate} {mkt}: 데이터 없음")
            continue
        if mkt == "fut":
            fut_close = _fut_close(bizdate)
        doc = {"date": bizdate, "market": mkt,
               "unit": "계약" if mkt == "fut" else "억원",
               "fut_close": fut_close if mkt == "fut" else None,
               "cols": COLS, "rows": rows,
               "collected_at": datetime.now(KST).isoformat(timespec="seconds")}
        path = os.path.join(DIR, f"{bizdate}_{mkt}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(tmp, path)
        saved += 1
        if verbose:
            last = rows[-1]
            print(f"  {bizdate} {mkt}: {len(rows)}행 ({rows[0][0]}~{last[0]}) 저장")
    return saved


def backfill(max_days: int = 21) -> None:
    """오늘부터 거슬러 올라가며 남아 있는 과거를 저장. 이미 있는 파일은 건너뛴다.

    주말은 세지 않고 건너뛴다 — 안 그러면 월요일 아침에 토·일·(아직 빈)월요일이
    연속 3회로 잡혀 금요일 이전을 시작도 못 한다(2026-09-07 실제 발생).
    평일 기준 연속 3일 빈 응답이면 보관 기간 밖으로 보고 멈춘다.
    """
    d = datetime.now(KST)
    empty_streak = 0
    for _ in range(max_days):
        bd, wd = d.strftime("%Y%m%d"), d.weekday()
        d -= timedelta(days=1)
        if wd >= 5:
            continue
        if os.path.exists(os.path.join(DIR, f"{bd}_kospi.json")):
            print(f"  {bd}: 이미 있음 — 건너뜀")
            empty_streak = 0
            continue
        n = collect_day(bd)
        if n == 0:
            empty_streak += 1
            if empty_streak >= 3:
                print(f"  {bd} 포함 평일 3일 연속 없음 — 보관 기간 끝, 중단")
                break
        else:
            empty_streak = 0


def load_day(bizdate: str, market: str = "kospi") -> dict | None:
    path = os.path.join(DIR, f"{bizdate}_{market}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_days() -> list:
    if not os.path.isdir(DIR):
        return []
    return sorted({f[:8] for f in os.listdir(DIR) if f.endswith("_kospi.json")})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if args and args[0] == "--backfill":
        backfill(int(args[1]) if len(args) > 1 else 21)
    else:
        collect_day(args[0] if args else None)
    print(f"보유 일자: {list_days()}")
