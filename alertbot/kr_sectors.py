# -*- coding: utf-8 -*-
"""한국 업종·테마 강약 (장중 실시간) — 네이버 금융.

07시 슬롯은 미국시장이 막 끝난 뒤라 '전일 미국 섹터'가 근거가 되지만,
14:30 / 19시 슬롯은 미국장이 닫혀 있고 한국장 데이터로 판단해야 하므로
이 모듈을 쓴다.

  업종: finance.naver.com/sise/sise_group.naver?type=upjong  (127개)
  테마: finance.naver.com/sise/theme.naver                   (64개, 주도주 포함)
"""
from __future__ import annotations

import io
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
UPJONG = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
THEME = "https://finance.naver.com/sise/theme.naver"


def _pct(s):
    if s is None:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(s))
    return float(m.group(0)) if m else None


def _read(url, pages: int = 1):
    """네이버 표를 DataFrame 리스트로. 실패 시 []."""
    try:
        import pandas as pd
    except Exception:
        return []
    out = []
    for p in range(1, pages + 1):
        u = url if p == 1 else f"{url}{'&' if '?' in url else '?'}page={p}"
        try:
            r = requests.get(u, headers=UA, timeout=20)
            r.encoding = "euc-kr"
            out.extend(pd.read_html(io.StringIO(r.text)))
        except Exception:
            continue
    return out


def _flat(cols):
    """MultiIndex 컬럼 평탄화. 상·하위가 같으면 하나로, 다르면 하위를 쓴다.
    ('주도주','주도주') → '주도주' / ('주도주','주도주.1') → '주도주.1'
    이렇게 해야 주도주 2개 컬럼이 중복되지 않아 r[col] 이 Series 가 되지 않는다."""
    out = []
    for c in cols:
        if isinstance(c, tuple):
            a, b = str(c[0]), str(c[-1])
            out.append(a if a == b else b)
        else:
            out.append(str(c))
    return out


def fetch_upjong(top: int = 3):
    """업종 등락률. {'up': [...], 'down': [...]} 각 [{name, change_pct, rise, fall}]"""
    for t in _read(UPJONG):
        cols = _flat(t.columns)
        if "업종명" not in cols:
            continue
        t = t.copy()
        t.columns = cols
        t = t.dropna(subset=["업종명"])
        rows = []
        for _, r in t.iterrows():
            c = _pct(r.get("전일대비"))
            if c is None:
                continue
            rows.append({"name": str(r["업종명"]).strip(), "change_pct": c,
                         "rise": r.get("상승"), "fall": r.get("하락")})
        if not rows:
            continue
        rows.sort(key=lambda x: x["change_pct"], reverse=True)
        # 상승 업종이 top 개 미만일 때 음수가 up 에 섞이지 않도록 부호로 먼저 거른다
        up = [x for x in rows if x["change_pct"] > 0][:top]
        down = [x for x in rows if x["change_pct"] < 0][-top:][::-1]
        return {"up": up, "down": down, "n": len(rows)}
    return None


def fetch_themes(top: int = 3, frame=None):
    """테마 등락률 + 주도주. {'up': [...], 'down': [...]}
    각 [{name, change_pct, d3_pct, amt_eok, leaders:[..], score}]

    1차: 키움 ka90001/ka90002 (네이버 테마 페이지는 2026-09 JS 렌더링 개편으로 표가
    사라져 read_html 이 0개를 돌려준다 — 09-10 20:00 알림에서 테마 실종된 원인).
    frame(전종목 스냅샷)이 있으면 상승 테마를 '구성종목에 실린 돈'으로 다시 줄 세운다:
      score = Σ 구성종목 max(0, 등락률) × 거래대금(억)
    하락 테마는 참고용이라 등락률 순 유지."""
    th = _themes_kiwoom(top, frame)
    if th:
        return th
    for t in _read(THEME):
        cols = _flat(t.columns)
        if "테마명" not in cols:
            continue
        t = t.copy()
        t.columns = cols
        t = t.dropna(subset=["테마명"])
        # '주도주' 컬럼이 2개(주도주, 주도주.1)로 들어온다
        lead_cols = [c for c in t.columns if str(c).startswith("주도주")]
        rows = []
        for _, r in t.iterrows():
            c = _pct(r.get("전일대비"))
            if c is None:
                continue
            leaders = [str(r[lc]).strip() for lc in lead_cols
                       if r.get(lc) is not None and str(r[lc]).strip() not in ("", "nan")]
            rows.append({"name": str(r["테마명"]).strip(), "change_pct": c,
                         "d3_pct": _pct(r.get("최근3일 등락률(평균)")),
                         "leaders": leaders})
        if not rows:
            continue
        rows.sort(key=lambda x: x["change_pct"], reverse=True)
        if frame is not None:
            _score_by_leaders(rows, frame)
            ups = [x for x in rows if x["change_pct"] > 0]
            ups.sort(key=lambda x: x.get("score", 0), reverse=True)
            # 주도주가 겹치는 테마는 사실상 같은 재료다(주유소/정유/윤활유 = S-Oil).
            # 첫 테마만 남겨 세 자리가 한 재료로 채워지는 걸 막는다.
            up, used = [], set()
            for t in ups:
                ls = set(t.get("leaders") or [])
                if ls & used:
                    continue
                up.append(t); used |= ls
                if len(up) >= top:
                    break
        else:
            up = [x for x in rows if x["change_pct"] > 0][:top]
        down = [x for x in rows if x["change_pct"] < 0][-top:][::-1]
        return {"up": up, "down": down, "n": len(rows)}
    return None


