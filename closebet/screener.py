"""종가배팅 주도주 후보 스크리너.

로직: 거래정지/무거래 제거 → (선택)우선주 제외 → 거래대금 상위 N개로 압축
      → 최소 거래대금·등락률 조건 필터 → 등락률·거래대금 순 정렬.

데이터는 FinanceDataReader의 '최근 거래일 종가 기준' 전종목 스냅샷을 사용합니다
(종가배팅 = 장 마감 후 판단이므로 스냅샷 성격이 목적에 부합).
"""

from __future__ import annotations

import pandas as pd

from . import market
from .config import DEFAULT, EOK, Settings


def screen_leaders(cfg: Settings = DEFAULT) -> tuple:
    """주도주 후보를 스크리닝한다.

    Returns:
        (기준일 YYYYMMDD, 결과 DataFrame[순위, 종목코드, 종목명, 종가, 등락률, 거래대금(억)])
    """
    df = market.get_snapshot(cfg.market)
    if df.empty:
        raise RuntimeError(f"'{cfg.market}' 스냅샷을 가져오지 못했습니다. (네트워크/시장코드 확인)")
    date = market.latest_trading_date()

    # 거래정지·무거래 종목 제거
    df = df[(df["종가"] > 0) & (df["거래량"] > 0)]

    # 우선주 제외 (보통주 코드는 끝자리 0)
    if cfg.exclude_preferred:
        df = df[df.index.str.endswith("0")]

    # 거래대금 상위 N개로 1차 압축 (주도주는 거래대금이 실려 있음)
    df = df.sort_values("거래대금", ascending=False)
    if cfg.top_by_value:
        df = df.head(cfg.top_by_value)

    # 조건 필터: 최소 거래대금 + '시총구간별' 등락률 문턱 (09-15 — 세 요소 고려)
    #   대형주는 작은 %도 주도주다(삼성전자 +3% 등). 고정 문턱은 대형주를 배제해서
    #   시총으로 눈높이를 조절한다: 10조↑ ×0.6 / 1~10조 ×1.0 / 1조↓ ×1.4.
    #   (픽 백테스트에서 검증한 3%/5%/7% 구간과 같은 비율)
    df = df[df["거래대금"] >= cfg.min_trading_value]
    mc = pd.to_numeric(df.get("시가총액"), errors="coerce").fillna(0)
    mult = pd.Series(1.0, index=df.index).where(mc < 10e12, 0.6).where(mc >= 1e12, 1.4)
    floor = cfg.min_change_pct * mult
    df = df.assign(등락률문턱=floor)[lambda d: d["등락률"] >= d["등락률문턱"]]
    if cfg.max_change_pct is not None:
        df = df[df["등락률"] <= cfg.max_change_pct]

    # 정렬·점수 비교는 '문턱 대비 배율' — 대형 +3.6%(문턱3)와 중형 +6%(문턱5)를 같은 잣대로
    df = df.assign(등락률배=(df["등락률"] / df["등락률문턱"]).round(2))
    df = df.sort_values(["등락률배", "거래대금"], ascending=False)

    out = df[["종목명", "종가", "등락률", "등락률배", "거래대금", "시가총액"]].copy()
    out["거래대금(억)"] = (out["거래대금"] / EOK).round(0).astype("int64")
    out["시가총액(조)"] = (out["시가총액"] / 1e12).round(1)
    out = out.drop(columns=["거래대금", "시가총액"]).reset_index()  # 종목코드를 컬럼으로
    out.insert(0, "순위", range(1, len(out) + 1))
    out = out[["순위", "종목코드", "종목명", "종가", "등락률", "등락률배",
               "거래대금(억)", "시가총액(조)"]]
    return date, out
