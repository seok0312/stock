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
TOP_N = 10          # 10-16 사용자: 20은 많다 — 10으로 시작
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
                return h["title"][:90]
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
                    return t[:90]
    except Exception:
        pass
    ths = (tmap.get(code) or [])[:2]
    return ("테마: " + "·".join(ths)) if ths else ""


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
    amt_rows, up_rows = _kiwoom_fetch()
    top = sorted(amt_rows.values(), key=lambda r: -r["amt_eok"])[:TOP_N]
    gain = [r for r in up_rows if (r["amt_eok"] or 0) >= 1000 and r["chg"] > 0]
    gain = sorted(gain, key=lambda r: -r["chg"])[:10]
    lim = sorted([r for r in up_rows if r["chg"] >= 29.5],
                 key=lambda r: -(r["amt_eok"] or 0))
    n_start = now.replace(hour=0, minute=0)
    for r in top + gain + lim:          # 이유 붙이기 (중복 종목은 캐시)
        if "issue" not in r:
            r["issue"] = _reason(r["name"], r["code"], news, now, n_start, tmap)
            time.sleep(0.05)
            for o in top + gain + lim:
                if o["code"] == r["code"]:
                    o["issue"] = r["issue"]

    def pack(rs):
        return [{"name": r["name"], "mkt": r["mkt"], "amt_eok": r["amt_eok"] or 0,
                 "chg": round(r["chg"], 2), "issue": r.get("issue") or ""} for r in rs]
    rec = {"date": today, "asof": now.strftime("%m/%d %H:%M"), "v": 2,
           "market": _market_summary(), "sections": [
        {"title": f"거래대금 상위 {TOP_N}", "rows": pack(top)},
        {"title": "상승률 상위 10 (거래대금 1,000억↑)", "rows": pack(gain)},
        {"title": "상한가", "rows": pack(lim)},
    ]}
    hist = []
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        pass
    hist = [h for h in hist if h["date"] != today] + [rec]
    os.makedirs(DATA, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        for h in hist:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    st = _load_state()
    if st.pop(today, None):
        _save_state(st)
    print("[top20] " + today + " 수집 — "
          + " · ".join(f"{s['title']} {len(s['rows'])}" for s in rec["sections"])
          + f" · 이유 {sum(1 for s in rec['sections'] for r in s['rows'] if r['issue'])}건")
    return rec


def push_all() -> int:
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    token = os.environ.get("SHEET_WEBHOOK_TOKEN", "").strip()
    try:
        hist = [json.loads(l) for l in open(STORE, encoding="utf-8")]
    except FileNotFoundError:
        return 0
    state = _load_state()
    todo = [h for h in hist if not state.get(h["date"])]
    if not url:
        print(f"[top20] SHEET_WEBHOOK_URL 미설정 — 전송 보류 (미전송 {len(todo)}일)")
        return 0
    n = 0
    for rec in sorted(todo, key=lambda h: h["date"]):
        if rec.get("v") != 2:
            state[rec["date"]] = "skip-v1"
            continue
        d = datetime.strptime(rec["date"], "%Y%m%d")
        d_lab = f"{d.month}/{d.day:02d} ({'월화수목금토일'[d.weekday()]})"
        payload = {"token": token, "date": d_lab, "asof": rec.get("asof"),
                   "market": rec.get("market") or [],
                   "sections": [{"title": s["title"],
                                 "rows": [[r["name"], r["mkt"], r["amt_eok"],
                                           r["chg"], r["issue"]] for r in s["rows"]]}
                                for s in rec["sections"]]}
        try:
            resp = requests.post(url, json=payload, timeout=40, allow_redirects=True)
            ok = resp.status_code == 200 and "ok" in resp.text[:50].lower()
        except Exception as e:
            print(f"[top20] {rec['date']} 전송 실패: {type(e).__name__}")
            ok = False
        if ok:
            state[rec["date"]] = datetime.now(KST).isoformat(timespec="seconds")
            n += 1
            print(f"[top20] {rec['date']} 시트 기록 완료 ({d_lab})")
        else:
            print(f"[top20] {rec['date']} 시트 응답 비정상 — 다음 실행 때 재시도")
            break
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
