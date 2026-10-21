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
BROKER_LOG = os.path.join(DATA, "broker_log.jsonl")   # 거래원 수급 원장 (10-20 사용자, 분석용)
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
                              "amt_eok": round(amt / 100), "chg": chg,
                              "cur": _num(r.get("cur_prc"))}
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
        try:
            flows.apply_kiwoom(fl)      # KRX+NXT 통합 (10-19: 네이버는 KRX 단독이라
        except Exception:               # 외인 등 수급이 크게 어긋남 — 브리핑과 동일 소스)
            pass
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


def _prev_trading_rec(hist, today: str):
    """전 거래일 기준판 — 20:00 우선, 없으면 그 날 마지막 슬롯 (09:10 추적용)."""
    days: dict = {}
    for h in hist:
        if h.get("v") == 2 and h.get("slot") not in (None, "week") \
                and h.get("date", "") < today:
            days.setdefault(h["date"], {})[h["slot"]] = h
    if not days:
        return None
    slots = days[max(days)]
    return slots.get("20:00") or slots[max(slots)]


def _build_track(base: dict, now, kc, cur_map=None):
    """전일 대금·상승률 TOP 합집합 → 현재 등락률순 추적 리스트 + 거래원 원장
    (10-20 사용자: ① 상승/하락 빨·파 채우기 ② 상승률순 리스트 ③ 거래원별 수급
    기록 — 분석용). 등락률·거래원은 ka10002 한 콜, 키움 실패 시 네이버 폴백.
    cur_map(10-21): 같은 수집의 ka10032 통합(KRX+NXT) 등락률 {code: chg} —
    ka10002 는 KRX 단독가라 대금 표와 0.1~0.8%p 어긋나던 것을 통일."""
    today = now.strftime("%Y%m%d")
    rows, seen = [], {}
    for si, s in enumerate((base.get("sections") or [])[:2]):
        tag = "대금" if si == 0 else "상승률"
        for x in s.get("rows") or []:
            code = x.get("code")
            if not code:
                continue
            if code in seen:
                seen[code]["src"] += "·" + tag
                continue
            r = {"code": code, "name": x["name"], "mkt": x["mkt"],
                 "sec": x.get("sec") or "", "src": tag, "chg": None,
                 "amt_prev": x.get("amt_eok"), "chg_prev": x.get("chg"),
                 "issue": x.get("issue") or "", "issue_url": x.get("issue_url") or "",
                 "flow": x.get("flow")}
            seen[code] = r
            rows.append(r)
    logs = []
    fails = 0
    for r in rows:
        d = None
        # 연속 3회 실패 시 잔여 종목은 전부 네이버 폴백 — 부분 장애 날 재시도
        # 백오프(종목당 최악 ~37초)가 아침 슬롯을 통째로 밀지 않게 (리뷰 10-20)
        if kc is not None and fails < 3:
            try:
                d, _ = kc.request("ka10002", {"stk_cd": r["code"]},
                                  endpoint="/api/dostk/stkinfo")
                # 등락률은 통합(ka10032) 값 우선 — 대금 표와 기준 통일 (10-21)
                r["chg"] = ((cur_map or {}).get(r["code"])
                            if (cur_map or {}).get(r["code"]) is not None
                            else _num(d.get("flu_rt")))
                fails = 0
            except Exception:
                d = None
                fails += 1
            time.sleep(0.2)
        if r["chg"] is None:                 # 키움 실패 — 네이버 폴백(등락률만)
            try:
                b = requests.get(f"https://m.stock.naver.com/api/stock/{r['code']}/basic",
                                 headers=NAVER_UA, timeout=10).json()
                r["chg"] = _num(b.get("fluctuationsRatio"))
            except Exception:
                pass
        if d:
            logs.append({"date": today, "asof": now.strftime("%m/%d %H:%M"),
                         "slot": "09:10", "base_date": base["date"],
                         "code": r["code"], "name": r["name"], "src": r["src"],
                         "chg": r["chg"],
                         "prev": {"amt_eok": r["amt_prev"], "chg": r["chg_prev"],
                                  "flow": r["flow"]},
                         "brokers": {k: v for k, v in d.items()
                                     if k.startswith(("sel_trde", "buy_trde"))
                                     and v not in ("", None)}})
    if logs:   # 원장은 무기한 적층 — (날짜,종목) 병합: 부분 성공 재실행이
        old = []   # 앞서 기록된 행을 깎지 않는다 (리뷰 10-20)
        try:
            for l in open(BROKER_LOG, encoding="utf-8"):
                try:
                    old.append(json.loads(l))   # 찢긴 줄은 건너뛰고 복구(크래시 내성)
                except ValueError:
                    continue
        except (FileNotFoundError, OSError):
            pass
        newk = {(x["date"], x["code"]) for x in logs}
        old = [x for x in old if (x.get("date"), x.get("code")) not in newk]
        tmp = BROKER_LOG + ".tmp"              # 원자 교체 — 쓰기 도중 사망에도 원본 보존
        with open(tmp, "w", encoding="utf-8") as f:
            for x in old + logs:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        os.replace(tmp, BROKER_LOG)
        print(f"[top20] 거래원 기록 {len(logs)}종목 → broker_log.jsonl")
    rows.sort(key=lambda r: (r["chg"] is None, -(r["chg"] or 0)))
    return {"base_date": base["date"], "base_slot": base.get("slot"), "rows": rows}


