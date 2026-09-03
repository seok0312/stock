# -*- coding: utf-8 -*-
"""투자자별 순매수 + 프로그램매매 — 키움 REST (KRX + NXT 통합).

왜 네이버가 아니라 키움인가:
  네이버 지수 API 는 **KRX 거래분만** 준다. 넥스트레이드(NXT)가 빠진다.
  2026-09-03 14:5x 실측 (코스피, 억원):

      개인      외국인    기관     기타법인
      -7,022    -4,748    -4,122   +15,922   ← 키움 stex=1(KRX)  ≒ 네이버
      -1,805    +1,958      -357      +148   ← 키움 stex=2(NXT)
      -8,827    -2,790    -4,480   +16,070   ← 키움 stex=3(통합)  = 실제 시장 전체

  외국인이 NXT 에서 +1,958억을 사고 있어서 KRX 만 보면 -4,748억(대량매도)이지만
  통합으로는 -2,790억이다. 방향 판단이 뒤집힐 수 있는 차이라 통합을 쓴다.

  프로그램(비차익)도 같은 문제였다. 같은 시각 비차익 순매수:
      KRX -4,279억 / NXT +1,666억 / 통합 -2,612억

TR:
  ka10051 업종별투자자순매수 — 첫 행이 '종합(KOSPI)' / '종합(KOSDAQ)' 즉 시장 전체.
          amt_qty_tp=0(금액), 단위 **억원**. 6개 주체 합이 정확히 0으로 닫힌다.
  ka90010 프로그램매매추이(일자별) — 단위 **백만원**. mrkt_tp P00101=코스피 / P10101=코스닥.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
_HERE = os.path.dirname(os.path.abspath(__file__))
# closebet 패키지 위치: 로컬은 저장소 루트(../closebet), 서버는 배포 디렉토리(./closebet)
for _p in (os.path.abspath(os.path.join(_HERE, "..")), _HERE):
    if os.path.isdir(os.path.join(_p, "closebet")) and _p not in sys.path:
        sys.path.insert(0, _p)

# 거래소를 나눠 받는다. KRX(정규장)와 NXT(넥스트레이드)는 흐름이 반대인 날이 있어
# (2026-09-03 코스피 외국인: KRX -4,232억 / NXT +2,411억) 어디서 체결할지 판단에 쓰인다.
# KRX + NXT = 통합 이 원 단위로 성립함을 확인했으므로 통합은 합으로 유도한다.
EXCHANGES = (("KRX", "1"), ("NXT", "2"))
MARKETS = (("코스피", "0", "P00101"), ("코스닥", "1", "P10101"))


def _load_keys():
    """closebet.kiwoom 의 .env 탐색이 cwd 의존이라 여기서 먼저 주입한다."""
    from notify import load_env
    load_env(os.path.join(_HERE, ".env"),
             os.path.abspath(os.path.join(_HERE, "..", ".env")),
             "/opt/upbit_bot/.env")


def _f(v):
    try:
        return float(str(v).replace(",", "").replace("--", "-"))
    except (TypeError, ValueError):
        return None


def _client():
    _load_keys()
    from closebet.kiwoom import KiwoomClient
    return KiwoomClient()


def _flow(kc, mrkt_tp: str, base_dt: str, stex: str = "3") -> dict | None:
    """ka10051 첫 행(= 시장 전체) → {개인, 외국인, 기관, 기타법인} 억원."""
    data, _ = kc.request("ka10051",
                         {"mrkt_tp": mrkt_tp, "amt_qty_tp": "0",
                          "base_dt": base_dt, "stex_tp": stex},
                         endpoint="/api/dostk/sect")
    rows = data.get("inds_netprps") or []
    if not rows:
        return None
    r = rows[0]
    ind, frgn, orgn = _f(r.get("ind_netprps")), _f(r.get("frgnr_netprps")), _f(r.get("orgn_netprps"))
    etc = _f(r.get("etc_corp_netprps"))
    if None in (ind, frgn, orgn, etc):
        return None
    # 내국인대우외국인은 외국인에, 국가·지자체는 기관에 붙인다.
    # 이렇게 해야 개인+외국인+기관+기타법인 = 0 이 정확히 성립한다.
    frgn += _f(r.get("native_trmt_frgnr_netprps")) or 0.0
    orgn += _f(r.get("natn_netprps")) or 0.0
    return {"개인": ind, "외국인": frgn, "기관": orgn, "기타법인": etc}


def _program(kc, mrkt_tp: str, base_dt: str, stex: str = "3") -> dict | None:
    """ka90010 → {차익, 비차익, 합계} 억원. 백만원 단위라 100으로 나눈다."""
    data, _ = kc.request("ka90010",
                         {"date": base_dt, "amt_qty_tp": "1", "mrkt_tp": mrkt_tp,
                          "min_tic_tp": "0", "stex_tp": stex},
                         endpoint="/api/dostk/mrkcond")
    rows = data.get("prm_trde_trnsn") or []
    row = next((r for r in rows if str(r.get("cntr_tm", ""))[:8] == base_dt), None)
    if row is None:
        return None
    arb, non = _f(row.get("dfrt_trde_netprps")), _f(row.get("ndiffpro_trde_netprps"))
    if non is None:
        return None
    return {"차익": arb / 100 if arb is not None else None,
            "비차익": non / 100,
            "합계": (_f(row.get("all_netprps")) or 0) / 100}


def fetch(base_dt: str | None = None) -> dict | None:
    """{'코스피': {'flow': {...}, 'program': {...}}, '코스닥': {...}} 또는 None.

    개별 시장이 실패해도 나머지는 반환한다(부분 성공 허용).
    """
    base_dt = base_dt or datetime.now(KST).strftime("%Y%m%d")
    try:
        kc = _client()
    except Exception:
        return None
    out = {}
    for label, mrkt_tp, prog_cd in MARKETS:
        by_ex = {}
        for ex_name, stex in EXCHANGES:
            e = {}
            try:
                e["flow"] = _flow(kc, mrkt_tp, base_dt, stex)
            except Exception:
                e["flow"] = None
            try:
                e["program"] = _program(kc, prog_cd, base_dt, stex)
            except Exception:
                e["program"] = None
            by_ex[ex_name] = e
        d = {"by_exchange": by_ex,
             "flow": _sum_of(by_ex, "flow"), "program": _sum_of(by_ex, "program")}
        if d["flow"] or d["program"]:
            out[label] = d
    return out or None


def _sum_of(by_ex: dict, key: str) -> dict | None:
    """거래소별 값을 합쳐 통합값을 만든다. 하나라도 실패하면 None(부분합은 오해를 부른다)."""
    parts = [(e.get(key) or None) for e in by_ex.values()]
    if any(p is None for p in parts) or not parts:
        return None
    acc = {}
    for p in parts:
        for k, v in p.items():
            if v is None:
                continue
            acc[k] = acc.get(k, 0.0) + v
    return acc or None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    r = fetch()
    if not r:
        raise SystemExit("키움 조회 실패")
    for label, d in r.items():
        f, p = d.get("flow") or {}, d.get("program") or {}
        for ex, e in (d.get("by_exchange") or {}).items():
            ef = e.get("flow") or {}
            if ef:
                print(f"   [{ex:<3}] " + "  ".join(f"{k} {v:+,.0f}" for k, v in ef.items())
                      + f"   비차익 {(e.get('program') or {}).get('비차익', 0):+,.0f}")
        print(f"■ {label}  (KRX+NXT 통합, 억원)")
        if f:
            print("   " + "  ".join(f"{k} {v:+,.0f}" for k, v in f.items()))
            print(f"   합계 {sum(f.values()):+,.0f}  (0이어야 함)")
        if p:
            print("   " + "  ".join(f"{k} {v:+,.0f}" for k, v in p.items() if v is not None))
