"""시장 데이터 접근 계층 (FinanceDataReader 기반).

FDR는 네이버 등 공개 소스를 사용해 이 환경에서 안정적으로 동작합니다.
- get_snapshot():        전종목 '최근 거래일 종가 기준' 스냅샷 — 거래대금·등락률 스크리닝용
- get_price_history():   개별 종목 과거 일봉 — 백테스팅용
- latest_trading_date(): 최근 거래일(YYYYMMDD)

참고: KRX 직접 조회(pykrx)는 최신 버전에서 KRX 로그인/차단 이슈가 있어
      선택 의존성(주도섹터 분석)으로만 사용합니다(sectors.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import FinanceDataReader as fdr
import pandas as pd

# FDR StockListing 컬럼 → 한글 표준 컬럼 (주의: FDR 원본은 'ChagesRatio' 오타 표기)
_RENAME = {
    "Code": "종목코드",
    "Name": "종목명",
    "Market": "시장",
    "Open": "시가",
    "High": "고가",
    "Low": "저가",
    "Close": "종가",
    "ChagesRatio": "등락률",
    "Changes": "전일대비",
    "Volume": "거래량",
    "Amount": "거래대금",
    "Marcap": "시가총액",
}

_KEEP = ["종목명", "시장", "시가", "고가", "저가", "종가", "등락률", "거래량", "거래대금", "시가총액"]
_NUMERIC = ["시가", "고가", "저가", "종가", "등락률", "거래량", "거래대금", "시가총액"]


def get_snapshot(market: str = "KRX") -> pd.DataFrame:
    """전종목 최근 거래일 스냅샷. index=종목코드(6자리 str), 표준 한글 컬럼.

    market: "KRX"(전체) / "KOSPI" / "KOSDAQ".
    """
    df = fdr.StockListing(market)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns=_RENAME)
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
    df = df.set_index("종목코드")

    cols = [c for c in _KEEP if c in df.columns]
    df = df[cols].copy()
    for c in _NUMERIC:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def get_price_history(code: str, start=None, end=None) -> pd.DataFrame:
    """개별 종목 과거 일봉(OHLCV). 백테스팅 단계에서 사용."""
    return fdr.DataReader(str(code).zfill(6), start, end)


def latest_trading_date() -> str:
    """최근 거래일(YYYYMMDD). KOSPI 지수 마지막 일자 기준, 실패 시 오늘."""
    try:
        start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        ks = fdr.DataReader("KS11", start)
        if len(ks):
            return ks.index[-1].strftime("%Y%m%d")
    except Exception:
        pass
    return datetime.now().strftime("%Y%m%d")