def _themes_kiwoom(top: int = 3, frame=None):
    """키움 테마 TR 기반 주도 테마. 실패 시 None (호출부가 네이버 폴백).

    ka90001(테마그룹, 100개·flu_rt 당일·dt_prft_rt 기간) 1콜 → 상승 상위 10개만
    ka90002(구성종목)로 열어 frame 과 매칭해 에너지·대표주·거래대금을 계산한다.
    구성종목 stk_cd 는 '005930_AL' 꼴이라 언더스코어 앞만 쓴다.
    """
    import time as _t
    try:
        kc = _kc()
        d, _ = kc.request("ka90001", {"qry_tp": "0", "date_tp": "1",
                                      "flu_pl_amt_tp": "1", "stex_tp": "3"},
                          endpoint="/api/dostk/thme")
    except Exception:
        return None
    rows = []
    for r in d.get("thema_grp") or []:
        c = _pct(r.get("flu_rt"))
        nm = (r.get("thema_nm") or "").strip()
        if c is None or not nm:
            continue
        rows.append({"name": nm, "code": r.get("thema_grp_cd"), "change_pct": c,
                     "d3_pct": _pct(r.get("dt_prft_rt")), "leaders": [],
                     "amt_eok": None})
    if not rows:
        return None
    rows.sort(key=lambda x: x["change_pct"], reverse=True)

    chg_map, amt_map = {}, {}
    if frame is not None:
        try:
            names = frame["종목명"].tolist()
            chg_map = dict(zip(names, frame["등락률"].tolist()))
            amt_map = dict(zip(names, (frame["거래대금"] / 1e8).tolist()))
        except Exception:
            pass

    scored = []
    for t in [x for x in rows if x["change_pct"] > 0][:10]:
        try:
            d2, _ = kc.request("ka90002", {"date_tp": "1", "thema_grp_cd": t["code"],
                                           "stex_tp": "3"}, endpoint="/api/dostk/thme")
            members = [(s.get("stk_nm") or "").strip()
                       for s in d2.get("thema_comp_stk") or []]
        except Exception:
            members = []
        if members and chg_map:
            energy = lambda n: max(0.0, chg_map.get(n) or 0.0) * (amt_map.get(n) or 0.0)
            t["score"] = sum(energy(n) for n in members)
            t["amt_eok"] = sum(amt_map.get(n) or 0.0 for n in members) or None
            t["leaders"] = [n for n in sorted(members, key=energy, reverse=True)
                            if amt_map.get(n)][:2]
        else:
            t["score"] = 0.0
        scored.append(t)
        _t.sleep(0.15)
    if chg_map:
        scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    up, used = [], set()
    for t in scored:
        ls = set(t["leaders"])
        if ls and ls & used:              # 같은 주도주 = 같은 재료인 테마 중복 제거
            continue
        up.append(t)
        used |= ls
        if len(up) >= top:
            break
    down = [x for x in rows if x["change_pct"] < 0][-top:][::-1]
    return {"up": up, "down": down, "n": len(rows), "src": "kiwoom"}


def _score_by_leaders(rows, frame) -> None:
    """테마 주도주(네이버는 이름을 '..'로 줄여 준다)를 전종목 스냅샷에서 찾아
    상승 에너지(등락률×거래대금)를 합산한다. 매칭 실패 시 score=0."""
    try:
        names = frame["종목명"].tolist()
        chg = dict(zip(names, frame["등락률"].tolist()))
        amt = dict(zip(names, (frame["거래대금"] / 1e8).tolist()))
    except Exception:
        return
    for x in rows:
        sc = 0.0
        for l in x.get("leaders") or []:
            key = l[:-2] if l.endswith("..") else l
            cands = [n for n in names if n.startswith(key)] if key else []
            if not cands:
                continue
            n = max(cands, key=lambda c: amt.get(c, 0))
            sc += max(0.0, chg.get(n, 0.0)) * amt.get(n, 0.0)
        x["score"] = sc


# ── 키움 업종지수 — 등락률과 거래대금을 함께 준다 ──────────────────────
# 집계성 지수는 '주도 섹터'가 아니므로 뺀다
_AGG = ("종합", "대형주", "중형주", "소형주", "제조", "제조업", "KOSPI", "KRX",
        "코스피", "코스닥", "배당", "우량기업", "벤처기업", "신성장", "중견기업",
        "기술성장", "글로벌", "150", "200", "100")


