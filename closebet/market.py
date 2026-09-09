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
    FDR(KRX 데이터)이 404 등으로 죽으면 네이버 모바일 시총 리스트 API 로 폴백한다
    (2026-09-09 KRX 가 FDR 엔드포인트를 막아 주도주가 통째로 빠진 사고 재발 방지).
    """
    try:
        df = fdr.StockListing(market)
    except Exception:
        df = None
    if df is None or df.empty:
        return _snapshot_naver(market)

    df = df.rename(columns=_RENAME)
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
    df = df.set_index("종목코드")

    cols = [c for c in _KEEP if c in df.columns]
    df = df[cols].copy()
    for c in _NUMERIC:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


_NAVER_LIST = "https://m.stock.naver.com/api/stocks/marketValue/{mk}"
_naver_cache: dict = {}          # market -> (epoch, df). 한 슬롯에서 여러 번 불려도 39콜 1회만.


def _snapshot_naver(market: str = "KRX") -> pd.DataFrame:
    """네이버 전종목 리스트 폴백. 시가/고가/저가는 없음(종가·등락률·거래대금·시총만).

    단위 실측: *Raw 필드는 원 단위 그대로 (콤마 표기 필드가 백만원/억원 축약본).
    pageSize 는 100 초과 시 에러 페이지가 온다.
    """
    import time as _t

    import requests

    hit = _naver_cache.get(market)
    if hit and _t.time() - hit[0] < 300:
        return hit[1]
    mks = {"KRX": ["KOSPI", "KOSDAQ"], "KOSPI": ["KOSPI"], "KOSDAQ": ["KOSDAQ"]}[market]
    hdr = {"User-Agent": "Mozilla/5.0"}
    rows = []
    for mk in mks:
        for page in range(1, 40):
            try:
                r = requests.get(_NAVER_LIST.format(mk=mk), headers=hdr, timeout=15,
                                 params={"page": page, "pageSize": 100}).json()
            except Exception:
                break
            batch = r.get("stocks") or []

            def _fn(v):
                try:
                    return float(str(v).replace(",", ""))
                except (TypeError, ValueError):
                    return None

            for s in batch:
                if s.get("stockEndType") != "stock":
                    continue
                rows.append({
                    "종목코드": str(s.get("itemCode") or "").zfill(6),
                    "종목명": s.get("stockName"),
                    "시장": mk,
                    "종가": _fn(s.get("closePriceRaw")),
                    "등락률": _fn(s.get("fluctuationsRatio")),
                    "거래량": _fn(s.get("accumulatedTradingVolumeRaw")),
                    # Raw 필드는 원 단위 그대로다 (콤마 표기 필드만 백만원/억원 축약)
                    "거래대금": _fn(s.get("accumulatedTradingValueRaw")),
                    "시가총액": _fn(s.get("marketValueRaw")),
                })
            if len(batch) < 100:
                break
            _t.sleep(0.15)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("종목코드")
    for c in _NUMERIC:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    _naver_cache[market] = (_t.time(), df)
    return df


def get_price_history(code: str, start=None, end=None) -> pd.DataFrame:
    """개별 종목 과거 일봉(OHLCV). 백테스팅 단계에서 사용."""
    return fdr.DataReader(str(code).zfill(6), start, end)


def latest_trading_date() -> str:
    """최근 거래일(YYYYMMDD). 네이버 지수 API 1차(FDR KS11 은 이틀씩 뒤처진 적 있음)."""
    try:
        import requests
        r = requests.get("https://m.stock.naver.com/api/index/KOSPI/price",
                         params={"pageSize": 1, "page": 1},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        d = ((r[0] if isinstance(r, list) else {}).get("localTradedAt") or "")[:10]
        if len(d) == 10:
            return d.replace("-", "")
    except Exception:
        pass
    try:
        start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        ks = fdr.DataReader("KS11", start)
        if len(ks):
            return ks.index[-1].strftime("%Y%m%d")
    except Exception:
        pass
    return datetime.now().strftime("%Y%m%d")
