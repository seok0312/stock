# -*- coding: utf-8 -*-
"""시황 수집 — 코스피·코스닥은 장중엔 실제 지수, 장외엔 퍼페추얼 프록시.

구간(앵커 09:00 / 15:30):
  장중 창(09:00~15:30 안)   → 네이버 지수(시가 대비 현재가). 실제 코스피다.
  장외 창(15:30~익일 09:00) → 바이낸스 EWY 퍼페추얼. 지수는 밤에 안 움직이므로
                              24시간 거래되는 프록시로 밤사이 변동을 잰다.
  오일·금·나스닥·비트코인    → 항상 퍼페추얼(24시간 연속이라 창 계산이 끊기지 않음)
  미국10Y                   → 네이버 채권(실시간). 창과 무관하게 '전일比'로 표시 —
                              금리의 임의 시점 과거값을 주는 무료 소스가 없다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime

import ccxt
import requests

KST = timezone(timedelta(hours=9))

# ★(유의미 변동) = |변동| ≥ Z_STAR × σ. σ는 자산별 최근 60거래일 일변동 표준편차
# (일 1회 캐시). 고정 1% 룰은 자산별 발화율이 35~85%로 제각각이라 폐기(09-11 분석).
# 퍼프 '창' 변동엔 sqrt(창시간/24h) 스케일을 적용해 짧은 창을 과대평가하지 않는다.
Z_STAR = 1.2
SIGMA_FALLBACK_PCT = 2.0      # σ 소스가 없을 때(신규 자산 등)의 보수적 기본값
SIGNIFICANT_PCT = 1.0         # (구) 고정 문턱 — z 소스 실패 시 최후 폴백용으로만 유지
SIGNIFICANT_BP = 5.0
_SIGMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "sigma_cache.json")

# 퍼페추얼 심볼 목록 — reactions.py(지표 반응 측정)도 이 목록을 쓴다. 순서 유지.
INSTRUMENTS = [
    ("오일",     "CL/USDT:USDT",  "CLUSDT.P",  2),
    ("금",       "XAU/USDT:USDT", "XAUUSDT.P", 2),
    ("나스닥",   "QQQ/USDT:USDT", "QQQUSDT.P", 2),
    ("코스피",   "EWY/USDT:USDT", "EWYUSDT.P", 2),
    ("비트코인", "BTC/USDT:USDT", "BTCUSDT.P", 0),
]

# 시황에 표시할 행. src: perp=퍼페추얼 / kr=지수(장중)+프록시(장외) / bond=금리
# main: 본장 시세 소스(09-11 개편) — 표시는 본장 전일比, 괄호에 (Perp. 창변동%).
#   mkidx = api.stock.naver.com/marketindex/{path} / widx = /index/{code}/basic
DISPLAY = [
    {"name": "미국10Y",  "src": "bond", "code": "US10YT=RR"},
    {"name": "오일",     "src": "perp", "sym": "CL/USDT:USDT",  "dp": 2,
     "main": ("mkidx", "energy/CLcv1")},
    {"name": "금",       "src": "perp", "sym": "XAU/USDT:USDT", "dp": 2,
     "main": ("mkidx", "metals/GCcv1")},
    {"name": "나스닥",   "src": "perp", "sym": "QQQ/USDT:USDT", "dp": 2,
     "main": ("widx", ".IXIC")},
    {"name": "코스피",   "src": "kr",   "index": "KOSPI",  "sym": "EWY/USDT:USDT", "dp": 2},
    {"name": "코스닥",   "src": "kr",   "index": "KOSDAQ", "sym": None, "dp": 2},
    {"name": "비트코인", "src": "perp", "sym": "BTC/USDT:USDT", "dp": 0},
]

# 주요 종목 — 시황 다음 카테고리. 본장 시세(전일比) + (Perp. 창변동%).
# SOX=필라델피아 반도체지수(퍼프 프록시 SMH), DRAM=Roundhill Memory ETF(네이버 DRAM.K).
KEY_STOCKS = [
    {"name": "SOX",        "widx": ".SOX", "sym": "SMH/USDT:USDT", "dp": 2},
    {"name": "DRAM",       "wstock": "DRAM.K", "sym": "DRAM/USDT:USDT", "dp": 2},
    {"name": "삼성전자",   "sym": "SAMSUNG/USDT:USDT", "code": "005930", "dp": 0},
    {"name": "SK하이닉스", "sym": "SKHYNIX/USDT:USDT", "code": "000660", "dp": 0},
]
STOCK_SIG_PCT = 1.0        # ★ 기준 통일: 퍼프(괄호) 있으면 퍼프, 없으면 표시 등락률 1%
STOCK_API = "https://m.stock.naver.com/api/stock/{code}/basic"
STOCK_DAILY = "https://m.stock.naver.com/api/stock/{code}/price"
INDEX_DAILY = "https://m.stock.naver.com/api/index/{code}/price"
MKIDX = "https://api.stock.naver.com/marketindex/{path}"
WIDX = "https://api.stock.naver.com/index/{code}/basic"
WSTOCK = "https://api.stock.naver.com/stock/{code}/basic"

# 변동폭 기준시점(앵커) — 15:30(정규장 마감) 단일.
# 모든 알림이 '직전 거래일 마감 대비'라는 한 가지 기준으로 통일된다.
# 장중 알림의 코스피·코스닥은 전일 15:30 종가 대비 = 흔히 보는 당일 등락률과 같다.
ANCHORS = ((15, 30),)

# 알람 슬롯: 키 = 발송시각(HHMM), at = (시, 분)
SLOTS = {
    "0600": {"label": "하루 시작",      "at": (6, 0)},
    "0750": {"label": "NXT 개장 전",    "at": (7, 50)},
    "0850": {"label": "정규장 개장 전",  "at": (8, 50)},
    "0930": {"label": "정규장 개장 후",  "at": (9, 30)},
    "1430": {"label": "정규장 마감 전",  "at": (14, 30)},
    # 1530/1630 은 '오늘 장이 어떻게 마무리됐나'가 목적이라 당일 15:30 앵커를 건너뛰고
    # 전일 마감부터 잰다(하루 전체). 19:00/20:00 은 당일 15:30 기준(마감 후 변동) 유지.
    # 1530 이 본편(마감 직후·수급은 잠정일 수 있음), 1630 은 확정치가 1530 과 유의미하게
    # 다를 때만 재발송된다(cli 에서 판정) — 09-11 사용자 요청.
    "1530": {"label": "정규장 마감",     "at": (15, 35), "prev_close": True},
    "1630": {"label": "마감 확정 갱신",  "at": (16, 30), "prev_close": True},
    # 일요일 18:00 주말 중간점검(cron 전용). manual=True 라 auto 판정에는 안 잡힌다 —
    # 평일 18시대에 --slot auto 로 돌려도 1630 이 뽑히던 기존 동작을 바꾸지 않기 위함.
    "1800": {"label": "주말 중간점검",   "at": (18, 0), "manual": True},
    "1900": {"label": "NXT 마감 전",    "at": (19, 0)},
    "2000": {"label": "NXT 마감 후",    "at": (20, 0)},
}

UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1",
      "Referer": "https://m.stock.naver.com/"}
POLL = "https://polling.finance.naver.com/api/realtime/domestic/index/{code}"
BOND = "https://api.stock.naver.com/marketindex/bond/{code}"

_ex = None


def exchange():
    global _ex
    if _ex is None:
        _ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        _ex.load_markets()
    return _ex


# ── 거래일 판정 (앵커 선택용) ──────────────────────────────────────
_TRADING_CACHE = {"dates": None, "last": None}


def _load_trading_dates():
    """최근 90일 한국 거래일 집합. KS11 에 데이터가 있으면 그날은 확실히 거래일."""
    if _TRADING_CACHE["dates"] is not None:
        return _TRADING_CACHE["dates"], _TRADING_CACHE["last"]
    dates, last = set(), None
    try:
        import FinanceDataReader as fdr
        start = (datetime.now(KST) - timedelta(days=90)).strftime("%Y-%m-%d")
        ks = fdr.DataReader("KS11", start)
        dates = {d.date() for d in ks.index}
        last = max(dates) if dates else None
    except Exception:
        pass
    _TRADING_CACHE["dates"], _TRADING_CACHE["last"] = dates, last
    return dates, last


def is_trading_date(d) -> bool:
    """d(date)가 한국 거래일인가.

    과거는 KS11 실적으로 정확히 판정한다(공휴일·임시휴장 모두 반영).
    KS11 에 아직 안 잡힌 당일/미래는 평일 여부로 근사한다 — 앵커 탐색은
    과거를 향하므로 이 근사가 문제되는 건 '당일 앵커'뿐이고,
    그날이 휴장이면 알림 자체가 스킵되므로 영향이 없다.
    """
    dates, last = _load_trading_dates()
    if last is not None and d <= last:
        return d in dates
    return d.weekday() < 5


def window_bounds(slot: str, now=None):
    """(구간 시작, 구간 끝) KST aware datetime.

    끝  = 슬롯 발송시각(지금을 넘으면 하루 당김)
    시작 = 그 끝보다 앞선 가장 가까운 앵커. 단 앵커는 **거래일에만** 놓는다.
           금요일 마감 → 월요일 알림처럼 휴장 구간을 통째로 건너뛰기 위함이다.
    """
    now = now or datetime.now(KST)
    h, m = SLOTS[slot]["at"]
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=1)

    skip_end_day = SLOTS[slot].get("prev_close", False)
    for back in range(0, 15):            # 최장 연휴 대비 15일 역행
        d = (end - timedelta(days=back)).date()
        if skip_end_day and d == end.date():
            continue                     # 당일 앵커 생략 → 전일 마감부터
        if not is_trading_date(d):
            continue
        cands = [end.replace(year=d.year, month=d.month, day=d.day,
                             hour=ah, minute=am) for ah, am in ANCHORS]
        cands = [a for a in cands if a < end]
        if cands:
            return max(cands), end
    # 전부 실패하면 달력 기준으로 폴백(데이터 소스 장애 등)
    return end - timedelta(hours=12), end


def index_available(start: datetime, end: datetime) -> bool:
    """이 창의 변동을 '지수 전일比'로 대신할 수 있는가.

    조건: 창 시작이 직전 거래일 마감이고(시작 날짜 < 끝 날짜), 끝이 오늘이며,
    오늘 지수가 이미 열렸을 것(09:00 이후). 이러면 창 변동 = 지수 당일 등락률이다.
    - 장중(09:30/14:30): 실시간 등락률 ✓
    - 마감 후 1630(전일마감→오늘 16:30): 지수는 15:30 에 멈추므로 종가 등락률이
      곧 창 변동 ✓ — '오늘 장 마무리'라는 슬롯 목적과 일치한다.
    - 19:00/20:00(당일 15:30→) : 시작·끝이 같은 날 → 지수 전일比는 창과 무관, 프록시.
    - 새벽·개장 전: 오늘 지수가 아직 없음 → 프록시.
    """
    return (start.date() < end.date()
            and end.date() == datetime.now(KST).date()
            and end.timetz().replace(tzinfo=None) >= dtime(9, 0))


def _close_at(symbol: str, ts: datetime, ex=None):
    """해당 시각 직전 1분봉 종가. 없으면 None."""
    ex = ex or exchange()
    ms = int(ts.timestamp() * 1000)
    try:
        oh = ex.fetch_ohlcv(symbol, "1m", since=ms - 20 * 60000, limit=30)
        prior = [c for c in oh if c[0] <= ms]
        return prior[-1][4] if prior else None
    except Exception:
        return None


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _poll_index(code: str) -> dict | None:
    try:
        r = requests.get(POLL.format(code=code), headers=UA, timeout=12)
        return (r.json().get("datas") or [None])[0] if r.status_code == 200 else None
    except Exception:
        return None


def _row_perp(spec, start, end, ex):
    p0, p1 = _close_at(spec["sym"], start, ex), _close_at(spec["sym"], end, ex)
    chg = (p1 / p0 - 1) * 100 if (p0 and p1) else None
    return {"end_px": p1, "chg_pct": chg, "decimals": spec["dp"]}


# ── σ (자산별 일변동 표준편차, 60거래일·일 1회 캐시) ─────────────
def _sigma_cache():
    try:
        with open(_SIGMA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _sigma(key: str, fetch):
    """오늘 캐시가 있으면 그것, 없으면 fetch() 후 저장. 실패 시 어제 값이라도."""
    today = datetime.now(KST).strftime("%Y%m%d")
    cache = _sigma_cache()
    v = cache.get(key)
    if v and v[0] == today:
        return v[1]
    s = None
    try:
        s = fetch()
    except Exception:
        pass
    if s:
        cache[key] = [today, round(s, 4)]
        try:
            os.makedirs(os.path.dirname(_SIGMA_PATH), exist_ok=True)
            with open(_SIGMA_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        except Exception:
            pass
        return s
    return v[1] if v else None


def _std(xs):
    if len(xs) < 20:
        return None
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** .5


def _sigma_perp(sym, ex=None):
    def fetch():
        oh = (ex or exchange()).fetch_ohlcv(sym, "1d", limit=95)
        cl = [c[4] for c in oh]
        return _std([(cl[i] / cl[i - 1] - 1) * 100 for i in range(1, len(cl))][-60:])
    return _sigma(f"perp:{sym}", fetch)


def _sigma_index(code):
    def fetch():
        rows = []
        for page in (1, 2, 3, 4):
            r = requests.get(INDEX_DAILY.format(code=code),
                             params={"pageSize": 20, "page": page},
                             headers=UA, timeout=12).json()
            rows += r if isinstance(r, list) else []
        chgs, seen = [], set()
        for x in rows:
            d = (x.get("localTradedAt") or "")[:10]
            v = _num(x.get("fluctuationsRatio"))
            if d and d not in seen and v is not None:
                seen.add(d)
                chgs.append(v)
        return _std(chgs[1:61])
    return _sigma(f"idx:{code}", fetch)


def _sigma_bond():
    def fetch():
        import FinanceDataReader as fdr
        d = fdr.DataReader("FRED:DGS10").dropna()
        bp = (d.iloc[:, 0].diff() * 100).dropna().tolist()[-60:]
        return _std(bp)
    return _sigma("bond:US10Y", fetch)


def _star_level(chg, sigma, hours=None) -> int:
    """★ 단계: z>=1.2 ★ / z>=1.5 ★★ / z>=2.0 ★★★ (09-11 사용자).
    sigma 없으면 고정 1% 최후 폴백(1단계만)."""
    if chg is None:
        return 0
    if not sigma:
        return 1 if abs(chg) >= SIGNIFICANT_PCT else 0
    eff = sigma * ((hours / 24) ** .5) if hours else sigma
    z = abs(chg) / max(eff, 1e-9)
    return 3 if z >= 2.0 else 2 if z >= 1.5 else 1 if z >= Z_STAR else 0


def _main_quote(main):
    """본장 시세 (가격, 전일比%) — 네이버 원자재(marketindex)/해외지수/해외개별주."""
    kind, key = main
    url = {"mkidx": MKIDX.format(path=key),
           "widx": WIDX.format(code=key),
           "wstock": WSTOCK.format(code=key)}[kind]
    try:
        j = requests.get(url, headers=UA, timeout=12).json()
    except Exception:
        return None, None
    return _num(j.get("closePrice")), _num(j.get("fluctuationsRatio"))


def _last_confirmed(url) -> tuple:
    """일별 시세 API 의 최근 '확정' 행 — 개장 전엔 전일比가 0 으로 리셋되는
    basic/폴링 대신 쓴다. 오늘 09시 이전이면 오늘 날짜 행(예상치)을 건너뛴다."""
    try:
        rows = requests.get(url, params={"pageSize": 3, "page": 1},
                            headers=UA, timeout=12).json()
        if not isinstance(rows, list):
            return None, None
        now = datetime.now(KST)
        today = now.strftime("%Y-%m-%d")
        for r in rows:
            d = (r.get("localTradedAt") or "")[:10]
            if d == today and now.timetz().replace(tzinfo=None) < dtime(9, 5):
                continue
            return _num(r.get("closePrice")), _num(r.get("fluctuationsRatio"))
    except Exception:
        pass
    return None, None


def _attach_main(row, px, chg):
    """퍼프 창 변동을 괄호(perp_pct)로 밀고 본장 시세를 앞세운다.
    화살표·★ 판정(chg_pct)은 밤사이 감지 목적에 맞게 퍼프 쪽을 유지한다."""
    if px is None or chg is None:
        return row
    row["perp_pct"] = row.get("chg_pct")
    row["end_px"] = px
    row["chg_label"] = f"{chg:+.2f}%"
    if row.get("perp_pct") is None:
        row["chg_pct"] = chg
    return row


def _row_kr(spec, start, end, ex):
    """끝이 오늘 장중이면 실제 지수(전일比), 아니면 퍼페추얼 프록시. 프록시 없으면 None.

    폴링 API 는 당일 값만 주므로 과거 창을 수동 재실행할 때는 지수를 쓰지 않는다.
    """
    if index_available(start, end):
        d = _poll_index(spec["index"])
        if d:
            c, ratio = _num(d.get("closePrice")), _num(d.get("fluctuationsRatio"))
            if c is not None and ratio is not None:
                return {"end_px": c, "chg_pct": ratio, "decimals": spec["dp"]}
    # 장외: 본장 마지막 확정(전일 종가·전일比)을 앞세우고, 코스피는 EWY 퍼프를 괄호로
    px, ch = _last_confirmed(INDEX_DAILY.format(code=spec["index"]))
    if spec.get("sym"):
        r = _row_perp(spec, start, end, ex)
        if px is not None and ch is not None:
            return _attach_main(r, px, ch)
        r["proxy"] = "EWY"                # 본장 조회 실패 시 기존 표시로 폴백
        return r
    if px is not None and ch is not None:
        return {"end_px": px, "chg_pct": ch, "decimals": spec["dp"]}
    return None                           # 코스닥: 본장도 못 얻으면 행 생략(기존 동작)


def _poll_stock(code: str) -> dict | None:
    try:
        r = requests.get(STOCK_API.format(code=code), headers=UA, timeout=12)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _key_stock_rows(start, end, ex) -> list:
    """주요 종목 행 — 본장 시세(전일比) 우선 + (Perp. 창변동%) 괄호.

    SOX 는 지수+SMH 퍼프, DRAM 은 DXI 단독(퍼프 없음), 개별주는
    장중 basic(실시간) / 장외 일별시세 확정 행 + 자사 퍼프.
    """
    out = []
    use_index = index_available(start, end)
    for spec in KEY_STOCKS:
        row = None
        px = ch = None
        if spec.get("wstock"):            # 해외 ETF(DRAM 등) — 본장 + (있으면) 퍼프
            px, ch = _main_quote(("wstock", spec["wstock"]))
            if not spec.get("sym"):
                if px is None:
                    continue
                row = {"end_px": px, "chg_pct": ch, "decimals": spec["dp"]}
        elif spec.get("widx"):
            px, ch = _main_quote(("widx", spec["widx"]))
        else:
            if use_index:
                b = _poll_stock(spec["code"]) or {}
                px = _num(str(b.get("closePrice") or "").replace(",", ""))
                ch = _num(b.get("fluctuationsRatio"))
            if px is None or ch is None:
                px, ch = _last_confirmed(STOCK_DAILY.format(code=spec["code"]))
        if row is None:
            row = _row_perp({"sym": spec["sym"], "dp": spec["dp"]}, start, end, ex)
            if px is not None and ch is not None:
                _attach_main(row, px, ch)
            else:
                row["proxy"] = "perp"     # 본장 조회 실패 시 기존 표시로 폴백
        row["name"] = spec["name"]
        c = row.get("chg_pct")
        if not row.get("chg_label"):
            row["chg_label"] = f"{c:+.2f}%" if c is not None else None
        hours = (end - start).total_seconds() / 3600
        if spec.get("sym"):               # ★ 기준은 퍼프 창 변동 → 퍼프 σ + 창 스케일
            row["stars"] = _star_level(c, _sigma_perp(spec["sym"], ex), hours)
        else:                             # 퍼프 없는 본장 전일比 단독 — 기본 σ
            row["stars"] = _star_level(c, SIGMA_FALLBACK_PCT)
        row["significant"] = row["stars"] > 0
        out.append(row)
        time.sleep(0.05)
    return out


def _row_bond(spec):
    """금리는 창 기준이 아니라 전일比 — 임의 과거 시점의 금리를 주는 무료 소스가 없다."""
    try:
        r = requests.get(BOND.format(code=spec["code"]), headers=UA, timeout=12)
        j = r.json() if r.status_code == 200 else {}
    except Exception:
        j = {}
    y, fl = _num(j.get("closePrice")), _num(j.get("fluctuations"))
    if y is None:
        return {"end_px": None, "chg_pct": None, "decimals": 2}
    sign = -1 if (j.get("fluctuationsType") or {}).get("name") == "FALLING" else 1
    bp = sign * abs(fl) * 100 if fl is not None else None
    return {"end_px": y, "chg_pct": (bp / 100 if bp is not None else None),
            "kind": "yield", "chg_bp": bp, "decimals": 2}


def fetch_window(slot: str, now: datetime | None = None):
    """슬롯 시간창의 시황. rows: [{name, end_px, chg_pct, chg_label, significant, ...}]"""
    start, end = window_bounds(slot, now)
    ex = exchange()
    out = []
    for spec in DISPLAY:
        if spec["src"] == "perp":
            row = _row_perp(spec, start, end, ex)
            if spec.get("main"):
                px, ch = _main_quote(spec["main"])
                _attach_main(row, px, ch)
        elif spec["src"] == "kr":
            row = _row_kr(spec, start, end, ex)
        elif spec["src"] == "bond":
            row = _row_bond(spec)
        else:
            row = None
        if row is None:
            continue
        row["name"] = spec["name"]
        hours = (end - start).total_seconds() / 3600
        if row.get("kind") == "yield":
            bp = row.get("chg_bp")
            # bp 대신 %p 로 표기 (+11.3bp → +0.11%), '(전일비)' 꼬리 제거 (09-11 사용자)
            row["chg_label"] = f"{bp/100:+.2f}%" if bp is not None else None
            row["stars"] = _star_level(bp, _sigma_bond())             # bp 단위끼리 비교
        else:
            c = row.get("chg_pct")
            if not row.get("chg_label"):          # 본장 라벨(_attach_main)이 있으면 유지
                row["chg_label"] = f"{c:+.2f}%" if c is not None else None
            # ★ 기준(chg_pct)이 퍼프 창 변동이면 퍼프 σ + 창 길이 스케일, 아니면 지수 σ
            if spec["src"] == "perp" or row.get("perp_pct") is not None or row.get("proxy"):
                row["stars"] = _star_level(c, _sigma_perp(spec.get("sym"), ex), hours)
            else:
                row["stars"] = _star_level(c, _sigma_index(spec["index"]))
        row["significant"] = row["stars"] > 0
        out.append(row)
        time.sleep(0.05)
    return {"slot": slot, "label": SLOTS[slot]["label"], "start": start, "end": end,
            "rows": out, "key_stocks": _key_stock_rows(start, end, ex)}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for s in SLOTS:
        w = fetch_window(s)
        print(f"\n[{s} — {w['label']}]  {w['start']:%m-%d %H:%M} → {w['end']:%m-%d %H:%M}")
        for r in w["rows"]:
            if r["chg_pct"] is None:
                print(f"   {r['name']:<8} 데이터 없음"); continue
            mark = " ★" if r["significant"] else ""
            tag = f" ({r['proxy']})" if r.get("proxy") else ""
            print(f"   {r['name']:<8} {r['end_px']:>10,.{r['decimals']}f}  "
                  f"{r['chg_label']:>14}{mark}{tag}")