def _strategy_picks(today: str, cur_map=None) -> dict:
    """확정 전략 부합 종목 (10-21 사용자: 등급표 아래 '종목:' 자동 기입).

    종목1 = 60일선 위 ∧ 태그1(갭) ∧ 주도주 ∧ 양봉 눌림(pos .5~.8, 8-13 보정)
    종목2 = 태그3(손바뀜) ∧ 주도주 ∧ 음봉 아님
    태그는 tag_track 15:40 기록 기준 — 그 전 판(09:10/14:30)엔 '집계 전'."""
    rows = []
    try:
        for l in open(os.path.join(DATA, "tag_picks.jsonl"), encoding="utf-8"):
            r = json.loads(l)
            if r.get("date") == today and r.get("band", "hot5") == "hot5":
                rows.append(r)
    except FileNotFoundError:
        pass
    if not rows:
        return {"ready": False}
    above = bool(rows[0].get("above60"))
    out = {"ready": True, "above60": above, "t1": [], "t1_near": [], "t3": []}

    def candle(code):
        try:
            j = requests.get(f"https://m.stock.naver.com/api/stock/{code}/price",
                             params={"pageSize": 1}, headers=NAVER_UA,
                             timeout=8).json()[0]
            o, h, l2, c = [float(str(j[k]).replace(",", ""))
                           for k in ("openPrice", "highPrice", "lowPrice",
                                     "closePrice")]
            pos = (c - l2) / (h - l2) if h > l2 else None
            return pos, c > o
        except Exception:
            return None, None

    for r in rows:
        tag = r.get("tag")
        if tag not in ("갭", "손바뀜") or not r.get("leader"):
            continue
        pos, bull = candle(r["code"])
        time.sleep(0.05)
        item = {"code": r["code"], "name": r["name"], "chg": r["chg"],
                "buy": r.get("close"),                 # 기록시점가(지연 시세 가능, 참고용)
                "buy_eve": (cur_map or {}).get(r["code"])}  # 수집 시점 통합가
                                                       # (20시판 = 저녁 최종가 E)
        if tag == "갭" and above:
            if bull and pos is not None and 0.5 <= pos < 0.8:
                out["t1"].append(item)
            elif bull is not False:
                out["t1_near"].append({**item, "pos": pos})
        elif tag == "손바뀜" and bull is not False:
            out["t3"].append(item)
    return out


def _eval_yesterday_picks(today: str) -> list:
    """전일 확정 전략 픽의 실제 성적 — 09:10판 (10-21 사용자).
    종목1(갭) = 매수가 대비 '오늘 시가' = 전략 확정 손익.
    종목2(손바뀜) = 매수가 대비 현재가(보유 중 평가).

    매수가(분모) = 전일 '정규장 종가 R' — 오늘 시세 row 의 (현재가−전일비)로 복원.
    tag 기록 close 는 지연 시세(15:20경)라 분모로 쓰지 않는다 (10-21 멜콘 14,990≠R 15,390).
    저녁 세션(9-14 개시, 시간외·NXT ~20시)에 산 경우를 위해 buy_eve(20:05 통합가) 병기."""
    hist = []
    try:
        for l in open(STORE, encoding="utf-8"):
            h = json.loads(l)
            if h.get("v") == 2 and h.get("slot") != "week" and h["date"] < today:
                hist.append(h)
    except FileNotFoundError:
        return []
    prev = next((h for h in reversed(hist) if h.get("picks", {}).get("ready")), None)
    if not prev:
        return []
    pk = prev["picks"]

    def quote(code):
        try:
            j = requests.get(f"https://m.stock.naver.com/api/stock/{code}/price",
                             params={"pageSize": 1}, headers=NAVER_UA,
                             timeout=8).json()[0]

            def f(k):
                return float(str(j[k]).replace(",", ""))

            return {"dt": str(j.get("localTradedAt", ""))[:10].replace("-", ""),
                    "o": f("openPrice"), "c": f("closePrice"),
                    "R": f("closePrice") - f("compareToPreviousClosePrice")}
        except Exception:
            return None

    out = []
    for key, head, use_open in (("t1", "종목1(갭→시가 매도)", True),
                                ("t3", "종목2(손바뀜 보유 중)", False)):
        items = [x for x in pk.get(key) or [] if isinstance(x, dict)]
        if not items:
            continue
        parts = []
        for x in items:
            q = quote(x["code"])
            time.sleep(0.05)
            if not q or q["dt"] != today or not q["R"] or q["R"] <= 0:
                parts.append(f"{x['name']}(?)")      # 거래정지·데이터 지연
                continue
            px = q["o"] if use_open else q["c"]
            s = f"{x['name']}({(px / q['R'] - 1) * 100:+.1f}%"
            eve = x.get("buy_eve")
            if eve and abs(eve / q["R"] - 1) > 0.001:
                s += f" / 저녁매수 {(px / eve - 1) * 100:+.1f}%"
            parts.append(s + ")")
        out.append(f"{head}: " + " · ".join(parts))
    if not out and (pk.get("t1") or pk.get("t3")):
        return []
    if not out:
        out = ["전일 픽 없음 (종목1 게이트/무신호)"] if not pk.get("above60") else []
    return out


