"""trade.xyz(=Hyperliquid HIP-3 'xyz' dex) 주식 퍼페추얼 펀딩비 과거 내역 수집.

app.trade.xyz의 주식 퍼페추얼은 Hyperliquid 위에 올라간 'xyz' HIP-3 DEX다.
따라서 펀딩비 이력은 Hyperliquid 공개 info API(fundingHistory)로 그대로 받는다.
심볼은 dex 프리픽스가 붙는다: SK하이닉스=xyz:SKHX, 삼성전자=xyz:SMSN.

펀딩은 '시간당' 정산이며, 한 번의 호출은 최대 500행(약 20.8일)이라 페이지네이션한다.

사용:
    python funding_trade_xyz.py                 # 최근 30일, SKHX+SMSN
    python funding_trade_xyz.py --days 60
    python funding_trade_xyz.py --coins xyz:SKHX xyz:SMSN xyz:NVDA
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

API = "https://api.hyperliquid.xyz/info"
HOUR_MS = 3_600_000


def fetch_funding(coin: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """[start_ms, end_ms] 구간 펀딩 이력 전체를 500행 페이지네이션으로 수집."""
    rows: list[dict] = []
    cursor = start_ms
    seen: set[int] = set()
    while cursor <= end_ms:
        body = {"type": "fundingHistory", "coin": coin,
                "startTime": cursor, "endTime": end_ms}
        r = requests.post(API, json=body, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        fresh = [x for x in batch if x["time"] not in seen]
        for x in fresh:
            seen.add(x["time"])
        rows.extend(fresh)
        last = batch[-1]["time"]
        if len(batch) < 500 or last <= cursor:
            break
        cursor = last + 1          # 다음 페이지는 마지막 시각 직후부터
        time.sleep(0.15)           # rate-limit 예의
    if not rows:
        return pd.DataFrame(columns=["time", "fundingRate", "premium"])
    df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    df["dt_utc"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["dt_kst"] = df["dt_utc"].dt.tz_convert("Asia/Seoul")
    df["fundingRate"] = df["fundingRate"].astype(float)
    df["premium"] = df["premium"].astype(float)
    return df


def summarize(coin: str, df: pd.DataFrame) -> None:
    if df.empty:
        print(f"[{coin}] 데이터 없음")
        return
    hourly = df["fundingRate"]
    cum = hourly.sum()
    print(f"\n=== {coin} ===")
    print(f"  구간   : {df['dt_kst'].iloc[0]:%Y-%m-%d %H:%M} ~ "
          f"{df['dt_kst'].iloc[-1]:%Y-%m-%d %H:%M} KST  ({len(df)} rows, hourly)")
    print(f"  평균   : {hourly.mean()*100:.5f}% /h  "
          f"(연율 ~ {hourly.mean()*24*365*100:.1f}%)")
    print(f"  누적   : {cum*100:.4f}%  (구간 전체 롱이 지불한 합)")
    print(f"  최댓값 : {hourly.max()*100:.5f}% /h   최솟값: {hourly.min()*100:.5f}% /h")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows cp949 콘솔 대응
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--coins", nargs="+", default=["xyz:SKHX", "xyz:SMSN"])
    ap.add_argument("--outdir", default="data")
    args = ap.parse_args()

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 24 * HOUR_MS
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    for coin in args.coins:
        df = fetch_funding(coin, start_ms, end_ms)
        summarize(coin, df)
        if not df.empty:
            safe = coin.replace(":", "_")
            path = f"{args.outdir}/funding_{safe}_{stamp}.csv"
            df.to_csv(path, index=False, encoding="utf-8-sig")
            print(f"  저장   : {path}")


if __name__ == "__main__":
    main()
