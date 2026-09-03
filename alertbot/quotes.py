# -*- coding: utf-8 -*-
"""시황 5종 가격/변동폭 수집 (바이낸스 USDT 무기한 선물 = 트레이딩뷰 *USDT.P 티커).

이 상품들을 쓰는 이유: 24시간 무중단 거래라 '전일 19시 → 당일 7시' 같은
임의 시간창의 변동폭을 끊김 없이 계산할 수 있다. 실물 선물/ETF는 세션 갭 때문에 불가능.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import ccxt

KST = timezone(timedelta(hours=9))

# 이 값 이상 움직이면 '유의미 변동'으로 보고 뉴스를 붙인다.
SIGNIFICANT_PCT = 1.0

# 표시명 → (ccxt 심볼, 트레이딩뷰 티커, 소수자리)
INSTRUMENTS = [
    ("오일",     "CL/USDT:USDT",  "CLUSDT.P",  2),
    ("금",       "XAU/USDT:USDT", "XAUUSDT.P", 2),
    ("나스닥",   "QQQ/USDT:USDT", "QQQUSDT.P", 2),
    ("코스피",   "EWY/USDT:USDT", "EWYUSDT.P", 2),
    ("비트코인", "BTC/USDT:USDT", "BTCUSDT.P", 0),
]

# 변동폭 기준시점(앵커). 알람 시점에서 '직전에 지난 앵커'까지가 측정 구간이 된다.
#   08:00  NXT 프리마켓 시작
#   20:00  NXT 애프터마켓 종료
# 15:30(정규장 마감)은 앵커에서 뺐다. 앵커로 두면 오후 알림의 구간이 15:30~로 잘려
# 그날 전체 흐름이 안 보인다. 대신 마감 이후 슬롯에서는 '마감후 변동'을 따로 병기한다.
ANCHORS = ((8, 0), (20, 0))

# 정규장 마감. 이 시각 이후 슬롯은 08:00 기준 변동과 별개로 마감후 변동도 낸다.
CLOSE_AT = (15, 30)
POST_CLOSE_SLOTS = {"1630", "1900", "2000"}

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
    과거를 향하므로 이 근사가 문제되는 건 '당일 08:00 앵커'뿐이고,
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
           금요일 20:00 → 월요일 08:00 처럼 휴장 구간을 통째로 건너뛰기 위함이다.
           5종 전부 24시간 거래되는 퍼페추얼이라 그 사이 미국장·BTC·오일
           움직임이 모두 이 구간에 포함된다.
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


def fetch_window(slot: str, now: datetime | None = None):
    """슬롯 시간창의 5종 변동폭. [{name, tv, start_px, end_px, chg_pct, significant}]"""
    start, end = window_bounds(slot, now)
    sub = None
    if slot in POST_CLOSE_SLOTS:
        c = end.replace(hour=CLOSE_AT[0], minute=CLOSE_AT[1], second=0, microsecond=0)
        if start < c < end:
            sub = c
    ex = exchange()
    out = []
    for name, sym, tv, dp in INSTRUMENTS:
        p0 = _close_at(sym, start, ex)
        p1 = _close_at(sym, end, ex)
        chg = (p1 / p0 - 1) * 100 if (p0 and p1) else None
        row = {
            "name": name, "symbol": sym, "tv": tv, "decimals": dp,
            "start_px": p0, "end_px": p1, "chg_pct": chg,
            "significant": (chg is not None and abs(chg) >= SIGNIFICANT_PCT),
        }
        if sub:
            ps = _close_at(sym, sub, ex)
            row["chg_post"] = (p1 / ps - 1) * 100 if (ps and p1) else None
        out.append(row)
        time.sleep(0.05)
    return {"slot": slot, "label": SLOTS[slot]["label"], "start": start, "end": end,
            "sub_start": sub, "rows": out}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for s in SLOTS:
        w = fetch_window(s)
        print(f"\n[{s}시 슬롯 — {w['label']}]  {w['start']:%m-%d %H:%M} → {w['end']:%m-%d %H:%M} KST")
        for r in w["rows"]:
            if r["chg_pct"] is None:
                print(f"   {r['name']:<6} 데이터 없음"); continue
            mark = " ★" if r["significant"] else ""
            print(f"   {r['name']:<6}({r['tv']:<11}) {r['start_px']:>10,.{r['decimals']}f}"
                  f" → {r['end_px']:>10,.{r['decimals']}f}  {r['chg_pct']:>+7.2f}%{mark}")
