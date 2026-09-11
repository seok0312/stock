# -*- coding: utf-8 -*-
"""종가베팅 매수후보 전방 검증 — 백테스트의 '역발상' 신호를 잠정치로 매일 기록·채점.

백테스트(250종목×80일) 결론:
  · 후보군 진입(강한 상승 + 큰 거래대금)이 오버나이트 알파의 원천
  · 후보 안에서는 과열 순위 '하위'(덜 뜨거운 쪽)가 상위보다 낫다 (하위5 +0.86%/일 vs 상위5 +0.68%)
  · 수급 팩터는 '외인+기관 연속 순매수일수'가 단독 IC 최강(-0.116, t=-2.4) — 연속으로
    사온 종목일수록 익일 갭이 약하다. 과거 확정치 기반이라 잠정 노이즈에도 강함 (09-09 테스트)
  · 캐퍼시티: 거래대금 2,000억↑ 후보의 픽 평균 거래대금 ~1.6조 — 억 단위 베팅에 무리 없음

기준(09-11 개편 — 상수 정의 위 주석 참고):
  후보 = 등락률 >= 시총구간별 문턱(10조↑3%/1~10조 5%/1조↓7%)
         AND 당일 거래대금 >= 시장 전체의 0.5%
  픽   = 과열 합성점수(거래대금배율 + 등락률 + 연속순매수일수 백분위 합) 하위/상위 K (A/B 기록)
  수급 = 키움 ka10059: 과거 확정 + '오늘' 행 잠정치(15:20, KRX 5차 공표) — 실전 재현 조건

크론(UTC): 20 6 * * 1-5 --record   (KST 15:20, 종가 직전)
           5  0 * * 1-5 --evaluate (KST 09:05, 익일 시가 채점)
로그: data/closebet_picks.jsonl (pick/eval 레코드, append-only)

실행: python3 closebet_picks.py --record | --evaluate | (없으면 통계)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for _p in (os.path.abspath(os.path.join(HERE, "..")), HERE):
    if os.path.isdir(os.path.join(_p, "closebet")) and _p not in sys.path:
        sys.path.insert(0, _p)
PATH = os.path.join(HERE, "data", "closebet_picks.jsonl")

# 후보 기준 (09-11 백테스트 재검증으로 개편):
#   거래대금 — 절대값 대신 시장 전체(코스피+코스닥) 거래대금의 0.5%.
#     절대 2,000억과 성능 동일(bot +1.24% vs +1.25%)하면서 후보 수가 장세에
#     적응(후보수-시장규모 상관 +0.16 vs 절대제 +0.42, 표준편차 4.5 vs 6.3).
#   등락률 — 시총 구간별: 10조↑ 3% / 1~10조 5% / 1조↓ 7%.
#     고정 5%는 삼성전자급 대형주를 배제(80일 중 0회) → 구간제로 25회 포함되고
#     bot +1.22%(t +3.1, 유효 58일)로 표본·유의성 모두 개선.
AMT_PCT_MKT = 0.5        # 후보: 거래대금 >= 시장 전체의 0.5%
CHG_TIERS = ((10e12, 3.0), (1e12, 5.0), (0, 7.0))   # (시총 하한, 등락률 문턱)
TOP_K = 5
MAX_CANDS = 40           # 폭주 장 대비 키움 호출 상한 (거래대금순 상위만)


def _chg_min(mc: float) -> float:
    for floor, th in CHG_TIERS:
        if (mc or 0) >= floor:
            return th
    return CHG_TIERS[-1][1]


def _load():
    if not os.path.exists(PATH):
        return []
    out = []
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def _append(rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _num(v):
    s = str(v or "").replace(",", "").replace("--", "-").lstrip("+")
    try:
        return float(s)
    except ValueError:
        return None


def record(now=None) -> int:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    if any(r.get("type") == "pick" and r.get("date") == today for r in _load()):
        print("이미 오늘 기록 있음 — 생략")
        return 0

    from notify import load_env
    load_env(os.path.join(HERE, ".env"), "/opt/upbit_bot/.env")
    from closebet import market as cbm
    from closebet.kiwoom import KiwoomClient
    import pandas as pd

    snap = cbm.get_snapshot("KRX")
    if snap.empty:
        print("스냅샷 실패")
        return 0
    mkt_total = float(snap["거래대금"].sum())        # 시장 전체 거래대금(원, 전종목 합)
    amt_floor = mkt_total * AMT_PCT_MKT / 100
    chg_floor = snap["시가총액"].map(_chg_min)
    cands = snap[(snap["등락률"] >= chg_floor) & (snap["거래대금"] >= amt_floor)]
    cands = cands.sort_values("거래대금", ascending=False).head(MAX_CANDS)
    print(f"시장 거래대금 {mkt_total/1e12:.1f}조 → 후보 문턱 {amt_floor/1e8:,.0f}억")
    if len(cands) < 4:
        print(f"후보 {len(cands)}개 — 오늘은 장이 조용함, 기록 생략")
        return 0

    kc = KiwoomClient()
    rows = []
    for code, s in cands.iterrows():
        try:
            d, _ = kc.request("ka10059",
                              {"dt": today, "stk_cd": code, "amt_qty_tp": "1",
                               "trde_tp": "0", "unit_tp": "1"},
                              endpoint="/api/dostk/stkinfo")
        except Exception as e:
            print(f"  {code} 수급 실패: {str(e)[:60]}")
            continue
        hist = sorted((r for r in d.get("stk_invsr_orgn") or []),
                      key=lambda r: r.get("dt") or "", reverse=True)
        t = next((r for r in hist if r.get("dt") == today), None)
        past_amt = [_num(r.get("acc_trde_prica")) for r in hist if (r.get("dt") or "") < today][:20]
        past_amt = [p for p in past_amt if p]
        if not t or len(past_amt) < 10:
            continue
        amt_mn = _num(t.get("acc_trde_prica"))
        chg = _num(t.get("flu_rt"))
        if chg is not None:
            chg /= 100.0                  # ka10059 flu_rt 는 % ×100 (실측: +5.68% → 568)
        if not amt_mn or chg is None:
            continue
        # 연속 순매수일수: 오늘(잠정)부터 거슬러 외인+기관 합이 +인 날 수
        streak = 0
        for r in hist:
            net = (_num(r.get("frgnr_invsr")) or 0) + (_num(r.get("orgn")) or 0)
            if net > 0:
                streak += 1
            else:
                break
        rows.append({"code": code, "name": s["종목명"], "chg": chg,
                     "amt_eok": round(amt_mn / 100), "f_amt": amt_mn / (sum(past_amt) / len(past_amt)),
                     "f_streak": streak})
        time.sleep(0.2)

    if len(rows) < 4:
        print(f"수급 확보 후보 {len(rows)}개 — 기록 생략")
        return 0
    df = pd.DataFrame(rows)
    k = max(2, min(TOP_K, len(df) // 3))
    score = (df["f_amt"].rank(pct=True) + df["chg"].rank(pct=True)
             + df["f_streak"].rank(pct=True))
    df["score"] = score.round(3)
    order = score.sort_values().index
    grp = {}
    for i in order[:k]:
        grp[i] = "bot"
    for i in order[-k:]:
        grp[i] = "top"
    df["grp"] = [grp.get(i, "mid") for i in df.index]

    rec = {"type": "pick", "date": today, "ts": now.strftime("%H:%M"),
           "n_cands": len(df), "k": k,
           "rows": df.round(3).to_dict(orient="records")}
    _append([rec])
    b = df[df.grp == "bot"]
    print(f"[{today} {rec['ts']}] 후보 {len(df)} · bot{k}: "
          + ", ".join(f"{r['name']}({r['chg']:+.1f}%)" for _, r in b.iterrows()))
    return len(df)


def evaluate(now=None) -> int:
    """미채점 pick 을 익일 시가로 채점. 다음 거래일 데이터가 없으면 다음 기회로."""
    now = now or datetime.now(KST)
    rows = _load()
    done = {r["date"] for r in rows if r.get("type") == "eval"}
    todo = [r for r in rows if r.get("type") == "pick"
            and r["date"] not in done and r["date"] < now.strftime("%Y%m%d")]
    if not todo:
        return 0
    from closebet import market as cbm
    out = []
    for p in todo:
        evs = []
        for r in p["rows"]:
            try:
                h = cbm.get_price_history(
                    r["code"], (datetime.strptime(p["date"], "%Y%m%d")
                                - timedelta(days=7)).strftime("%Y-%m-%d"))
                import pandas as pd
                d = pd.Timestamp(datetime.strptime(p["date"], "%Y%m%d").date())
                if d not in h.index:
                    continue
                pos = h.index.get_loc(d)
                if pos + 1 >= len(h):        # 다음 거래일 시가가 아직 없음
                    evs = None
                    break
                r_on = (h.iloc[pos + 1]["Open"] / h.iloc[pos]["Close"] - 1) * 100
                evs.append({"code": r["code"], "grp": r["grp"], "r_on": round(r_on, 2)})
            except Exception:
                continue
            time.sleep(0.1)
        if evs:
            out.append({"type": "eval", "date": p["date"], "rows": evs})
            print(f"채점 {p['date']}: {len(evs)}종목")
    _append(out)
    return len(out)


def stats():
    rows = _load()
    evs = [(e["date"], r) for e in rows if e.get("type") == "eval" for r in e["rows"]]
    if not evs:
        print("아직 채점 표본 없음 (--record 다음 거래일 09:05에 채점됩니다)")
        return
    import pandas as pd
    df = pd.DataFrame([{"date": d, **r} for d, r in evs])
    print(f"채점 {df['date'].nunique()}일 · {len(df)}관측\n")
    print(f"{'그룹':<8}{'평균%':>8}{'승률':>7}{'n':>5}")
    for g in ("bot", "mid", "top"):
        day = df[df.grp == g].groupby("date")["r_on"].mean()
        if len(day):
            print(f"{g:<8}{day.mean():>+8.2f}{(day > 0).mean()*100:>6.0f}%{len(day):>5}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--record" in sys.argv:
        record()
    elif "--evaluate" in sys.argv:
        evaluate()
        stats()
    else:
        stats()
