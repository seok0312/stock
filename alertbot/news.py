# -*- coding: utf-8 -*-
"""유의미 변동(±0.5%) 자산의 원인 뉴스 수집 — 구글 뉴스 RSS(한국어).

CryptoPanic은 403(키 필요), finviz 뉴스는 JS 로딩이라 제외.
구글 뉴스 RSS는 키 불필요·한국어 지원·시간필터(when:) 지원으로 이 용도에 가장 적합.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

# 자산 → 검색어 (한국어 우선, 방향에 따라 보조어 추가)
QUERY = {
    "오일":     "국제유가 OR WTI유가",
    "금":       "국제금값 OR 금시세",
    "나스닥":   "나스닥 지수 OR 뉴욕증시",
    "코스피":   "코스피 OR 한국증시 외국인",
    "비트코인": "비트코인 시세",
}


def _parse_pubdate(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(KST)
        except Exception:
            continue
    return None


def fetch_news(asset: str, hours: int = 12, limit: int = 3):
    """[{title, url, source, published}] — 최신순, hours 이내."""
    q = QUERY.get(asset)
    if not q:
        return []
    url = RSS.format(q=quote_plus(f"{q} when:{max(1, hours)}h"))
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
    except Exception:
        return []

    cutoff = datetime.now(KST) - timedelta(hours=hours)
    out = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = _parse_pubdate(item.findtext("pubDate") or "")
        src = item.findtext("{http://news.google.com}source") or ""
        if not src and " - " in title:            # "제목 - 언론사" 형태 분리
            title, src = title.rsplit(" - ", 1)
        if pub and pub < cutoff:
            continue
        out.append({"title": title, "url": link, "source": src.strip(),
                    "published": pub})
        if len(out) >= limit:
            break
    return out


def news_for_window(win, hours: int | None = None, limit: int = 3):
    """변동폭 결과에서 significant 자산만 뉴스 수집. {자산명: [...]}"""
    if hours is None:
        hours = max(2, round((win["end"] - win["start"]).total_seconds() / 3600))
    out = {}
    for r in win["rows"]:
        if not r["significant"]:
            continue
        items = fetch_news(r["name"], hours=hours, limit=limit)
        if items:
            out[f"{r['name']} {r['chg_pct']:+.2f}%"] = items
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for a in QUERY:
        items = fetch_news(a, hours=12, limit=3)
        print(f"\n[{a}] {len(items)}건")
        for it in items:
            p = it["published"].strftime("%m-%d %H:%M") if it["published"] else "시각미상"
            print(f"   {p}  {it['title'][:60]}")
            print(f"          {it['source']}  {it['url'][:70]}...")
