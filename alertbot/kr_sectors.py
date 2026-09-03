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


def fetch_themes(top: int = 3):
    """테마 등락률 + 주도주. {'up': [...], 'down': [...]}
    각 [{name, change_pct, d3_pct, leaders:[..]}]"""
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
        up = [x for x in rows if x["change_pct"] > 0][:top]
        down = [x for x in rows if x["change_pct"] < 0][-top:][::-1]
        return {"up": up, "down": down, "n": len(rows)}
    return None


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
