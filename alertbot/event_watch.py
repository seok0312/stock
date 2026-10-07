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


def run(now=None, dry_run=False) -> int:
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    cal = _load(CAL, {})
    day = next((d for d in cal.get("days") or [] if d.get("date") == today), None)
    if not day:
        return 0
    state = _load(STATE, {})
    import notify
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.abspath(os.path.join(HERE, "..", ".env")))
    esc = notify.esc
    n_sent, fx_cache = 0, {}
    for it in day.get("items") or []:
        if not it.get("sss"):
            continue
        key = f"{today}|{it['t']}|{it['name']}"
        if key in state:
            continue
        try:
            hh, mm = it["t"].split(":")
            when = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            continue
        if when > now:
            continue
        age_min = (now - when).total_seconds() / 60
        if age_min > FRESH_MIN and not (it.get("type") == "지표" and age_min <= WAIT_ACTUAL_MIN):
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
        ok = notify.send(msg, dry_run=dry_run)
        state[key] = now.isoformat(timespec="seconds")
        n_sent += 1
        print(f"[watch] 알림: {it['name']} ({it['t']}) 발송={ok}")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    keep = {k: v for k, v in sorted(state.items())[-300:]}
    json.dump(keep, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    return n_sent


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, HERE)
    dry = "--dry-run" in sys.argv
    n = run(dry_run=dry)
    if n == 0:
        print("[watch] 새 전환 없음")