def _kc():
    import os
    import sys as _sys
    here = os.path.dirname(os.path.abspath(__file__))
    for _p in (here, os.path.abspath(os.path.join(here, ".."))):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
    from notify import load_env
    load_env(os.path.join(here, ".env"),
             os.path.abspath(os.path.join(here, "..", ".env")),
             "/opt/upbit_bot/.env")
    from closebet.kiwoom import KiwoomClient
    return KiwoomClient()


def fetch_upjong_kiwoom(top: int = 3):
    """주도 섹터 — 키움 ka20003(전업종지수)의 등락률 × 거래대금.

    규칙: 거래대금이 중앙값 이상인 업종 중에서 등락률 상위/하위 top개.
    등락률만 보면 거래대금이 안 실린 소형 업종(예: 항공 +5%에 수백억)이
    맨 위로 오는데, 돈이 안 실린 상승은 종가베팅 근거가 못 된다.
    반환 [{name, change_pct, amt_eok, code, mrkt_tp}] 구조의 {'up','down','n'}.
    """
    try:
        kc = _kc()
    except Exception:
        return None
    rows = []
    for inds, mrkt in (("001", "0"), ("101", "1")):
        try:
            d, _ = kc.request("ka20003", {"inds_cd": inds}, endpoint="/api/dostk/sect")
        except Exception:
            continue
        for r in d.get("all_inds_idex") or []:
            nm = (r.get("stk_nm") or "").strip()
            if any(a in nm for a in _AGG):
                continue
            try:
                chg = float(str(r.get("flu_rt")).replace("+", ""))
                amt = float(str(r.get("trde_prica")).replace(",", "")) / 100  # 백만원→억
            except (TypeError, ValueError):
                continue
            rows.append({"name": nm, "change_pct": chg, "amt_eok": amt,
                         "code": r.get("stk_cd"), "mrkt_tp": mrkt})
    if not rows:
        return None
    med = sorted(x["amt_eok"] for x in rows)[len(rows) // 2]
    liquid = [x for x in rows if x["amt_eok"] >= med]
    liquid.sort(key=lambda x: x["change_pct"], reverse=True)

    def pick(cands):
        # 코스피·코스닥에 같은 이름의 업종이 있어(예: IT 서비스) 이름으로 dedup —
        # 정렬이 앞선(더 강한) 쪽만 남긴다
        out, seen = [], set()
        for x in cands:
            if x["name"] in seen:
                continue
            seen.add(x["name"]); out.append(x)
            if len(out) >= top:
                break
        return out

    up = pick([x for x in liquid if x["change_pct"] > 0])
    down = pick([x for x in reversed(liquid) if x["change_pct"] < 0])
    # 코스피·코스닥 동명 업종이 상승/하락 양쪽에 뜨면(09-10 '기계/장비') 시장을 밝힌다
    both = {x["name"] for x in up} & {x["name"] for x in down}
    for x in up + down:
        if x["name"] in both:
            x["name"] += "(코스피)" if x.get("mrkt_tp") == "0" else "(코스닥)"
    return {"up": up, "down": down, "n": len(rows), "src": "kiwoom"}


def sector_members(sectors) -> dict:
    """주도 섹터 구성종목 → {종목코드6: 섹터명}. 주도주에 소속 섹터를 태깅해
    '주도 섹터 → 주도주' 내러티브를 잇는 데 쓴다. sectors: fetch_upjong_kiwoom 의 up."""
    try:
        kc = _kc()
    except Exception:
        return {}
    out = {}
    for s in sectors or []:
        if not s.get("code"):
            continue
        try:
            d, _ = kc.request("ka20002", {"inds_cd": s["code"], "mrkt_tp": s.get("mrkt_tp", "0"),
                                          "stex_tp": "3"}, endpoint="/api/dostk/sect")
        except Exception:
            continue
        for r in d.get("inds_stkpc") or []:
            code = str(r.get("stk_cd") or "")[:6]
            if code and code not in out:
                out[code] = s["name"]
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    u = fetch_upjong()
    if u:
        print(f"■ 업종 {u['n']}개 중 강세/약세")
        for x in u["up"]:
            print(f"   🔺 {x['name']:<16}{x['change_pct']:+6.2f}%")
        for x in u["down"]:
            print(f"   🔻 {x['name']:<16}{x['change_pct']:+6.2f}%")
    print()
    th = fetch_themes()
    if th:
        print(f"■ 주도 테마 (전체 {th['n']}개)")
        for x in th["up"] + th["down"]:
            d3 = f" (3일 {x['d3_pct']:+.2f}%)" if x["d3_pct"] is not None else ""
            print(f"   · {x['name']:<14}{x['change_pct']:+6.2f}%{d3}  → {', '.join(x['leaders'])}")
