# -*- coding: utf-8 -*-
"""주도주 팩터 백테스트 — '데이터로 주도주를 고를 수 있나'의 1차 검증.

질문: 마감 시점에 (1)거래대금 배율 (2)등락률 (3)수급강도로 종목을 줄 세우면
      종가베팅 수익(당일 종가 → 익일 시가)을 예측하는가?

패널: 오늘 거래대금 상위 N종목 × 최근 ~90거래일
  - 수급/거래대금/등락률: 키움 ka10059 (종목당 1콜, 100일, 금액=백만원)
  - 익일 시가: FDR DataReader (네이버 소스)
주의: 오늘(잠정)과 상장 초기 구간은 제외. '오늘 상위 250'을 과거로 소급하므로
      생존/선택 편향이 있다 — 팩터 상대 비교용이지 절대 수익률 추정용이 아니다.

실행: python3 factor_backtest.py [--n 250] [--days 90]
출력: 팩터별 일별 IC(스피어만) / 5분위 오버나이트 수익 / 상위5 포트폴리오 비교
저장: data/factor_bt_panel.json (재분석용)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for _p in (os.path.abspath(os.path.join(HERE, "..")), HERE):
    if os.path.isdir(os.path.join(_p, "closebet")) and _p not in sys.path:
        sys.path.insert(0, _p)

MIN_CHG = 3.0          # 후보: 등락률 3% 이상
MIN_AMT_MN = 30_000    # + 거래대금 300억(백만원 단위) 이상
MIN_CANDS = 8          # 그날 후보가 이보다 적으면 IC 계산 제외


def build_panel(n_stocks: int, days: int) -> pd.DataFrame:
    from notify import load_env
    load_env(os.path.join(HERE, ".env"), "/opt/upbit_bot/.env")
    from closebet import market as cbm
    from closebet.kiwoom import KiwoomClient

    snap = cbm.get_snapshot("KRX")
    univ = snap.sort_values("거래대금", ascending=False).head(n_stocks)
    today = datetime.now(KST).strftime("%Y%m%d")
    kc = KiwoomClient()

    rows = []
    for i, (code, s) in enumerate(univ.iterrows()):
        try:
            d, _ = kc.request("ka10059",
                              {"dt": today, "stk_cd": code, "amt_qty_tp": "1",
                               "trde_tp": "0", "unit_tp": "1"},
                              endpoint="/api/dostk/stkinfo")
        except Exception as e:
            print(f"  {code} kiwoom 실패: {str(e)[:60]}")
            continue
        for r in d.get("stk_invsr_orgn") or []:
            dt = r.get("dt")
            if not dt or dt >= today:          # 오늘(잠정) 제외
                continue

            def num(k):
                v = str(r.get(k) or "").replace(",", "").replace("--", "-")
                try:
                    return float(v.lstrip("+"))
                except ValueError:
                    return None

            rows.append({"code": code, "name": s["종목명"], "date": dt,
                         "chg": num("flu_rt"), "amt_mn": num("acc_trde_prica"),
                         "frgn_mn": num("frgnr_invsr"), "orgn_mn": num("orgn")})
        if (i + 1) % 50 == 0:
            print(f"  kiwoom {i+1}/{len(univ)}")
        time.sleep(0.2)

    df = pd.DataFrame(rows)
    df = df[df["date"] >= sorted(df["date"].unique())[-days] if len(df["date"].unique()) > days else df["date"].min()]

    # 익일 시가/당일 종가 (FDR·네이버)
    start = (datetime.now(KST) - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    px = {}
    from closebet import market as cbm2
    for i, code in enumerate(df["code"].unique()):
        try:
            h = cbm2.get_price_history(code, start)
            px[code] = h[["Open", "Close"]]
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            print(f"  fdr {i+1}")
        time.sleep(0.1)

    def overnight(row):
        h = px.get(row["code"])
        if h is None:
            return None
        d = pd.Timestamp(row["date"])
        if d not in h.index:
            return None
        pos = h.index.get_loc(d)
        if pos + 1 >= len(h):
            return None
        c, o1 = h.iloc[pos]["Close"], h.iloc[pos + 1]["Open"]
        return (o1 / c - 1) * 100 if c else None

    df["r_on"] = df.apply(overnight, axis=1)

    # 팩터: 거래대금 배율(자기 20일 평균 대비) / 등락률 / 수급강도
    df = df.sort_values(["code", "date"])
    df["amt_ma20"] = df.groupby("code")["amt_mn"].transform(
        lambda x: x.shift(1).rolling(20, min_periods=10).mean())
    df["f_amt"] = df["amt_mn"] / df["amt_ma20"]
    df["f_chg"] = df["chg"]
    df["f_flow"] = (df["frgn_mn"].fillna(0) + df["orgn_mn"].fillna(0)) / df["amt_mn"] * 100
    return df


def analyze(df: pd.DataFrame):
    cands = df[(df["f_chg"] >= MIN_CHG) & (df["amt_mn"] >= MIN_AMT_MN)
               & df["r_on"].notna() & df["f_amt"].notna()].copy()
    print(f"\n후보 관측치 {len(cands)}건 · {cands['date'].nunique()}일 · "
          f"{cands['code'].nunique()}종목 (등락률>={MIN_CHG}% & 거래대금>={MIN_AMT_MN/100:.0f}억)")

    factors = [("거래대금배율", "f_amt"), ("등락률", "f_chg"), ("수급강도", "f_flow")]
    print(f"\n{'팩터':<14}{'평균IC':>8}{'IC>0':>7}{'t':>6}   5분위 오버나이트(하위→상위)")
    for name, col in factors:
        ics = []
        for _, g in cands.groupby("date"):
            if len(g) < MIN_CANDS:
                continue
            ics.append(g[col].rank().corr(g["r_on"].rank()))
        ics = pd.Series(ics).dropna()
        q = cands.groupby(cands.groupby("date")[col].transform(
            lambda x: pd.qcut(x.rank(method="first"), 5, labels=False, duplicates="drop")
            if len(x) >= 5 else pd.Series([None] * len(x), index=x.index)))["r_on"].mean()
        qs = " ".join(f"{q.get(i, float('nan')):+.2f}" for i in range(5))
        t = ics.mean() / (ics.std() / len(ics) ** .5) if len(ics) > 2 and ics.std() else 0
        print(f"{name:<14}{ics.mean():>+8.3f}{(ics > 0).mean()*100:>6.0f}%{t:>6.1f}   [{qs}]%")

    # 상위5 포트폴리오 비교 (매일 후보 중 상위 5 종목 균등)
    def port(colspec):
        rets = []
        for _, g in cands.groupby("date"):
            if len(g) < MIN_CANDS:
                continue
            sc = sum(g[c].rank(pct=True) * w for c, w in colspec)
            rets.append(g.loc[sc.nlargest(5).index, "r_on"].mean())
        s = pd.Series(rets)
        return s.mean(), (s > 0).mean() * 100, len(s)

    print(f"\n{'전략(매일 상위5)':<24}{'평균%':>8}{'승률':>7}{'일수':>6}")
    base = cands.groupby("date")["r_on"].mean()
    print(f"{'후보 전체 평균(기준선)':<24}{base.mean():>+8.2f}{(base > 0).mean()*100:>6.0f}%{len(base):>6}")
    for label, spec in [
            ("거래대금배율 단독", [("f_amt", 1)]),
            ("등락률 단독", [("f_chg", 1)]),
            ("수급강도 단독", [("f_flow", 1)]),
            ("합성 40/25/35", [("f_amt", .40), ("f_chg", .25), ("f_flow", .35)]),
            ("합성 균등", [("f_amt", 1/3), ("f_chg", 1/3), ("f_flow", 1/3)])]:
        m, w, n = port(spec)
        print(f"{label:<24}{m:>+8.2f}{w:>6.0f}%{n:>6}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 250
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 90
    print(f"[{datetime.now(KST):%m-%d %H:%M}] 패널 수집 시작 — {n}종목 × ~{days}일")
    df = build_panel(n, days)
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    df.to_json(os.path.join(HERE, "data", "factor_bt_panel.json"),
               orient="records", force_ascii=False)
    print(f"패널 {len(df)}행 저장")
    analyze(df)
