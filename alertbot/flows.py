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

    d = j.get("dealTrendInfo") or {}
    # 순매수 단위는 억원 (네이버 표기 관례)
    flow = {"개인": _num(d.get("personalValue")),
            "외국인": _num(d.get("foreignValue")),
            "기관": _num(d.get("institutionalValue"))}
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


def history(days: int = 20, exclude_partial: bool = True):
    """코스피+코스닥 일별 거래대금 추이(조원). 선물은 일별 무료소스가 없어 제외.

    exclude_partial: 장 마감(15:40) 전이면 당일 행은 미완성이라 제외한다.
    (장중 FDR Amount 는 그 시점까지의 누적이라 평균 비교를 왜곡한다)
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
    df = pd.DataFrame(out).dropna()
    now = datetime.now(KST)
    if exclude_partial and len(df):
        closed = now.hour > 15 or (now.hour == 15 and now.minute >= 40)
        if df.index[-1].date() == now.date() and not closed:
            df = df.iloc[:-1]
    if not len(df):
        return None
    df["합계"] = df.sum(axis=1)
    return df.tail(days) / JO


def summary(days: int = 20):
    """현재 거래대금 + 과거 평균 대비 온도. render 에서 쓰기 좋은 형태."""
    cur = fetch_all()
    h = history(days)
    ref = None
    if h is not None and len(h):
        avg = float(h["합계"].mean())
        # 선물 제외 비교(과거 추이가 코스피+코스닥이므로 같은 기준으로 맞춘다)
        spot = sum(m["amount_won"] for m in cur["rows"]
                   if not m.get("error") and m["label"] != "선물" and m["amount_won"])
        ref = {"avg_jo": avg, "spot_jo": spot / JO,
               "vs_avg_pct": (spot / JO / avg - 1) * 100 if avg else None,
               "days": len(h)}
    cur["ref"] = ref
    return cur


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
