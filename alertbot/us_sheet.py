# -*- coding: utf-8 -*-
"""미국 시장 일일 분석 → 구글시트 탭 (10-16 사용자 — KR 시트의 미국판).

매일 06:20 KST(화~토, 미국 마감 후): 대형주 유니버스(movers.US_UNIV)를 야후
chart 로 스캔해 두 표를 만든다 (미국은 상한가 제도가 없어 급등주 표는 상승률
표와 겹침 → 표 2개로 통합, 10-16 사용자 확인):
  ① 달러 거래대금 상위 10 (종가×거래량)
  ② 상승률 상위 10 (거래대금 $1B↑)
행 = [종목(한글명·티커), 섹터, 등락률, 거래대금($B), 이유]
시장 표 = S&P500·나스닥·다우·러셀2000·VIX·ES/NQ 선물 × 지수·등락률
         + 거래대금은 SPY/QQQ 달러대금 프록시(사용자 지정).
이유 체인 = ① 한글 뉴스 풀(네이버 월드뉴스+스탁허브, 있으면 우선)
           ② 야후 종목 뉴스 API(티커 정확 매칭, 당일 세션 창) ③ 빈칸.
탭 이름 = KST 오늘 "M/DD (요일) 미국" (아침에 보는 간밤 마감). 같은 웹훅·
범용 렌더러 사용, 상태 키는 날짜+"US" 로 분리. 멱등·자동 백필 동일.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STORE = os.path.join(DATA, "us_sheet.jsonl")
STATE = os.path.join(DATA, "sheet_push_state.json")      # KR 과 공유 (키 분리)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"}
# ES/NQ 삭제, WTI·금·미10Y 추가 (10-16 사용자). ^TNX 는 수익률×10 스케일.
# 순서·구성 (10-23 사용자): 리스크 지표(VIX·WTI·금·금리) 먼저, 지수, 그 아래
# 반도체(SOX) + DRAM = Roundhill Memory ETF(티커 DRAM, 10-29 사용자: DXI 현물지수 아님)
IDX = [("VIX", "^VIX"), ("WTI", "CL=F"), ("금", "GC=F"), ("미국10Y", "^TNX"),
       ("S&P500", "^GSPC"), ("나스닥", "^IXIC"), ("SOX", "^SOX"), ("DRAM(메모리ETF)", "DRAM")]
UNIV_CACHE = os.path.join(DATA, "us_univ.json")
GICS_KR = {"Information Technology": "기술", "Communication Services": "커뮤니케이션",
           "Consumer Discretionary": "경기소비", "Consumer Staples": "필수소비",
           "Health Care": "헬스케어", "Financials": "금융", "Industrials": "산업재",
           "Energy": "에너지", "Materials": "소재", "Utilities": "유틸리티",
           "Real Estate": "부동산"}


def _load_universe() -> list:
    """S&P500 + 나스닥100 전체(~530) [(sym, name, sec)] — 주 1회 캐시 (10-16
    사용자: 주요종목 118개 → 전체로). 한글명·섹터는 movers.US_UNIV 수동맵 우선,
    그 외 섹터는 GICS 한글 변환. 수집 실패 시 캐시 → US_UNIV 폴백."""
    from movers import US_UNIV
    manual = {s: (k, sec) for s, k, sec in US_UNIV}
    now = datetime.now(KST)
    try:
        doc = json.load(open(UNIV_CACHE, encoding="utf-8"))
        if (now - datetime.fromisoformat(doc["built"])).days < 7:
            return [tuple(x) for x in doc["univ"]]
    except Exception:
        doc = None
    univ: dict = {}
    try:
        import csv as _csv
        import io
        r = requests.get("https://raw.githubusercontent.com/datasets/"
                         "s-and-p-500-companies/main/data/constituents.csv",
                         headers=UA, timeout=20)
        for row in _csv.DictReader(io.StringIO(r.text)):
            sym = (row.get("Symbol") or "").strip().replace(".", "-")
            if sym:
                univ[sym] = [sym, (row.get("Security") or sym).strip()[:20],
                             GICS_KR.get((row.get("GICS Sector") or "").strip(), "")]
        h2 = dict(UA, Origin="https://www.nasdaq.com",
                  Referer="https://www.nasdaq.com/", Accept="application/json")
        j = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                         headers=h2, timeout=20).json()
        d = j.get("data") or {}
        rows = (d.get("data") or {}).get("rows") or d.get("rows") or []
        for x in rows:
            sym = (x.get("symbol") or "").strip().replace(".", "-")
            if sym and sym not in univ:
                nm = (x.get("companyName") or sym).replace(" Common Stock", "")
                univ[sym] = [sym, nm.strip()[:20], ""]
        for sym, (kr, sec) in manual.items():
            univ[sym] = [sym, kr, sec]
        if len(univ) >= 400:
            os.makedirs(DATA, exist_ok=True)
            json.dump({"built": now.isoformat(timespec="seconds"),
                       "univ": list(univ.values())},
                      open(UNIV_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
            return [tuple(x) for x in univ.values()]
    except Exception as e:
        print(f"[us_sheet] 유니버스 갱신 실패({type(e).__name__}) — 캐시/수동맵 폴백")
    if doc:
        return [tuple(x) for x in doc["univ"]]
    return US_UNIV


def _dxi():
    """DRAMeXchange DXI(DRAM 현물 종합지수) — (지수, 전일比 %). 야후에 없어 홈페이지
    DXI 위젯 파싱: '1162000.00<img ...dxi_up.gif...>4025.97 (0.35 %)'. 매일 18:00
    GMT+8 갱신이라 06:20 KST 수집 땐 전날 저녁값(미국 세션과 같은 날짜). 실패 시 None."""
    try:
        t = requests.get("https://www.dramexchange.com/", headers=UA, timeout=12).text
        seg = t.split("DXI Start", 1)[1][:3000]
        m = re.search(r">\s*([0-9][0-9,]*\.?[0-9]*)\s*<img[^>]*dxi_(up|down|flat)?[^>]*>"
                      r"\s*([0-9.,]+)\s*\(\s*([0-9.]+)\s*%\s*\)", seg)
        if not m:
            return None, None
        lv = float(m.group(1).replace(",", ""))
        pc = float(m.group(4)) * (-1 if m.group(2) == "down" else 1)
        return round(lv, 2), round(pc, 2)
    except Exception:
        return None, None


def _chart(sym: str):
    """야후 일봉(1mo) — (전일比%, 종가, 달러대금 $B, 마지막 봉 MM/DD, bars).
    bars = [(YYYYMMDD(ET), close, volume)] 과거→최신. 주간 집계(10-17)에 재사용."""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                         params={"range": "1mo", "interval": "1d"}, headers=UA,
                         timeout=12)
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        et = timezone(timedelta(hours=-5))
        pts = [(datetime.fromtimestamp(t, tz=et).strftime("%Y%m%d"), c, v or 0)
               for t, c, v in zip(res.get("timestamp") or [],
                                  q.get("close") or [], q.get("volume") or [])
               if c]
        if len(pts) < 2:
            return None
        pct = (pts[-1][1] / pts[-2][1] - 1) * 100
        dvol = (pts[-1][1] * pts[-1][2]) / 1e9
        return pct, pts[-1][1], dvol, pts[-1][0][4:6] + "/" + pts[-1][0][6:], pts
    except Exception:
        return None


def _week_agg(bars):
    """bars → (주간 등락률%, 주간 달러대금 $B, 주간 라벨). 마지막 봉 기준 주."""
    if not bars or len(bars) < 2:
        return None, None, ""
    last = bars[-1][0]
    d = datetime.strptime(last, "%Y%m%d")
    monday = (d - timedelta(days=d.weekday())).strftime("%Y%m%d")
    wk = [b for b in bars if b[0] >= monday]
    prev = [b for b in bars if b[0] < monday]
    if not wk or not prev:
        return None, None, ""
    wchg = (wk[-1][1] / prev[-1][1] - 1) * 100
    wvol = sum(b[1] * b[2] for b in wk) / 1e9
    lab = (f"{monday[4:6].lstrip('0') or '0'}/{monday[6:]}~"
           f"{last[4:6].lstrip('0') or '0'}/{last[6:]}")
    return wchg, wvol, lab


def _yahoo_news(sym: str, hours: int = 22) -> str:
    """야후 종목 뉴스 — 최근 세션 창 안의 첫 기사 제목(영어). 없으면 ''."""
    try:
        j = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                         params={"q": sym, "newsCount": 6, "quotesCount": 0},
                         headers=UA, timeout=12).json()
        cut = datetime.now(timezone.utc).timestamp() - hours * 3600
        for n in j.get("news") or []:
            if (n.get("providerPublishTime") or 0) >= cut and n.get("title"):
                return n["title"][:90], n.get("link") or ""
    except Exception:
        pass
    return "", ""


def _tr_ko(text: str) -> str:
    """영어 제목 → 한글 (구글 무키 번역 gtx, 10-19 사용자). 실패·한글이면 원문."""
    if not text or any("가" <= ch <= "힣" for ch in text):
        return text
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "en", "tl": "ko",
                                 "dt": "t", "q": text}, headers=UA, timeout=10)
        out = "".join(seg[0] for seg in r.json()[0] if seg and seg[0]).strip()
        return out[:90] if out else text
    except Exception:
        return text


def _reason(sym: str, kr: str, now) -> str:
    try:
        import news
        # 2글자 티커(IP 등)는 풀 매칭 오염(IPO챗 등) — 한글명 키워드로만 (10-16)
        kw = (kr, sym) if len(sym) >= 3 else (kr,)
        tk = (sym,) if len(sym) >= 3 else ()
        hits = news._pool_match(keywords=kw, tickers=tk, limit=3,
                                start=now - timedelta(hours=22), end=now)
        for h in hits:
            if not any(w in h["title"] for w in ("시황", "마감", "요약", "다우",
                                                 "나스닥", "종목 알림", "3대 지수")):
                return h["title"][:90], h.get("url") or ""
    except Exception:
        pass
    t, u = _yahoo_news(sym)
    return _tr_ko(t), u


# 이슈 키워드 사전 — 번역된 한글 뉴스에서 테마 집계 (10-22 사용자: 세션 요약)
ISSUE_PAT = [
    ("실적", r"실적|어닝|분기|가이던스|매출|순이익|EPS"),
    ("AI·반도체", r"\bAI\b|인공지능|데이터센터|반도체|칩|GPU|파운드리"),
    ("금리·연준", r"금리|연준|국채|인플레|CPI|고용지표|파월"),
    ("관세·무역", r"관세|무역|수출\s?규제|공급망"),
    ("M&A·지분", r"인수|합병|지분|사모|상장폐지"),
    ("규제·소송", r"규제|소송|조사|리콜|벌금"),
    ("유가·에너지", r"유가|원유|OPEC|감산|천연가스"),
    ("신약·FDA", r"FDA|임상|승인|신약"),
]


def _summary(market, rows, top, gain):
    """미국 세션 요약 (10-22 사용자: 시황 이슈 → 주목 섹터 → 개별 종목 뉴스).

    전부 룰 기반 — ①지수·VIX·금리·원자재 조합 한 줄 ②뉴스 키워드 테마 집계
    ③대금가중 섹터 등락 상·하위 ④대금 상위 중 변동 큰 종목의 뉴스."""
    g = {m[0]: m for m in market}
    L = []
    parts = []
    spx, ndx = g.get("S&P500"), g.get("나스닥")
    if spx and spx[2] is not None and ndx and ndx[2] is not None:
        a, b = spx[2], ndx[2]
        tone = ("동반 상승" if a > 0 and b > 0
                else "동반 하락" if a < 0 and b < 0 else "혼조")
        parts.append(f"S&P {a:+.1f}% · 나스닥 {b:+.1f}% {tone}")
    vix = g.get("VIX")
    if vix and vix[1] and (vix[1] >= 22 or abs(vix[2] or 0) >= 7):
        parts.append(f"VIX {vix[1]:.0f}({(vix[2] or 0):+.0f}%) "
                     + ("급등 — 불안 확대" if (vix[2] or 0) > 0 else "진정"))
    tnx = g.get("미국10Y")
    if tnx and tnx[2] is not None and abs(tnx[2]) >= 0.04:
        parts.append(f"10Y {tnx[1]:.2f}%({tnx[2]:+.2f}%p) "
                     + ("금리 부담" if tnx[2] > 0 else "금리 안도"))
    for lab, th in (("WTI", 1.5), ("금", 1.5), ("SOX", 1.5), ("DRAM(메모리ETF)", 1.5)):
        c = g.get(lab)
        if c and c[2] is not None and abs(c[2]) >= th:
            parts.append(f"{lab} {c[2]:+.1f}%")
    if parts:
        L.append("시황: " + " | ".join(parts))
    themed = {}
    for r in {x["sym"]: x for x in top + gain}.values():
        t = r.get("issue") or ""
        for nm, pat in ISSUE_PAT:
            if t and re.search(pat, t, re.I):
                themed.setdefault(nm, []).append(f"{r['name']} {r['chg']:+.1f}%")
    iss = sorted(themed.items(), key=lambda kv: -len(kv[1]))[:3]
    if iss:
        L.append("주요 이슈: " + "  /  ".join(
            f"{nm}({len(v)}건: " + " · ".join(v[:3]) + ")" for nm, v in iss))
    agg = {}
    for r in rows:
        a = agg.setdefault(r["sec"] or "기타", [0.0, 0.0])
        a[0] += r["chg"] * max(r["dvol"], 0.1)
        a[1] += max(r["dvol"], 0.1)
    secs = sorted(((s, w / v) for s, (w, v) in agg.items() if v > 0),
                  key=lambda kv: kv[1])

    def _sec_lab(s, c):
        reps = sorted([r for r in rows if (r["sec"] or "기타") == s],
                      key=lambda r: -r["dvol"])[:6]
        # 섹터 방향과 같은 쪽 대표만 (약세 섹터에 +종목이 뽑히는 역설 방지)
        reps = sorted(reps, key=lambda r: r["chg"] if c < 0 else -r["chg"])[:2]
        names = " · ".join(f"{r['name']} {r['chg']:+.1f}" for r in reps)
        return f"{s} {c:+.1f}%({names})"
    if len(secs) >= 2:
        L.append("주목 섹터: 강세 "
                 + " / ".join(_sec_lab(s, c) for s, c in list(reversed(secs))[:2])
                 + "   약세 " + " / ".join(_sec_lab(s, c) for s, c in secs[:2]))
    for r in sorted([r for r in top if r.get("issue") and abs(r["chg"]) >= 2],
                    key=lambda r: -abs(r["chg"]))[:4]:
        L.append(f"· {r['name']}({r['chg']:+.1f}%): {r['issue'][:80]}")
    return (["🌐 미국 세션 요약"] + L) if L else []


def collect(now=None) -> dict | None:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    univ = _load_universe()
    print(f"[us_sheet] 유니버스 {len(univ)}종목 스캔")
    wk_mode = now.weekday() == 5          # 토요일 아침 = 금요 세션 마감 직후 (10-17)
    rows, stale = [], 0
    for sym, kr, sec in univ:
        got = _chart(sym)
        time.sleep(0.08)
        if not got:
            continue
        pct, px, dvol, last_d, bars = got
        row = {"sym": sym, "name": kr, "sec": sec, "chg": round(pct, 2),
               "dvol": round(dvol, 1), "last": last_d}
        if wk_mode:
            wchg, wvol, wlab = _week_agg(bars)
            if wchg is not None:
                row["wchg"], row["wvol"], row["wlab"] = (round(wchg, 2),
                                                         round(wvol, 1), wlab)
        rows.append(row)
    if len(rows) < 100:
        print(f"[us_sheet] 수신 {len(rows)} — 휴장/장애 추정, 생략")
        return None
    sess = max(r["last"] for r in rows)
    rows = [r for r in rows if r["last"] == sess]        # 묵은 종목 제외
    byvol = sorted(rows, key=lambda r: -r["dvol"])
    top = byvol[:15]
    # 상승률은 거래대금 상위 100위 풀에서만 (10-22 사용자 — 한국 시트와 동일
    # 규칙. 10-16의 $1B 고정 필터 제거 결정을 '상대 순위 필터'로 대체)
    gain = sorted([r for r in byvol[:100] if r["chg"] > 0],
                  key=lambda r: -r["chg"])[:15]
    for r in top + gain:
        if "issue" not in r:
            r["issue"], r["issue_url"] = _reason(r["sym"], r["name"], now)
            time.sleep(0.05)
            for o in top + gain:
                if o["sym"] == r["sym"]:
                    o["issue"] = r["issue"]
                    o["issue_url"] = r["issue_url"]
    market = []
    market_w = []
    spy = _chart("SPY"); qqq = _chart("QQQ")
    for i, (lab, sym) in enumerate(IDX):
        g = _chart(sym)
        time.sleep(0.06)
        dv = None
        if lab == "S&P500" and spy:
            dv = round(spy[2], 1)
        elif lab == "나스닥" and qqq:
            dv = round(qqq[2], 1)
        if wk_mode and g:
            wchg, _wv, _ = _week_agg(g[4])
            wdv = None
            if lab == "S&P500" and spy:
                wdv = round(_week_agg(spy[4])[1] or 0, 1)
            elif lab == "나스닥" and qqq:
                wdv = round(_week_agg(qqq[4])[1] or 0, 1)
            if sym == "^TNX" and wchg is not None:
                raw = g[1]
                sc = 10 if raw > 20 else 1
                prevw = raw / (1 + wchg / 100)
                market_w.append([lab, round(raw / sc, 2),
                                 round((raw - prevw) / sc, 2), None])
            else:
                market_w.append([lab, round(g[1], 2),
                                 wchg if wchg is None else round(wchg, 2), wdv])
        if g and sym == "^TNX":
            # 변동은 %p(전일 대비 금리차). ^TNX 스케일은 데이터에 따라 ×10/×1
            # 혼재 → 값 크기로 자동 판별 (10-16 버그: /10 고정이 0.51 표시)
            raw = g[1]
            prev = raw / (1 + g[0] / 100)
            sc = 10 if raw > 20 else 1
            market.append([lab, round(raw / sc, 2),
                           round((raw - prev) / sc, 2), None])
        else:
            market.append([lab, round(g[1], 2) if g else None,
                           g[0] if g else None, dv])
    def pack(rs):
        return [{"name": f"{r['name']} ({r['sym']})", "sec": r["sec"],
                 "chg": r["chg"], "dvol": r["dvol"],
                 "issue": r.get("issue") or "",
                 "issue_url": r.get("issue_url") or ""} for r in rs]
    rec = {"date": today, "asof": f"{now:%m/%d %H:%M} · 미국 {sess} 세션",
           "market": market,
           "summary": _summary(market, rows, top, gain),
           "sections": [
               {"title": "거래대금 TOP 15", "rows": pack(top)},
               {"title": "상승률 TOP 15 (거래대금 상위 100위)", "rows": pack(gain)}]}
    hist = []
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        pass
    hist = [h for h in hist if not (h["date"] == today and not h.get("week"))] + [rec]
    if wk_mode:
        wrows = [r for r in rows if r.get("wchg") is not None]
        wlab = next((r["wlab"] for r in wrows if r.get("wlab")), "")
        wbyvol = sorted(wrows, key=lambda r: -(r.get("wvol") or 0))
        wtop = wbyvol[:15]
        wgain = sorted([r for r in wbyvol[:100] if r["wchg"] > 0],
                       key=lambda r: -r["wchg"])[:15]

        def wpack(rs):
            return [{"name": f"{r['name']} ({r['sym']})", "sec": r["sec"],
                     "chg": r["wchg"], "dvol": r.get("wvol") or 0,
                     "issue": "", "issue_url": ""} for r in rs]
        wrec = {"date": today, "week": True, "v": 2,
                "asof": f"{now:%m/%d %H:%M} · 주간 {wlab}",
                "week_label": wlab, "market": market_w,
                "sections": [
                    {"title": f"주간 거래대금 TOP 15 ({wlab})", "rows": wpack(wtop)},
                    {"title": f"주간 상승률 TOP 15 (대금 상위 100위, {wlab})",
                     "rows": wpack(wgain)}]}
        hist = [h for h in hist
                if not (h["date"] == today and h.get("week"))] + [wrec]
        print(f"[us_sheet] 주간 서머리 생성 ({wlab}, {len(wrows)}종목)")
    os.makedirs(DATA, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        for h in hist[-60:]:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    # 존 적층 구조(10-16)에선 미국 존은 하루 1회만 — 재수집해도 재푸시하지
    # 않는다(이미 올라간 존을 중복 삽입하지 않기 위함).
    print(f"[us_sheet] {today} 수집 — 대금 {len(top)} · 상승 {len(gain)} "
          f"(세션 {sess}, 이유 {sum(1 for s in rec['sections'] for r in s['rows'] if r['issue'])}건)")
    return rec


def push_all() -> int:
    """푸시는 sheet_top20 이 담당 — 미국 존은 "M월 (미국)" 전용 탭 (10-23 사용자 분리)."""
    import sheet_top20
    return sheet_top20.push_all()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, HERE)
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")))
    if "--push-only" not in sys.argv:
        collect()
    push_all()
