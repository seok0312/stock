# -*- coding: utf-8 -*-
"""거래대금·상승률·상한가 일일 분석 → 구글시트 일별 탭 기록 (10-16 사용자, v3).

매일 20:00 크론(평일) 세 섹션:
  ① 거래대금 상위 10   ② 상승률 상위 10(거래대금 1,000억↑)   ③ 상한가 전 종목
행 = [종목, 시장(코스피/KOSDAQ — 사용자: 구분 쉽게 한글/영어), 거래대금(억),
     등락률(%), 이유(뉴스)]

v3 소스 전환(10-16): FDR 스냅샷이 장중 시점에 멈춰 마감치와 어긋남(엘앤씨바이오
+22%/1000억↑이 +16.75%/190억으로 보임, 상한가 9→4 누락) → **키움 TR 로 교체**.
  ka10032 거래대금상위(코스피/코스닥 각각 → 시장 라벨 확보, trde_prica 백만원)
  ka10027 등락률상위(상한가·상승률 — flu_rt % 그대로)
  상한가 중 대금상위 100 밖 소형주의 거래대금은 네이버 종목 API 로 보강.
이유 체인: ① 뉴스 풀(주요뉴스 우선) ② 네이버 종목뉴스 API(당일 1건)
  ③ 테마맵("테마: …"). 빈칸 최소화(10-16 사용자 ⑤).
저장·전송: data/sheet_top20.jsonl 멱등, 재수집 시 push 상태 해제(20시 정식분
교체), 실패분 다음 실행 자동 백필. 시트 쪽은 날짜 탭 재생성(멱등).
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
STORE = os.path.join(DATA, "sheet_top20.jsonl")
STATE = os.path.join(DATA, "sheet_push_state.json")
TOP_N = 15          # 10-17 사용자: 15로 확대
EXCL_PAT = re.compile(r"KODEX|TIGER|ACE |PLUS |SOL |RISE |HANARO|KOSEF|KIWOOM |ARIRANG"
                      r"|1Q |스팩|ETN|레버리지|인버스")
NAVER_UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "Referer": "https://m.stock.naver.com/"}


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("+", "").replace("--", "-"))
    except (TypeError, ValueError):
        return None


def _load_state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def _save_state(s: dict):
    os.makedirs(DATA, exist_ok=True)
    json.dump(s, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


def _kiwoom_fetch():
    """(대금상위 {code: row}, 등락률상위 [row]) — row: name/mkt/amt_eok/chg."""
    import kr_sectors as krs
    kc = krs._kc()
    amt_rows: dict = {}
    for mrkt, lab in (("001", "코스피"), ("101", "KOSDAQ")):
        d, _ = kc.request("ka10032", {"mrkt_tp": mrkt, "mang_stk_incls": "1",
                                      "stex_tp": "3"}, endpoint="/api/dostk/rkinfo")
        for r in d.get("trde_prica_upper") or []:
            code = str(r.get("stk_cd") or "")[:6]
            nm = (r.get("stk_nm") or "").strip()
            amt, chg = _num(r.get("trde_prica")), _num(r.get("flu_rt"))
            if not code or not nm or amt is None or EXCL_PAT.search(nm):
                continue
            amt_rows[code] = {"code": code, "name": nm, "mkt": lab,
                              "amt_eok": round(amt / 100), "chg": chg}
        time.sleep(0.1)
    up_rows = []
    for mrkt, lab in (("001", "코스피"), ("101", "KOSDAQ")):
        d, _ = kc.request("ka10027", {"mrkt_tp": mrkt, "sort_tp": "1",
                                      "trde_qty_cnd": "0000", "updown_incls": "1",
                                      "stk_cnd": "0", "crd_cnd": "0",
                                      "trde_prica_cnd": "0", "pric_cnd": "0",
                                      "stex_tp": "3"}, endpoint="/api/dostk/rkinfo")
        key = next((k for k in d if isinstance(d[k], list)), None)
        for r in (d.get(key) or []):
            code = str(r.get("stk_cd") or "")[:6]
            nm = (r.get("stk_nm") or "").strip()
            chg = _num(r.get("flu_rt"))
            if not code or not nm or chg is None or EXCL_PAT.search(nm):
                continue
            row = {"code": code, "name": nm, "mkt": lab, "chg": chg,
                   "amt_eok": (amt_rows.get(code) or {}).get("amt_eok")}
            if row["amt_eok"] is None:        # 대금상위 100 밖 — 네이버로 보강
                row["amt_eok"] = _naver_amt(code)
            up_rows.append(row)
        time.sleep(0.1)
    return amt_rows, up_rows


def _naver_amt(code: str):
    """네이버 종목 통합 API 에서 당일 누적 거래대금(억). 실패 시 None."""
    try:
        j = requests.get(f"https://m.stock.naver.com/api/stock/{code}/integration",
                         headers=NAVER_UA, timeout=10).json()
        for it in j.get("totalInfos") or []:
            if it.get("code") in ("accTradeValue", "accumulatedTradingValue") \
                    or "거래대금" in str(it.get("key") or ""):
                v = _num(str(it.get("value") or "").replace("억", "").replace("백만", ""))
                if v is None:
                    continue
                raw = str(it.get("value") or "")
                if "백만" in raw:
                    return round(v / 100)
                return round(v)
    except Exception:
        pass
    return None


NOISE = ("시황", "마감", "요약", "코스피", "코스닥", "사이드카", "종목 알림",
         "특징주 요약", "급등주 포착", "개미", "증시", "주간", "오늘의")


def _reason(name: str, code: str, news_mod, now, n_start, tmap) -> str:
    """이유 체인: 뉴스 풀 → 종목뉴스 API(당일) → 테마맵.
    시장 랩업·큐레이션 기사(NOISE)는 종목의 '이유'가 아니라 걸러낸다."""
    try:
        hits = news_mod._pool_match(keywords=(name,), tickers=(code,), limit=4,
                                    start=n_start, end=now)
        for h in hits:
            if not any(w in h["title"] for w in NOISE):
                return h["title"][:90], h.get("url") or ""
    except Exception:
        pass
    try:
        j = requests.get(f"https://m.stock.naver.com/api/news/stock/{code}",
                         params={"pageSize": 5}, headers=NAVER_UA, timeout=10).json()
        items = (j[0].get("items") if isinstance(j, list) and j else
                 j.get("items") if isinstance(j, dict) else []) or []
        today8 = now.strftime("%Y%m%d")
        for it in items:
            if str(it.get("datetime") or "").startswith(today8):
                t = (it.get("title") or "").strip()
                if t and not any(w in t for w in NOISE):
                    u = (f"https://n.news.naver.com/mnews/article/"
                         f"{it.get('officeId')}/{it.get('articleId')}"
                         if it.get("officeId") and it.get("articleId") else "")
                    return t[:90], u
    except Exception:
        pass
    ths = (tmap.get(code) or [])[:2]
    return (("테마: " + "·".join(ths)) if ths else ""), ""


def _market_summary():
    """시장 요약 — [라벨, 지수, 등락률%, 거래대금(조), 개인(억), 외국인(억), 기관(억)].
    수급·거래대금 = flows(네이버 투자자 동향, 20시엔 당일 확정), 지수·등락률 =
    네이버 지수 API 마감치(flows 의 close 는 시점 어긋날 수 있어 안 씀)."""
    import flows
    try:
        fl = flows.fetch_all()
        by = {r["code"]: r for r in fl.get("rows") or []}
    except Exception:
        by = {}
    out = []
    for code, lab in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("FUT", "선물")):
        r = by.get(code) or {}
        idx = chg = None
        try:
            j = requests.get(f"https://m.stock.naver.com/api/index/{code}/price",
                             params={"pageSize": 1}, headers=NAVER_UA, timeout=10).json()
            idx, chg = _num(j[0].get("closePrice")), _num(j[0].get("fluctuationsRatio"))
        except Exception:
            idx = r.get("close")
        f = r.get("flow_eok") or {}
        amt = r.get("amount_won")
        out.append([lab, idx, chg, round(amt / 1e12, 2) if amt else None,
                    f.get("개인"), f.get("외국인"), f.get("기관")])
    return out


def _slot_of(hour: int, minute: int) -> str:
    """수집 시각 → 슬롯 버킷. 14:30/19:00/20:00 크론 ± 수동 실행 흡수."""
    if hour < 17:
        return "14:30"
    if (hour, minute) < (19, 40):
        return "19:00"
    return "20:00"


def collect(now=None) -> dict | None:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    try:
        import quotes
        hol = quotes.kr_holiday(now)
        if hol:
            print(f"[top20] 휴장({hol}) — 생략")
            return None
    except Exception:
        pass
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
    sys.path.insert(0, HERE)
    import news
    try:
        import sector_pulse
        tmap = sector_pulse._load_theme_map()
    except Exception:
        tmap = {}
    try:
        import nxt
        smap = nxt.load_sector_map()
    except Exception:
        smap = {}
    amt_rows, up_rows = _kiwoom_fetch()
    top = sorted(amt_rows.values(), key=lambda r: -r["amt_eok"])[:TOP_N]
    # 상승률 표 모집단 = 거래대금 상위 50 (10-16 사용자: 1,000억 하드코딩 대신
    # 상대 기준 — 한산한 날에도 활황인 날에도 같은 의미)
    top100 = sorted(amt_rows.values(), key=lambda r: -r["amt_eok"])[:100]
    gain = sorted([r for r in top100 if r["chg"] > 0],
                  key=lambda r: -r["chg"])[:15]
    lim = sorted([r for r in up_rows if r["chg"] >= 29.5],
                 key=lambda r: -(r["amt_eok"] or 0))
    n_start = now.replace(hour=0, minute=0)
    for r in top + gain + lim:          # 이유 붙이기 (중복 종목은 캐시)
        if "issue" not in r:
            r["issue"], r["issue_url"] = _reason(r["name"], r["code"], news,
                                                 now, n_start, tmap)
            time.sleep(0.05)
            for o in top + gain + lim:
                if o["code"] == r["code"]:
                    o["issue"] = r["issue"]
                    o["issue_url"] = r["issue_url"]

    def pack(rs):
        return [{"code": r["code"], "name": r["name"], "mkt": r["mkt"],
                 "sec": smap.get(r["code"]) or "",
                 "amt_eok": r["amt_eok"] or 0,
                 "chg": round(r["chg"], 2), "issue": r.get("issue") or "",
                 "issue_url": r.get("issue_url") or ""} for r in rs]
    amt200 = [[r["code"], r["name"], r["mkt"], r["amt_eok"]]
              for r in sorted(amt_rows.values(), key=lambda x: -x["amt_eok"])[:200]]
    rec = {"date": today, "asof": now.strftime("%m/%d %H:%M"), "v": 2,
           "amt100": amt200,
           "market": _market_summary(), "sections": [
        {"title": f"거래대금 TOP {TOP_N}", "rows": pack(top)},
        {"title": "상승률 TOP 15 (거래대금 상위 100위)", "rows": pack(gain)},
        {"title": "상한가", "rows": pack(lim)},
    ]}
    # 슬롯 스냅샷 (10-16 사용자: 14:30/19/20 버전을 지우지 않고 적층) —
    # 같은 날 같은 슬롯만 교체, 다른 슬롯은 보존
    slot = _slot_of(now.hour, now.minute)
    rec["slot"] = slot
    hist = []
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        pass
    for h in hist:                         # 구형 레코드 슬롯 소급 — asof 시각 역산
        if "slot" not in h:
            try:
                hh, mm = map(int, h["asof"].split()[1].split(":"))
                h["slot"] = _slot_of(hh, mm)
            except Exception:
                h["slot"] = "20:00"
    hist = [h for h in hist if not (h["date"] == today and h["slot"] == slot)] + [rec]
    os.makedirs(DATA, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        for h in hist[-240:]:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    print("[top20] " + today + " 수집 — "
          + " · ".join(f"{s['title']} {len(s['rows'])}" for s in rec["sections"])
          + f" · 이유 {sum(1 for s in rec['sections'] for r in s['rows'] if r['issue'])}건")
    # 주간 서머리 (10-16 사용자): 주중 마지막 거래일 20:00판 직후 생성
    if slot == "20:00" and _is_week_last_trading(now):
        try:
            wk = _build_week(now, hist, kc)
            if wk:
                hist = [h for h in hist
                        if not (h["date"] == today and h.get("slot") == "week")] + [wk]
                with open(STORE, "w", encoding="utf-8") as f:
                    for h in hist[-240:]:
                        f.write(json.dumps(h, ensure_ascii=False) + "\n")
                print(f"[top20] 주간 서머리 생성 ({wk['week_label']}, "
                      f"{len(wk['cands'])}종목 집계)")
        except Exception as e:
            print(f"[top20] 주간 서머리 실패(계속): {type(e).__name__}: {e}")
    return rec


def _is_week_last_trading(now) -> bool:
    """오늘이 이번 주 마지막 거래일인가 — 남은 평일이 전부 휴장이면 True."""
    if now.weekday() >= 5:
        return False
    try:
        import quotes
    except Exception:
        return now.weekday() == 4
    d = now + timedelta(days=1)
    while d.weekday() < 5:
        try:
            if not quotes.kr_holiday(d):
                return False
        except Exception:
            return False
        d += timedelta(days=1)
    return True


def _naver_week_chg(code: str, monday: str):
    """지수 주간 등락률% — 이번 주 마지막 종가 vs 전주 마지막 종가 (네이버)."""
    try:
        j = requests.get(f"https://m.stock.naver.com/api/index/{code}/price",
                         params={"pageSize": 15, "page": 1}, headers=NAVER_UA,
                         timeout=10).json()
        rows = [(x["localTradedAt"][:10].replace("-", ""),
                 _num(str(x["closePrice"]).replace(",", ""))) for x in j]
        cur = next((v for d, v in rows if d >= monday), None)
        base = next((v for d, v in rows if d < monday), None)
        if cur and base:
            return (cur / base - 1) * 100, cur
    except Exception:
        pass
    return None, None


def _week_flows(code: str, wdates):
    """시장 주간 수급 — 네이버 trend+bizdate 소급 합 (개인/외인/기관, 억원)."""
    p = f = o = 0.0
    got = 0
    for ds in sorted(wdates):
        try:
            r = requests.get(f"https://m.stock.naver.com/api/index/{code}/trend",
                             params={"bizdate": ds}, headers=NAVER_UA,
                             timeout=12).json()
        except Exception:
            continue
        if not isinstance(r, dict) or r.get("bizdate") != ds:
            continue
        fv, iv = _num(r.get("foreignValue")), _num(r.get("institutionalValue"))
        if fv is None or iv is None or (fv == 0 and iv == 0):
            continue
        pv = _num(r.get("individualValue"))
        p += pv if pv is not None else -(fv + iv)
        f += fv
        o += iv
        got += 1
        time.sleep(0.05)
    return (round(p), round(f), round(o)) if got else (None, None, None)


def _build_week(now, hist, kc):
    """주간 서머리 (10-17 2차: ① 시장 수급=네이버 소급 주간 합·대금=저장 일수
    합+일수 명기 ② 유니버스=일일 대금 top200 합산 상위 100 → ka10059 주간 집계).

    종목 주간 수치(대금·수급·등락)는 ka10059 일별 행 합산 — 전부 주간 정확치."""
    monday_d = now - timedelta(days=now.weekday())
    monday = monday_d.strftime("%Y%m%d")
    wdates = []
    for i2 in range(6):
        d2 = monday_d + timedelta(days=i2)
        if d2.date() > now.date() or d2.weekday() >= 5:
            continue
        try:
            import quotes
            if quotes.kr_holiday(d2):
                continue
        except Exception:
            pass
        wdates.append(d2.strftime("%Y%m%d"))
    wset = set(wdates)
    wrecs = [h for h in hist if h["date"] in wset and h.get("slot") != "week"]
    if not wrecs:
        return None
    by_day = {}
    for h in sorted(wrecs, key=lambda x: (x["date"], x.get("slot") or "")):
        by_day[h["date"]] = h
    # 시장 주간
    mk_rows = []
    n_amt_days = 0
    for i2, (code, lab) in enumerate((("KOSPI", "코스피"), ("KOSDAQ", "코스닥"),
                                      ("FUT", "선물"))):
        wchg, idxv = _naver_week_chg(code, monday)
        if wchg is None:
            comp = 1.0
            for h in by_day.values():
                m = h.get("market") or []
                if len(m) > i2 and m[i2][2] is not None:
                    comp *= 1 + m[i2][2] / 100
            wchg = (comp - 1) * 100
            idxv = (by_day[max(by_day)]["market"][i2][1]
                    if by_day and len(by_day[max(by_day)].get("market") or []) > i2
                    else None)
        amt = 0.0
        days = 0
        for h in by_day.values():
            m = h.get("market") or []
            if len(m) > i2 and m[i2][3]:
                amt += m[i2][3]
                days += 1
        n_amt_days = max(n_amt_days, days)
        p, f, o = _week_flows(code, wdates)
        mk_rows.append([lab, idxv, wchg, round(amt, 1), p, f, o])
    # 종목 유니버스: 주간 대금 top200 합산 → 상위 110 후보
    agg: dict = {}
    for h in by_day.values():
        for code, name, mkt, amt in h.get("amt100") or []:
            a = agg.setdefault(code, {"name": name, "mkt": mkt, "amt": 0.0})
            a["amt"] += amt or 0
    if not agg:                              # 과도기 폴백: 일일 표 등장 종목
        for h in by_day.values():
            for s in h.get("sections") or []:
                for r in s.get("rows") or []:
                    if r.get("code"):
                        agg[r["code"]] = {"name": r["name"], "mkt": r["mkt"],
                                          "amt": r.get("amt_eok") or 0}
    cand_codes = [c for c, _ in sorted(agg.items(), key=lambda kv: -kv[1]["amt"])[:110]]
    try:
        import nxt
        smap = nxt.load_sector_map()
    except Exception:
        smap = {}
    rows = []
    for code in cand_codes:
        meta = agg[code]
        try:
            d, _ = kc.request("ka10059", {"dt": now.strftime("%Y%m%d"),
                                          "stk_cd": code, "amt_qty_tp": "1",
                                          "trde_tp": "0", "unit_tp": "1"},
                              endpoint="/api/dostk/stkinfo")
            drows = sorted((x for x in d.get("stk_invsr_orgn") or []),
                           key=lambda x: x.get("dt") or "", reverse=True)
        except Exception:
            continue
        wamt = wp = wf = wo = 0.0
        cur = base = None
        for x in drows:
            dt = x.get("dt") or ""
            px = _num(x.get("cur_prc"))
            if dt in wset:
                wamt += (_num(x.get("acc_trde_prica")) or 0) / 100
                wp += (_num(x.get("ind_invsr")) or 0) / 100
                wf += (_num(x.get("frgnr_invsr")) or 0) / 100
                wo += (_num(x.get("orgn")) or 0) / 100
                if cur is None and px:
                    cur = abs(px)
            elif dt < monday and base is None and px:
                base = abs(px)
                break
        if not wamt or not cur or not base:
            continue
        rows.append({"code": code, "name": meta["name"], "mkt": meta["mkt"],
                     "sec": smap.get(code) or "",
                     "chg": round((cur / base - 1) * 100, 2),
                     "amt_eok": round(wamt), "issue": "",
                     "flow": {"p": round(wp), "f": round(wf), "i": round(wo)}})
        time.sleep(0.2)
    rows.sort(key=lambda r: -r["amt_eok"])
    top = rows[:10]
    pool = rows[:100]                        # 주간 대금 상위 100 (10-17 사용자)
    gain = sorted([r for r in pool if r["chg"] > 0], key=lambda r: -r["chg"])[:10]
    lab = f"{monday_d.month}/{monday_d.day:02d}~{now.month}/{now.day:02d}"
    note = (f" · 시장 대금 {n_amt_days}일 합" if n_amt_days < len(wdates) else "")
    return {"date": now.strftime("%Y%m%d"), "slot": "week", "v": 2,
            "asof": now.strftime("%m/%d %H:%M"), "cands": cand_codes,
            "week_label": lab + note,
            "market": mk_rows,
            "sections": [
                {"title": f"주간 거래대금 TOP 10 ({lab})", "rows": top},
                {"title": f"주간 상승률 TOP 10 (주간 거래대금 상위 100위, {lab})",
                 "rows": gain}]}


PCT = "[Red]+0.0%;[Blue]-0.0%;0.0%"
NET = "+#,##0;-#,##0;0"
WIDTHS = {"1": 80, "2": 80, "3": 80, "4": 95, "5": 85, "6": 85, "7": 85,
          "8": 30, "9": 140, "10": 75, "11": 110, "12": 90, "13": 100,
          "14": 430, "15": 50, "16": 85, "17": 85, "18": 85}


def _stock_blocks(secs, us_mode, r0, with_flow=False):
    """종목 표들(col 9) — (blocks, 끝다음행).
    with_flow: 주간 서머리만 True — 일일 표의 종목별 수급 열은 삭제(10-17),
    주간 '한 주간 수급'은 사용자 명시 요구라 유지."""
    blocks, r = [], r0
    for s in secs:
        def _lnk(x):
            u = (x.get("issue_url") or "").replace('"', "%22")
            return f'=HYPERLINK("{u}","🔗")' if u else ""
        if us_mode:
            hd = ["종목", "시장", "섹터", "등락률", "거래대금($B)", "이유", "링크"]
            rws = [[x["name"], "미국", x["sec"], x["chg"] / 100, x["dvol"],
                    x["issue"], _lnk(x)] for x in s["rows"]]
            fm = {"4": PCT, "5": "0.0"}
        elif with_flow:
            hd = ["종목", "시장", "섹터", "등락률", "거래대금(억)", "이유", "링크",
                  "개인(억)", "외국인(억)", "기관(억)"]
            rws = [[x["name"], x["mkt"], x.get("sec") or "", x["chg"] / 100,
                    x["amt_eok"], x["issue"], _lnk(x),
                    (x.get("flow") or {}).get("p"),
                    (x.get("flow") or {}).get("f"),
                    (x.get("flow") or {}).get("i")] for x in s["rows"]]
            fm = {"4": PCT, "5": "#,##0", "8": NET, "9": NET, "10": NET}
        else:
            hd = ["종목", "시장", "섹터", "등락률", "거래대금(억)", "이유", "링크"]
            rws = [[x["name"], x["mkt"], x.get("sec") or "", x["chg"] / 100,
                    x["amt_eok"], x["issue"], _lnk(x)] for x in s["rows"]]
            fm = {"4": PCT, "5": "#,##0"}
        blocks.append({"title": ("🇺🇸 " if us_mode else "🇰🇷 ") + s["title"],
                       "col": 9, "row": r, "headers": hd, "rows": rws,
                       "fmts": fm,
                       "align": {"1": "center", "2": "center", "3": "center",
                                 "7": "center"}})
        r += 2 + max(len(rws), 1) + 1
    return blocks, r


def _date_lab(date: str) -> str:
    d = datetime.strptime(date, "%Y%m%d")
    return f"{d.month}/{d.day:02d} ({'월화수목금토일'[d.weekday()]})"


def _us_zone(us):
    """미국 존 — 1행부터. (blocks, 높이)."""
    mk = [[m[0], m[1], (m[2] / 100 if m[2] is not None else None), m[3]]
          for m in us.get("market") or []]
    blocks = [{"title": f"🇺🇸 {_date_lab(us['date'])} 시장 ({us.get('asof', '')})",
               "col": 1, "row": 1,
               "headers": ["", "지수", "등락률", "거래대금($B)"], "rows": mk,
               "fmts": {"2": "#,##0.00", "3": PCT, "4": "0.0"},
               "boldCol": 1, "align": {"1": "center"}}]
    left_end = 1 + 2 + max(len(mk), 1)
    sb, right_end = _stock_blocks(us["sections"], True, 1)
    return blocks + sb, max(left_end, right_end - 1)


def _kr_zone(kr):
    """국내 존 — 내용 먼저, 맨 아래 검은 띠(아래 존과 분리). 1행 기준 상대 블록.
    모든 존이 맨 위(1행) 삽입이라 시간순으로 위에 쌓인다 (10-16 사용자:
    아래부터 US→14:30→19→20)."""
    mk = [[m[0], m[1], (m[2] / 100 if m[2] is not None else None),
           m[3], m[4], m[5], m[6]] for m in kr.get("market") or []]
    head = (f"📅 주간 서머리 ({kr.get('week_label', '')})" if kr.get("slot") == "week"
            else f"🇰🇷 {_date_lab(kr['date'])} 시장 ({kr.get('asof', '')} · "
                 f"{kr.get('slot', '')} 판)")
    blocks = [{"title": head,
               "col": 1, "row": 1,
               "headers": ["", "지수", "등락률" + (" (주간)" if kr.get("slot") == "week" else ""),
                           "거래대금(조)", "개인(억)", "외국인(억)", "기관(억)"], "rows": mk,
               "fmts": {"2": "#,##0", "3": PCT, "4": "0.0",
                        "5": NET, "6": NET, "7": NET},
               "boldCol": 1, "align": {"1": "center"}}]
    left_end = 1 + 2 + max(len(mk), 1)
    sb, right_end = _stock_blocks(kr["sections"], False, 1,
                                  with_flow=kr.get("slot") == "week")
    end = max(left_end, right_end - 1)
    blocks += sb
    blocks.append({"col": 1, "row": end + 1, "rows": [[""] * 17], "bg": "#000000"})
    return blocks, end + 2


def _shift(blocks, off):
    out = []
    for b in blocks:
        b = dict(b)
        b["row"] = b["row"] + off
        out.append(b)
    return out


def _post(payload):
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    if not url:
        return None
    try:
        r = requests.post(url, json=payload, timeout=40, allow_redirects=True)
        if r.status_code == 200 and r.text[:30].strip() in ("ok", "created", "exists"):
            return r.text[:30].strip()
    except Exception as e:
        print(f"[sheet] 전송 실패: {type(e).__name__}")
    return None


def push_all() -> int:
    """존 단위 삽입 푸시 (10-16 사용자: 시간대 적층 + 수동 수정 보존).

    탭을 지우지 않는다. 미국 존은 하루 1회 생성(1행~), 국내 존은 새 슬롯마다
    '미국 존 바로 아래'에 행 삽입 — 기존 존(사용자가 고친 셀 포함)은 그대로
    아래로 밀린다. 같은 슬롯 재수집은 재전송하지 않아 수동 수정을 덮지 않는다.
    상태: sheet_push_state.json {date: {"us": bool, "us_h": int, "slots": [...]}}"""
    token = os.environ.get("SHEET_WEBHOOK_TOKEN", "").strip()
    state = _load_state()
    us_hist, kr_hist = {}, {}
    try:
        for l in open(os.path.join(DATA, "us_sheet.jsonl"), encoding="utf-8"):
            h = json.loads(l)
            us_hist[h["date"]] = h
    except FileNotFoundError:
        pass
    try:
        for l in open(STORE, encoding="utf-8"):
            h = json.loads(l)
            if h.get("v") == 2:
                if "slot" not in h:
                    try:
                        hh, mm = map(int, h["asof"].split()[1].split(":"))
                        h["slot"] = _slot_of(hh, mm)
                    except Exception:
                        h["slot"] = "20:00"
                kr_hist.setdefault(h["date"], {})[h["slot"]] = h
    except FileNotFoundError:
        pass
    n = 0
    for date in sorted(set(us_hist) | set(kr_hist)):
        st = state.get(date)
        if isinstance(st, str):            # 구형(탭 재생성 시절) 상태 — 완료 간주
            continue
        st = st or {"us": False, "slots": []}
        d = datetime.strptime(date, "%Y%m%d")
        d_lab = f"{d.month}월"            # 월 단위 탭 (10-16 사용자)
        us = us_hist.get(date)
        if us and not st["us"]:
            blocks, h = _us_zone(us)
            got = _post({"token": token, "date": d_lab, "mode": "insert_or_create",
                         "at": 1, "n": h + 1, "widths": WIDTHS, "blocks": blocks})
            if got:
                st["us"] = True
                n += 1
                print(f"[sheet] {d_lab} 미국 존 기록")
            else:
                state[date] = st
                break
        # 모든 존을 맨 위(1행) 삽입 — 이른 슬롯부터 처리하면 늦은 슬롯이
        # 그 위에 쌓여, 위에서부터 20:00→19:00→14:30→미국(맨 아래) 순이 된다.
        for slot in sorted(kr_hist.get(date, {})):
            if slot in st["slots"]:
                continue
            kr = kr_hist[date][slot]
            zb, h = _kr_zone(kr)
            got = _post({"token": token, "date": d_lab, "mode": "insert_or_create",
                         "at": 1, "n": h, "widths": WIDTHS, "blocks": zb})
            if got:
                st["slots"].append(slot)
                n += 1
                print(f"[sheet] {d_lab} 국내 {slot} 존 기록")
            else:
                break
        state[date] = st
    _save_state(state)
    return n


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, HERE)
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")))
    if "--push-only" not in sys.argv:
        collect()
    push_all()
