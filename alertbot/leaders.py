# -*- coding: utf-8 -*-
"""당일 주도주 후보 — closebet 파이프라인 재사용.

closebet(c:\dev\stock\closebet)에 이미 구현·검증된 것을 그대로 호출한다:
  screener.screen_leaders : FDR 전종목 스냅샷 → 거래대금·등락률 상위 압축
  score.score_leaders     : 키움 ka10059(외국인·기관 수급) + ka90013(프로그램)
                            을 붙여 가중합 점수화

키움 인증(IP·계좌 등록)이 필요하다. 실패하면 네이버 크롤링으로 폴백한다.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# closebet 패키지 위치: 로컬은 저장소 루트(../closebet), 서버는 배포 디렉토리(./closebet)
for _p in (os.path.abspath(os.path.join(_HERE, "..")), _HERE):
    if os.path.isdir(os.path.join(_p, "closebet")) and _p not in sys.path:
        sys.path.insert(0, _p)


def _load_keys():
    """closebet.kiwoom 은 자체 .env 탐색이 cwd 의존이라 여기서 먼저 주입한다."""
    from notify import load_env
    load_env(os.path.join(_HERE, ".env"),
             os.path.abspath(os.path.join(_HERE, "..", ".env")),
             "/opt/upbit_bot/.env")


def fetch_leaders(top: int = 8, min_change: float = 2.0, use_kiwoom: bool = True):
    """[{종목명, 종목코드, 등락률, 거래대금, 외국인, 기관, 프로그램, 점수}] 또는 None."""
    _load_keys()
    try:
        from closebet.config import Settings
        from closebet.screener import screen_leaders
        from closebet.score import score_leaders
    except Exception:
        return None

    cfg = Settings(min_change_pct=min_change, flow_top=max(top * 2, 20))
    try:
        date, lead = screen_leaders(cfg)
    except Exception:
        return None
    if lead is None or lead.empty:
        return None

    df, src = None, None
    if use_kiwoom:
        try:
            df = score_leaders(lead, cfg, source="kiwoom", dt=date)
            src = "kiwoom"
        except Exception as e:
            print(f"  키움 수급 실패 → 네이버 폴백: {type(e).__name__}: {str(e)[:120]}")
            df = None
    if df is None:
        try:
            df = score_leaders(lead, cfg, source="naver")
            src = "naver"
        except Exception as e:
            print(f"  네이버 수급도 실패: {type(e).__name__}: {str(e)[:120]}")
            return None

    out = []
    for _, r in df.head(top).iterrows():
        out.append({
            "종목명": r.get("종목명"), "종목코드": r.get("종목코드"),
            "등락률": _f(r.get("등락률")), "거래대금": _f(r.get("거래대금(억)")),
            "외국인": _f(r.get("외국인순매매")), "기관": _f(r.get("기관순매매")),
            "프로그램": _f(r.get("프로그램순매수(억)")), "점수": _f(r.get("점수")),
        })
    return {"date": date, "source": src, "rows": out}


def _f(v):
    try:
        import pandas as pd
        if v is None or (hasattr(pd, "isna") and pd.isna(v)):
            return None
        return float(v)
    except Exception:
        return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    r = fetch_leaders()
    if not r:
        print("주도주 조회 실패"); raise SystemExit(1)
    print(f"기준일 {r['date']} · 소스 {r['source']} · {len(r['rows'])}종목\n")
    print(f"{'종목':<14}{'등락률':>8}{'거래대금':>10}{'외국인':>12}{'기관':>12}{'프로그램':>10}{'점수':>7}")
    for x in r["rows"]:
        fmt = lambda v, w, d=0: (f"{v:>{w},.{d}f}" if v is not None else " " * (w - 1) + "-")
        print(f"{x['종목명'][:12]:<14}{fmt(x['등락률'],8,2)}{fmt(x['거래대금'],10)}"
              f"{fmt(x['외국인'],12)}{fmt(x['기관'],12)}{fmt(x['프로그램'],10)}{fmt(x['점수'],7,2)}")
