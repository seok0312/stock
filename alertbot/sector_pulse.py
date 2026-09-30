# -*- coding: utf-8 -*-
"""주도 섹터·테마 펄스 — 거래대금 상위 200 집계를 매일 축적 (09-30 사용자).

목적: '메인 주도 섹터'(연속성 있는 진짜 주도)와 '반짝 부각', '신규 부상'을
데이터로 구분한다. 예: 전력·로봇이 하루 튀어도 20일 연속성은 반도체가 압도
→ 메인은 반도체. 축적될수록 근거가 강해지는 구조.

record (15:50 크론, 마감 후):
  스냅샷에서 보통주 거래대금 상위 200 → 섹터(sector_map)·테마(theme_map)별
  {점유율%, 에너지 Σmax(0,등락)×대금, 대금가중 등락, 대표주} 집계
  → data/sector_pulse.jsonl (1줄/일, 같은 날 재실행 시 교체)
  → /var/www/html/sector_pulse.json (대시보드 카드: 메인/오늘/부상)

지표 정의:
  메인   = 최근 20일 중 점유율 top3 진입 일수(연속성 N/20) 순
  부상   = 최근 3일 평균 점유율 ÷ 이전 17일 평균 배율 (신규 등장 = ∞ 표기 NEW)
  반짝   = 오늘 상위인데 연속성 낮음 (카드에서 자연히 드러남)

build_theme_map (주간 크론): ka90001 전 테마그룹 × ka90002 구성종목
  → data/theme_map.json {code6: [테마명, ...]} — 종목은 여러 테마 소속 가능.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WEB_DIR = "/var/www/html"
PULSE = os.path.join(DATA, "sector_pulse.jsonl")
THEME_MAP = os.path.join(DATA, "theme_map.json")
TOP_N = 200


def build_theme_map(verbose: bool = True) -> dict:
    import kr_sectors as krs
    kc = krs._kc()
    d, _ = kc.request("ka90001", {"qry_tp": "0", "date_tp": "1",
                                  "flu_pl_amt_tp": "1", "stex_tp": "3"},
                      endpoint="/api/dostk/thme")
    groups = [(r.get("thema_grp_cd"), (r.get("thema_nm") or "").strip())
              for r in d.get("thema_grp") or []]
    out: dict = {}
    for cd, nm in groups:
        if not cd or not nm:
            continue
        try:
            d2, _ = kc.request("ka90002", {"date_tp": "1", "thema_grp_cd": cd,
                                           "stex_tp": "3"}, endpoint="/api/dostk/thme")
        except Exception:
            continue
        n = 0
        for s in d2.get("thema_comp_stk") or []:
            code = str(s.get("stk_cd") or "")[:6]
            if code:
                out.setdefault(code, [])
                if nm not in out[code]:
                    out[code].append(nm)
                    n += 1
        if verbose:
            print(f"  {nm}: {n}종목")
        time.sleep(0.12)
    if out:
        os.makedirs(DATA, exist_ok=True)
        with open(THEME_MAP + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"built_at": datetime.now(KST).isoformat(timespec="seconds"),
                       "map": out}, f, ensure_ascii=False)
        os.replace(THEME_MAP + ".tmp", THEME_MAP)
    print(f"테마맵 {len(out)}종목 · {len(groups)}테마 → {THEME_MAP}")
    return out


def _load_theme_map() -> dict:
    try:
        return json.load(open(THEME_MAP, encoding="utf-8"))["map"]
    except Exception:
        return {}


def _agg(rows, key_of):
    """rows → {그룹: {share, energy, wchg, amt_eok, tops[]}} (다중 소속 허용)."""
    total = sum(r["amt"] for r in rows) or 1.0
    g: dict = {}
    for r in rows:
        for k in key_of(r):
            d = g.setdefault(k, {"amt": 0.0, "energy": 0.0, "wc": 0.0, "rows": []})
            d["amt"] += r["amt"]
            d["energy"] += max(0.0, r["chg"]) * r["amt"]
            d["wc"] += r["chg"] * r["amt"]
            d["rows"].append(r)
    out = []
    for k, d in g.items():
        tops = sorted(d["rows"], key=lambda x: max(0.0, x["chg"]) * x["amt"],
                      reverse=True)[:2]
        out.append({"name": k, "share": round(d["amt"] / total * 100, 2),
                    "energy": round(d["energy"] / 1e4, 1),
                    "chg": round(d["wc"] / d["amt"], 2),
                    "amt_eok": round(d["amt"]),
                    "tops": [t["name"] for t in tops]})
    out.sort(key=lambda x: x["share"], reverse=True)
    return out


def record(now=None):
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
    sys.path.insert(0, HERE)
    from closebet import market
    import nxt
    snap = market.get_snapshot()
    df = snap[(snap["종가"] > 0) & (snap["거래량"] > 0)]
    df = df[df.index.str.endswith("0")]
    df = df[~df.index.duplicated()]
    df = df.sort_values("거래대금", ascending=False).head(TOP_N)
    smap, tmap = nxt.load_sector_map(), _load_theme_map()
    rows = []
    for code, x in df.iterrows():
        rows.append({"code": code, "name": str(x["종목명"]),
                     "chg": float(x["등락률"]), "amt": float(x["거래대금"]) / 1e8,
                     "sec": smap.get(code), "th": tmap.get(code) or []})
    sectors = _agg(rows, lambda r: [r["sec"]] if r["sec"] else [])
    themes = _agg(rows, lambda r: r["th"])
    rec = {"date": today, "total_eok": round(sum(r["amt"] for r in rows)),
           "sectors": sectors[:15], "themes": themes[:25]}
    # 같은 날 재실행 = 교체 (멱등)
    hist = load_history()
    hist = [h for h in hist if h["date"] != today] + [rec]
    os.makedirs(DATA, exist_ok=True)
    with open(PULSE, "w", encoding="utf-8") as f:
        for h in hist:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    dash(hist)
    print(f"[record] {today} top{TOP_N} → 섹터 {len(sectors)} · 테마 {len(themes)} "
          f"(축적 {len(hist)}일)")
    return rec


def load_history() -> list:
    out = []
    try:
        for line in open(PULSE, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except FileNotFoundError:
        pass
    return sorted(out, key=lambda h: h["date"])


def _continuity(hist, kind, top_k=3, win=20):
    """{그룹: {'n_top': 최근 win일 중 top_k 진입 일수, 'avg': 평균 점유율,
              'recent3': 최근3일 평균, 'prev': 그 이전 평균}}"""
    hs = hist[-win:]
    stats: dict = {}
    for i, h in enumerate(hs):
        groups = h.get(kind) or []
        tops = {g["name"] for g in groups[:top_k]}
        for g in groups:
            s = stats.setdefault(g["name"], {"n_top": 0, "shares": [None] * len(hs)})
            s["shares"][i] = g["share"]
            if g["name"] in tops:
                s["n_top"] += 1
    out = {}
    for k, s in stats.items():
        vals = [v for v in s["shares"] if v is not None]
        r3 = [v for v in s["shares"][-3:] if v is not None]
        pv = [v for v in s["shares"][:-3] if v is not None]
        out[k] = {"n_top": s["n_top"], "days": len(hs),
                  "avg": sum(vals) / len(vals) if vals else 0,
                  "recent3": sum(r3) / len(r3) if r3 else 0,
                  "prev": sum(pv) / len(pv) if pv else None}
    return out


def dash(hist=None):
    hist = hist or load_history()
    if not hist:
        return
    today = hist[-1]
    doc = {"generated": datetime.now(KST).strftime("%m/%d %H:%M"),
           "days": len(hist), "date": today["date"]}
    for kind in ("sectors", "themes"):
        cont = _continuity(hist, kind)
        cur = []
        for g in (today.get(kind) or [])[:8]:
            c = cont.get(g["name"], {})
            cur.append({**g, "n_top": c.get("n_top", 0), "win": c.get("days", 1)})
        # 메인 = 연속성 순 / 부상 = 최근3일 vs 이전 배율 (이전 없으면 NEW)
        main = sorted(cur, key=lambda x: (x["n_top"], x["share"]), reverse=True)
        rise = []
        for k, c in cont.items():
            if c["recent3"] < 1.0 or len(hist) < 4:
                continue
            if c["prev"] is None or c["prev"] == 0:
                rise.append({"name": k, "ratio": None, "recent3": round(c["recent3"], 2)})
            elif c["recent3"] / c["prev"] >= 2.0:
                rise.append({"name": k, "ratio": round(c["recent3"] / c["prev"], 1),
                             "recent3": round(c["recent3"], 2)})
        rise.sort(key=lambda x: x["ratio"] or 99, reverse=True)
        doc[kind] = {"today": cur, "main": main[:5], "rising": rise[:5]}
    out_dir = WEB_DIR if os.path.isdir(WEB_DIR) else os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "sector_pulse.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "--build-theme-map":
        build_theme_map()
        raise SystemExit(0)
    rec = record()
    for s in rec["sectors"][:8]:
        print(f"  {s['name']:<12} 점유 {s['share']:4.1f}% · {s['chg']:+5.2f}% · {', '.join(s['tops'])}")
