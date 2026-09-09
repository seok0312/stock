# -*- coding: utf-8 -*-
"""관심도(투자자 주목) 수집기 — 주도주 스코어링 4번째 팩터의 원천 데이터.

네이버 인기검색 랭킹은 과거를 제공하지 않아 지금부터 자체 축적한다(5분 크론).
  · m.stock.naver.com searchTop  — 인기검색 100종목 (순위 = 배열 순서)
  · finance.naver.com lastsearch2 — 상위 30 + 검색비율(%) 강도

저장: data/attention/YYYYMMDD.jsonl — 폴마다 한 줄
  {"ts": "HH:MM", "top": [[rank, code, name, chg_pct], ...100], "ratio": {code: pct}}

크론은 */5 상시로 걸고 스크립트가 스스로 창(평일 08:20~15:45 KST, 공휴일 제외)을
판정해 밖이면 조용히 종료한다 — 크론 표기 실수보다 이게 안전하다.

실행: python3 attention.py --collect   # 크론용 (창 밖이면 no-op)
      python3 attention.py             # 현재 랭킹 출력 (창 무시)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "attention")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"}


def in_window(now=None) -> bool:
    now = now or datetime.now(KST)
    if now.weekday() >= 5:
        return False
    try:
        import holidays
        if now.date() in holidays.KR(years=now.year):
            return False
    except Exception:
        pass
    hm = now.hour * 60 + now.minute
    return 8 * 60 + 20 <= hm <= 15 * 60 + 45


def fetch_searchtop() -> list:
    """[[rank, code, name, chg_pct], ...] 최대 100."""
    out = []
    for page in (1, 2):
        try:
            r = requests.get("https://m.stock.naver.com/api/stocks/searchTop",
                             params={"page": page, "pageSize": 60}, headers=UA,
                             timeout=15).json()
        except Exception:
            break
        for s in r.get("stocks") or []:
            try:
                chg = float(str(s.get("fluctuationsRatio")).replace(",", ""))
            except (TypeError, ValueError):
                chg = None
            out.append([len(out) + 1, s.get("itemCode"), s.get("stockName"), chg])
        if len(out) >= (r.get("totalCount") or 100):
            break
    return out


def fetch_ratio() -> dict:
    """PC 인기검색 상위 30 의 검색비율(%) — {code: pct}."""
    try:
        r = requests.get("https://finance.naver.com/sise/lastsearch2.naver",
                         headers=UA, timeout=15)
        r.encoding = "euc-kr"
    except Exception:
        return {}
    out = {}
    for m in re.finditer(r'code=(\d{6})"[^>]*>.*?</a>(.*?)</tr>', r.text, re.S):
        # 검색비율은 행의 첫 번째 % 셀 (마지막 % 는 등락률 — 보합이면 부호가 없어 헷갈린다)
        pct = re.findall(r">([\d.]+)%<", m.group(2))
        if pct:
            out[m.group(1)] = float(pct[0])
    return out


def collect(force: bool = False) -> bool:
    now = datetime.now(KST)
    if not force and not in_window(now):
        return False
    top = fetch_searchtop()
    if not top:
        return False
    rec = {"ts": now.strftime("%H:%M"), "top": top, "ratio": fetch_ratio()}
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, now.strftime("%Y%m%d") + ".jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--collect" in sys.argv:
        ok = collect()
        if ok:
            print(f"[{datetime.now(KST):%m-%d %H:%M}] 관심도 수집 OK")
        raise SystemExit(0)
    top = fetch_searchtop()
    ratio = fetch_ratio()
    print(f"인기검색 {len(top)}종목 · 검색비율 {len(ratio)}종목")
    for rank, code, name, chg in top[:15]:
        rt = f"  {ratio[code]:.2f}%" if code in ratio else ""
        print(f"  {rank:>3}. {name:<12} {chg if chg is not None else '-':>7}%{rt}")
