# -*- coding: utf-8 -*-
"""태그 전략 전방검증 — 실전 데이터로 갭/손바뀜 룰의 유효성을 채점 (09-28 사용자).

record  (15:40 크론): 당일 과열 종목(등락률 +5%↑ · 거래대금 50억↑ · 대금 상위 60)
        전체의 3주체 태그를 기록한다. 태그 없는 종목도 기록 — 무태그가 대조군.
evaluate(16:10 크론): 기록 건에 결과를 붙인다 — r_o1(익일 시가/종가매수),
        r_c5(5거래일 후 종가). 태그와 무관하게 둘 다 채점해 두면 룰 변경 시 재활용.
report  (수동): 태그별 평균/승률 — 백테스트(갭 +0.83%/53%, 손바뀜 +1.71%/53%,
        무차별 +0.16%/46%, 비용 0.2% 차감)와 대조해 실전 유효성 판정.

저장: data/tag_picks.jsonl (한 줄 = 한 종목-일). 실전 잠정치(15:40)로 태그를
판정하므로 확정치 기반 백테스트와의 괴리 자체가 검증 대상이다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.abspath(os.path.join(HERE, "..")), HERE):
    if os.path.isdir(os.path.join(_p, "closebet")) and _p not in sys.path:
        sys.path.insert(0, _p)

PATH = os.path.join(HERE, "data", "tag_picks.jsonl")
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1",
      "Referer": "https://m.stock.naver.com/"}
COST = 0.20          # 왕복 비용(수수료+거래세+슬리피지) — report 에서 차감 표기


def _kospi_above60():
    """코스피 종가 vs 60일 이평 국면 — 10-02 검증: 갭 태그는 60일선 아래에서
    엣지 소멸(위 +1.04%/56% vs 아래 +0.31%/46%, welch t=+2.0), 손바뀜은 비유의.
    기록만 쌓고 룰 확정은 전방검증 판독에서. 실패 시 None(기록은 계속)."""
    closes = []
    try:
        for p in range(1, 5):
            r = requests.get("https://m.stock.naver.com/api/index/KOSPI/price",
                             params={"pageSize": 20, "page": p}, headers=UA,
                             timeout=12).json()
            if not isinstance(r, list) or not r:
                break
            for x in r:
                try:
                    closes.append(float(str(x["closePrice"]).replace(",", "")))
                except (KeyError, ValueError):
                    continue
        if len(closes) < 60:
            return None
        cur, ma60 = closes[0], sum(closes[:60]) / 60
        return {"kospi": cur, "ma60": round(ma60, 2), "above60": cur > ma60}
    except Exception:
        return None


def record(now=None, force=False) -> int:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    if not force and os.path.exists(PATH):        # 재실행 멱등 — 같은 날 중복 기록 방지
        for line in open(PATH, encoding="utf-8"):
            if f'"date": "{today}"' in line:
                print(f"[record] {today} 이미 기록됨 — 생략")
                return 0
    from closebet import market
    import leaders
    leaders._load_keys()
    from closebet.kiwoom import KiwoomClient
    snap = market.get_snapshot()
    df = snap.reset_index()
    code_col = next(c for c in ("종목코드", "index") if c in df.columns)
    hot = df[(df["등락률"] >= 5) & (df["거래대금"] >= 50e8) & df["종목명"].notna()]
    hot = hot.sort_values("거래대금", ascending=False).head(60)
    # 주도주 플래그(09-30 사용자 '주도주 한정' 정책) — A안 문턱: 시총구간 배율
    # (10조↑ ×0.6 / 1~10조 ×1.0 / 1조↓ ×1.4, 기준 2%) & 거래대금 상위 20위 이내.
    # 몇 주 뒤 '주도주 한정 vs 전체 과열'을 실측 비교하는 근거가 된다.
    import pandas as _pd
    _mc = _pd.to_numeric(df["시가총액"], errors="coerce").fillna(0)
    _mult = _pd.Series(1.0, index=df.index).where(_mc < 10e12, 0.6).where(_mc >= 1e12, 1.4)
    _lead = df[df["등락률"] >= 2.0 * _mult].sort_values("거래대금", ascending=False)
    lead_codes = {str(df.loc[i][code_col]).zfill(6) if code_col in df.columns else str(i)
                  for i in _lead.index[:20]}
    # 2군 밴드(10-02 사용자 '대형주는 2~3%도 주도주'): 티어문턱(2%×배율)~5% 미만
    # · 대금 50억↑ 상위 20. 백테스트상 엣지는 과열의 1/3(태그1 +0.27%, 2024 음수)
    # 이라 베팅 트리거 아님 — band="tier" 로 기록만 쌓아 전방 판독.
    tier = df[(df["등락률"] >= 2.0 * _mult) & (df["등락률"] < 5)
              & (df["거래대금"] >= 50e8) & df["종목명"].notna()]
    tier = tier.sort_values("거래대금", ascending=False).head(20)
    reg = _kospi_above60()            # 하루 한 번 — 전 행 공통 국면 플래그
    kc = KiwoomClient()
    n, written = 0, set()
    with open(PATH, "a", encoding="utf-8") as f:
      for band, part in (("hot5", hot), ("tier", tier)):
        for _, x in part.iterrows():
            code = str(x[code_col]).zfill(6)
            if code in written:
                continue
            written.add(code)
            try:
                d, _ = kc.request("ka10059",
                                  {"dt": today, "stk_cd": code, "amt_qty_tp": "1",
                                   "trde_tp": "0", "unit_tp": "1"},
                                  endpoint="/api/dostk/stkinfo")
                rows = sorted((r for r in d.get("stk_invsr_orgn") or []),
                              key=lambda r: r.get("dt") or "", reverse=True)
                tag = leaders._flow_tag(rows, today)
            except Exception:
                tag = None
            f.write(json.dumps({
                "date": today, "code": code, "leader": code in lead_codes,
                "name": str(x["종목명"]),
                "chg": round(float(x["등락률"]), 2),
                "close": float(x["종가"]) if x.get("종가") == x.get("종가") else None,
                "amt_eok": round(float(x["거래대금"]) / 1e8),
                "above60": reg["above60"] if reg else None,
                "band": band,
                "tag": tag}, ensure_ascii=False) + "\n")
            n += 1
            time.sleep(0.25)
    n_tier = sum(1 for c in written if c not in
                 {str(x[code_col]).zfill(6) for _, x in hot.iterrows()})
    print(f"[record] {today} 과열 {n - n_tier} + 2군(티어~5%) {n_tier} 기록 (당일 잠정치)")
    return n


def nxt_log(now=None, write=True) -> int:
    """08:52 크론: 전 거래일 갭(태그1) 기록 종목의 NXT 프리마켓 가격 스냅샷.

    ka10080 분봉에 프리마켓(08:00~08:50) 봉이 없어 과거 소급 검증이 불가(10-02
    확인) — 전방 수집으로 'NXT 막판가에 매도 vs 09시 동시호가 매도'를 판독할
    근거를 쌓는다. NXT 거래대금 상위(ka10032)에 없으면 그 자체가 유동성 없음
    신호라 in_nxt_top=False 로 남긴다. → data/nxt_prelog.jsonl"""
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    recs = []
    try:
        recs = [json.loads(l) for l in open(PATH, encoding="utf-8")]
    except FileNotFoundError:
        pass
    prev = sorted({r["date"] for r in recs if r["date"] < today})
    if not prev:
        print("[nxt_log] 이전 기록 없음")
        return 0
    last = prev[-1]
    targets = {r["code"]: r["name"] for r in recs
               if r["date"] == last and r.get("tag") == "갭"}
    if not targets:
        print(f"[nxt_log] {last} 갭 태그 없음 — 생략")
        return 0
    import nxt
    rows = {r["code"]: r for r in nxt.fetch_quant()}
    out_path = os.path.join(HERE, "data", "nxt_prelog.jsonl")
    n = 0
    lines = []
    for code, name in targets.items():
        r = rows.get(code)
        lines.append({"date": today, "sig_date": last, "code": code, "name": name,
                      "nxt_price": (r or {}).get("price"),
                      "nxt_chg": (r or {}).get("chg_pct"),
                      "nxt_amt_eok": (r or {}).get("amt_eok"),
                      "in_nxt_top": bool(r)})
        n += 1
    if write:
        with open(out_path, "a", encoding="utf-8") as f:
            for x in lines:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
    hit = sum(1 for x in lines if x["in_nxt_top"])
    print(f"[nxt_log] {today} 전일({last}) 갭 {n}종목 · NXT 상위 포착 {hit}"
          + ("" if write else " (dry)"))
    return n


def _daily_px(code: str, pages: int = 1) -> list:
    """[(YYYYMMDD, open, close)] 최신순."""
    out = []
    try:
        for p in range(1, pages + 1):
            r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/price",
                             params={"pageSize": 20, "page": p}, headers=UA,
                             timeout=12).json()
            if not isinstance(r, list):
                break
            for x in r:
                dt = (x.get("localTradedAt") or "")[:10].replace("-", "")
                try:
                    out.append((dt, float(str(x["openPrice"]).replace(",", "")),
                                float(str(x["closePrice"]).replace(",", ""))))
                except (ValueError, KeyError):
                    continue
    except Exception:
        pass
    return out


def evaluate() -> int:
    import quotes
    dates, _ = quotes._load_trading_dates()
    tdays = [d.strftime("%Y%m%d") for d in sorted(dates)]
    recs = []
    try:
        for line in open(PATH, encoding="utf-8"):
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
    except FileNotFoundError:
        print("[evaluate] 기록 없음")
        return 0
    pend = [r for r in recs if r.get("close") and
            (r.get("r_o1") is None or r.get("r_c1") is None
             or r.get("r_c5") is None or r.get("r_c10") is None)]
    by_code: dict = {}
    for r in pend:
        by_code.setdefault(r["code"], []).append(r)
    n = 0
    for code, rs in by_code.items():
        px = {d: (o, c) for d, o, c in _daily_px(code, pages=2)}  # 20일 커버
        for r in rs:
            if r["date"] not in tdays:
                continue
            i = tdays.index(r["date"])
            if r.get("r_o1") is None and i + 1 < len(tdays):
                nx = px.get(tdays[i + 1])
                if nx:
                    r["r_o1"] = round((nx[0] / r["close"] - 1) * 100, 2)
                    n += 1
            if r.get("r_c1") is None and i + 1 < len(tdays):
                d1 = px.get(tdays[i + 1])
                if d1:
                    r["r_c1"] = round((d1[1] / r["close"] - 1) * 100, 2)
                    n += 1
            if r.get("r_c5") is None and i + 5 < len(tdays):
                d5 = px.get(tdays[i + 5])
                if d5:
                    r["r_c5"] = round((d5[1] / r["close"] - 1) * 100, 2)
                    n += 1
            if r.get("r_c10") is None and i + 10 < len(tdays):
                dx = px.get(tdays[i + 10])
                if dx:
                    r["r_c10"] = round((dx[1] / r["close"] - 1) * 100, 2)
                    n += 1
        time.sleep(0.05)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, PATH)
    print(f"[evaluate] 결과 {n}건 채점 (누적 기록 {len(recs)}건)")
    return n


def evaluate_decisions() -> int:
    """decision_log 채점(09-29) — ①비중: 그날 과열 종목 평균 익일시가 수익(t1)
    ②타이밍: EWY 퍼프 14:30·15:30 → 익일 09:00 수익(r_1430/r_1530).
    종목 채점은 tag_picks 가 담당. 완료 필드는 재계산하지 않는다."""
    path = os.path.join(HERE, "data", "decision_log.jsonl")
    recs = []
    try:
        for line in open(path, encoding="utf-8"):
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
    except FileNotFoundError:
        return 0
    # ① 비중 타깃: tag_picks 날짜별 r_o1 평균
    day_ret = {}
    try:
        agg = {}
        for line in open(PATH, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("r_o1") is not None:
                agg.setdefault(r["date"], []).append(r["r_o1"])
        day_ret = {d: sum(v) / len(v) for d, v in agg.items() if len(v) >= 3}
    except FileNotFoundError:
        pass
    # ② 타이밍: 필요한 날짜만 EWY 30분봉 한 번에
    need = sorted({r["date"] for r in recs
                   if r.get("slot") == "1430" and r.get("r_1430") is None})
    grid = {}
    if need:
        try:
            import quotes
            ex = quotes.exchange()
            since = int(datetime.strptime(need[0], "%Y%m%d")
                        .replace(tzinfo=KST).timestamp() * 1000)
            oh = ex.fetch_ohlcv("EWY/USDT:USDT", "30m", since=since, limit=1500)
            for c in oh or []:
                t = datetime.fromtimestamp(c[0] / 1000, tz=KST)
                grid[(t.strftime("%Y%m%d"), t.strftime("%H:%M"))] = c[1]
        except Exception:
            grid = {}
    tdays = sorted({d for d, _ in grid})
    n = 0
    for r in recs:
        d = r.get("date")
        if r.get("t1") is None and d in day_ret:
            r["t1"] = round(day_ret[d], 3)
            n += 1
        if r.get("slot") == "1430" and r.get("r_1430") is None and d in tdays:
            i = tdays.index(d)
            if i + 1 < len(tdays):
                p14, p15 = grid.get((d, "14:30")), grid.get((d, "15:30"))
                n9 = grid.get((tdays[i + 1], "09:00"))
                if p14 and p15 and n9:
                    r["r_1430"] = round((n9 / p14 - 1) * 100, 3)
                    r["r_1530"] = round((n9 / p15 - 1) * 100, 3)
                    n += 1
    if n:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    print(f"[evaluate_decisions] {n}건 채점 (누적 {len(recs)}건)")
    return n


def report():
    recs = []
    for line in open(PATH, encoding="utf-8"):
        try:
            recs.append(json.loads(line))
        except ValueError:
            continue
    print(f"태그 전방검증 — 누적 {len(recs)}건 (비용 {COST}% 차감, 백테스트 대조:"
          " 갭 +0.83%/53% · 손바뀜 +1.71%/53% · 무차별 +0.16%/46%)")
    groups = {"갭": [], "손바뀜": [], "회피": [], None: []}
    for r in recs:
        if r.get("band") == "tier":        # 2군(티어~5%)은 본 집계에서 분리
            groups.setdefault("2군:" + (r.get("tag") or "무태그"), []).append(r)
            continue
        groups.setdefault(r.get("tag"), []).append(r)
    for tag, rs in groups.items():
        label = tag or "무태그(대조군)"
        o1 = [r["r_o1"] - COST for r in rs if r.get("r_o1") is not None]
        c5 = [r["r_c5"] - COST for r in rs if r.get("r_c5") is not None]
        seg = f"  {label:<10} 기록 {len(rs):<4}"
        if o1:
            seg += (f" | 익일시가 n={len(o1)} 평균 {sum(o1)/len(o1):+.2f}%"
                    f" 승률 {sum(v > 0 for v in o1)/len(o1)*100:.0f}%")
        if c5:
            seg += (f" | D+5 n={len(c5)} 평균 {sum(c5)/len(c5):+.2f}%"
                    f" 승률 {sum(v > 0 for v in c5)/len(c5)*100:.0f}%")
        c1 = [r["r_c1"] - COST for r in rs if r.get("r_c1") is not None]
        if c1:
            seg += f" | 익일종가 평균 {sum(c1)/len(c1):+.2f}%"
        c10 = [r["r_c10"] - COST for r in rs if r.get("r_c10") is not None]
        if c10:
            seg += f" | D+10 평균 {sum(c10)/len(c10):+.2f}%"
        print(seg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--nxt-log", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")),
                    "/opt/upbit_bot/.env")
    if a.record:
        record()
    if a.evaluate:
        evaluate()
        evaluate_decisions()
    if a.report:
        report()
    if a.nxt_log:
        nxt_log()