def _slot_of(hour: int, minute: int) -> str:
    """수집 시각 → 슬롯 버킷. 09:10/14:30/19:00/20:00 크론 ± 수동 실행 흡수."""
    if hour < 12:
        return "09:10"        # 장 초반 (10-19 사용자)
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

    # 종목별 수급 (10-17 복원 — 사용자: 다음 주까지 보고 결정) — ka10059 당일,
    # 백만원→억. 장중(14:35)엔 개인 미공표라 -(외+기) 근사(97.1% 부호 일치).
    try:
        import kr_sectors as krs
        kc = krs._kc()
    except Exception:
        kc = None
    fl_cache: dict = {}
    for r in top + gain + lim:
        code = r["code"]
        if code in fl_cache or kc is None:
            r["flow"] = fl_cache.get(code)
            continue
        try:
            d, _ = kc.request("ka10059", {"dt": today, "stk_cd": code,
                                          "amt_qty_tp": "1", "trde_tp": "0",
                                          "unit_tp": "1"},
                              endpoint="/api/dostk/stkinfo")
            rows0 = sorted((x for x in d.get("stk_invsr_orgn") or []),
                           key=lambda x: x.get("dt") or "", reverse=True)
            t0 = rows0[0] if rows0 and rows0[0].get("dt") == today else None
            if t0:
                f = _num(t0.get("frgnr_invsr")) or 0
                i = _num(t0.get("orgn")) or 0
                p = _num(t0.get("ind_invsr"))
                if not p:
                    p = -(f + i)
                fl_cache[code] = {"p": round(p / 100), "f": round(f / 100),
                                  "i": round(i / 100)}
            else:
                fl_cache[code] = None
        except Exception:
            fl_cache[code] = None
        r["flow"] = fl_cache[code]
        time.sleep(0.25)
    for r in top + gain + lim:
        r.setdefault("flow", fl_cache.get(r["code"]))

    def pack(rs):
        return [{"code": r["code"], "name": r["name"], "mkt": r["mkt"],
                 "sec": smap.get(r["code"]) or "",
                 "amt_eok": r["amt_eok"] or 0,
                 "chg": round(r["chg"], 2), "issue": r.get("issue") or "",
                 "issue_url": r.get("issue_url") or "",
                 "flow": r.get("flow")} for r in rs]
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
    rec["picks"] = _strategy_picks(
        today, cur_map={c: abs(v["cur"]) for c, v in amt_rows.items()
                        if v.get("cur")})   # 확정 전략 + 수집 시점 실행가 (10-21)
    if slot == "09:10":
        rec["yest_eval"] = _eval_yesterday_picks(today)
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
    if slot == "09:10":                    # 전일 TOP 추적 (10-20 사용자)
        base = _prev_trading_rec(hist, today)
        if base:
            try:
                rec["track"] = _build_track(
                    base, now, kc,
                    cur_map={c: v["chg"] for c, v in amt_rows.items()})
                print(f"[top20] 전일({base['date']} {base.get('slot')}판) TOP 추적 "
                      f"{len(rec['track']['rows'])}종목")
            except Exception as e:
                print(f"[top20] 전일 추적 실패(계속): {type(e).__name__}: {e}")
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
          "14": 430, "15": 430, "16": 85, "17": 85, "18": 85, "19": 85}


RANK_UP_BG = "#f8d0cd"     # 순위 상승·신규 진입 (연빨강)
RANK_DN_BG = "#cfe0f7"     # 순위 하락 (연파랑)


def _rank_bg(rows, prev_rows):
    """이전 판 대비 순위 변동 → 종목 칸 배경색 리스트 (10-19 사용자)."""
    def key(x):
        return x.get("code") or x.get("name")
    prev = {key(x): i for i, x in enumerate(prev_rows or [])}
    out = []
    for i, x in enumerate(rows):
        p = prev.get(key(x))
        if p is None or p > i:
            out.append(RANK_UP_BG)          # 신규 진입 또는 순위 상승
        elif p < i:
            out.append(RANK_DN_BG)
        else:
            out.append(None)
    return out if any(out) else None


def _stock_blocks(secs, us_mode, r0, with_flow=False, prev_secs=None):
    """종목 표들(col 9) — (blocks, 끝다음행).
    with_flow: 주간 서머리만 True — 일일 표의 종목별 수급 열은 삭제(10-17),
    주간 '한 주간 수급'은 사용자 명시 요구라 유지."""
    blocks, r = [], r0
    for si, s in enumerate(secs):
        def _lnk(x):
            # URL 원문 그대로 (10-19 사용자: 아이콘 대신 주소 노출) — 시트가
            # 자동으로 링크화한다.
            return x.get("issue_url") or ""
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
        blk = {"title": ("🇺🇸 " if us_mode else "🇰🇷 ") + s["title"],
               "col": 9, "row": r, "headers": hd, "rows": rws,
               "fmts": fm,
               "align": {"1": "center", "2": "center", "3": "center",
                         "7": "left"}}   # 링크 열 명시 왼쪽(10-19 — 잔존 서식 덮기)
        # 대금(0)·상승률(1) 표만 순위 변동 색칠 — 상한가·주간 제외
        if prev_secs is not None and si < 2 and si < len(prev_secs):
            bg1 = _rank_bg(s["rows"], (prev_secs[si] or {}).get("rows"))
            if bg1:
                blk["bg1"] = bg1
        blocks.append(blk)
        r += 2 + max(len(rws), 1) + 1
    return blocks, r


