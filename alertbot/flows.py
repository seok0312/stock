# -*- coding: utf-8 -*-
"""시장 거래대금 + 투자자별 수급 — '시장에 돈과 관심이 얼마나 쏠려 있나' 지표.

소스: 네이버 모바일 지수 API (m.stock.naver.com/api/index/{code}/integration)
  · KOSPI  = 코스피
  · KOSDAQ = 코스닥
  · FUT    = 코스피200 선물      ← 지수코드가 FUT 이다(실측 확인)
장중에는 실시간 누적값, 장 마감 후에는 당일 확정값이 온다.

KRX 데이터마켓(getJsonData / OTP 다운로드)은 이 환경에서 전 경로 'LOGOUT' 응답으로
차단돼 있어 쓸 수 없다. 그래서 '기타법인'은 현재 수집 불가 — TODO 참조.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
API = "https://m.stock.naver.com/api/index/{code}/integration"
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1",
      "Referer": "https://m.stock.naver.com/"}

MARKETS = [("코스피", "KOSPI"), ("코스닥", "KOSDAQ"), ("선물", "FUT")]
EOK = 1e8          # 1억
JO = 1e12          # 1조


def _num(s):
    """'11,490,651백만' / '+18,643' → float. 실패 시 None."""
    if s is None:
        return None
    m = re.search(r"[-+]?[\d,]+(?:\.\d+)?", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def fetch_market(code: str) -> dict | None:
    try:
        r = requests.get(API.format(code=code), headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        j = r.json()
    except Exception:
        return None

    info = {x.get("key"): x.get("value") for x in (j.get("totalInfos") or [])}
    amt_won = None
    raw = info.get("대금") or info.get("거래대금")
    v = _num(raw)
    if v is not None:
        amt_won = v * 1e6 if "백만" in str(raw) else v      # '백만' 단위 표기

    d = fetch_trend(code) or (j.get("dealTrendInfo") or {})
    # 순매수 단위는 억원. 2026-09-03 마감 실측으로 확정:
    #   FUT  개인 -3,768 / 외국인 +8,076 / 기관 -3,229  = 키움 0780 선물과 동일
    #   화면의 계약수(-1,450/+3,077/-1,211) x 1,029.15p x 25만원 과도 오차 1~6% 일치
    # 코스피·코스닥은 키움 ka10051 stex=1(KRX) 과 원 단위까지 같다.
    flow = {"개인": _num(d.get("personalValue")),
            "외국인": _num(d.get("foreignValue")),
            "기관": _num(d.get("institutionalValue"))}
    # 모든 주체의 순매수 합은 0이다(누가 사면 누가 팔았으므로).
    # 네이버가 3분류만 주므로 잔여분 = 기타법인. 키움 폴백일 때만 쓰이는 파생값.
    if all(v is not None for v in flow.values()):
        flow["기타법인"] = -(flow["개인"] + flow["외국인"] + flow["기관"])
    p = j.get("programTrendInfo") or {}
    return {
        "name": j.get("stockName"), "code": code,
        "close": _num(info.get("전일")),
        "bizdate": d.get("bizdate") or p.get("bizdate"),
        "amount_won": amt_won,
        "flow_eok": flow,
        "program_eok": {"차익": _num(p.get("indexDifferenceReal")),
                        "비차익": _num(p.get("indexBiDifferenceReal")),
                        "합계": _num(p.get("indexTotalReal"))},
    }


TREND = "https://m.stock.naver.com/api/index/{code}/trend"


def fetch_trend(code: str) -> dict | None:
    """투자자별 순매수(억원). 네이버 선물 화면이 쓰는 바로 그 엔드포인트다.

    integration 응답 안의 dealTrendInfo 와 같은 값이지만, 장중에 선물 쪽이
    잠정치로 다르게 나온 적이 있어(2026-09-03 16:07 실측) 화면과 같은 경로를 직접 쓴다.
    """
    try:
        r = requests.get(TREND.format(code=code), headers=UA, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def fetch_all() -> dict:
    """{rows: [...], total_amount_won, total_amount_jo, bizdate}"""
    rows, total = [], 0.0
    bizdate = None
    for label, code in MARKETS:
        m = fetch_market(code)
        if not m:
            rows.append({"label": label, "code": code, "error": True})
            continue
        m["label"] = label
        rows.append(m)
        if m["amount_won"]:
            total += m["amount_won"]
        bizdate = bizdate or m.get("bizdate")
    return {"rows": rows, "total_amount_won": total,
            "total_amount_jo": total / JO, "bizdate": bizdate}


FUT_MULTIPLIER = 250_000      # 코스피200 선물 1계약 = 지수 1p당 25만원 (실측 검증 99.1%)
CHART = "https://api.stock.naver.com/chart/domestic/index/{code}"


def _fut_amount_series(count: int = 60):
    """선물 일별 거래대금(원). 네이버 차트는 계약수만 주므로
    계약수 × 종가 × 25만원 으로 환산한다."""
    try:
        import pandas as pd
        r = requests.get(CHART.format(code="FUT"), headers=UA,
                         params={"periodType": "dayCandle", "count": count}, timeout=20)
        if r.status_code != 200:
            return None
        rows = r.json().get("priceInfos") or []
        idx, val = [], []
        for x in rows:
            q, px = x.get("accumulatedTradingVolume"), x.get("closePrice")
            if not q or not px:
                continue
            idx.append(pd.to_datetime(str(x["localDate"])))
            val.append(q * px * FUT_MULTIPLIER)
        return pd.Series(val, index=idx, name="선물") if idx else None
    except Exception:
        return None


def history(days: int = 20, exclude_partial: bool = True, include_futures: bool = True):
    """코스피+코스닥+선물 일별 거래대금 추이(조원).

    exclude_partial: 장 마감(15:40) 전이면 당일 행은 미완성이라 제외한다.
    (장중 누적값을 과거 완결일 평균과 비교하면 크게 왜곡된다)
    """
    try:
        import FinanceDataReader as fdr
        import pandas as pd
    except Exception:
        return None
    start = (datetime.now(KST) - timedelta(days=days * 2 + 20)).strftime("%Y-%m-%d")
    out = {}
    for label, code in (("코스피", "KS11"), ("코스닥", "KQ11")):
        try:
            d = fdr.DataReader(code, start)
            if "Amount" in d.columns:
                out[label] = d["Amount"]
        except Exception:
            pass
    if not out:
        return None
    df = pd.DataFrame(out)
    if include_futures:
        f = _fut_amount_series(count=days * 3 + 30)
        if f is not None:
            df = df.join(f, how="left")
    df = df.dropna()
    now = datetime.now(KST)
    if exclude_partial and len(df):
        closed = now.hour > 15 or (now.hour == 15 and now.minute >= 40)
        if df.index[-1].date() == now.date() and not closed:
            df = df.iloc[:-1]
    if not len(df):
        return None
    df["합계"] = df.sum(axis=1)
    return df.tail(days) / JO


def summary(short: int = 5, long: int = 20):
    """현재 거래대금 + 종가 완결일 평균 대비(폴백용).

    store 에 같은 시각 표본이 쌓이기 전까지 쓰는 경로다.
    5일(단기 국면)과 20일(평상시)을 함께 낸다.
    """
    cur = fetch_all()
    h = history(long, include_futures=True)
    ref, ref_market = None, {}
    if h is not None and len(h):
        hs = h.tail(short)
        today = sum(m["amount_won"] for m in cur["rows"]
                    if not m.get("error") and m["amount_won"]) / JO
        a_s, a_l = float(hs["합계"].mean()), float(h["합계"].mean())
        ref = {"today_jo": today, "with_futures": "선물" in h.columns,
               "avg_short": a_s, "n_short": len(hs),
               "pct_short": (today / a_s - 1) * 100 if a_s else None,
               "avg_long": a_l, "n_long": len(h),
               "pct_long": (today / a_l - 1) * 100 if a_l else None}
        for m in cur["rows"]:
            lab = m.get("label")
            if m.get("error") or lab not in h.columns or not m.get("amount_won"):
                continue
            t = m["amount_won"] / JO
            s_avg, l_avg = float(hs[lab].mean()), float(h[lab].mean())
            d = {"today_jo": t}
            if s_avg:
                d["pct_short"] = (t / s_avg - 1) * 100
            if l_avg:
                d["pct_long"] = (t / l_avg - 1) * 100
            if len(d) > 1:
                ref_market[lab] = d
    cur["ref"] = ref
    cur["ref_market"] = ref_market
    apply_kiwoom(cur)
    return cur


# 순매수 소스. "kiwoom" 이면 현물(코스피·코스닥)만 키움 KRX+NXT 통합으로 덮어쓴다.
# 선물은 어느 쪽이든 네이버다 — 넥스트레이드는 상장주권만 취급하고 파생상품은
# 다루지 않으므로 선물은 KRX 가 곧 시장 전체다. 즉 현물 통합 + 선물 KRX 조합은
# 둘 다 '해당 상품의 시장 전체'라서 범위가 어긋나지 않는다.
# "naver" 로 두면 전부 네이버(=KRX 만)로 통일된다. 대신 현물에서 NXT 가 빠진다
# (2026-09-03 마감 코스피 외국인: KRX -4,232억 vs 통합 -1,836억).
FLOW_SOURCE = "kiwoom"


def apply_kiwoom(cur: dict) -> str:
    """순매수·프로그램을 키움(KRX+NXT 통합) 값으로 덮어쓴다. 반환: 실제 사용한 소스명.

    네이버는 KRX 거래분만 주므로 NXT 가 통째로 빠진다. 외국인처럼 NXT 비중이 큰
    주체는 방향이 뒤집히기도 해서(kflows 모듈 주석의 실측 참조) 키움을 우선한다.
    키움이 실패하면 네이버 값을 그대로 두고 'naver' 를 반환한다.
    """
    cur["flow_src"] = "naver"
    if FLOW_SOURCE != "kiwoom":
        return "naver"
    try:
        import kflows
        k = kflows.fetch()
    except Exception:
        k = None
    if not k:
        return "naver"
    used = False
    for m in cur.get("rows", []):
        d = k.get(m.get("label"))
        if not d:
            continue
        if d.get("flow"):
            m["flow_eok"] = dict(d["flow"]); used = True
        if d.get("program"):
            m["program_eok"] = dict(d["program"]); used = True
        if d.get("by_exchange"):
            m["by_exchange"] = d["by_exchange"]
    if used:
        cur["flow_src"] = "kiwoom"
    return cur["flow_src"]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    r = fetch_all()
    print(f"기준일 {r['bizdate']}\n")
    print(f"{'시장':<8}{'거래대금':>14}{'개인':>12}{'외국인':>12}{'기관':>12}")
    for m in r["rows"]:
        if m.get("error"):
            print(f"{m['label']:<8} 조회 실패"); continue
        f = m["flow_eok"]
        print(f"{m['label']:<8}{m['amount_won']/JO:>12.2f}조"
              f"{(f['개인'] or 0):>+11,.0f}억{(f['외국인'] or 0):>+11,.0f}억"
              f"{(f['기관'] or 0):>+11,.0f}억")
    print(f"\n총 거래대금(코스피+코스닥+선물) = {r['total_amount_jo']:.2f}조")

    h = history(15)
    if h is not None:
        print(f"\n■ 최근 거래대금 추이 (코스피+코스닥, 조원)")
        for d, row in h.iterrows():
            bar = "█" * int(row["합계"] / 2)
            print(f"   {d:%m-%d}  코스피 {row['코스피']:>6.2f}  코스닥 {row['코스닥']:>5.2f}  "
                  f"합계 {row['합계']:>6.2f}조  {bar}")
        avg = h["합계"].mean()
        cur = h["합계"].iloc[-1]
        print(f"\n   최근 {len(h)}일 평균 {avg:.2f}조 · 최신 {cur:.2f}조 "
              f"({(cur/avg-1)*100:+.1f}% vs 평균)")
