"""(실험적·선택) 주도섹터 근사 — KRX 지수 일간 등락률 상위.

pykrx로 KRX 업종/시장 지수의 당일 등락률을 계산해 주도 흐름을 근사합니다.
- pykrx는 선택 의존성입니다(미설치 시 자동 건너뜀).
- 최신 pykrx/KRX 환경에서 로그인·차단 이슈로 데이터가 비어 있을 수 있습니다.
  이 경우 None을 반환하며 메인 스크리너 실행에는 영향이 없습니다.
- 정확한 '테마'(2차전지·AI 등)는 공식 표준이 없어 향후 네이버 크롤링 모듈로 보강 예정입니다.
"""

from __future__ import annotations

import contextlib
import io
import time
from datetime import datetime, timedelta

import pandas as pd


def leading_sectors(
    date: str | None = None,
    krx_market: str = "KOSPI",
    lookback_days: int = 10,
    sleep: float = 0.2,
    top: int = 15,
):
    """KRX 지수 일간 등락률 상위 = 주도섹터 근사. 실패/미설치 시 None."""
    noise = io.StringIO()  # pykrx 내부 print(로그인/에러) 억제 (import 로그 포함)
    try:
        with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
            try:
                from pykrx import stock  # 선택 의존성 (lazy import)
            except Exception:
                return None

            date = stock.get_nearest_business_day_in_a_week(date) if date else stock.get_nearest_business_day_in_a_week()
            start = (datetime.strptime(date, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")
            tickers = stock.get_index_ticker_list(date, market=krx_market)

            rows = []
            for t in tickers:
                try:
                    idf = stock.get_index_ohlcv(start, date, t)
                    if idf is None or len(idf) < 2 or "종가" not in idf.columns:
                        continue
                    last, prev = idf["종가"].iloc[-1], idf["종가"].iloc[-2]
                    if prev <= 0:
                        continue
                    chg = (last / prev - 1) * 100
                    rows.append({"지수코드": t, "지수명": stock.get_index_ticker_name(t), "등락률": round(chg, 2)})
                except Exception:
                    continue
                time.sleep(sleep)
    except Exception:
        return None

    if not rows:
        return None
    return (
        pd.DataFrame(rows)
        .sort_values("등락률", ascending=False)
        .reset_index(drop=True)
        .head(top)
    )