def _date_lab(date: str) -> str:
    d = datetime.strptime(date, "%Y%m%d")
    return f"{d.month}/{d.day:02d} ({'월화수목금토일'[d.weekday()]})"


def _hist_metric(fn, cur_date, limit=40):
    """과거 판들(주간 제외)에서 지표 추출 — 백분위 비교용. 오늘 제외."""
    vals = []
    try:
        for l in open(STORE, encoding="utf-8"):
            h = json.loads(l)
            if h.get("v") != 2 or h.get("slot") == "week" or h.get("date") == cur_date:
                continue
            v = fn(h)
            if v is not None:
                vals.append(v)
    except FileNotFoundError:
        pass
    return vals[-limit:]


def _analysis_rows(kr: dict, prev: dict | None):
    """시장·종목 해석 (10-20 사용자 2차: 표에 있는 숫자 재진술 금지 — 인사이트만).

    전부 룰 기반: 검증된 사실(HANDOFF 8-7 국면, 8-8 주체 성향)과 자체 이력
    백분위에만 근거. 전망·추측 문장은 만들지 않는다."""
    secs = kr.get("sections") or []
    mk = {m[0]: m for m in kr.get("market") or [] if m and m[0]}

    def eok(v):
        if v is None:
            return "?"
        return f"{v / 10000:,.1f}조" if abs(v) >= 10000 else f"{v:,.0f}억"

    def sec_agg(rows):
        agg: dict = {}
        for x in rows:
            s = (x.get("sec") or "").strip()
            if s:
                a = agg.setdefault(s, [0, 0.0])
                a[0] += 1
                a[1] += x.get("amt_eok") or 0
        return sorted(agg.items(), key=lambda kv: -kv[1][1])

    Lm: list = []
    ks, kq = mk.get("코스피"), mk.get("코스닥")
    # ① 수급 구조 판독 — 방향(지수) × 주체 조합의 의미 (8-8: 개인 역추세 -0.64)
    if ks and ks[2] is not None and ks[5] is not None:
        chg, p, f_, i = ks[2], ks[4] or 0, ks[5] or 0, ks[6] or 0
        smart = f_ + i
        if chg < -0.5 and smart < 0 and p > 0:
            Lm.append(f"수급 구조: 외인·기관 매도(합 {eok(smart)})를 개인이 흡수 — "
                      "개인은 역추세 주체(8-8, 상관 -0.64)라 받침이지 반전 신호 아님")
        elif chg < -0.5 and f_ > 0:
            Lm.append(f"수급 구조: 낙폭에 외인 역매수({eok(f_)}) — 순추세 주체의 "
                      "역방향 유입, 드문 조합(전환 관찰 대상)")
        elif chg > 0.5 and f_ > 0 and i > 0:
            Lm.append("수급 구조: 외인·기관 동반 매수의 상승 — 순추세 주체 주도"
                      "(지속성은 개인 주도 상승보다 양호)")
        elif chg > 0.5 and smart < 0 and p > 0:
            Lm.append("수급 구조: 개인이 끌어올린 상승(외인·기관은 매도) — "
                      "순추세 주체 부재, 되돌림 취약 구조")
    # ② 외인 극단성 — 이력 백분위 (극단일 때만 한 줄)
    if ks and ks[5] is not None:
        hist_f = _hist_metric(
            lambda h: next((m[5] for m in h.get("market") or []
                            if m and m[0] == "코스피"), None), kr.get("date"))
        if len(hist_f) >= 10:
            below = sum(1 for v in hist_f if v < ks[5])
            pr = below / len(hist_f) * 100
            if pr <= 15:
                Lm.append(f"외인 코스피 순매도 강도: 최근 {len(hist_f)}판 중 "
                          f"하위 {pr:.0f}% — 투매 극단 구간")
            elif pr >= 85:
                Lm.append(f"외인 코스피 순매수 강도: 최근 {len(hist_f)}판 중 "
                          f"상위 {100 - pr:.0f}% — 유입 극단 구간")
    # ③ 대금 증감의 성격 (직전 판 대비 — 증감률만, 절대값 재진술 없음)
    if ks and kq and (ks[3] or kq[3]):
        amt = (ks[3] or 0) + (kq[3] or 0)
        pmk = {m[0]: m for m in (prev or {}).get("market") or [] if m and m[0]}
        pamt = ((pmk.get("코스피") or [None] * 4)[3] or 0) +                ((pmk.get("코스닥") or [None] * 4)[3] or 0)
        if amt and pamt:
            d = (amt / pamt - 1) * 100
            chg = ks[2] or 0
            if abs(d) >= 5:
                nature = ("대금 실린 하락(손바뀜 동반 투매, 관망성 아님)"
                          if d > 0 and chg < 0 else
                          "대금 실린 상승(참여 확산)" if d > 0 else
                          "대금 마른 하락(매수 공백형 — 투매보다 관망)" if chg < 0 else
                          "대금 마른 상승(참여 얕음)")
                Lm.append(f"거래대금 직전판 대비 {d:+.0f}% — {nature}")
    # ④ 국면 룰 (검증 8-7: 갭은 60일선 아래 엣지 소멸)
    try:
        import tag_track
        reg = tag_track._kospi_above60()
        if reg:
            Lm.append("국면: 코스피 60일선 "
                      + ("위 — 갭(태그1)·손바뀜 정상 운용" if reg["above60"]
                         else "아래 — 갭(태그1) 자제 국면(8-7), 손바뀜 D+5 는 정상"))
    except Exception:
        pass
    # ⑤ 전일 주도주 추적 판정 (기존 유지 — 해석형)
    tr = kr.get("track") or {}
    if tr.get("rows"):
        chgs = [x["chg"] for x in tr["rows"] if x.get("chg") is not None]
        if chgs:
            up = sum(1 for c in chgs if c > 0)
            dn = sum(1 for c in chgs if c < 0)
            Lm.append(f"전일 주도주 {len(chgs)}종목: 상승 {up} · 하락 {dn} · "
                      f"평균 {sum(chgs) / len(chgs):+.1f}% — "
                      + ("갭 지속 우세" if up > dn else
                         "갭 소멸 우세" if dn > up else "팽팽"))

    rows_amt = (secs[0].get("rows") if secs else []) or []
    rows_up = (secs[1].get("rows") if len(secs) > 1 else []) or []
    rows_lim = (secs[2].get("rows") if len(secs) > 2 else []) or []
    Ls: list = []
    # 09:10판 전용 — 아침 상승 TOP15 의 '전일 프로필' (10-20 분석, HANDOFF 8-12:
    # 아침 갭 top15 리프트 = 상한가+잠김 5.0x · 전일 태그1 2.6x · 상한가 2.6x ·
    # 12%급등 2.2x · 눌림마감 2.0x > 고가마감 1.7x, 기저 11.9%)
    if kr.get("slot") == "09:10" and rows_up and prev:
        psecs = prev.get("sections") or []
        p_lim = {x.get("code") for x in (psecs[2].get("rows") if len(psecs) > 2
                                         else []) or []}
        p_up = {x.get("code") for x in (psecs[1].get("rows") if len(psecs) > 1
                                        else []) or []}
        p_amt = {x.get("code") for x in (psecs[0].get("rows") if psecs
                                         else []) or []}
        ptags = {}
        try:
            for l in open(os.path.join(DATA, "tag_picks.jsonl"), encoding="utf-8"):
                r = json.loads(l)
                if r.get("date") == prev.get("date") and r.get("tag"):
                    ptags[r["code"]] = r["tag"]
        except Exception:
            pass
        cs = [x.get("code") for x in rows_up]
        n_lim = sum(1 for c in cs if c in p_lim)
        n_up = sum(1 for c in cs if c in p_up)
        n_amt = sum(1 for c in cs if c in p_amt)
        n_t1 = sum(1 for c in cs if ptags.get(c) == "갭")
        n_t3 = sum(1 for c in cs if ptags.get(c) == "손바뀜")
        n_new = sum(1 for c in cs if c not in (p_lim | p_up | p_amt)
                    and c not in ptags)
        Ls.append(f"아침 TOP15 전일 프로필: 상한가 출신 {n_lim} · 상승TOP 재등장 {n_up}"
                  f" · 대금TOP {n_amt} · 태그1 {n_t1} · 태그3 {n_t3} · 전일 무흔적 {n_new}")
        Ls.append("(역사 리프트: 상한가+잠김 5.0x · 태그1 2.6x · 상한가 2.6x · "
                  "12%급등 2.2x — 전일 강세의 연장이 아침 갭의 본체)")
    # ⑥ 주도 섹터 — 1위 섹터의 지배력 해석
    ag = sec_agg(rows_amt)
    top_amt = sum(x.get("amt_eok") or 0 for x in rows_amt)
    if ag and top_amt:
        s1, (n1, a1) = ag[0]
        dom = a1 / top_amt * 100
        tail = (f" — TOP{TOP_N} 대금의 {dom:.0f}%, 단일 섹터 장세" if dom >= 60
                else f" — {dom:.0f}% 지배, " + ("양강 구도" if len(ag) > 1
                     and ag[1][1][1] / top_amt >= 0.2 else "분산 장세"))
        Ls.append(f"주도 섹터: {s1} {n1}종목 {eok(a1)}{tail}")
    # ⑦ 쏠림 집중도 — 이력 중앙값 대비
    a200 = sum((r[3] or 0) for r in kr.get("amt100") or [])
    if top_amt and a200:
        share = top_amt / a200 * 100
        hist_s = _hist_metric(
            lambda h: (sum(x.get("amt_eok") or 0
                           for x in (h.get("sections") or [{}])[0].get("rows") or [])
                       / s2 * 100) if (s2 := sum((r[3] or 0)
                                                 for r in h.get("amt100") or [])) else None,
            kr.get("date"))
        if len(hist_s) >= 8:
            med = sorted(hist_s)[len(hist_s) // 2]
            if share - med >= 5:
                Ls.append(f"쏠림 {share:.0f}% (평소 {med:.0f}%) — 주도주 압축 장세, "
                          "소수 종목 외 소외")
            elif med - share >= 5:
                Ls.append(f"쏠림 {share:.0f}% (평소 {med:.0f}%) — 분산 장세, "
                          "주도주 식별 난도 상승")
    # ⑧ 동반 매수/매도 — 해석 꼬리 포함
    def _duo(rows, buy):
        out = []
        for x in rows:
            fl = x.get("flow") or {}
            f_, i = fl.get("f") or 0, fl.get("i") or 0
            if (buy and f_ > 0 and i > 0) or (not buy and f_ < 0 and i < 0):
                out.append((x, f_ + i))
        out.sort(key=lambda t: -abs(t[1]))
        return out
    bb = _duo(rows_amt, True)
    if bb:
        Ls.append("외인·기관 동반 매수: " + " · ".join(
            f"{x['name']}({v:+,.0f}억)" for x, v in bb[:4]))
    bs = _duo(rows_amt, False)
    if bs:
        tail = ""
        if rows_amt and bs[0][0].get("code") == rows_amt[0].get("code"):
            tail = " — 대금 1위가 매도 집중 대상(주도주 차익실현)"
        Ls.append("외인·기관 동반 매도: " + " · ".join(
            f"{x['name']}({v:+,.0f}억)" for x, v in bs[:4]) + tail)
    # ⑨ 오늘 태그 조인 — 종가베팅 실행과 직결
    try:
        tag_map = {}
        for l in open(os.path.join(DATA, "tag_picks.jsonl"), encoding="utf-8"):
            r = json.loads(l)
            if r.get("date") == kr.get("date") and r.get("tag"):
                tag_map[r["code"]] = r["tag"]
        if tag_map:
            seen_codes = {x.get("code") for x in rows_amt + rows_up}
            hits = {}
            for c in seen_codes & set(tag_map):
                nm = next((x["name"] for x in rows_amt + rows_up
                           if x.get("code") == c), c)
                hits.setdefault(tag_map[c], []).append(nm)
            lab = {"갭": "갭(익일 매도)", "손바뀜": "손바뀜(D+5 보유)",
                   "회피": "회피(자제)"}
            if hits:
                Ls.append("오늘 수급 태그: " + " / ".join(
                    f"{lab.get(t, t)} {len(ns)} — {'·'.join(ns[:4])}"
                    for t, ns in hits.items()))
    except Exception:
        pass
    # ⑩ 상한가 그룹성 감지 — 같은 2글자 접두사 ≥2 = 계열/테마 연쇄
    if rows_lim:
        pref: dict = {}
        for x in rows_lim:
            pref.setdefault(x["name"][:2], []).append(x["name"])
        grp = [v for v in pref.values() if len(v) >= 2]
        if grp:
            g = max(grp, key=len)
            Ls.append(f"상한가 {len(rows_lim)}종목 중 '{g[0][:2]}' 계열 {len(g)}종 연쇄 — "
                      "그룹성 수급, 종가베팅 비대상(잡주 정책)")
        else:
            agl = sec_agg(rows_lim)
            if agl and agl[0][1][0] >= 3:
                Ls.append(f"상한가 {len(rows_lim)}종목 중 {agl[0][0]} {agl[0][1][0]}종 — "
                          "단일 섹터 테마 연쇄")
    # ⑪ 신규 진입 — 섹터 꼬리
    if prev and (prev.get("sections") or []):
        pcodes = {x.get("code") for x in (prev["sections"][0].get("rows") or [])}
        fresh = [x for x in rows_amt if x.get("code") not in pcodes]
        if fresh:
            Ls.append("대금 TOP 신규 진입: " + " · ".join(
                x["name"] + (f"({(x.get('sec') or '').strip()})" if x.get("sec") else "")
                for x in fresh[:6]))
    out = []
    if Lm:
        out += ["💡 시장 해석"] + Lm
    if Ls:
        out += ([""] if out else []) + ["💡 종목 해석"] + Ls
    return out[:38] if out else None          # A8~G45 한도


def _track_block(tr: dict, r0: int):
    """09:10 전일 TOP 추적 표 — 현재 등락률순, 종목 칸 상승=연빨강/하락=연파랑
    (10-20 사용자). 이유·수급은 전일판에서 그대로 끌어와 아침 복기용으로 병기."""
    rws, bgs = [], []
    for x in tr["rows"]:
        chg = x.get("chg")
        rws.append([x["name"], x["mkt"], x.get("sec") or "",
                    (chg / 100 if chg is not None else None),
                    x.get("amt_prev"), x.get("issue") or "",
                    x.get("issue_url") or "",
                    (x.get("chg_prev") / 100 if x.get("chg_prev") is not None else None),
                    (x.get("flow") or {}).get("p"),
                    (x.get("flow") or {}).get("f"),
                    (x.get("flow") or {}).get("i")])
        bgs.append(RANK_UP_BG if (chg or 0) > 0
                   else RANK_DN_BG if (chg or 0) < 0 else None)
    blk = {"title": (f"🔁 전일({_date_lab(tr['base_date'])} {tr.get('base_slot', '')}판) "
                     f"대금·상승률 TOP{TOP_N} 추적 — 현재 등락률순"),
           "col": 9, "row": r0,
           "headers": ["종목", "시장", "섹터", "등락률", "전일 대금(억)", "전일 이유",
                       "링크", "전일 등락률", "전일 개인(억)", "전일 외국인(억)",
                       "전일 기관(억)"],
           "rows": rws,
           "fmts": {"4": PCT, "5": "#,##0", "8": PCT,
                    "9": NET, "10": NET, "11": NET},
           "align": {"1": "center", "2": "center", "3": "center", "7": "left"}}
    if any(bgs):
        blk["bg1"] = bgs
    return blk, r0 + 2 + max(len(rws), 1) + 1


def _us_zone(us, prev=None):
    """미국 존 — 1행부터. (blocks, 높이). prev=전일 레코드(순위 색칠용)."""
    mk = [[m[0], m[1], (m[2] / 100 if m[2] is not None else None), m[3]]
          for m in us.get("market") or []]
    blocks = [{"title": f"🇺🇸 {_date_lab(us['date'])} 시장 ({us.get('asof', '')})",
               "col": 1, "row": 1,
               "headers": ["", "지수", "등락률", "거래대금($B)"], "rows": mk,
               "fmts": {"2": "#,##0.00", "3": PCT, "4": "0.0"},
               "boldCol": 1, "align": {"1": "center"}}]
    left_end = 1 + 2 + max(len(mk), 1)
    sb, right_end = _stock_blocks(us["sections"], True, 1,
                                  prev_secs=(prev or {}).get("sections"))
    end = max(left_end, right_end - 1)
    blocks += sb
    blocks.append({"col": 1, "row": end + 1, "rows": [[""] * 19], "bg": "#000000"})
    return blocks, end + 2


# 아침 갭 등급표 (검증 8-12·검증리스트 16, 10-20 확정) — 시장 표와 해석 사이 고정.
# '잠김'은 예측력 최강이나 매수 불가(상따 영역) → 관찰 지표로 분류 (10-20 사용자).
GAP_GUIDE = [
    "📌 아침 갭 등급표 (기저율 11.9%)",
    "1. 상한가 잠김(종가=고가) = 59% · 5.0x · 갭 +3.7%",
    "2. 태그1(개+ 외− 기+) = 31% · 2.6x · +1.0% | 익일 동시호가 매도",
    "게이트: 코스피 60일선 아래 = 갭 플레이 자제",
    "(범례: % = 익일 아침 상승률 TOP15 진입 확률 · x = 평범한 종목(11.9%) 대비 배수 · 갭 = 익일 시가 평균 상승폭)",
]


def _kr_zone(kr, prev=None):
    """국내 존 — 내용 먼저, 맨 아래 검은 띠(아래 존과 분리). 1행 기준 상대 블록.
    모든 존이 맨 위(1행) 삽입이라 시간순으로 위에 쌓인다 (10-16 사용자:
    아래부터 US→14:30→19→20)."""
    mk = [[m[0], m[1], (m[2] / 100 if m[2] is not None else None),
           m[3], m[4], m[5], m[6]] for m in kr.get("market") or []]
    if mk:   # 시장 전체 합계 (10-19 사용자): 대금·개인/외인/기관 — 코스피+코스닥+선물
        mk = mk + [["합계", "", "",
                    round(sum(r[3] or 0 for r in mk), 1),
                    sum(r[4] or 0 for r in mk),
                    sum(r[5] or 0 for r in mk),
                    sum(r[6] or 0 for r in mk)]]
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
    if kr.get("slot") != "week":
        # 등급표(고정) + 확정 전략(동적 종목 기입) — 시장 표와 해석 사이
        pk = kr.get("picks") or {}

        def lab(x):
            return f"{x['name']}({x['chg']:+.1f}%)" if isinstance(x, dict) else str(x)
        if not pk.get("ready"):
            s1 = s3 = "없음 (태그 집계 15:40 이후 표시)"
        else:
            if not pk.get("above60"):
                s1 = "없음 — 60일선 아래 게이트"
            elif pk.get("t1"):
                s1 = " · ".join(lab(x) for x in pk["t1"])
            elif pk.get("t1_near"):
                s1 = ("조건 완화 후보(눌림 아님): "
                      + " · ".join(lab(x) for x in pk["t1_near"][:4]))
            else:
                s1 = "없음"
            s3 = (" · ".join(lab(x) for x in pk["t3"])
                  if pk.get("t3") else "없음")
        strategy = [
            "✅ 확정 전략",
            "1. 코스피 60일선 위 + 태그1 + 양봉 눌림(0.5~0.8) = 종가 매수 → 익일 동시호가 매도",
            f"종목: {s1}",
            "2. 코스피 무관 + 태그3(손바뀜) + 음봉 회피 = 종가 매수 → D+5 종가 매도",
            f"종목: {s3}",
        ]
        ev = kr.get("yest_eval")
        if ev:
            strategy.append("")
            strategy.append("📊 전일 전략 성적 (10-21 사용자: 09:10 자동 채점)")
            strategy += ev
        lines = GAP_GUIDE + [""] + strategy
        blocks.append({"col": 1, "row": 8, "rows": [[t] for t in lines]})
        a_row = 8 + len(lines) + 1
        anal = _analysis_rows(kr, prev)       # 해석 블록 — 등급표 아래
        if anal:
            blocks.append({"col": 1, "row": a_row, "rows": [[t] for t in anal]})
            left_end = max(left_end, a_row + len(anal))
        else:
            left_end = max(left_end, a_row - 1)
    r0 = 1
    if (kr.get("track") or {}).get("rows"):   # 09:10 전일 TOP 추적 — 종목 표 위
        tb, r0 = _track_block(kr["track"], 1)
        blocks.append(tb)
    sb, right_end = _stock_blocks(
        kr["sections"], False, r0, with_flow=True,  # 10-17 복원(일일도 수급 표시)
        prev_secs=None if kr.get("slot") == "week"
        else (prev or {}).get("sections"))
    end = max(left_end, right_end - 1)
    blocks += sb
    blocks.append({"col": 1, "row": end + 1, "rows": [[""] * 19], "bg": "#000000"})
    return blocks, end + 2


def _us_week_zone(usw):
    """미국 주간 서머리 존 — 토요일 아침, 미국 일일 존 위에 삽입 (10-17)."""
    mk = [[m[0], m[1], (m[2] / 100 if m[2] is not None else None), m[3]]
          for m in usw.get("market") or []]
    blocks = [{"title": f"📅 🇺🇸 미국 주간 서머리 ({usw.get('week_label', '')})",
               "col": 1, "row": 1,
               "headers": ["", "지수", "등락률(주간)", "거래대금($B)"], "rows": mk,
               "fmts": {"2": "#,##0.00", "3": PCT, "4": "0.0"},
               "boldCol": 1, "align": {"1": "center"}}]
    left_end = 1 + 2 + max(len(mk), 1)
    sb, right_end = _stock_blocks(usw["sections"], True, 1)
    end = max(left_end, right_end - 1)
    blocks += sb
    blocks.append({"col": 1, "row": end + 1, "rows": [[""] * 19], "bg": "#000000"})
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
    us_week = {}
    try:
        for l in open(os.path.join(DATA, "us_sheet.jsonl"), encoding="utf-8"):
            h = json.loads(l)
            if h.get("week"):
                us_week[h["date"]] = h
            else:
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
        dates_sorted = sorted(set(us_hist) | set(kr_hist))

        def _prev_kr(cur_date, cur_slot):
            """같은 날 직전 슬롯 → 없으면 전 거래일 마지막 슬롯."""
            slots = kr_hist.get(cur_date, {})
            earlier = [s for s in slots if s < cur_slot and s != "week"]
            if earlier:
                return slots[max(earlier)]
            for d2 in reversed([x for x in dates_sorted if x < cur_date]):
                s2 = {k: v for k, v in kr_hist.get(d2, {}).items() if k != "week"}
                if s2:
                    return s2[max(s2)]
            return None

        us = us_hist.get(date)
        if us and not st["us"]:
            prev_us = next((us_hist[d2] for d2 in
                            reversed([x for x in dates_sorted
                                      if x < date and x in us_hist])), None)
            blocks, h = _us_zone(us, prev_us)
            got = _post({"token": token, "date": d_lab, "mode": "insert_or_create",
                         "at": 1, "n": h + 1, "widths": WIDTHS, "blocks": blocks})
            if got:
                st["us"] = True
                n += 1
                print(f"[sheet] {d_lab} 미국 존 기록")
            else:
                state[date] = st
                break
        usw = us_week.get(date)
        if usw and "usweek" not in st["slots"]:
            blocks, h = _us_week_zone(usw)
            got = _post({"token": token, "date": d_lab, "mode": "insert_or_create",
                         "at": 1, "n": h + 1, "widths": WIDTHS, "blocks": blocks})
            if got:
                st["slots"].append("usweek")
                n += 1
                print(f"[sheet] {d_lab} 미국 주간 존 기록")
            else:
                state[date] = st
                break
        # 모든 존을 맨 위(1행) 삽입 — 이른 슬롯부터 처리하면 늦은 슬롯이
        # 그 위에 쌓여, 위에서부터 20:00→19:00→14:30→미국(맨 아래) 순이 된다.
        for slot in sorted(kr_hist.get(date, {})):
            if slot in st["slots"]:
                continue
            kr = kr_hist[date][slot]
            zb, h = _kr_zone(kr, None if slot == "week"
                             else _prev_kr(date, slot))   # 풀 레코드(분석 블록이 market·date 사용)
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
