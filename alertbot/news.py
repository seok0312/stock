# -*- coding: utf-8 -*-
"""유의미 변동(±0.5%) 자산의 원인 뉴스 수집.

1차 소스(사용자 지정, 09-11): 네이버 증권뉴스 + 스탁허브 — 두 곳을 합친 풀에서
키워드/티커 매칭으로 고른다. 매칭이 없으면 구글 뉴스 RSS 로 폴백.
  · 네이버 m.stock front-api: category(mainnews 주요/flashnews 속보) + worldnews(해외,
    종목 태깅). pageSize<=60, 시간필터 없음(datetime 으로 클라이언트 필터), 무인증.
    국내 기사 링크는 officeId+articleId 로 n.news.naver.com 조립.
  · 스탁허브 /api/news?tab=all&limit=100 — AI 번역 애그리게이터(해외속보/국내/공시).
    해외속보의 link 는 소스 홈뿐이라 그 경우 stockhub.kr/news/{id} 상세로 링크.
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
    "코스닥":   "코스닥 OR 코스닥 급등",
    "SK하이닉스": "SK하이닉스",
    "삼성전자": "삼성전자 주가",
    "미국10Y":  "미국 국채금리 OR 미국채 10년물",
    "비트코인": "비트코인 시세",
}


# ── 1차 소스: 네이버 증권뉴스 + 스탁허브 풀 ─────────────────────
NAVER_CAT = "https://m.stock.naver.com/front-api/news/category"
NAVER_WORLD = "https://m.stock.naver.com/front-api/news/worldnews"
STOCKHUB = "https://stockhub.kr/api/news"

# 자산명 → 풀 제목 매칭 키워드 (QUERY 와 별개 — 풀은 이미 한국어 기사라 단순 부분일치)
KEYWORDS = {
    "오일":     ("유가", "WTI", "원유", "OPEC"),
    "금":       ("금값", "금 선물", "국제 금", "금 가격"),
    "나스닥":   ("나스닥", "뉴욕증시", "미 증시", "미국 증시", "S&P"),
    "코스피":   ("코스피", "한국 증시", "국내 증시"),
    "코스닥":   ("코스닥",),
    "SK하이닉스": ("SK하이닉스", "하이닉스"),
    "삼성전자": ("삼성전자",),
    "미국10Y":  ("국채금리", "국채 금리", "10년물", "연준", "금리 인상", "금리 인하"),
    "비트코인": ("비트코인",),
}

_pool_cache = {"ts": None, "items": []}


def _dt14(s):
    try:
        return datetime.strptime(str(s), "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


def _fetch_pool():
    """네이버(주요+속보+해외) + 스탁허브(all) 합본. 슬롯당 1회만 (120초 캐시)."""
    now = datetime.now(KST)
    if _pool_cache["ts"] and (now - _pool_cache["ts"]).total_seconds() < 120:
        return _pool_cache["items"]
    items = []
    for cat in ("mainnews", "flashnews"):
        try:
            r = requests.get(NAVER_CAT, params={"category": cat, "pageSize": 60, "page": 1},
                             headers=UA, timeout=15).json()
            for it in r.get("result") or []:
                oid, aid = it.get("officeId"), it.get("articleId")
                items.append({
                    "title": (it.get("title") or "").strip(),
                    "url": f"https://n.news.naver.com/mnews/article/{oid}/{aid}",
                    "source": (it.get("officeName") or "").strip(),
                    "published": _dt14(it.get("datetime")),
                    "tickers": (), "main": cat == "mainnews"})
        except Exception:
            pass
    try:
        r = requests.get(NAVER_WORLD, params={"pageSize": 60, "page": 1},
                         headers=UA, timeout=15).json()
        for it in r.get("result") or []:
            oid, aid = it.get("officeId"), it.get("articleId")
            tks = tuple((x.get("itemName") or "").upper()
                        for x in it.get("relatedItems") or [])
            items.append({
                "title": (it.get("title") or "").strip(),
                "url": f"https://m.stock.naver.com/investment/news/worldnews/{oid}/{aid}",
                "source": (it.get("officeName") or "").strip(),
                "published": _dt14(it.get("datetime")),
                "tickers": tks, "main": False})
    except Exception:
        pass
    try:
        r = requests.get(STOCKHUB, params={"page": 1, "limit": 100, "tab": "all"},
                         headers=UA, timeout=15).json()
        for it in r.get("data") or []:
            lk = (it.get("link") or "").strip()
            # 해외속보류는 link 가 기사 아닌 소스 홈/트위터 계정 → 스탁허브 상세로
            if (not lk.startswith("http")
                    or ("x.com" in lk or "twitter.com" in lk) and "/status/" not in lk
                    or lk.rstrip("/").endswith(("financialjuice.com", "x.com"))):
                lk = f"https://stockhub.kr/news/{it.get('id')}"
            ts = it.get("timestamp")
            pub = (datetime.fromtimestamp(ts, tz=KST) if ts else None)
            items.append({
                "title": (it.get("title") or "").strip(),
                "url": lk,
                "source": (it.get("source") or "스탁허브").strip(),
                "published": pub,
                "tickers": tuple((t or "").upper() for t in it.get("tickers") or []),
                "main": it.get("importance") == "high"})
    except Exception:
        pass
    # 제목 앞부분 기준 중복 제거 (네이버-스탁허브 간 같은 기사)
    seen, out = set(), []
    for it in items:
        if not it["title"] or any(w in it["title"] for w in _SPAM):
            continue
        key = it["title"][:24]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    _pool_cache.update(ts=now, items=out)
    return out


def _pool_match(keywords=(), tickers=(), limit=2, start=None, end=None):
    """풀에서 창 안 + (제목 키워드 or 티커 교집합) 기사 선별. 주요뉴스 우선, 최신순."""
    tset = {t.upper() for t in tickers or ()}
    hits = []
    for it in _fetch_pool():
        p = it["published"]
        if p is None:
            continue
        if start is not None and p < start:
            continue
        if end is not None and p > end:
            continue
        if (keywords and any(k in it["title"] for k in keywords)) \
                or (tset and tset & set(it["tickers"])):
            hits.append(it)
    hits.sort(key=lambda x: x["published"], reverse=True)
    hits.sort(key=lambda x: not x["main"])          # 안정 정렬 → 주요뉴스 먼저, 그 안은 최신순
    return [{"title": h["title"], "url": h["url"], "source": h["source"],
             "published": h["published"]} for h in hits[:limit]]


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


def _query_hours(start, hours):
    """구글 when: 파라미터는 '지금부터 N시간 전'이라 창의 시작까지 덮어야 한다.
    알람이 늦게 돌면 창 끝이 이미 과거이므로 now 기준으로 잡고 1시간 여유를 준다."""
    if start is None:
        return max(1, int(hours))
    return max(1, int((datetime.now(KST) - start).total_seconds() // 3600) + 1)


def _pick(root, limit, start=None, end=None, spam_filter=False):
    """RSS 아이템 → [{title,url,source,published}]. 창이 주어지면 그 안의 기사만.

    창 밖 기사를 넣으면 '이 변동의 원인'이 아닌 기사를 원인처럼 보여주게 된다.
    발행시각을 못 읽은 항목은 검증이 안 되므로 창이 있을 때는 버린다.
    """
    out = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = _parse_pubdate(item.findtext("pubDate") or "")
        src = item.findtext("{http://news.google.com}source") or ""
        if not src and " - " in title:            # "제목 - 언론사" 형태 분리
            title, src = title.rsplit(" - ", 1)
        if spam_filter and any(w in title for w in _SPAM):
            continue
        if start is not None or end is not None:
            if pub is None:
                continue
            if start is not None and pub < start:
                continue
            if end is not None and pub > end:
                continue
        out.append({"title": title, "url": link, "source": src.strip(),
                    "published": pub})
        if len(out) >= limit:
            break
    return out


def _rss(q, hours):
    url = RSS.format(q=quote_plus(f"{q} when:{max(1, int(hours))}h"))
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        return ET.fromstring(r.content)
    except Exception:
        return None


def fetch_news(asset: str, hours: int = 12, limit: int = 3, start=None, end=None):
    """[{title, url, source, published}] — 네이버·스탁허브 풀 우선, 없으면 구글 RSS."""
    if start is None and end is None:
        start = datetime.now(KST) - timedelta(hours=hours)
    hits = _pool_match(KEYWORDS.get(asset) or (asset,), limit=limit,
                       start=start, end=end)
    if hits:
        return hits
    q = QUERY.get(asset)
    if not q:
        return []
    root = _rss(q, _query_hours(start, hours))
    if root is None:
        return []
    return _pick(root, limit, start, end)


def news_for_window(win, limit: int = 2):
    """변동폭 결과에서 significant 자산만 뉴스 수집. {자산명: [...]}

    검색 구간은 변동폭 계산 구간과 같되 최근 24시간으로 자른다
    (예: 14:30 알람 → 08:00~14:30, 월요일 06:00 알람 → 58시간이 아니라 최근 24시간).

    키는 자산명 그대로다 — 시황 목록 아래에 자산별로 묶어 링크를 붙이므로
    렌더러가 quotes 행의 name 으로 바로 찾을 수 있어야 한다.
    """
    # 24시간 상한. 앵커가 08/20 두 개뿐이라 연휴 뒤엔 구간이 58시간까지 벌어지는데,
    # 그만큼 오래된 기사는 오늘 변동의 원인이 아니라 노이즈다.
    floor = datetime.now(KST) - timedelta(hours=24)
    start = max(win["start"], floor)
    out = {}
    for r in list(win["rows"]) + list(win.get("key_stocks") or []):
        if not r["significant"]:
            continue
        items = fetch_news(r["name"], limit=limit,
                           start=start, end=win["end"])
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
               tickers=None, start=None, end=None):
    """섹터·업종·테마 이름으로 원인 뉴스 검색.

    kind="us": 섹터명만으로 검색하면 한국 기사가 섞이므로(예: '소재' → 색조·소재주)
               주도주 회사명 한글표기를 우선 검색어로 쓴다.
    kind="kr": 업종/테마명 그대로.
    """
    if start is None and end is None:
        start = datetime.now(KST) - timedelta(hours=hours)
    if kind == "us":
        # 풀 우선: 스탁허브 해외뉴스는 미국 티커 태깅, 네이버 해외뉴스도 relatedItems 보유
        kws = tuple(n for n in (US_TICKER_KR.get(t) for t in tickers or []) if n)
        hits = _pool_match(kws or (name,), tickers=tickers or (), limit=limit,
                           start=start, end=end)
        if hits:
            return hits
        names = list(kws)[:2]
        q = " OR ".join(names) if names else US_SECTOR_Q.get(name, f"미국 {name}주")
    else:
        hits = _pool_match((name,), limit=limit, start=start, end=end)
        if hits:
            return hits
        q = f"{name} 주가 OR {name} 급등"
    root = _rss(q, _query_hours(start, hours))
    if root is None:
        return []
    return _pick(root, limit, start, end, spam_filter=True)
