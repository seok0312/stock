# -*- coding: utf-8 -*-
"""발표예정 → 발표완료 전환 즉시 알림 (10-08 사용자: "바뀌면 바로 알림").

5분 크론. 일정 목록은 calendar.json(하루 2회 전체 수집본)을 읽어 소스 타격 없이
감시하고, 시각이 지난 SSS 일정만 처리한다:
  지표(type=지표)  → fxstreet 라이브 1콜로 실제값 확인 후 "실제 vs 예상" 알림.
                     실제값이 아직 없으면 보류(마킹 안 함) — 다음 주기 재시도,
                     45분 넘게 안 나오면 수치 없이 알림.
  그 외(실적·IPO·만기·휴장 등) → 시각 도래 즉시 알림.
중복 방지: data/event_watch.json 에 알린 키 기록. 첫 가동·재가동 시 이미 30분
이상 지난 일정은 조용히 마킹만(과거 일정 폭주 방지).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "data", "event_watch.json")
CAL = "/var/www/html/calendar.json"
FRESH_MIN = 30          # 이보다 오래 지난 일정은 알리지 않고 마킹만
WAIT_ACTUAL_MIN = 45    # 지표 실제값 대기 한도
US_EARN_WAIT_MIN = 120  # 미국 실적 실제 EPS 1차 대기 — 이후엔 '집계 중'으로 발송하고
                        # pending 후속(36h, 5분마다 재확인 → 확정 재알림) (10-29 사용자)
US_EARN_PENDING_H = 36
UA_STR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fx_actual(now, hhmm, core_name):
    """시각·이름이 맞는 fxstreet SSS 지표의 (actual, consensus, previous) — 라이브 1콜."""
    import events as ev
    try:
        evs = ev.collect(now - timedelta(hours=3), now + timedelta(minutes=5),
                         only=("fxstreet",))
    except TypeError:
        evs = ev.collect(now - timedelta(hours=3), now + timedelta(minutes=5))
    for e in evs:
        if e.get("src") != "fxstreet" or not ev.is_sss(e):
            continue
        nm = (e.get("name_kr") or e.get("name") or "")
        if e["when"].strftime("%H:%M") == hhmm and (nm in core_name or core_name in nm):
            return e.get("actual"), e.get("consensus"), e.get("previous")
    return None, None, None


KR_EARN_CODE = {"삼성전자": "005930", "SK하이닉스": "000660"}
Q_MM = {"1Q": "03", "2Q": "06", "3Q": "09", "4Q": "12"}


def _naver_cons(code: str, qkey: str):
    """네이버 분기 재무의 컨센서스 (영업이익, 매출액) 조원 — 없으면 (None, None)."""
    import requests
    ua = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 Mobile/15E148",
          "Referer": "https://m.stock.naver.com/"}
    try:
        j = requests.get(f"https://m.stock.naver.com/api/stock/{code}/finance/quarter",
                         headers=ua, timeout=10).json()
        rows = (j.get("financeInfo") or {}).get("rowList") or []
        out = {}
        for row in rows:
            if row.get("title") in ("영업이익", "매출액"):
                v = ((row.get("columns") or {}).get(qkey) or {}).get("value")
                if v:
                    out[row["title"]] = float(str(v).replace(",", "")) / 10000  # 억→조
        return out.get("영업이익"), out.get("매출액")
    except Exception:
        return None, None


def _kr_earn_actual(comp: str, event_name: str, now):
    """실적 발표 수치 — 뉴스 풀 제목에서 영업익(조) 추출 + 분기 컨센 대비.

    뉴스룸(news.samsung.com)은 서버·로컬 모두 타임아웃(봇 가드)이라 네이버 뉴스
    풀이 소스 — 속보·공시 기사 제목에 수치가 먼저 뜬다(10-08 확인). 미발견 None."""
    import news
    hits = news._pool_match(keywords=(comp,), tickers=(KR_EARN_CODE.get(comp, ""),),
                            limit=30, start=now - timedelta(minutes=90), end=now)
    cands = []
    for h in hits:
        t = h["title"]
        m = re.search(r"영업(?:이익|익)\s*([0-9]+(?:\.[0-9]+)?)\s*조"
                      r"\s*(?:([0-9,]{1,6})\s*(천억|억))?", t)
        if not m:
            continue
        sub = 0.0
        if m.group(2):                   # '4천억'=0.4조, '4000억'=0.4조 둘 다 지원
            n = float(m.group(2).replace(",", ""))
            sub = (n * 1000 if m.group(3) == "천억" else n) / 10000
        v = float(m.group(1)) + sub
        my = re.search(r"전년\S{0,2}\s*\+?([0-9,.]+)\s*%", t)
        cands.append({"op": v, "detail": bool(m.group(2) or "." in m.group(1)),
                      "yoy": my.group(1) if my else None,
                      "title": t, "url": h.get("url")})
    if not cands:
        return None
    # 억 단위까지 적은 기사(107조4000억) 우선 — 라운드 제목(107조)보다 정확
    best = next((c for c in cands if c["detail"]), cands[0])
    op, yoy, title, url = best["op"], best["yoy"], best["title"], best["url"]
    qm = re.search(r"([1-4]Q)", event_name)
    cons = (None, None)
    if qm:
        cons = _naver_cons(KR_EARN_CODE.get(comp, ""), f"{now.year}{Q_MM[qm.group(1)]}")
    body = [f"영업익 <b>{op:,.1f}조</b>" + (f" (전년比 +{yoy}%)" if yoy else "")]
    if cons[0]:
        d = (op / cons[0] - 1) * 100
        body.append(f"컨센 {cons[0]:,.1f}조 — <b>{abs(d):.1f}% "
                    + ("상회" if d >= 0 else "하회") + "</b>")
    return {"line": " · ".join(body), "title": title, "url": url}


US_KR_NAME: dict = {}


def _kr_name(sym: str) -> str:
    """티커 → 한글명 (movers.US_UNIV 수동맵, 지연 로드). 뉴스 풀 검색용."""
    if not US_KR_NAME:
        try:
            import movers
            US_KR_NAME.update({s: k for s, k, _ in movers.US_UNIV})
        except Exception:
            pass
    return US_KR_NAME.get(sym, "")


def _us_earn_actual(sym: str, now, when=None):
    """미국 실적 실제 EPS — 다중 소스 캐스케이드 (10-29 사용자: 예상치만 오는 문제 재발).
    ① 야후 earningsHistory(신규 분기 행, 분기말 75일 내) ② 야후 earnings 모듈 quarterly
    (reportedDate 3일 내) ③ 나스닥 earnings-surprise(dateReported 3일 내) ④ 한글 뉴스 제목
    'EPS n달러'(발표 이후 기사, 제목 첨부). 반환 {a, e, sp, src, title?, url?} 또는 None."""
    import requests
    out = None
    try:
        s = requests.Session()
        s.headers["User-Agent"] = UA_STR
        try:
            s.get("https://fc.yahoo.com", timeout=8)
        except requests.RequestException:
            pass
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                      timeout=8).text.strip()
        if crumb and len(crumb) <= 24:
            j = s.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
                      params={"modules": "earningsHistory,earnings", "crumb": crumb},
                      timeout=10).json()
            res = ((j.get("quoteSummary") or {}).get("result") or [{}])[0]
            rows = [r for r in ((res.get("earningsHistory") or {}).get("history") or [])
                    if (r.get("quarter") or {}).get("raw")]
            if rows:
                nw = max(rows, key=lambda r: r["quarter"]["raw"])
                a = (nw.get("epsActual") or {}).get("raw")
                if a is not None and now.timestamp() - nw["quarter"]["raw"] <= 75 * 86400:
                    out = {"a": a, "e": (nw.get("epsEstimate") or {}).get("raw"),
                           "sp": (nw.get("surprisePercent") or {}).get("raw"), "src": "yahoo"}
            if out is None:
                q = ((res.get("earnings") or {}).get("earningsChart") or {}).get("quarterly") or []
                for r in reversed(q):
                    a = (r.get("actual") or {}).get("raw")
                    rd = (r.get("reportedDate") or {}).get("raw")
                    if a is not None and rd and now.timestamp() - rd <= 3 * 86400:
                        e = (r.get("estimate") or {}).get("raw")
                        out = {"a": a, "e": e, "sp": ((a / e - 1) if e else None), "src": "yahoo-q"}
                        break
    except Exception:
        pass
    if out is None:
        try:
            r = requests.get(f"https://api.nasdaq.com/api/company/{sym}/earnings-surprise",
                             headers={"User-Agent": UA_STR,
                                      "Accept": "application/json, text/plain, */*",
                                      "Origin": "https://www.nasdaq.com",
                                      "Referer": "https://www.nasdaq.com/"}, timeout=12)
            rows = ((((r.json() if r.ok else {}).get("data") or {})
                     .get("earningsSurpriseTable") or {}).get("rows") or [])
            for row in rows[:2]:
                try:
                    rd = datetime.strptime(str(row.get("dateReported", "")),
                                           "%m/%d/%Y").replace(tzinfo=KST)
                except ValueError:
                    continue
                a = _num(row.get("eps"))
                if (now - rd).days <= 3 and a is not None:
                    e = _num(str(row.get("consensusForecast", "")).replace(",", ""))
                    sp = _num(row.get("percentageSurprise"))
                    out = {"a": a, "e": e, "sp": (sp / 100 if sp is not None else None),
                           "src": "nasdaq"}
                    break
        except Exception:
            pass
    if out is None and when is not None:
        try:
            import news
            kr = _kr_name(sym)
            hits = news._pool_match(keywords=tuple(k for k in (kr, sym) if k), tickers=(sym,),
                                    limit=15, start=when - timedelta(minutes=30), end=now)
            for h in hits:
                t = h["title"]
                m = re.search(r"(?:EPS|주당\s*순이익|주당순익)\D{0,12}([0-9]+(?:\.[0-9]+)?)\s*달러", t)
                if not m:
                    continue
                a = float(m.group(1))
                me = re.search(r"(?:예상|전망|컨센서스|시장\s*예상)\D{0,10}([0-9]+(?:\.[0-9]+)?)\s*달러", t)
                e = float(me.group(1)) if me else None
                out = {"a": a, "e": e, "sp": ((a / e - 1) if e else None), "src": "news",
                       "title": t[:70], "url": h.get("url")}
                break
        except Exception:
            pass
    return out


def _us_earn_line(got: dict, esc, val: str = "") -> str:
    """실제 EPS 라인 — 예상치 없으면 캘린더 val('EPS 예상 $4.72')에서 보충."""
    a, e, sp = got.get("a"), got.get("e"), got.get("sp")
    if e is None and val:
        m = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)", val)
        if m:
            e = float(m.group(1))
            sp = (a / e - 1) if e else None
    body = [f"EPS 실제 <b>${a:g}</b>"]
    if e is not None:
        body.append(f"예상 ${e:.2f}")
    if sp is not None:
        body.append("<b>" + f"{abs(sp) * 100:.0f}% " + ("상회" if sp >= 0 else "하회") + "</b>")
    line = " · ".join(body)
    if got.get("src") == "news":
        line += " (뉴스 제목 기준)"
    if got.get("title"):
        line += f"\n<i>{esc(got['title'])}</i>"
    return line

def _us_earn_announced(sym: str, when, kr_name: str = "") -> bool:
    """발표가 '실제로 있었는지' 판별 (10-22: META 실발표를 earningsHistory 지연
    때문에 미발표로 오판 → 보류 가드가 침묵시킬 뻔한 결함 보완).

    ① 야후 calendarEvents.earningsDate 가 이벤트 당일(미국 기준 = KST−1일)과
    일치하면 발표로 간주 — META 사례에서 수치는 없어도 날짜는 즉시 정확했음.
    ② 폴백: 한글 뉴스 풀에 직후 '실적/어닝' 기사가 있으면 발표."""
    import requests
    try:
        s = requests.Session()
        s.headers["User-Agent"] = UA_STR
        try:
            s.get("https://fc.yahoo.com", timeout=8)
        except requests.RequestException:
            pass
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                      timeout=8).text.strip()
        j = s.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
                  params={"modules": "calendarEvents", "crumb": crumb},
                  timeout=10).json()
        ds = {d.get("fmt") for d in
              (((((j.get("quoteSummary") or {}).get("result") or [{}])[0]
                 .get("calendarEvents") or {}).get("earnings") or {})
               .get("earningsDate") or [])}
        want = {when.strftime("%Y-%m-%d"),
                (when - timedelta(days=1)).strftime("%Y-%m-%d")}
        if ds & want:
            return True
    except Exception:
        pass
    if kr_name:
        try:
            import news
            hits = news._pool_match(keywords=(kr_name,), tickers=(sym,), limit=10,
                                    start=when - timedelta(hours=2),
                                    end=when + timedelta(hours=10))
            if any(("실적" in h["title"] or "어닝" in h["title"]) for h in hits):
                return True
        except Exception:
            pass
    return False


def run(now=None, dry_run=False) -> int:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    cal = _load(CAL, {})
    day = next((d for d in cal.get("days") or [] if d.get("date") == today), None)
    state = _load(STATE, {})
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")),
                    "/opt/upbit_bot/.env")   # 텔레그램 토큰은 서버에선 여기 (10-20 장애: 누락 시 콘솔 출력만 되고 True)
    esc = notify.esc
    n_sent, fx_cache = 0, {}
    # 실제치 미확정으로 나간 미국 실적 알림의 후속 (10-29 사용자): 36시간까지 매 주기
    # 소스 재확인 → 확정되면 '📢 실적 확정' 재알림 후 pending 해제
    for key, v in list(state.items()):
        pend = v.get("pending") if isinstance(v, dict) else None
        if not pend:
            continue
        try:
            when_p = datetime.fromisoformat(pend["when"])
        except Exception:
            when_p = now
        if (now - when_p).total_seconds() > US_EARN_PENDING_H * 3600:
            v.pop("pending", None)
            continue
        got = _us_earn_actual(pend["sym"], now, when_p)
        if not got:
            continue
        line = _us_earn_line(got, esc, pend.get("val", ""))
        ok = notify.send(f"📢 <b>실적 확정</b> {esc(pend['name'])}\n{line}",
                         dry_run=dry_run, allow_muted=True)
        v["line"] = line
        v.pop("pending", None)
        n_sent += 1
        print(f"[watch] 실적 확정 재알림: {pend['name']} 발송={ok} ({got.get('src')})")
    for it in (day or {}).get("items") or []:
        if not it.get("sss"):
            continue
        key = f"{today}|{it['t']}|{it['name']}"
        if key in state:
            continue
        pending = None
        try:
            hh, mm = it["t"].split(":")
            when = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            continue
        if when > now:
            continue
        age_min = (now - when).total_seconds() / 60
        us_earn = it.get("type") == "실적" and "🇺🇸" in it["name"]
        if age_min > FRESH_MIN and not (
                (it.get("type") == "지표" and age_min <= WAIT_ACTUAL_MIN)
                or (us_earn and age_min <= US_EARN_WAIT_MIN)):
            state[key] = f"{now.isoformat(timespec='seconds')} (지남-마킹)"
            continue
        core = re.sub(r"^[^\w가-힣]+", "", it["name"]).strip()
        lines = None
        if it.get("type") == "지표":
            if it["t"] not in fx_cache:
                fx_cache[it["t"]] = {}
            a, c, p = _fx_actual(now, it["t"], core)
            if a is None and age_min < WAIT_ACTUAL_MIN:
                continue                     # 실제값 대기 — 마킹 안 하고 다음 주기
            body = []
            if a is not None:
                body.append(f"실제 <b>{esc(str(a))}</b>")
                an, cn = _num(a), _num(c)
                if cn is not None:
                    body.append(f"예상 {esc(str(c))}")
                    if an is not None and an != cn:
                        body.append("<b>예상 " + ("상회" if an > cn else "하회") + "</b>")
                if p is not None:
                    body.append(f"이전 {esc(str(p))}")
            else:
                body.append("실제값 미게시 — 수치는 다음 브리핑에서")
                if it.get("val"):
                    body.append(esc(it["val"]))
            lines = " · ".join(body)
        elif us_earn:
            m = re.match(r"([A-Z]{1,5})\b", core)
            sym = m.group(1) if m else ""
            got = _us_earn_actual(sym, now, when) if sym else None
            if got is None and sym and age_min < US_EARN_WAIT_MIN:
                continue                 # 실제치 대기 — 마킹 안 함
            if got is None and "잠정일" in (it.get("val") or ""):
                # 잠정일 + 실제치 無: 발표 자체가 없었는지 판별 (10-22 META 교훈)
                if not (sym and _us_earn_announced(sym, when, _kr_name(sym))):
                    state[key] = (f"{now.isoformat(timespec='seconds')} "
                                  "(잠정일-발표 확인 안 됨, 알림 보류)")
                    print(f"[watch] 보류(잠정일 미확인): {it['name']}")
                    continue
            if got:
                lines = _us_earn_line(got, esc, it.get("val") or "")
            else:                        # 1차 한도 초과 — 집계 중 명시 + 후속 재알림 예약
                lines = (esc(it.get("val") or "")
                         + " · <b>실제치 집계 중</b> — 확인되면 재알림").strip(" ·")
                pending = {"sym": sym, "name": it["name"], "when": when.isoformat(),
                           "val": it.get("val") or ""}
        elif it.get("type") == "실적" and "🇰🇷" in it["name"]:
            comp = next((c for c in KR_EARN_CODE if c in core), None)
            got = _kr_earn_actual(comp, core, now) if comp else None
            if got is None and comp and age_min < WAIT_ACTUAL_MIN:
                continue                 # 수치 기사 대기 — 마킹 안 하고 다음 주기
            if got:
                lines = got["line"]
                if got.get("title"):
                    lines += f"\n<i>{esc(got['title'][:70])}</i>"
            else:
                lines = esc(it.get("val") or "") or None
        else:
            lines = esc(it.get("val") or "") or None
        msg = (f"📢 <b>발표완료</b> ({it['t']})\n{esc(it['name'])}"
               + (f"\n{lines}" if lines else ""))
        ok = notify.send(msg, dry_run=dry_run, allow_muted=True)
        # 실제치 라인을 상태에 보존 → 다이제스트 완료분에 재사용 (10-22); 미확정이면 pending
        state[key] = {"at": now.isoformat(timespec="seconds"), "line": lines or ""}
        if pending:
            state[key]["pending"] = pending
        n_sent += 1
        print(f"[watch] 알림: {it['name']} ({it['t']}) 발송={ok}")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    keep = {k: v for k, v in sorted(state.items())[-300:]}
    json.dump(keep, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    return n_sent


def digest(now=None, dry_run=False) -> bool:
    """일정 다이제스트 — 매일 06:00(예정 1주)·20:00(오늘 완료 + 예정 1주).
    10-19 사용자: "06시/20시 두 번 + 중간 발표는 실시간(발표완료)만"."""
    now = now or datetime.now(KST)
    cal = _load(CAL, {})
    items = []
    for d in (cal.get("days") or []):
        for it in d.get("items") or []:
            if not it.get("sss"):
                continue
            try:
                hh, mm = it["t"].split(":")
                when = datetime.strptime(d["date"], "%Y%m%d").replace(
                    hour=int(hh), minute=int(mm), tzinfo=KST)
            except Exception:
                continue
            items.append((when, it, d["date"]))
    items.sort(key=lambda x: x[0])
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")),
                    "/opt/upbit_bot/.env")   # 텔레그램 토큰은 서버에선 여기 (10-20 장애: 누락 시 콘솔 출력만 되고 True)
    esc = notify.esc
    up = [(w, it) for w, it, _ in items
          if now < w <= now + timedelta(days=7)]      # 향후 1주 (10-19 사용자)
    L = []
    state = _load(STATE, {})
    # 완료분: 아침판도 포함 (10-22 사용자: 밤사이 미국 실적이 빠졌음 → 수정).
    # 아침판 = 전날 20:00 다이제스트 이후(−10h), 저녁판 = 오늘 0시부터.
    if now.hour >= 12:
        d_from, d_hdr = now.replace(hour=0, minute=0, second=0,
                                    microsecond=0), "오늘 발표완료"
    else:
        d_from, d_hdr = now - timedelta(hours=10), "밤사이 발표완료"
    done = [(w, it, dk) for w, it, dk in items if d_from <= w <= now]
    if done:
        L.append(f"<b>{d_hdr}</b>")
        for w, it, dk in done:
            sv = state.get(f"{dk}|{it['t']}|{it['name']}")
            extra = ((sv.get("line") or "").split("\n")[0]
                     if isinstance(sv, dict) else "")
            if (it.get("type") == "실적" and "🇺🇸" in it["name"]
                    and "실제" not in extra):
                # 야후 수치 지연분 재조회 — 알림 땐 없던 실제치가 아침엔 반영됨 (10-22)
                m2 = re.match(r"([A-Z]{1,5})\b",
                              re.sub(r"^[^\w가-힣]+", "", it["name"]).strip())
                got = _us_earn_actual(m2.group(1), now, w) if m2 else None
                if got:
                    extra = _us_earn_line(got, esc, it.get("val") or "").split("\n")[0]
                else:                     # 아직 어느 소스에도 없음 — 예상치를 결과처럼 쓰지 않는다
                    extra = (esc(it.get("val") or "") + " · 실제치 집계 중(확인 시 재알림)").strip(" ·")
            if not extra and it.get("val"):      # 실제치 미보존 — 예정 문구 폴백
                extra = esc(it["val"])
            L.append(f"  · {w:%H:%M} {esc(it['name'])}"
                     + (f" — {extra}" if extra else ""))
    if up:
        if L:
            L.append("")
        L.append("<b>발표예정 (1주)</b>")
        WD = "월화수목금토일"
        for w, it in up:
            day = ("오늘" if w.date() == now.date() else
                   f"{w.month}/{w.day:02d}({WD[w.weekday()]})")
            L.append(f"  · {day} {w:%H:%M} {esc(it['name'])}"
                     + (f" — {esc(it['val'])}" if it.get("val") else ""))
    if not L:
        L = ["향후 1주 SSS 일정 없음"]
    title = "🌅 <b>일정 브리핑</b>" if now.hour < 12 else "🌙 <b>일정 브리핑</b>"
    msg = f"{title} ({now:%m/%d %H:%M})\n" + "\n".join(L)
    return notify.send(msg, dry_run=dry_run, allow_muted=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, HERE)
    dry = "--dry-run" in sys.argv
    if "--digest" in sys.argv:
        print("[digest] 발송:", digest(dry_run=dry))
    else:
        n = run(dry_run=dry)
        if n == 0:
            print("[watch] 새 전환 없음")
