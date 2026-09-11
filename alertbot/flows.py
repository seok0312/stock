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
    today = datetime.now(KST).strftime("%Y%m%d")
    stale = bool(d.get("bizdate")) and d.get("bizdate") != today
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
    # stale(기준일이 전일)이어도 여기서 지우지 않는다 — 개장 전 슬롯은 "전일 확정"을
    # 보여주기로 했으므로(사용자 결정 09-08), 기준일 통일은 summary()가 담당한다.
    # 단 전부 0이면 '오늘 자정 리셋 직후, 거래 전' 상태이므로 데이터 없음으로 본다.
    if flow and all((v or 0) == 0 for v in flow.values()):
        flow = {}
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
    # 기준일 통일: 표의 모든 값은 flow_asof 하루의 것이어야 한다.
    # (08:50 처럼 키움은 오늘 NXT, 네이버 선물·거래대금은 아직 전일인 혼합 상태 방지)
    tgt = cur.get("flow_asof")
    today = datetime.now(KST).strftime("%Y%m%d")
    for m in cur.get("rows", []):
        if m.get("error") or not m.get("bizdate") or not tgt:
            continue
        if m["bizdate"] != tgt:
            m["amount_won"] = None
            if not m.get("_kiwoom"):
                m["flow_eok"] = {}
    if tgt and tgt != today:
        cur["asof_label"] = f"{tgt[4:6]}-{tgt[6:8]} 마감"
        cur["total_amount_jo"] = sum((m.get("amount_won") or 0)
                                     for m in cur["rows"] if not m.get("error")) / JO
    else:
        cur["asof_label"] = None
    return cur


# 순매수 소스. "kiwoom" 이면 현물(코스피·코스닥)만 키움 KRX+NXT 통합으로 덮어쓴다.
# 선물은 어느 쪽이든 네이버다 — 넥스트레이드는 상장주권만 취급하고 파생상품은
# 다루지 않으므로 선물은 KRX 가 곧 시장 전체다. 즉 현물 통합 + 선물 KRX 조합은
# 둘 다 '해당 상품의 시장 전체'라서 범위가 어긋나지 않는다.
# "naver" 로 두면 전부 네이버(=KRX 만)로 통일된다. 대신 현물에서 NXT 가 빠진다
# (2026-09-03 마감 코스피 외국인: KRX -4,232억 vs 통합 -1,836억).
FLOW_SOURCE = "kiwoom"


def _prev_trading_date(now=None) -> str | None:
    """직전 거래일(YYYYMMDD). 네이버 bizdate 는 자정~개장 사이에 오늘로 먼저 넘어가
    믿을 수 없어(2026-09-08 08:24 실측: 오늘 날짜 + 전부 0), 거래일 캘린더로 직접 센다."""
    try:
        import quotes
    except Exception:
        return None
    d = (now or datetime.now(KST)).date() - timedelta(days=1)
    for _ in range(15):
        if quotes.is_trading_date(d):
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
    return None


def _fill_prev_fut(cur: dict, prev: str) -> None:
    """전일 확정 표시 때 선물 열 보충 — 네이버가 이미 오늘로 넘어가 전일 선물을 못 주면
    전일 스냅샷(20:00/19:00/16:30 순)에 저장된 값을 쓴다."""
    row = next((m for m in cur.get("rows", [])
                if m.get("label") == "선물" and not m.get("error")), None)
    if row is None or row.get("flow_eok"):
        if row is not None:
            row["bizdate"] = prev if row.get("flow_eok") else row.get("bizdate")
        return
    try:
        import store
        best = None
        for r in store.load_all():
            if r.get("date") == prev and (r.get("flow") or {}).get("선물"):
                if best is None or str(r.get("slot")) > str(best.get("slot")):
                    best = r
        if best:
            row["flow_eok"] = dict(best["flow"]["선물"])
            row["bizdate"] = prev
            row["_kiwoom"] = True          # 기준일 통일 필터가 지우지 않도록
    except Exception:
        pass


