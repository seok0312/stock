"""주도테마 도출 — 키움 테마 랭킹 위에 '오늘의 종목 신호'를 얹어 동적으로 산출.

접근(theme-first):
  1) ka90001로 테마 랭킹(상위등락률) 수신 → 상위 N개
  2) 각 테마의 구성종목(ka90002)에서 거래대금 합(근사)·상승비율 집계
  3) 우리 스크리너가 뽑은 '주도주 후보(scored)'와 교집합(hit) + 그 점수 합
  4) 테마 점수 = 정규화 가중합 → 주도테마 랭킹 + 대장주(구성종목 거래대금 1위)

주의:
  - 테마 편입은 키움(인포스탁 계열) 사전정의 정적 분류. 오늘 수급으로 갱신되지 않음.
  - ka90001/ka90002 응답에는 수급·프로그램·정확한 거래대금(원)이 없음
    → 거래대금은 현재가×누적거래량으로 근사, 수급은 scored(종목 신호)에서 옴.
  - 한 종목이 여러 테마에 중복 소속(1:N).
"""

from __future__ import annotations

import time

import pandas as pd

from .config import DEFAULT, EOK, Settings
from .kiwoom import _num


def _code6(x) -> str:
    """'112040_AL' 같은 통합거래소 코드 → 6자리 표준 코드."""
    return str(x).split("_")[0].zfill(6)


def _minmax(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or hi - lo == 0:
        return pd.Series(0.0, index=s.index)
    return (s - lo) / (hi - lo)


def derive_leading_themes(client, scored: pd.DataFrame | None = None, cfg: Settings = DEFAULT,
                          stex_tp: str = "1") -> pd.DataFrame:
    """오늘의 주도테마 랭킹을 반환한다. client=KiwoomClient(조회 전용)."""
    groups = client.theme_groups(flu_pl_amt_tp="3", stex_tp=stex_tp)
    if not groups:
        return pd.DataFrame()

    rows = []
    for g in groups:
        stk_num = _num(g.get("stk_num")) or 0
        rising = _num(g.get("rising_stk_num")) or 0
        rows.append({
            "테마코드": g.get("thema_grp_cd"),
            "테마명": g.get("thema_nm"),
            "종목수": int(stk_num),
            "테마등락률": _num(g.get("flu_rt")),
            "상승비율": round(rising / stk_num, 2) if stk_num else 0.0,
            "주요종목": g.get("main_stk"),
        })
    gdf = pd.DataFrame(rows)
    if cfg.theme_min_rising:
        gdf = gdf[gdf["상승비율"] >= cfg.theme_min_rising]
    gdf = gdf.head(cfg.theme_top).reset_index(drop=True)
    if gdf.empty:
        return gdf

    leader_score = {}
    if scored is not None and len(scored):
        leader_score = dict(zip(scored["종목코드"].astype(str), scored["점수"]))
    leader_codes = set(leader_score)

    val_eok, hit_cnt, lead_sum, boss, boss_chg = [], [], [], [], []
    for _, g in gdf.iterrows():
        stocks = client.theme_stocks(g["테마코드"], stex_tp=stex_tp)
        time.sleep(cfg.theme_sleep)
        tv = 0.0
        hits = 0
        lsum = 0.0
        best, best_v = None, -1.0
        for s in stocks:
            code = _code6(s.get("stk_cd"))
            prc = abs(_num(s.get("cur_prc")) or 0)
            qty = _num(s.get("acc_trde_qty")) or 0
            v = prc * qty  # 원 (근사 거래대금)
            tv += v
            if v > best_v:
                best_v, best = v, (s.get("stk_nm"), _num(s.get("flu_rt")))
            if code in leader_codes:
                hits += 1
                lsum += leader_score.get(code, 0) or 0
        val_eok.append(round(tv / EOK))
        hit_cnt.append(hits)
        lead_sum.append(round(lsum, 3))
        boss.append(best[0] if best else "")
        boss_chg.append(best[1] if best else None)

    gdf["거래대금합(억)"] = val_eok
    gdf["주도주수"] = hit_cnt
    gdf["주도주점수합"] = lead_sum
    gdf["대장주"] = boss
    gdf["대장주등락률"] = boss_chg

    w = cfg.theme_weights
    gdf["테마점수"] = (
        w["테마등락률"] * _minmax(gdf["테마등락률"])
        + w["거래대금"] * _minmax(gdf["거래대금합(억)"])
        + w["상승비율"] * _minmax(gdf["상승비율"])
        + w["주도주수"] * _minmax(gdf["주도주수"].astype(float))
        + w["주도주점수"] * _minmax(gdf["주도주점수합"])
    ).round(3)

    gdf = gdf.sort_values("테마점수", ascending=False).reset_index(drop=True)
    gdf.insert(0, "순위", range(1, len(gdf) + 1))
    return gdf[["순위", "테마명", "테마점수", "종목수", "상승비율", "테마등락률",
                "거래대금합(억)", "주도주수", "대장주", "대장주등락률"]]
