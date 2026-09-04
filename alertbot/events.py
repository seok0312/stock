# -*- coding: utf-8 -*-
"""경제 일정 수집 + 변동과의 연결 — '왜 움직였나'의 근거를 뉴스 대신 일정에서 찾는다.

왜 필요한가:
  뉴스는 사후 서술이라 근거가 약하다("~때문으로 풀이된다"). 반면 일정은
  **변동이 일어나기 전에 이미 알고 있던 사실**이고, 발표시각·예상치·실제치가
  숫자로 남아 인과를 검증할 수 있다. 종가베팅은 시가매도라 오버나이트
  노출이 필수인데, 그 구간에 무엇이 예정돼 있는지가 뉴스보다 중요하다.

소스:
  FXStreet 캘린더 API — actual/consensus/previous/ratioDeviation 을 모두 준다.
  investing.com 은 이 환경(로컬·서버 모두)에서 전 경로 403(WAF)이라 쓸 수 없다.
  ForexFactory 미러(nfs.faireconomy.media)는 열리지만 actual 이 없고 한국도 빠져
  '서프라이즈' 계산이 불가능해 제외했다.

확장:
  프로바이더 레지스트리 구조다. 소스를 추가하려면 @provider 로 함수 하나만 붙이면
  되고, 코드를 안 건드리고 넣으려면 data/events.json 에 항목을 적으면 된다.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
UTC = timezone.utc
HERE = os.path.dirname(os.path.abspath(__file__))
# 사용자가 직접 넣는 일정. 저장소에 같이 다니도록 모듈 옆에 둔다
# (data/ 는 .gitignore 대상이라 서버로 배포되지 않는다).
CUSTOM_PATH = os.environ.get("ALERTBOT_EVENTS") or os.path.join(HERE, "events_custom.json")

FXS = "https://calendar-api.fxstreet.com/en/api/v1/eventDates/{a}/{b}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      "Origin": "https://www.fxstreet.com", "Referer": "https://www.fxstreet.com/"}

COUNTRIES = ("US", "KR", "CN", "JP", "EMU")
VOLS = ("MEDIUM", "HIGH")          # LOW 는 가격을 거의 안 움직여 노이즈다
FLAG = {"US": "🇺🇸", "KR": "🇰🇷", "CN": "🇨🇳", "JP": "🇯🇵",
        "EMU": "🇪🇺", "DE": "🇩🇪", "GB": "🇬🇧"}

# ── 이벤트명 → (한글표기, 태그). 위에서부터 첫 매치를 쓴다.
#    태그가 링크의 유일한 기준이다. 새 지표가 나와도 태그만 맞으면 자동 연결된다.
NAME_MAP: list[tuple[str, str, tuple]] = [
    (r"Nonfarm Payroll",                "비농업고용",        ("고용", "통화정책")),
    (r"ADP Employment",                 "ADP 민간고용",      ("고용", "통화정책")),
    (r"Unemployment Rate",              "실업률",            ("고용",)),
    (r"Initial Jobless|Unemployment Claims", "신규 실업수당",  ("고용",)),
    (r"Continuing Jobless",             "연속 실업수당",      ("고용",)),
    (r"Challenger Job Cuts",            "감원 발표",          ("고용",)),
    (r"Average Hourly Earnings",        "시간당 임금",        ("고용", "물가")),
    (r"JOLTS",                          "구인건수",           ("고용",)),
    (r"Core.*Personal Consumption|Core PCE", "근원 PCE",      ("물가", "통화정책")),
    (r"Personal Consumption Expenditure", "PCE 물가",         ("물가", "통화정책")),
    (r"Consumer Price Index|^CPI",      "소비자물가",         ("물가", "통화정책")),
    (r"Producer Price Index|^PPI",      "생산자물가",         ("물가",)),
    (r"Import Price|Export Price",      "수출입물가",         ("물가", "무역")),
    (r"Interest Rate Decision|Rate Decision", "기준금리 결정", ("금리", "통화정책")),
    (r"FOMC|Fed's|Federal Reserve|Beige Book|Powell|Jackson Hole",
                                        "연준",              ("금리", "통화정책")),
    (r"Treasury.*Auction|Bond Auction", "국채 입찰",          ("금리",)),
    (r"ISM Manufacturing",              "ISM 제조업",         ("경기", "제조")),
    (r"ISM Services|ISM Non-Manufacturing", "ISM 서비스업",   ("경기", "서비스")),
    (r"Manufacturing PMI",              "제조업 PMI",         ("경기", "제조")),
    (r"Services PMI|Composite PMI",     "서비스 PMI",         ("경기", "서비스")),
    (r"Industrial Production|Industrial Output", "산업생산",   ("경기", "제조")),
    (r"Durable Goods|Factory Orders",   "내구재·공장주문",     ("경기", "제조")),
    (r"Retail Sales",                   "소매판매",           ("소비", "경기")),
    (r"Consumer Confidence|Consumer Sentiment", "소비자심리",  ("소비",)),
    (r"Gross Domestic Product|^GDP",    "GDP",               ("경기",)),
    (r"Trade Balance",                  "무역수지",           ("무역", "경기")),
    (r"Current Account",                "경상수지",           ("무역",)),
    (r"Crude Oil Inventories|EIA|API Weekly", "원유 재고",     ("원유",)),
    (r"OPEC",                           "OPEC",              ("원유",)),
    (r"Baker Hughes",                   "가동 시추기",         ("원유",)),
    (r"Housing Starts|Building Permits|Home Sales", "주택지표", ("경기", "금리")),
    (r"FX Reserves|Foreign Exchange Reserves", "외환보유액",   ("환율",)),
    (r"Productivity|Unit Labor Cost",   "생산성·노동비용",     ("고용", "물가")),
    (r"Participation Rate|Underemployment", "고용참가·불완전", ("고용",)),
    (r"ECB's|BoE's|BoJ's|BoK's|Lagarde|Ueda", "중앙은행 발언", ("금리", "통화정책")),
    (r"Money Supply|Loans",             "통화·대출",          ("금리",)),
    (r"Business Climate|Sentiment Index|ZEW|Ifo", "기업심리",  ("경기",)),
]

# 같은 지표의 MoM/YoY/QoQ 를 구분해 남기기 위한 접미사
_QUAL = re.compile(r"\((MoM|YoY|QoQ|Q/Q|M/M|Y/Y)\)", re.I)

# ── 태그 → 영향받는 시황 자산(quotes.INSTRUMENTS 의 이름)
ASSET_TAGS = {
    "오일":     {"원유", "경기", "무역"},
    "금":       {"금리", "물가", "통화정책", "환율"},
    "나스닥":   {"금리", "물가", "고용", "통화정책", "경기", "소비", "실적"},
    "코스피":   {"금리", "고용", "무역", "경기", "제조", "통화정책", "수급", "실적"},
    "비트코인": {"금리", "통화정책"},
    "코스닥":   {"금리", "고용", "경기", "제조", "통화정책", "수급", "실적"},
    "미국10Y":  {"금리", "물가", "통화정책", "고용"},
}

# ── 태그 → 한국 업종·테마 키워드. 업종명에 이 단어가 들어가면 연결한다.
SECTOR_TAGS = {
    "원유":     ("정유", "화학", "에너지", "가스", "조선"),
    "금리":     ("은행", "보험", "증권", "금융", "건설", "리츠", "부동산"),
    "물가":     ("음식료", "유통", "필수소비", "화장품"),
    "고용":     ("유통", "서비스"),
    "무역":     ("조선", "해운", "운송", "철강", "무역", "항공"),
    "제조":     ("반도체", "기계", "전기", "전자", "자동차", "철강", "부품"),
    "경기":     ("반도체", "철강", "화학", "기계", "조선", "해운"),
    "서비스":   ("인터넷", "소프트웨어", "게임", "미디어", "엔터"),
    "소비":     ("유통", "화장품", "의류", "음식료", "면세"),
    "통화정책": ("은행", "증권", "보험", "금융"),
    "환율":     ("항공", "여행", "수입", "정유"),
    "실적":     ("반도체", "전자", "부품", "IT", "소프트웨어"),
}

# 미국 실적: 시총이 이 이상이거나 아래 목록에 들면 한국 개장에 영향이 있다고 본다.
# 반도체·AI 체인은 시총이 기준에 못 미쳐도 삼성전자·SK하이닉스를 직접 움직인다.
EARN_MIN_CAP = 200e9
EARN_ALWAYS = {"NVDA", "AVGO", "TSM", "MU", "AMAT", "LRCX", "KLAC", "ASML",
               "INTC", "AMD", "ORCL", "DELL", "SMCI", "MRVL", "WDC", "STX"}
NASDAQ_EARN = "https://api.nasdaq.com/api/calendar/earnings?date={d}"

PROVIDERS: dict = {}


def provider(name):
    """소스 등록용 데코레이터. fn(start, end) -> [Event]"""
    def deco(fn):
        PROVIDERS[name] = fn
        return fn
    return deco


def _classify(name: str):
    """이벤트명 → (한글표기, 태그). 매칭 실패면 (None, 빈집합) — 원문을 그대로 쓴다."""
    for pat, kr, tags in NAME_MAP:
        if re.search(pat, name, re.I):
            m = _QUAL.search(name)
            if m:                       # 소매판매(MoM) / 소매판매(YoY) 를 따로 남긴다
                kr = f"{kr}({m.group(1).upper().replace('/', '')})"
            return kr, set(tags)
    return None, set()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@provider("fxstreet")
def _fxstreet(start: datetime, end: datetime) -> list:
    q = "?" + "".join(f"&volatilities={v}" for v in VOLS) \
            + "".join(f"&countries={c}" for c in COUNTRIES)
    url = FXS.format(a=start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     b=end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")) + q
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return []
        rows = r.json()
    except Exception:
        return []
    out = []
    for e in rows:
        try:
            when = datetime.strptime(e["dateUtc"][:19], "%Y-%m-%dT%H:%M:%S") \
                           .replace(tzinfo=UTC).astimezone(KST)
        except Exception:
            continue
        kr, tags = _classify(e.get("name") or "")
        out.append({
            "when": when, "country": e.get("countryCode") or "",
            "name": (e.get("name") or "").strip(), "name_kr": kr,
            "actual": _num(e.get("actual")), "consensus": _num(e.get("consensus")),
            "previous": _num(e.get("previous")), "unit": e.get("unit"),
            "vol": e.get("volatility") or "", "dev": _num(e.get("ratioDeviation")),
            "better": e.get("isBetterThanExpected"),
            "speech": bool(e.get("isSpeech")), "tags": tags, "src": "fxstreet",
        })
    return out


@provider("us_earnings")
def _us_earnings(start: datetime, end: datetime) -> list:
    """미국 대형주 실적. 나스닥 공식 캘린더(시총 포함)라 '중요한 것만' 거르기 쉽다.

    발표 시각은 pre-market / after-hours 구분만 주므로 KST 로 근사한다
    (장전 21:00 / 장후 다음날 05:30, 미 동부 서머타임 기준).
    한국 기업 실적은 이 캘린더에 없다 — events_custom.json 으로 넣으면 된다.
    """
    hdr = {"User-Agent": UA["User-Agent"], "Accept": "application/json, text/plain, */*",
           "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
    out, seen = [], set()
    d = start.date()
    while d <= end.date() and len(seen) < 9:
        seen.add(d)
        try:
            r = requests.get(NASDAQ_EARN.format(d=d.strftime("%Y-%m-%d")),
                             headers=hdr, timeout=20)
            rows = (r.json().get("data") or {}).get("rows") or [] if r.status_code == 200 else []
        except Exception:
            rows = []
        for x in rows:
            sym = (x.get("symbol") or "").strip().upper()
            cap = _num(str(x.get("marketCap") or "").replace("$", "").replace(",", ""))
            if sym not in EARN_ALWAYS and not (cap and cap >= EARN_MIN_CAP):
                continue
            t = x.get("time") or ""
            base = datetime.combine(d, datetime.min.time()).replace(tzinfo=KST)
            when = (base + timedelta(hours=21) if "pre-market" in t
                    else base + timedelta(days=1, hours=5, minutes=30))
            if not (start <= when <= end):
                continue
            eps = (x.get("epsForecast") or "").strip()
            out.append({
                "when": when, "country": "US",
                "name": f"{sym} 실적", "name_kr": f"{sym} 실적",
                "actual": None, "consensus": None, "previous": None, "unit": None,
                "vol": "HIGH", "dev": None, "better": None, "speech": False,
                "tags": {"실적"}, "src": "us_earnings",
                "note": f"EPS 예상 {eps}" if eps else None,
            })
        d += timedelta(days=1)
    return out


IPO_URL = "http://www.38.co.kr/html/fund/index.htm?o=nw"


@provider("kr_ipo")
def _kr_ipo(start: datetime, end: datetime) -> list:
    """국내 신규상장 일정 — 38커뮤니케이션 신규상장 표.

    상장일·공모가·공모가대비 등락률을 한 페이지에서 준다. 스팩은 뺀다
    (공모가 2,000원 고정의 페이퍼컴퍼니라 종가베팅 재료가 아니다).
    상장 완료 건은 네이버에서 현재가를 찾아 공모가 대비 몇 % 인지 계산한다.
    """
    try:
        r = requests.get(IPO_URL, headers={"User-Agent": UA["User-Agent"]}, timeout=15)
        r.encoding = "euc-kr"
        html = r.text
    except Exception:
        return []
    now = datetime.now(KST)
    out = []
    for tr in re.split(r"<tr", html):
        cells = [re.sub(r"&nbsp;?|\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 9 or not re.match(r"20\d\d/\d\d/\d\d", cells[1] or ""):
            continue
        name = cells[0]
        if "스팩" in name:
            continue
        try:
            when = datetime.strptime(cells[1], "%Y/%m/%d").replace(hour=9, tzinfo=KST)
        except ValueError:
            continue
        if not (start <= when <= end):
            continue
        po = _num(cells[4].replace(",", ""))
        e = {"when": when, "country": "KR", "name": f"{name} 신규상장",
             "name_kr": f"{name} 신규상장", "actual": None, "consensus": None,
             "previous": None, "unit": None, "vol": "HIGH", "dev": None,
             "better": None, "speech": False, "tags": set(), "src": "kr_ipo",
             "note": f"공모가 {po:,.0f}원" if po else None}
        if when <= now and po:
            pct, cur, base = _ipo_live_pct(name, po)
            if pct is None:            # 폴백: 38의 공모가대비, 그마저 없으면 시초/공모
                pct = _num((cells[5] or "").replace("%", ""))
                base = "공모가"
                if pct is None:
                    pct = _num((cells[7] or "").replace("%", ""))
                    base = "시초가/공모가"
            if pct is not None:
                e["actual"] = pct
                e["verdict_str"] = f"{base} 대비 {pct:+.1f}%"
                e["nums_str"] = (f"공모가 {po:,.0f}원 → 현재 {cur:,.0f}원"
                                 if cur else f"공모가 {po:,.0f}원")
                e["note"] = None
        out.append(e)
    return out


def _ipo_live_pct(name: str, po: float):
    """네이버에서 종목코드를 찾아 현재가/공모가 등락률. 실패 시 (None, None, None)."""
    try:
        j = requests.get("https://ac.stock.naver.com/ac",
                         params={"q": name, "target": "stock"},
                         headers={"User-Agent": UA["User-Agent"]}, timeout=10).json()
        item = next((x for x in j.get("items") or []
                     if x.get("name") == name and x.get("nationCode") == "KOR"), None)
        if not item:
            return None, None, None
        b = requests.get(f"https://m.stock.naver.com/api/stock/{item['code']}/basic",
                         headers={"User-Agent": UA["User-Agent"],
                                  "Referer": "https://m.stock.naver.com/"}, timeout=10).json()
        cur = _num(str(b.get("closePrice") or "").replace(",", ""))
        if not cur or not po:
            return None, None, None
        return (cur / po - 1) * 100, cur, "공모가"
    except Exception:
        return None, None, None


@provider("kr_expiry")
def _kr_expiry(start: datetime, end: datetime) -> list:
    """코스피200 선물·옵션 만기일 — 외부 소스 없이 규칙으로 계산한다.

    옵션만기는 매월 둘째 목요일, 3·6·9·12월은 선물까지 겹치는 동시만기(네 마녀).
    만기일 장 막판은 프로그램 청산이 몰려 종가가 흔들린다. 종가베팅 입장에선
    뉴스로는 절대 안 잡히는데 날짜만으로 100% 미리 아는 리스크다.
    """
    out = []
    d = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while d <= end:
        first = d.replace(day=1)
        # 그 달 첫 목요일 + 7일 = 둘째 목요일
        thu = first + timedelta(days=(3 - first.weekday()) % 7 + 7)
        if start <= thu.replace(hour=15, minute=20) <= end:
            quad = thu.month in (3, 6, 9, 12)
            out.append({
                "when": thu.replace(hour=15, minute=20), "country": "KR",
                "name": "선물·옵션 동시만기(네 마녀)" if quad else "옵션 만기",
                "name_kr": "선물·옵션 동시만기(네 마녀)" if quad else "옵션 만기",
                "actual": None, "consensus": None, "previous": None, "unit": None,
                "vol": "HIGH" if quad else "MEDIUM", "dev": None, "better": None,
                "speech": False, "tags": {"수급"},
                "note": "장 막판 프로그램 청산 물량 주의", "src": "kr_expiry",
            })
        # 다음 달 1일로
        d = (first + timedelta(days=32)).replace(day=1)
    return out


@provider("custom")
def _custom(start: datetime, end: datetime) -> list:
    """사용자가 직접 넣는 일정. data/events.json (없으면 조용히 건너뜀).

    형식: [{"when": "2026-09-11 15:30", "name": "9월 옵션만기",
            "country": "KR", "vol": "HIGH", "tags": ["수급"], "note": "..."}]
    when 은 KST. tags 를 적으면 자동으로 자산·업종에 연결된다.
    """
    if not os.path.exists(CUSTOM_PATH):
        return []
    try:
        with open(CUSTOM_PATH, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return []
    if isinstance(rows, dict):          # {"_설명": ..., "events": [...]} 형태도 허용
        rows = rows.get("events") or []
    out = []
    for e in rows if isinstance(rows, list) else []:
        try:
            s = str(e["when"]).strip()
            fmt = "%Y-%m-%d %H:%M" if " " in s else "%Y-%m-%d"
            when = datetime.strptime(s, fmt).replace(tzinfo=KST)
        except Exception:
            continue
        if not (start <= when <= end):
            continue
        kr, tags = _classify(e.get("name") or "")
        out.append({
            "when": when, "country": e.get("country") or "KR",
            "name": e.get("name") or "", "name_kr": e.get("name_kr") or kr,
            "actual": _num(e.get("actual")), "consensus": _num(e.get("consensus")),
            "previous": _num(e.get("previous")), "unit": e.get("unit"),
            "vol": (e.get("vol") or "HIGH").upper(), "dev": None, "better": None,
            "speech": False, "tags": set(e.get("tags") or []) | tags,
            "note": e.get("note"), "src": "custom",
        })
    return out


def _rank(e):
    """중복 후보 중 무엇을 남길지. 수치가 있는 헤드라인 > 중요도 > 이름 길이."""
    return (e.get("actual") is not None or e.get("consensus") is not None,
            e.get("vol") == "HIGH", -len(e.get("name") or ""))


def collect(start: datetime, end: datetime, only=None) -> list:
    """등록된 모든 프로바이더에서 [start, end] 구간 일정을 모아 시간순 정렬.

    FXStreet 는 한 발표의 하위지표를 개별 행으로 준다(ISM 서비스업이 헤드라인 +
    고용/신규주문/가격 4행). 브리핑에는 헤드라인만 필요하므로
    (시각, 표기명)이 같으면 수치가 있는 쪽 하나만 남긴다.
    """
    out = []
    for name, fn in PROVIDERS.items():
        if only and name not in only:
            continue
        try:
            out.extend(fn(start, end) or [])
        except Exception:
            continue
    best = {}
    for e in out:
        k = (e["when"], e.get("name_kr") or e.get("name"), e.get("country"))
        if k not in best or _rank(e) > _rank(best[k]):
            best[k] = e
    return sorted(best.values(), key=lambda e: e["when"])


def label(e) -> str:
    """'🇺🇸 ISM 서비스업' 처럼 한 줄 표기용 이름.

    연설은 누가 말하느냐가 핵심이라 번역명 대신 원문(발언자 포함)을 쓴다.
    """
    nm = e.get("name") if e.get("speech") else (e.get("name_kr") or e.get("name"))
    return f"{FLAG.get(e['country'], e['country'])} {nm or ''}"


def verdict_text(e) -> str:
    """'하회(서프라이즈)' 처럼 예상 대비 판정만. 판정할 수 없으면 빈 문자열."""
    if e.get("verdict_str"):               # 신규상장처럼 자체 판정문을 가진 이벤트
        return e["verdict_str"]
    if e.get("dev") is None or e.get("actual") is None:
        return ""
    v = "상회" if e["dev"] > 0 else ("하회" if e["dev"] < 0 else "부합")
    return v + ("(서프라이즈)" if abs(e["dev"]) >= 0.5 else "")


def numbers_text(e) -> str:
    """'실제 38 / 예상 47' — 수치만. 제목 줄이 길어지지 않게 분리해 쓴다."""
    if "nums_str" in e:                    # 자체 수치문(예: 공모가 → 현재가)
        return e.get("nums_str") or ""
    u = e.get("unit") or ""
    fmt = lambda v: f"{v:g}{u}" if v is not None else None
    a, c = fmt(e.get("actual")), fmt(e.get("consensus"))
    if a is None:
        return f"예상 {c}" if c else ""
    return f"실제 {a} / 예상 {c}" if c else f"실제 {a}"


def value_text(e) -> str:
    """'54.6 (예상 55.2 · 하회)' — 실제치가 없으면 예상치만."""
    u = e.get("unit") or ""
    fmt = lambda v: f"{v:g}{u}" if v is not None else None
    a, c = fmt(e.get("actual")), fmt(e.get("consensus"))
    if a is None:
        return f"예상 {c}" if c else ""
    if c is None:
        return f"실제 {a}"
    tail = ""
    if e.get("dev") is not None:
        tail = " · " + ("상회" if e["dev"] > 0 else ("하회" if e["dev"] < 0 else "부합"))
        if abs(e["dev"]) >= 0.5:
            tail += "(서프라이즈)"
    return f"실제 {a} / 예상 {c}{tail}"


def link_assets(e, rows) -> list:
    """이 일정이 설명할 수 있는 시황 자산 [(이름, 등락률)]. 유의미 변동만."""
    if not e.get("tags"):
        return []
    out = []
    for r in rows or []:
        if r.get("chg_pct") is None or not r.get("significant"):
            continue
        if e["tags"] & ASSET_TAGS.get(r["name"], set()):
            out.append((r["name"], r["chg_pct"]))
    return out


def link_sectors(e, names) -> list:
    """이 일정과 태그가 맞는 업종·테마 이름들."""
    if not e.get("tags") or not names:
        return []
    words = set()
    for t in e["tags"]:
        words |= set(SECTOR_TAGS.get(t, ()))
    out, seen = [], set()
    for n in names:                     # 업종과 테마에 같은 이름이 있어 중복 제거
        if n not in seen and any(w in n for w in words):
            seen.add(n); out.append(n)
    return out


def split(events, win_start, win_end, now=None):
    """(창 안에서 발표된 것, 앞으로 예정된 것)."""
    now = now or datetime.now(KST)
    done = [e for e in events if win_start <= e["when"] <= win_end]
    ahead = [e for e in events if e["when"] > max(now, win_end)]
    return done, ahead


def _next_open(now: datetime) -> datetime:
    """다음 한국 정규장 시가(09:00). 종가베팅은 시가매도라 여기까지가 노출 구간이다."""
    o = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now >= o:
        o += timedelta(days=1)
    while o.weekday() >= 5:            # 주말이면 월요일로
        o += timedelta(days=1)
    return o


def stars(e) -> str:
    return "***" if e.get("vol") == "HIGH" else "**"


def brief(events, win, quote_rows=None, sector_names=None, now=None,
          max_done: int = 3, max_ahead: int = 5, only_high: bool = True) -> dict:
    """렌더러가 그대로 찍을 수 있는 형태로 가공.

    done  — 변동폭 창 안에서 발표된 일정 + 그것이 설명하는 자산·업종
    ahead — 앞으로의 일정. 다음 시가(09:00)까지는 오버나이트 노출이라 따로 표시한다.

    only_high: 중요도 최상(★★★)만 남긴다. 중간 지표까지 다 실으면 줄이 길어져
    결국 아무도 안 읽는다 — 없는 것과 같아진다. 수집은 그대로 하고 표시만 좁힌다.
    """
    now = now or datetime.now(KST)
    # 발표 완료는 변동폭 창이 아니라 최근 24시간으로 본다. 창(예: 08:00~14:30)만
    # 보면 전날 밤 미국 지표가 통째로 빠지는데, 그게 한국 개장의 주된 근거다.
    done_raw = [e for e in events if now - timedelta(hours=24) <= e["when"] <= now]
    _, ahead_raw = split(events, win["start"], win["end"], now)
    open_at = _next_open(now)
    try:
        import reactions
        rrows = reactions.load_all()
    except Exception:
        reactions, rrows = None, []

    done = []
    for e in sorted(done_raw, key=lambda x: x["when"], reverse=True):
        if only_high and e.get("vol") != "HIGH":
            continue
        if e.get("actual") is None and not e.get("note"):
            continue          # 수치도 메모도 없으면 브리핑에 담을 내용이 없다
        # 창 안의 발표만 시황 등락률과 나란히 둔다. 20시간 전 지표를 오늘 창의
        # 등락률과 연결하면 인과처럼 보이지만 근거가 없다.
        in_win = win["start"] <= e["when"] <= win["end"]
        done.append({"when": e["when"], "label": label(e), "stars": stars(e),
                     "verdict": verdict_text(e), "nums": numbers_text(e),
                     "assets": link_assets(e, quote_rows) if in_win else [],
                     "react": reactions.react_text(e, rows=rrows) if reactions else None,
                     "note": e.get("note")})
        if len(done) >= max_done:
            break
    done.sort(key=lambda d: d["when"])

    # 예정 목록은 자리가 몇 줄뿐이라 '가까운 순'으로 채우면 유럽 중간지표가
    # 다 차지하고 정작 미국 고용지표가 밀린다. 중요도로 먼저 거른 뒤 시간순 정렬.
    # 지표는 48시간, 실적은 7일까지 본다. 실적 날짜는 훨씬 전부터 확정돼 있고
    # '이번 주에 엔비디아가 있다'는 정보 자체가 포지션 크기를 바꾸기 때문이다.
    ind_end = now + timedelta(hours=48)
    earn_end = now + timedelta(days=7)
    cand, n_earn = [], 0
    for e in ahead_raw:
        high = e.get("vol") == "HIGH"
        if only_high and not high:
            continue
        if e.get("country") not in ("US", "KR", "CN"):   # 코스피에 직접 닿는 곳만
            continue
        if e.get("src") in ("us_earnings", "kr_ipo"):
            if e["when"] > earn_end or n_earn >= 3:
                continue
            n_earn += 1
        elif e["when"] > ind_end:
            continue
        cand.append((not high, e["when"], e["when"] <= open_at, e))
    # 같은 시각·같은 나라는 한 번의 발표다(고용보고서 = 비농업고용 + 시간당임금 + 실업률).
    # 넷을 다 실으면 네 줄이 같은 사건으로 채워지므로 헤드라인 하나만 남긴다.
    # 헤드라인은 (MOM)/(YOY) 꼬리표가 없는 쪽으로 본다.
    def _headline(c):
        e = c[3]
        return ("(" not in (e.get("name_kr") or ""),   # 꼬리표 없는 헤드라인 우선
                not c[0])                              # 그다음 중요도
    head = {}
    for c in cand:
        k = (c[3]["when"], c[3].get("country"))
        if k not in head or _headline(c) > _headline(head[k]):
            head[k] = c
    cand = list(head.values())
    cand.sort(key=lambda x: (x[0], x[1]))          # HIGH 먼저, 그 안에서 시간순
    ahead = []
    for _, _, ov, e in cand[:max_ahead]:
        ahead.append({"when": e["when"], "label": label(e), "stars": stars(e),
                      "value": value_text(e), "overnight": ov, "note": e.get("note"),
                      "stat": reactions.summary_for(e, rows=rrows) if reactions else None})
    ahead.sort(key=lambda a: a["when"])            # 표시는 다시 시간순

    return {"done": done, "ahead": ahead, "n_done": len(done_raw),
            "open_at": open_at, "win": (win["start"], win["end"])}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(KST)
    ev = collect(now - timedelta(hours=12), now + timedelta(hours=36))
    print(f"일정 {len(ev)}건 (프로바이더: {', '.join(PROVIDERS)})\n")
    for e in ev:
        mark = "완료" if e["when"] <= now else "예정"
        star = "★" * (3 if e["vol"] == "HIGH" else 2)
        print(f"  {e['when']:%m-%d %H:%M} [{mark}] {star:<3} {label(e):<22} "
              f"{value_text(e):<34} tags={','.join(sorted(e['tags'])) or '-'}")
