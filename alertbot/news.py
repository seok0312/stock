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


def news_for_window(win, hours: int | None = None, limit: int = 2):
    """변동폭 결과에서 significant 자산만 뉴스 수집. {자산명: [...]}

    키는 자산명 그대로다 — 시황 각 줄 바로 아래에 링크를 붙이므로
    렌더러가 quotes 행의 name 으로 바로 찾을 수 있어야 한다.
    """
    if hours is None:
        hours = max(2, round((win["end"] - win["start"]).total_seconds() / 3600))
    out = {}
    for r in win["rows"]:
        if not r["significant"]:
            continue
        items = fetch_news(r["name"], hours=hours, limit=limit)
        if items:
            out[r["name"]] = items
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


# 미국 티커 → 한글 통용명. 섹터명("소재")으로 검색하면 한국 기사가 섞이므로
# 주도주 회사명으로 검색해야 정확한 원인 기사가 잡힌다.
US_TICKER_KR = {
    "AAPL":"애플","MSFT":"마이크로소프트","NVDA":"엔비디아","AVGO":"브로드컴","ORCL":"오라클",
    "CRM":"세일즈포스","AMD":"AMD","ADBE":"어도비","CSCO":"시스코","ACN":"액센츄어",
    "TXN":"텍사스인스트루먼트","NOW":"서비스나우","GOOGL":"구글","META":"메타","NFLX":"넷플릭스",
    "DIS":"디즈니","CMCSA":"컴캐스트","TMUS":"T모바일","VZ":"버라이즌","T":"AT&T","EA":"EA",
    "AMZN":"아마존","TSLA":"테슬라","HD":"홈디포","MCD":"맥도날드","NKE":"나이키",
    "SBUX":"스타벅스","LOW":"로우스","BKNG":"부킹홀딩스","TJX":"TJX","GM":"제너럴모터스","F":"포드",
    "WMT":"월마트","PG":"P&G","KO":"코카콜라","PEP":"펩시코","COST":"코스트코","PM":"필립모리스",
    "MO":"알트리아","MDLZ":"몬델리즈","CL":"콜게이트","KMB":"킴벌리클라크",
    "XOM":"엑슨모빌","CVX":"셰브론","COP":"코노코필립스","SLB":"슐룸베르거","EOG":"EOG리소시스",
    "PSX":"필립스66","MPC":"마라톤페트롤리엄","VLO":"발레로","OXY":"옥시덴탈","HAL":"할리버튼","DVN":"데본에너지",
    "JPM":"JP모건","V":"비자","MA":"마스터카드","BAC":"뱅크오브아메리카","WFC":"웰스파고",
    "GS":"골드만삭스","MS":"모건스탠리","SPGI":"S&P글로벌","AXP":"아메리칸익스프레스","C":"씨티그룹","BLK":"블랙록",
    "LLY":"일라이릴리","UNH":"유나이티드헬스","JNJ":"존슨앤드존슨","ABBV":"애브비","MRK":"머크",
    "TMO":"서모피셔","ABT":"애보트","PFE":"화이자","AMGN":"암젠","BMY":"BMS","GILD":"길리어드",
    "GE":"GE","CAT":"캐터필러","RTX":"RTX","UNP":"유니온퍼시픽","HON":"허니웰","BA":"보잉",
    "LMT":"록히드마틴","UPS":"UPS","DE":"디어","ETN":"이튼","EMR":"에머슨",
    "LIN":"린데","SHW":"셔윈윌리엄스","APD":"에어프로덕츠","ECL":"에코랩","FCX":"프리포트",
    "NEM":"뉴몬트","DOW":"다우","NUE":"뉴코","VMC":"벌컨머터리얼즈","MLM":"마틴마리에타",
    "NEE":"넥스트에라","SO":"서던컴퍼니","DUK":"듀크에너지","CEG":"컨스텔레이션에너지",
    "AEP":"AEP","SRE":"셈프라","D":"도미니언","EXC":"엑셀론","XEL":"엑셀에너지","ED":"콘에디슨",
    "PLD":"프로로지스","AMT":"아메리칸타워","EQIX":"에퀴닉스","WELL":"웰타워","SPG":"사이먼프로퍼티",
    "O":"리얼티인컴","CCI":"크라운캐슬","PSA":"퍼블릭스토리지","DLR":"디지털리얼티","VICI":"비치프로퍼티",
    "TSM":"TSMC","ASML":"ASML","AMAT":"어플라이드머티어리얼즈","LRCX":"램리서치","KLAC":"KLA",
    "MU":"마이크론","INTC":"인텔","ADI":"아나로그디바이스","QCOM":"퀄컴",
    "ALB":"앨버말","ENPH":"엔페이즈","PLUG":"플러그파워","FSLR":"퍼스트솔라","QS":"퀀텀스케이프",
    "NOC":"노스럽그러먼","GD":"제너럴다이내믹스","LHX":"L3해리스","HII":"헌팅턴잉걸스",
    "TDG":"트랜스다임","LDOS":"레이도스",
    "VRTX":"버텍스","REGN":"리제네론","MRNA":"모더나","BIIB":"바이오젠","ALNY":"알닐람",
    "INCY":"인사이트","BMRN":"바이오마린","SRPT":"사렙타",
    "SEDG":"솔라엣지","RUN":"선런","NXT":"넥스트래커","ARRY":"어레이테크",
    "ZIM":"ZIM","MATX":"매슨","KEX":"커비","GNK":"제네코","SBLK":"스타벌크",
}

