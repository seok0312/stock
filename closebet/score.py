"""주도주 점수화 — 스크리너 후보에 수급·프로그램 신호를 붙여 복합 점수 산출.

신호 소스:
  - source="naver" (기본, 앱키 불필요): 네이버 외인·기관 순매수 크롤링
  - source="kiwoom" (.env 필요): 키움 ka10059(외인·기관 순매수) + ka90013(프로그램 순매수)

점수 = Σ (가중치 · 신호_정규화). 순매수/프로그램은 양수만 신호로 사용(min-max 0~1).
외국인·기관 동반 순매수 시 가점. (테마 클러스터링은 이 점수 위에 얹음 — README 참고)
"""

from __future__ import annotations

import time

import pandas as pd

from . import flow as flow_mod
from .config import DEFAULT, Settings

# 점수 컬럼 → 가중치 키. 방향성(순매수/프로그램)은 양수만 신호.
_WEIGHT_COL = {
    "거래대금(억)": "거래대금",
    "등락률": "등락률",
    "외국인순매매": "외국인",
    "기관순매매": "기관",
    "프로그램순매수(억)": "프로그램",
}
_DIRECTIONAL = {"외국인순매매", "기관순매매", "프로그램순매수(억)"}


def _minmax(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or hi - lo == 0:
        return pd.Series(0.0, index=s.index)
    return (s - lo) / (hi - lo)


def _score(df: pd.DataFrame, weights: dict) -> pd.Series:
    total = pd.Series(0.0, index=df.index)
    for col, wkey in _WEIGHT_COL.items():
        w = weights.get(wkey, 0.0)
        if col not in df.columns or not w:
            continue
        s = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        if col in _DIRECTIONAL:
            s = s.clip(lower=0)  # 양수 순매수만
        total = total + w * _minmax(s)
    if {"외국인순매매", "기관순매매"} <= set(df.columns):
        both = ((df["외국인순매매"].fillna(0) > 0) & (df["기관순매매"].fillna(0) > 0)).astype(float)
        total = total + weights.get("동반매수", 0.0) * both
    return total.round(3)


def _attach_kiwoom(df: pd.DataFrame, dt: str, cfg: Settings) -> pd.DataFrame:
    from .kiwoom import KiwoomClient

    kc = KiwoomClient()
    frgn, inst, prog = [], [], []
    for code in df["종목코드"]:
        fl = kc.stock_flow(code, dt) or {}
        pr = kc.stock_program(code, dt) or {}
        frgn.append(fl.get("외국인"))
        inst.append(fl.get("기관"))
        prog.append(pr.get("프로그램순매수억"))
        time.sleep(cfg.kiwoom_sleep)
    df["외국인순매매"] = frgn
    df["기관순매매"] = inst
    df["프로그램순매수(억)"] = [round(p, 1) if p is not None else None for p in prog]
    return df


def _attach_naver(df: pd.DataFrame, cfg: Settings) -> pd.DataFrame:
    flows = flow_mod.get_flows(df["종목코드"].tolist(), sleep=cfg.flow_sleep)
    df["외국인순매매"] = df["종목코드"].map(lambda c: (flows.get(c) or {}).get("외국인순매매"))
    df["기관순매매"] = df["종목코드"].map(lambda c: (flows.get(c) or {}).get("기관순매매"))
    return df


def score_leaders(leaders: pd.DataFrame, cfg: Settings = DEFAULT,
                  source: str = "naver", dt: str | None = None) -> pd.DataFrame:
    """leaders(screener 출력)에 수급 신호를 붙여 점수화·재정렬한다.

    source="kiwoom"이면 키움 공식(ka10059 수급 + ka90013 프로그램), 아니면 네이버 크롤링.
    """
    df = leaders.head(cfg.flow_top).copy()
    if source == "kiwoom":
        df = _attach_kiwoom(df, dt or "", cfg)
    else:
        df = _attach_naver(df, cfg)

    df["점수"] = _score(df, cfg.weights)
    # 순매매 수량은 정수로 표시(지수표기 방지). 결측은 <NA>.
    for c in ("외국인순매매", "기관순매매"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").round().astype("Int64")
    df = df.sort_values("점수", ascending=False).reset_index(drop=True)
    df["순위"] = range(1, len(df) + 1)

    cols = ["순위", "종목코드", "종목명", "종가", "등락률", "거래대금(억)", "외국인순매매", "기관순매매"]
    if "프로그램순매수(억)" in df.columns:
        cols.append("프로그램순매수(억)")
    cols.append("점수")
    return df[cols]
