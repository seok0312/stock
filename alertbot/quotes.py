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
#   15:30  정규장 마감
#   20:00  NXT 마감
ANCHORS = ((8, 0), (15, 30), (20, 0))

# 알람 슬롯: 키 = 발송시각(HHMM), at = (시, 분)
SLOTS = {
    "0600": {"label": "하루 시작",      "at": (6, 0)},
    "0750": {"label": "NXT 개장 전",    "at": (7, 50)},
    "0850": {"label": "정규장 개장 전",  "at": (8, 50)},
    "0930": {"label": "정규장 개장 후",  "at": (9, 30)},
    "1430": {"label": "정규장 마감 전",  "at": (14, 30)},
    "1600": {"label": "정규장 마감 후",  "at": (16, 0)},
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


def window_bounds(slot: str, now=None):
    """(구간 시작, 구간 끝) KST aware datetime.

    끝  = 슬롯 발송시각(지금을 넘으면 하루 당김 — 정시보다 일찍 돌려도 어긋나지 않게)
    시작 = 그 끝보다 '엄격히 앞선' 가장 가까운 앵커(08:00 / 15:30 / 20:00)
    """
    now = now or datetime.now(KST)
    h, m = SLOTS[slot]["at"]
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if end > now:
        end -= timedelta(days=1)

    cands = []
    for day_off in (0, -1):
        d = end + timedelta(days=day_off)
        for ah, am in ANCHORS:
            a = d.replace(hour=ah, minute=am, second=0, microsecond=0)
            if a < end:
                cands.append(a)
    start = max(cands)
    return start, end


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
    ex = exchange()
    out = []
    for name, sym, tv, dp in INSTRUMENTS:
        p0 = _close_at(sym, start, ex)
        p1 = _close_at(sym, end, ex)
        chg = (p1 / p0 - 1) * 100 if (p0 and p1) else None
        out.append({
            "name": name, "symbol": sym, "tv": tv, "decimals": dp,
            "start_px": p0, "end_px": p1, "chg_pct": chg,
            "significant": (chg is not None and abs(chg) >= SIGNIFICANT_PCT),
        })
        time.sleep(0.05)
    return {"slot": slot, "label": SLOTS[slot]["label"], "start": start, "end": end, "rows": out}


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
