# -*- coding: utf-8 -*-
"""크립토 섹션 수집 — 브리핑 📊 크립토 블록 (10-08 사용자 포맷 개편).

항목: 공포탐욕지수(alternative.me) · 코베프리미엄(코인베이스/바이낸스 현물) ·
김프(업비트/바이낸스×환율) · 업비트 24h 거래대금 · BTC/ETH(바이낸스 선물 24h) ·
TOTAL3ES(코인게코 global 근사 = 총시총 - BTC - ETH - USDT - USDC, 전일比는
자체 스냅샷 diff — 첫날은 표시 없음).

ETF 순유입(BTC+ETH)은 무료 소스 부재로 보류(farside=CF차단, llama=유료,
sosovalue=키 필요) — 키 확보 시 여기 추가.
각 항목은 독립 try — 하나 죽어도 나머지는 나간다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "data", "crypto_snap.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"}
FNG_KR = {"Extreme Fear": "극단 공포", "Fear": "공포", "Neutral": "중립",
          "Greed": "탐욕", "Extreme Greed": "극단 탐욕"}


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 10), **kw)
    return r.json()


def _yahoo_last(sym: str):
    j = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
             params={"range": "5d", "interval": "1d"})
    res = j["chart"]["result"][0]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c]
    return closes[-1] if closes else None


def fetch() -> dict:
    out = {}
    try:  # 공포탐욕 (오늘 + 어제)
        d = _get("https://api.alternative.me/fng/?limit=2")["data"]
        out["fng"] = {"v": int(d[0]["value"]),
                      "label": FNG_KR.get(d[0]["value_classification"],
                                          d[0]["value_classification"]),
                      "prev": int(d[1]["value"]) if len(d) > 1 else None}
    except Exception:
        pass
    bn_spot = None
    try:
        bn_spot = float(_get("https://api.binance.com/api/v3/ticker/price",
                             params={"symbol": "BTCUSDT"})["price"])
    except Exception:
        pass
    try:  # 코베프리미엄 — 코인베이스 USD vs 바이낸스 USDT 현물
        cb = float(_get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")["price"])
        if bn_spot:
            out["cb_prem"] = (cb / bn_spot - 1) * 100
    except Exception:
        pass
    krw = None
    try:
        krw = _yahoo_last("KRW=X")
        out["usdkrw"] = krw
    except Exception:
        pass
    try:  # 김프 — 업비트 BTC(KRW) vs 바이낸스 현물 × 환율
        up = _get("https://api.upbit.com/v1/ticker",
                  params={"markets": "KRW-BTC"})[0]["trade_price"]
        if bn_spot and krw:
            out["kimp"] = (up / (bn_spot * krw) - 1) * 100
    except Exception:
        pass
    try:  # 업비트 KRW 마켓 24h 거래대금 합 (조원)
        mk = [m["market"] for m in _get("https://api.upbit.com/v1/market/all")
              if m["market"].startswith("KRW-")]
        tot = 0.0
        for i in range(0, len(mk), 100):
            for t in _get("https://api.upbit.com/v1/ticker",
                          params={"markets": ",".join(mk[i:i + 100])}):
                tot += t.get("acc_trade_price_24h") or 0
        out["upbit_vol_jo"] = tot / 1e12
    except Exception:
        pass
    for key, sym in (("btc", "BTCUSDT"), ("eth", "ETHUSDT")):
        try:  # 바이낸스 선물 24h (사용자 지정: ethusdt.p = 바이낸스 선물가)
            t = _get("https://fapi.binance.com/fapi/v1/ticker/24hr",
                     params={"symbol": sym})
            out[key] = {"px": float(t["lastPrice"]),
                        "chg": float(t["priceChangePercent"])}
        except Exception:
            pass
    try:  # TOTAL3ES 근사 — 상위 스테이블(USDT·USDC)만 차감하는 한계 명시
        g = _get("https://api.coingecko.com/api/v3/global")["data"]
        pct = g["market_cap_percentage"]
        share = 100 - sum(pct.get(k, 0) for k in ("btc", "eth", "usdt", "usdc"))
        t3 = g["total_market_cap"]["usd"] * share / 100
        chg = None
        today = datetime.now(KST).strftime("%Y%m%d")
        snap = {}
        try:
            snap = json.load(open(SNAP, encoding="utf-8"))
        except Exception:
            pass
        if snap.get("date") == today:
            if snap.get("prev_v"):
                chg = (t3 / snap["prev_v"] - 1) * 100
            snap["v"] = t3
        else:
            if snap.get("v"):
                chg = (t3 / snap["v"] - 1) * 100
            snap = {"prev_v": snap.get("v"), "date": today, "v": t3}
        os.makedirs(os.path.dirname(SNAP), exist_ok=True)
        json.dump(snap, open(SNAP, "w", encoding="utf-8"))
        out["total3es"] = {"t": t3 / 1e12, "chg": chg}
    except Exception:
        pass
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fetch(), ensure_ascii=False, indent=1))
