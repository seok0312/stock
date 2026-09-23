# -*- coding: utf-8 -*-
"""당일 주도주 후보 — closebet 파이프라인 재사용.

closebet(stock/closebet)에 이미 구현·검증된 것을 그대로 호출한다:
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


def fetch_leaders(top: int = 5, min_change: float = 2.0, use_kiwoom: bool = True):
    """[{종목명, 종목코드, 등락률, 거래대금, 주도일수, 점수, ...}] 또는 None.

    상위 5개(09-15 사용자: 최종 고민은 이 안에서만). 선정은 3요소 스크리너
    (시총구간 배율 — 백테스트 +1.13%/일 승률 65%로 최강 룰) + 수급 점수 유지,
    연속성(주도일수)은 선정이 아니라 태그로만 붙인다 — 연속성만으로 뽑으면
    삼전·하이닉스 고정 바스켓이 되어 엣지가 흐려짐(09-15 백테스트 B안 기각)."""
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
            "시가총액조": _f(r.get("시가총액(조)")),
            "외국인주": _f(r.get("외국인순매매")), "기관주": _f(r.get("기관순매매")),
            "외국인": None, "기관": None,
            "프로그램": _f(r.get("프로그램순매수(억)")), "점수": _f(r.get("점수")),
        })

    # 연속성 태그(09-15): 최근 20일 중 시총구간 문턱을 넘은 '주도일' 수 —
    # ka10059 한 콜(일별 flu_rt, %×100 주의)로 계산. 선정에는 쓰지 않는다.
    if src == "kiwoom":
        try:
            import time
            from closebet.kiwoom import KiwoomClient
            kc = KiwoomClient()
            for x in out:
                x["주도일수"], x["수급태그"] = _kiwoom_tags(
                    kc, x["종목코드"], x.get("시가총액조"), date)
                time.sleep(0.2)
        except Exception as e:
            print(f"  주도일수 계산 실패(태그 생략): {type(e).__name__}: {str(e)[:80]}")

    return {"date": date, "source": src, "rows": out}


def _kiwoom_tags(kc, code: str, mc_jo, date: str, days: int = 20):
    """(주도일수, 수급태그) — ka10059 한 콜로 둘 다 계산한다.

    주도일수: 최근 days 거래일 중 시총구간 등락률 문턱(10조↑3%/1~10조 5%/1조↓7%)
    을 넘은 날 수. 연속성 표시용 — 삼전닉스류(수십일)와 반짝 테마주를 구분해 준다."""
    tier = 3.0 if (mc_jo or 0) >= 10 else (5.0 if (mc_jo or 0) >= 1 else 7.0)
    try:
        d, _ = kc.request("ka10059",
                          {"dt": date, "stk_cd": code, "amt_qty_tp": "1",
                           "trde_tp": "0", "unit_tp": "1"},
                          endpoint="/api/dostk/stkinfo")
        rows = sorted((r for r in d.get("stk_invsr_orgn") or []),
                      key=lambda r: r.get("dt") or "", reverse=True)
        n = 0
        for r in rows[:days]:
            try:
                chg = float(str(r.get("flu_rt")).replace(",", "").lstrip("+")) / 100.0
            except (TypeError, ValueError):
                continue
            if chg >= tier:
                n += 1
        return n, _flow_tag(rows, date)
    except Exception:
        return None, None


# 태그 → 액션. 표기·범례는 이 두 상수를 유일 원천으로 (render/hot_stocks 가 공용).
TAG_ACT = {"갭": "시가매도", "손바뀜": "보유", "회피": "매수 자제"}
TAG_LEGEND = ("태그: 갭 = 개인·기관 매수 + 외인 매도 → 시가매도 유리 / "
              "손바뀜 = 개인 팔기 시작 + 외인 사기 시작 → 보유 / "
              "회피 = 외인만 매수(개인·기관 매도) → 매수 자제")


def _flow_tag(rows, date: str):
    """3주체(개인×외인×기관) 수급 태그 — 09-23 믹싱 검증(과열 110종목×3년).

    '갭'    = 개인·기관 매수 ∧ 외인 매도 → D+1시가 +1.44%/승률 63%(1년 +1.83%/64%),
              단 기관까지 팔면 D+5 -1.24% 붕괴라 기관 매수 필수 조건.
    '손바뀜' = 개인 매도 전환(어제 매수→오늘 매도) ∧ 외인 매수 전환 → D+5 +1.77~2.30%.
              기관 방향은 안 가름(전 셀 양수) — 조건에서 제외.
    '회피'  = 외인만 매수, 개인·기관 매도 → 시가 -0.1~-0.2%/승률 42%.
    당일 개인 잠정은 KRX 장중 공표에 없어 -(외인+기관) 부호로 근사
    (캐시 실측: 실제 개인 부호와 97.1% 일치, 과열일 96.9%).
    최신 행이 당일이 아니거나 외인·기관 모두 0이면 판정 보류(None). rows: dt 내림차순."""
    def _n(x):
        try:
            return float(str(x).replace(",", "").lstrip("+"))
        except (TypeError, ValueError):
            return None

    seq = []
    for r in rows:
        f, i = _n(r.get("frgnr_invsr")), _n(r.get("orgn"))
        if f is None or i is None:
            continue
        seq.append({"dt": r.get("dt"), "f": f, "i": i, "p": _n(r.get("ind_invsr"))})
    if not seq or seq[0]["dt"] != date or (seq[0]["f"] == 0 and seq[0]["i"] == 0):
        return None
    t = seq[0]
    p0 = t["p"] if t["p"] not in (None, 0) else -(t["f"] + t["i"])
    if p0 > 0 and t["f"] <= 0 and t["i"] > 0:
        return "갭"
    if p0 < 0 and t["f"] > 0 and len(seq) >= 2:
        y = seq[1]
        yp = y["p"] if y["p"] not in (None, 0) else -(y["f"] + y["i"])
        if y["f"] <= 0 and yp >= 0:
            return "손바뀜"
    if p0 <= 0 and t["f"] > 0 and t["i"] <= 0:
        return "회피"
    return None


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