def apply_kiwoom(cur: dict) -> str:
    """순매수·프로그램을 키움(KRX+NXT 통합) 값으로 덮어쓴다. 반환: 실제 사용한 소스명.

    네이버는 KRX 거래분만 주므로 NXT 가 통째로 빠진다. 외국인처럼 NXT 비중이 큰
    주체는 방향이 뒤집히기도 해서(kflows 모듈 주석의 실측 참조) 키움을 우선한다.
    키움이 실패하면 네이버 값을 그대로 두고 'naver' 를 반환한다.
    """
    cur["flow_src"] = "naver"
    today = datetime.now(KST).strftime("%Y%m%d")
    cur["flow_asof"] = today
    if FLOW_SOURCE != "kiwoom":
        return "naver"
    try:
        import kflows
        k = kflows.fetch()
    except Exception:
        k = None
    # 오늘 거래가 아직 없으면(새벽·주말) 전일 확정치로 전환한다 —
    # 개장 전 알림은 '전일이 어떻게 끝났나'를 보여주기로 했다(2026-09-08 결정).
    if not (k and any(v.get("flow") for v in k.values())):
        prev = _prev_trading_date()
        if prev and prev != today:
            try:
                k2 = kflows.fetch(base_dt=prev)
            except Exception:
                k2 = None
            if k2 and any(v.get("flow") for v in k2.values()):
                k = k2
                cur["flow_asof"] = prev
    if not k:
        return "naver"
    if cur.get("flow_asof") != today:
        _fill_prev_fut(cur, cur["flow_asof"])
    used = False
    for m in cur.get("rows", []):
        d = k.get(m.get("label"))
        if not d:
            continue
        if d.get("flow"):
            m["flow_eok"] = dict(d["flow"]); used = True
            m["_kiwoom"] = True
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


# ── 15:30 마감 알림 ↔ 16:30 확정 대조 (09-11 사용자 요청) ────────────
# 1530 슬롯이 발송한 수급을 저장해 두고, 1630 은 확정치가 유의미하게 다를 때만
# 재발송한다. 거래대금은 NXT 애프터(15:40~) 체결로 항상 늘어나므로 대조에서 제외 —
# 대조 대상은 투자자별 순매수(개인/외국인/기관/기타법인)와 프로그램 비차익.
import json as _json
import os as _os

_CLOSE_REF = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                           "data", "flow_close_ref.json")


def _close_ref(fl) -> dict:
    out = {}
    for m in (fl or {}).get("rows") or []:
        if m.get("error"):
            continue
        d = {}
        for k, v in (m.get("flow_eok") or {}).items():
            if v is not None:
                d[k] = round(v)
        p = (m.get("program_eok") or {}).get("비차익")
        if p is not None:
            d["비차익"] = round(p)
        if m.get("amount_won"):
            d["_amt"] = round(m["amount_won"] / 1e8)   # 기록용 (대조 제외)
        if d:
            out[m.get("label") or "?"] = d
    return out


def save_close_ref(fl, now=None) -> None:
    now = now or datetime.now(KST)
    _os.makedirs(_os.path.dirname(_CLOSE_REF), exist_ok=True)
    with open(_CLOSE_REF, "w", encoding="utf-8") as f:
        _json.dump({"date": now.strftime("%Y%m%d"), "ref": _close_ref(fl)},
                   f, ensure_ascii=False)


def close_ref_diff(fl, now=None):
    """1530 저장분 대비 최대 변화(억). 오늘 저장분이 없으면 None(=그냥 발송)."""
    now = now or datetime.now(KST)
    try:
        with open(_CLOSE_REF, encoding="utf-8") as f:
            doc = _json.load(f)
    except Exception:
        return None
    if doc.get("date") != now.strftime("%Y%m%d"):
        return None
    ref, cur = doc.get("ref") or {}, _close_ref(fl)
    diff = 0
    for mk in set(ref) | set(cur):
        a, b = ref.get(mk) or {}, cur.get(mk) or {}
        for k in set(a) | set(b):
            if k == "_amt":
                continue
            diff = max(diff, abs((a.get(k) or 0) - (b.get(k) or 0)))
    return diff
