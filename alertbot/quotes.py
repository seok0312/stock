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

# 표시명 → (ccxt 심볼, 트레이딩뷰 티커, 소수자리)
INSTRUMENTS = [
    ("나스닥",   "QQQ/USDT:USDT", "QQQUSDT.P", 2),
    ("코스피",   "EWY/USDT:USDT", "EWYUSDT.P", 2),
    ("금",       "XAU/USDT:USDT", "XAUUSDT.P", 2),
    ("오일",     "CL/USDT:USDT",  "CLUSDT.P",  2),
    ("비트코인", "BTC/USDT:USDT", "BTCUSDT.P", 0),
]

# 슬롯별 (창 시작시각, 창 종료시각, 시작이 전일인지)
SLOTS = {
    "07": {"label": "장 시작 전",   "start_h": 19, "end_h": 7,  "start_prev_day": True},
    "14": {"label": "정규장 마감 전", "start_h": 7,  "end_h": 14, "start_prev_day": False},
    "19": {"label": "NXT 마감 전",  "start_h": 14, "end_h": 19, "start_prev_day": False},
}

_ex = None


def exchange():
    global _ex
    if _ex is None:
        _ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        _ex.load_markets()
    return _ex


def window_bounds(slot: str, now: datetime | None = None):
    """슬롯 기준 (시작시각, 종료시각) KST aware datetime.
    종료시각은 '지금'을 넘지 않도록 잘라낸다(정시보다 일찍 돌려도 동작)."""
    now = now or datetime.now(KST)
    cfg = SLOTS[slot]
    end = now.replace(hour=cfg["end_h"], minute=0, second=0, microsecond=0)
    if end > now:                       # 아직 그 시각 전이면 어제 기준
        end -= timedelta(days=1)
    start = end.replace(hour=cfg["start_h"], minute=0, second=0, microsecond=0)
    if cfg["start_prev_day"] or start >= end:
        start -= timedelta(days=1)
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
            "significant": (chg is not None and abs(chg) >= 0.5),
        })
        time.sleep(0.05)
    return {"slot": slot, "label": SLOTS[slot]["label"], "start": start, "end": end, "rows": out}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for s in ("07", "14", "19"):
        w = fetch_window(s)
        print(f"\n[{s}시 슬롯 — {w['label']}]  {w['start']:%m-%d %H:%M} → {w['end']:%m-%d %H:%M} KST")
        for r in w["rows"]:
            if r["chg_pct"] is None:
                print(f"   {r['name']:<6} 데이터 없음"); continue
            mark = " ★" if r["significant"] else ""
            print(f"   {r['name']:<6}({r['tv']:<11}) {r['start_px']:>10,.{r['decimals']}f}"
                  f" → {r['end_px']:>10,.{r['decimals']}f}  {r['chg_pct']:>+7.2f}%{mark}")
