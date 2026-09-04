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

import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime

import ccxt
import requests

KST = timezone(timedelta(hours=9))

# 이 값 이상 움직이면 '유의미 변동'으로 보고 뉴스를 붙인다.
SIGNIFICANT_PCT = 1.0
SIGNIFICANT_BP = 5.0          # 금리는 5bp(0.05%p) 이상을 유의미로 본다

# 퍼페추얼 심볼 목록 — reactions.py(지표 반응 측정)도 이 목록을 쓴다. 순서 유지.
INSTRUMENTS = [
    ("오일",     "CL/USDT:USDT",  "CLUSDT.P",  2),
    ("금",       "XAU/USDT:USDT", "XAUUSDT.P", 2),
    ("나스닥",   "QQQ/USDT:USDT", "QQQUSDT.P", 2),
    ("코스피",   "EWY/USDT:USDT", "EWYUSDT.P", 2),
    ("비트코인", "BTC/USDT:USDT", "BTCUSDT.P", 0),
]

# 시황에 표시할 행. src: perp=퍼페추얼 / kr=지수(장중)+프록시(장외) / bond=금리
DISPLAY = [
    {"name": "오일",     "src": "perp", "sym": "CL/USDT:USDT",  "dp": 2},
    {"name": "금",       "src": "perp", "sym": "XAU/USDT:USDT", "dp": 2},
    {"name": "미국10Y",  "src": "bond", "code": "US10YT=RR"},
    {"name": "나스닥",   "src": "perp", "sym": "QQQ/USDT:USDT", "dp": 2},
    {"name": "코스피",   "src": "kr",   "index": "KOSPI",  "sym": "EWY/USDT:USDT", "dp": 2},
    {"name": "코스닥",   "src": "kr",   "index": "KOSDAQ", "sym": None, "dp": 2},
    {"name": "비트코인", "src": "perp", "sym": "BTC/USDT:USDT", "dp": 0},
]

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
    "1630": {"label": "마감 집계 후",    "at": (16, 30)},
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

    for back in range(0, 15):            # 최장 연휴 대비 15일 역행
        d = (end - timedelta(days=back)).date()
        if not is_trading_date(d):
            continue
        cands = [end.replace(year=d.year, month=d.month, day=d.day,
                             hour=ah, minute=am) for ah, am in ANCHORS]
        cands = [a for a in cands if a < end]
        if cands:
            return max(cands), end
    # 전부 실패하면 달력 기준으로 폴백(데이터 소스 장애 등)
    return end - timedelta(hours=12), end


def index_available(end: datetime) -> bool:
    """창의 끝이 '오늘 정규장 시간(09:00~15:30)' 안인가.

    앵커가 15:30 단일이라 창 시작은 항상 직전 거래일 마감이다. 그 시작점의
    지수값 = 전일 종가이므로, 끝이 오늘 장중이면 '지수 전일比'가 곧 창 변동이 된다.
    끝이 장외면 지수는 멈춰 있으므로 퍼페추얼 프록시를 쓴다.
    """
    return (end.date() == datetime.now(KST).date()
            and dtime(9, 0) <= end.timetz().replace(tzinfo=None) <= dtime(15, 30))


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


def _row_kr(spec, start, end, ex):
    """끝이 오늘 장중이면 실제 지수(전일比), 아니면 퍼페추얼 프록시. 프록시 없으면 None.

    폴링 API 는 당일 값만 주므로 과거 창을 수동 재실행할 때는 지수를 쓰지 않는다.
    """
    if index_available(end):
        d = _poll_index(spec["index"])
        if d:
            c, ratio = _num(d.get("closePrice")), _num(d.get("fluctuationsRatio"))
            if c is not None and ratio is not None:
                return {"end_px": c, "chg_pct": ratio, "decimals": spec["dp"]}
    if not spec.get("sym"):
        return None                       # 코스닥은 장외 프록시가 없다 — 행 생략
    r = _row_perp(spec, start, end, ex)
    r["proxy"] = "EWY"
    return r


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
        elif spec["src"] == "kr":
            row = _row_kr(spec, start, end, ex)
        elif spec["src"] == "bond":
            row = _row_bond(spec)
        else:
            row = None
        if row is None:
            continue
        row["name"] = spec["name"]
        if row.get("kind") == "yield":
            bp = row.get("chg_bp")
            row["chg_label"] = f"{bp:+.1f}bp · 전일비" if bp is not None else None
            row["significant"] = (bp is not None and abs(bp) >= SIGNIFICANT_BP)
        else:
            c = row.get("chg_pct")
            row["chg_label"] = f"{c:+.2f}%" if c is not None else None
            row["significant"] = (c is not None and abs(c) >= SIGNIFICANT_PCT)
        out.append(row)
        time.sleep(0.05)
    return {"slot": slot, "label": SLOTS[slot]["label"], "start": start, "end": end,
            "rows": out}


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
