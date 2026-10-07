# -*- coding: utf-8 -*-
"""크립토 섹션 수집 — 브리핑 📊 크립토 블록 (10-08 사용자 포맷, 같은 날 2차 개편).

항목: 공포탐욕(5일/20일 평균 대비) · 코베프리미엄 · 김프 · 업비트 24h 거래대금
(5일/20일 평균 대비) · BTC/ETH(바이낸스 선물 24h) · TOTAL3ES(B 단위 + 전일比).

이력: data/crypto_hist.json {YYYYMMDD: {fng, upvol, t3es}} — fetch() 가 매 실행
오늘 칸을 덮어쓰며(멱등) 축적. 업비트 과거 20일은 backfill()(일봉 캔들 292콜)
1회 소급, 공포탐욕 과거는 API 가 직접 줌. TOTAL3ES 과거는 소스 없음(전방 축적).

ETF 순유입(BTC+ETH)은 무료 소스 부재로 보류(farside=CF차단, llama=유료,
sosovalue=키 필요). 각 항목 독립 try — 하나 죽어도 나머지는 나간다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "data", "crypto_hist.json")
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
    closes = [c for c in j["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
    return closes[-1] if closes else None


def _load_hist() -> dict:
    try:
        return json.load(open(HIST, encoding="utf-8"))
    except Exception:
        return {}


def _save_hist(h: dict):
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    keep = dict(sorted(h.items())[-60:])          # 60일이면 20일 비교에 충분
    json.dump(keep, open(HIST, "w", encoding="utf-8"))


def _vs_avg(cur, hist, key, today, n):
    """오늘 제외 직전 n일 평균 대비 %. 표본 절반 미만이면 None."""
    vals = [v.get(key) for d, v in sorted(hist.items(), reverse=True)
            if d < today and v.get(key)]
    vals = vals[:n]
    if cur is None or len(vals) < max(2, n // 2):
        return None
    avg = sum(vals) / len(vals)
    return (cur / avg - 1) * 100 if avg else None


def fetch() -> dict:
    out = {}
    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    hist = _load_hist()
    fng_v = None
    try:  # 공포탐욕 — API 가 과거를 직접 주므로 이력도 여기서 채움
        d = _get("https://api.alternative.me/fng/?limit=25")["data"]
        fng_v = int(d[0]["value"])
        out["fng"] = {"v": fng_v,
                      "label": FNG_KR.get(d[0]["value_classification"],
                                          d[0]["value_classification"]),
                      "prev": int(d[1]["value"]) if len(d) > 1 else None}
        for x in d:  # UTC 자정 타임스탬프 → KST 날짜
            dt = datetime.fromtimestamp(int(x["timestamp"]), tz=KST).strftime("%Y%m%d")
            hist.setdefault(dt, {})["fng"] = int(x["value"])
        out["fng"]["d5"] = _vs_avg(fng_v, hist, "fng", today, 5)
        out["fng"]["d20"] = _vs_avg(fng_v, hist, "fng", today, 20)
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
    try:  # 김프
        up = _get("https://api.upbit.com/v1/ticker",
                  params={"markets": "KRW-BTC"})[0]["trade_price"]
        if bn_spot and krw:
            out["kimp"] = (up / (bn_spot * krw) - 1) * 100
    except Exception:
        pass
    upvol = None
    try:  # 업비트 KRW 마켓 24h 거래대금 합
        mk = [m["market"] for m in _get("https://api.upbit.com/v1/market/all")
              if m["market"].startswith("KRW-")]
        tot = 0.0
        for i in range(0, len(mk), 100):
            for t in _get("https://api.upbit.com/v1/ticker",
                          params={"markets": ",".join(mk[i:i + 100])}):
                tot += t.get("acc_trade_price_24h") or 0
        upvol = tot / 1e12
        out["upbit_vol_jo"] = upvol
        hist.setdefault(today, {})["upvol"] = upvol
        out["upvol_d5"] = _vs_avg(upvol, hist, "upvol", today, 5)
        out["upvol_d20"] = _vs_avg(upvol, hist, "upvol", today, 20)
    except Exception:
        pass
    for key, sym in (("btc", "BTCUSDT"), ("eth", "ETHUSDT")):
        try:  # 바이낸스 선물 24h (사용자 지정 ethusdt.p)
            t = _get("https://fapi.binance.com/fapi/v1/ticker/24hr",
                     params={"symbol": sym})
            out[key] = {"px": float(t["lastPrice"]),
                        "chg": float(t["priceChangePercent"])}
        except Exception:
            pass
    try:  # TOTAL3ES 근사(총시총-BTC-ETH-USDT-USDC) — B 단위, 전일比는 이력 diff
        g = _get("https://api.coingecko.com/api/v3/global")["data"]
        pct = g["market_cap_percentage"]
        share = 100 - sum(pct.get(k, 0) for k in ("btc", "eth", "usdt", "usdc"))
        t3 = g["total_market_cap"]["usd"] * share / 100 / 1e9      # B
        prev = [v.get("t3es") for d, v in sorted(hist.items(), reverse=True)
                if d < today and v.get("t3es")]
        chg = (t3 / prev[0] - 1) * 100 if prev else None
        hist.setdefault(today, {})["t3es"] = t3
        out["total3es"] = {"b": t3, "chg": chg}
    except Exception:
        pass
    _save_hist(hist)
    return out


def backfill(days: int = 21):
    """업비트 일별 거래대금 과거 소급 — KRW 전 마켓 일봉 캔들 합 (1회용, ~35초).

    candle_acc_trade_price = 그 캘린더 일의 총 체결대금. 24h 롤링과 정의가 약간
    다르지만 5/20일 '평균 대비' 비교용으로는 충분한 근사다."""
    mk = [m["market"] for m in _get("https://api.upbit.com/v1/market/all")
          if m["market"].startswith("KRW-")]
    agg: dict = {}
    for i, m in enumerate(mk):
        try:
            for c in _get("https://api.upbit.com/v1/candles/days",
                          params={"market": m, "count": days}):
                d = c["candle_date_time_kst"][:10].replace("-", "")
                agg[d] = agg.get(d, 0.0) + (c.get("candle_acc_trade_price") or 0)
        except Exception:
            pass
        if i % 50 == 0:
            print(f"  {i}/{len(mk)}")
        time.sleep(0.12)
    hist = _load_hist()
    today = datetime.now(KST).strftime("%Y%m%d")
    for d, v in agg.items():
        if d != today:                      # 오늘은 24h 롤링(fetch)이 담당
            hist.setdefault(d, {})["upvol"] = v / 1e12
    _save_hist(hist)
    print(f"백필 {len(agg)}일 → {HIST}")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "--backfill":
        backfill()
        raise SystemExit(0)
    print(json.dumps(fetch(), ensure_ascii=False, indent=1))