# 광고·도박 스팸 헤드라인 배제
_SPAM = ("카지노", "토토", "바카라", "슬롯", "베팅사이트", "먹튀", "출장", "대출상담")

# 섹터·테마 이름으로 원인 뉴스를 찾을 때 쓰는 검색어 보정.
# 그냥 '에너지'로 검색하면 엉뚱한 기사가 섞여서 맥락어를 붙인다.
US_SECTOR_Q = {
    "에너지": "미국 에너지주 OR 유가 정유주", "반도체": "미국 반도체주 OR 엔비디아",
    "기술": "미국 기술주 OR 나스닥 기술주", "헬스케어": "미국 제약주 OR 헬스케어주",
    "바이오": "미국 바이오주", "금융": "미국 은행주 OR 금융주",
    "산업재": "미국 산업재 OR 방산주", "소재": "미국 소재주 OR 원자재주",
    "유틸리티": "미국 유틸리티주", "리츠": "미국 리츠",
    "커뮤니케이션": "미국 빅테크 OR 구글 메타", "경기소비재": "미국 소비주 OR 테슬라",
    "필수소비재": "미국 소비재주", "2차전지": "미국 2차전지 OR 배터리주",
    "방산": "미국 방산주", "태양광": "미국 태양광주", "조선/해운": "해운 운임",
}


def topic_news(name: str, kind: str = "kr", hours: int = 24, limit: int = 1,
               tickers=None):
    """섹터·업종·테마 이름으로 원인 뉴스 검색.

    kind="us": 섹터명만으로 검색하면 한국 기사가 섞이므로(예: '소재' → 색조·소재주)
               주도주 회사명 한글표기를 우선 검색어로 쓴다.
    kind="kr": 업종/테마명 그대로.
    """
    if kind == "us":
        names = [US_TICKER_KR.get(t) for t in (tickers or [])]
        names = [n for n in names if n][:2]
        q = " OR ".join(names) if names else US_SECTOR_Q.get(name, f"미국 {name}주")
    else:
        q = f"{name} 주가 OR {name} 급등"
    url = RSS.format(q=quote_plus(f"{q} when:{max(1, hours)}h"))
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        src = item.findtext("{http://news.google.com}source") or ""
        if not src and " - " in title:
            title, src = title.rsplit(" - ", 1)
        if any(w in title for w in _SPAM):      # 도박·광고 스팸 제외
            continue
        out.append({"title": title, "url": link, "source": src.strip()})
        if len(out) >= limit:
            break
    return out
