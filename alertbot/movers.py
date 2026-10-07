# -*- coding: utf-8 -*-
"""주요종목 무버 일별 아카이브 — 뉴스→가격 해석 훈련용 (10-08 사용자).

목적: "오른 종목의 이유"를 매일 축적해 뉴스 중요도 감각을 기른다.
  06:10 KST (--us): 간밤 미국 주요 종목 상승률 상위 + 섹터 묶음 + 원인 뉴스
  20:00 KST (--kr): 당일 한국 주요 종목 상승률 상위 + 섹터·테마 + 원인 뉴스
저장: data/movers/YYYYMMDD.json (일 1파일, us/kr 키 병합)
대시보드: /var/www/html/movers_data.json (최근 30일) ← movers.html 이 일별 탭으로 표시

미국 시세: 야후 v8 chart (crumb 불요 — 이 코드베이스에서 검증된 소스).
뉴스: news._pool_match (네이버 증권뉴스 + 스탁허브 풀, 국내외 모두 커버).
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
DATA = os.path.join(HERE, "data", "movers")
WEB_DIR = "/var/www/html"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"}

# (티커, 한글명, 섹터) — 미국 '주요 종목' 유니버스. 시총 큰 대표주 위주 수동 관리.
US_UNIV = [
    ("NVDA", "엔비디아", "반도체"), ("AVGO", "브로드컴", "반도체"), ("TSM", "TSMC", "반도체"),
    ("AMD", "AMD", "반도체"), ("MU", "마이크론", "반도체"), ("INTC", "인텔", "반도체"),
    ("QCOM", "퀄컴", "반도체"), ("TXN", "텍사스인스트루먼트", "반도체"), ("ARM", "ARM", "반도체"),
    ("AMAT", "어플라이드머티리얼즈", "반도체장비"), ("LRCX", "램리서치", "반도체장비"),
    ("KLAC", "KLA", "반도체장비"), ("ASML", "ASML", "반도체장비"),
    ("SMCI", "슈퍼마이크로", "AI인프라"), ("VRT", "버티브", "AI인프라"), ("DELL", "델", "AI인프라"),
    ("ANET", "아리스타", "AI인프라"), ("ETN", "이튼", "전력"), ("PWR", "콴타서비스", "전력"),
    ("CEG", "컨스텔레이션", "원전"), ("VST", "비스트라", "원전"), ("OKLO", "오클로", "원전"),
    ("SMR", "뉴스케일", "원전"), ("GEV", "GE버노바", "전력"),
    ("AAPL", "애플", "빅테크"), ("MSFT", "마이크로소프트", "빅테크"), ("GOOGL", "구글", "빅테크"),
    ("META", "메타", "빅테크"), ("AMZN", "아마존", "빅테크"), ("NFLX", "넷플릭스", "미디어"),
    ("TSLA", "테슬라", "전기차"), ("RIVN", "리비안", "전기차"), ("NIO", "니오", "전기차"),
    ("ORCL", "오라클", "소프트웨어"), ("CRM", "세일즈포스", "소프트웨어"), ("ADBE", "어도비", "소프트웨어"),
    ("NOW", "서비스나우", "소프트웨어"), ("INTU", "인튜이트", "소프트웨어"), ("IBM", "IBM", "소프트웨어"),
    ("PLTR", "팔란티어", "소프트웨어"), ("SNOW", "스노우플레이크", "소프트웨어"),
    ("CRWD", "크라우드스트라이크", "보안"), ("PANW", "팔로알토", "보안"), ("ZS", "지스케일러", "보안"),
    ("FTNT", "포티넷", "보안"), ("NET", "클라우드플레어", "소프트웨어"), ("DDOG", "데이터독", "소프트웨어"),
    ("MDB", "몽고DB", "소프트웨어"), ("CSCO", "시스코", "네트워크"),
    ("UBER", "우버", "플랫폼"), ("ABNB", "에어비앤비", "플랫폼"), ("DASH", "도어대시", "플랫폼"),
    ("SHOP", "쇼피파이", "플랫폼"), ("BKNG", "부킹홀딩스", "플랫폼"), ("MELI", "메르카도리브레", "플랫폼"),
    ("PDD", "핀둬둬", "중국테크"), ("BABA", "알리바바", "중국테크"), ("JD", "징둥", "중국테크"),
    ("SE", "씨그룹", "중국테크"), ("NTES", "넷이즈", "중국테크"), ("BIDU", "바이두", "중국테크"),
    ("JPM", "JP모건", "금융"), ("BAC", "뱅크오브아메리카", "금융"), ("WFC", "웰스파고", "금융"),
    ("GS", "골드만삭스", "금융"), ("MS", "모건스탠리", "금융"), ("C", "씨티", "금융"),
    ("BLK", "블랙록", "금융"), ("SCHW", "찰스슈왑", "금융"),
    ("V", "비자", "결제"), ("MA", "마스터카드", "결제"), ("AXP", "아멕스", "결제"),
    ("PYPL", "페이팔", "결제"), ("COIN", "코인베이스", "암호화폐"), ("HOOD", "로빈후드", "암호화폐"),
    ("MSTR", "마이크로스트래티지", "암호화폐"), ("MARA", "마라홀딩스", "암호화폐"),
    ("CRCL", "서클", "암호화폐"),
    ("LLY", "일라이릴리", "제약"), ("NVO", "노보노디스크", "제약"), ("UNH", "유나이티드헬스", "헬스케어"),
    ("JNJ", "존슨앤존슨", "제약"), ("PFE", "화이자", "제약"), ("MRK", "머크", "제약"),
    ("ABBV", "애브비", "제약"), ("AMGN", "암젠", "바이오"), ("GILD", "길리어드", "바이오"),
    ("MRNA", "모더나", "바이오"), ("ISRG", "인튜이티브서지컬", "의료기기"), ("HIMS", "힘스앤허스", "헬스케어"),
    ("XOM", "엑슨모빌", "에너지"), ("CVX", "셰브론", "에너지"), ("COP", "코노코필립스", "에너지"),
    ("SLB", "슐룸베르거", "에너지"),
    ("GE", "GE에어로스페이스", "방산항공"), ("BA", "보잉", "방산항공"), ("LMT", "록히드마틴", "방산항공"),
    ("RTX", "RTX", "방산항공"), ("RKLB", "로켓랩", "우주"),
    ("CAT", "캐터필러", "산업재"), ("DE", "디어", "산업재"), ("HON", "하니웰", "산업재"),
    ("UNP", "유니온퍼시픽", "운송"), ("UPS", "UPS", "운송"), ("FDX", "페덱스", "운송"),
    ("WMT", "월마트", "유통"), ("COST", "코스트코", "유통"), ("TGT", "타깃", "유통"),
    ("HD", "홈디포", "유통"), ("MCD", "맥도날드", "소비"), ("SBUX", "스타벅스", "소비"),
    ("NKE", "나이키", "소비"), ("KO", "코카콜라", "소비"), ("PEP", "펩시", "소비"),
    ("DIS", "디즈니", "미디어"), ("IONQ", "아이온큐", "양자"), ("RGTI", "리게티", "양자"),
    ("QBTS", "디웨이브", "양자"),
]

ETF_PAT = re.compile(r"KODEX|TIGER|ACE |PLUS |SOL |RISE |HANARO|KOSEF|KIWOOM |ARIRANG|1Q |스팩")


def _ychart_chg(sym: str):
    """야후 chart — (전일比 %, 마지막 봉 날짜 'YYYYMMDD' ET 기준) 또는 None."""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                         params={"range": "7d", "interval": "1d"}, headers=UA, timeout=12)
        res = r.json()["chart"]["result"][0]
        ts = res.get("timestamp") or []
        closes = (res["indicators"]["quote"][0].get("close")) or []
        pts = [(t, c) for t, c in zip(ts, closes) if c]
        if len(pts) < 2:
            return None
        pct = (pts[-1][1] / pts[-2][1] - 1) * 100
        last = datetime.fromtimestamp(pts[-1][0], tz=timezone(timedelta(hours=-5)))
        return pct, last.strftime("%Y%m%d")
    except Exception:
        return None


def _news_for(keywords, tickers, start, end, limit=2):
    try:
        import news
        hits = news._pool_match(keywords=keywords, tickers=tickers, limit=limit + 2,
                                start=start, end=end)
        out = []
        for h in hits:  # 시황 랩업 기사는 '이유'가 아니다 (us_movers 와 동일 규칙)
            if any(w in h["title"] for w in ("시황", "마감", "요약", "코스피", "다우")):
                continue
            out.append({"t": h["title"][:80], "u": h.get("url"), "s": h.get("source") or ""})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def _cats(rows, key="sector"):
    """상위 종목을 섹터로 묶어 '오늘의 카테고리' — 2종목 이상 겹치면 흐름."""
    g = {}
    for r in rows:
        s = r.get(key)
        if s:
            g.setdefault(s, []).append(r["name"])
    out = [{"cat": k, "n": len(v), "names": v[:4]} for k, v in g.items() if len(v) >= 2]
    out.sort(key=lambda x: -x["n"])
    return out[:5]


def _merge_save(date: str, part: str, doc: dict):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, f"{date}.json")
    cur = {}
    try:
        cur = json.load(open(path, encoding="utf-8"))
    except Exception:
        pass
    cur["date"] = date
    cur[part] = doc
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def record_us(now=None, top=10) -> int:
    """간밤 미국 세션 상승률 상위 — 06:10 크론. 날짜 라벨 = 오늘(KST)."""
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    rows, stale = [], 0
    for sym, kr, sec in US_UNIV:
        got = _ychart_chg(sym)
        time.sleep(0.08)
        if not got:
            continue
        pct, last_d = got
        # 마지막 봉이 이틀 넘게 묵으면 휴장/지연 — 해당 종목 제외
        if (now.date() - datetime.strptime(last_d, "%Y%m%d").date()).days > 2:
            stale += 1
            continue
        rows.append({"sym": sym, "name": kr, "pct": round(pct, 2), "sector": sec})
    if len(rows) < 30 or stale > len(US_UNIV) * 0.5:
        print(f"[us] 데이터 부족(수신 {len(rows)} · 묵음 {stale}) — 휴장 추정, 기록 생략")
        return 0
    rows.sort(key=lambda x: -x["pct"])
    gain = [r for r in rows if r["pct"] > 0][:top]
    n_start = now.replace(hour=0, minute=0) - timedelta(hours=15)   # 전일 15시 KST ~
    for r in gain:
        r["news"] = _news_for((r["name"], r["sym"]), (r["sym"],), n_start, now)
        time.sleep(0.03)
    doc = {"asof": now.strftime("%m/%d %H:%M"), "rows": gain,
           "cats": _cats([r for r in rows if r["pct"] > 0][:15]),
           "breadth": f"{sum(1 for r in rows if r['pct'] > 0)}/{len(rows)}"}
    _merge_save(today, "us", doc)
    dash()
    print(f"[us] {today} 상승 상위 {len(gain)} 기록 (유니버스 {len(rows)} 수신)")
    return len(gain)


def record_kr(now=None, top=10) -> int:
    """당일 한국 마감 상승률 상위(주요 종목) — 20:00 크론."""
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    try:
        import quotes
        hol = quotes.kr_holiday(now)
        if hol:
            print(f"[kr] 휴장({hol}) — 생략")
            return 0
    except Exception:
        pass
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
    from closebet import market
    import nxt
    snap = market.get_snapshot()
    df = snap[(snap["종가"] > 0) & (snap["거래량"] > 0)]
    df = df[df.index.str.endswith("0")]
    df = df[~df.index.duplicated()]
    df = df[~df["종목명"].astype(str).str.contains(ETF_PAT, na=False)]
    import pandas as pd
    mc = pd.to_numeric(df["시가총액"], errors="coerce").fillna(0)
    amt = pd.to_numeric(df["거래대금"], errors="coerce").fillna(0)
    major = df[(mc >= 5e11) & (amt >= 3e10) & (df["등락률"] > 0)]
    major = major.sort_values("등락률", ascending=False).head(top)
    smap = nxt.load_sector_map()
    try:
        import sector_pulse
        tmap = sector_pulse._load_theme_map()
    except Exception:
        tmap = {}
    n_start = now.replace(hour=0, minute=0)
    rows = []
    for code, x in major.iterrows():
        name = str(x["종목명"])
        r = {"code": code, "name": name, "pct": round(float(x["등락률"]), 2),
             "amt_eok": round(float(x["거래대금"]) / 1e8),
             "sector": smap.get(code), "themes": (tmap.get(code) or [])[:2],
             "limit_up": float(x["등락률"]) >= 29.5,
             "news": _news_for((name,), (code,), n_start, now)}
        rows.append(r)
        time.sleep(0.03)
    if not rows:
        print("[kr] 대상 없음 — 생략")
        return 0
    doc = {"asof": now.strftime("%m/%d %H:%M"), "rows": rows, "cats": _cats(rows)}
    _merge_save(today, "kr", doc)
    dash()
    print(f"[kr] {today} 상승 상위 {len(rows)} 기록")
    return len(rows)


def dash(days=30):
    try:
        files = sorted(os.listdir(DATA))[-days:]
    except FileNotFoundError:
        return
    out = []
    for fn in reversed(files):           # 최신이 앞
        try:
            out.append(json.load(open(os.path.join(DATA, fn), encoding="utf-8")))
        except Exception:
            continue
    out_dir = WEB_DIR if os.path.isdir(WEB_DIR) else os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    doc = {"generated": datetime.now(KST).strftime("%m/%d %H:%M"), "days": out}
    with open(os.path.join(out_dir, "movers_data.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, HERE)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--us", action="store_true")
    ap.add_argument("--kr", action="store_true")
    ap.add_argument("--dash", action="store_true")
    a = ap.parse_args()
    if a.us:
        record_us()
    if a.kr:
        record_kr()
    if a.dash or not (a.us or a.kr):
        dash()
        print("dash 갱신")
