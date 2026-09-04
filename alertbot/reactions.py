# -*- coding: utf-8 -*-
"""경제지표 발표 → 자산 반응 로그. '이 지표가 보통 어떻게 움직였나'를 실측으로 답한다.

왜 필요한가:
  뉴스 서사("고용 호조에 상승")는 검증이 안 된다. 발표시각·예상치·실제치가
  남아 있고 자산이 24시간 거래되면, 발표 직전 대비 +1h/+4h 변동을 그대로 잴 수 있다.
  이걸 쌓으면 "ISM 서비스업이 예상을 상회했을 때 나스닥이 1시간 뒤 평균 몇 %" 를
  추정으로가 아니라 표본으로 말할 수 있다.

측정:
  시황 5종(바이낸스 USDT 무기한)을 쓴다. 24시간 거래라 미국 새벽 발표도
  끊김 없이 측정된다 — 현물 ETF/지수로는 불가능한 부분.
  자산당 5분봉 한 번만 받아 base/+1h/+4h 세 점을 뽑는다(호출 절약).

형식: JSON Lines, 한 줄 = 한 이벤트. 경로: ALERTBOT_REACTIONS > <모듈위치>/data/reactions.jsonl
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("ALERTBOT_REACTIONS") or os.path.join(HERE, "data", "reactions.jsonl")

HORIZONS = (1, 4)          # 시간. 1h=즉각 반응, 4h=하루 세션 안에서의 정착
MIN_N = 4                  # 이 표본 수 미만이면 통계를 보여주지 않는다
# 기록 대상 중요도. 브리핑이 HIGH 만 보여주므로 기록도 HIGH 만 한다
# (자산당 5분봉 1회씩 호출이라 MEDIUM 까지 넣으면 백필이 10배가 된다).
LOG_VOLS = ("HIGH",)


def key_of(e) -> str:
    nm = e.get("name_kr") or e.get("name") or ""
    return f"{e['when']:%Y%m%d%H%M}|{e.get('country','')}|{nm}"


def load_all() -> list:
    if not os.path.exists(PATH):
        return []
    out = []
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _append(rows: list) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _series(ex, symbol, at: datetime, span_h: int):
    """발표 30분 전 ~ span_h 시간 후 5분봉. [(ms, close)]"""
    since = int((at - timedelta(minutes=30)).timestamp() * 1000)
    need = (span_h * 60 + 40) // 5 + 2
    try:
        oh = ex.fetch_ohlcv(symbol, "5m", since=since, limit=min(need, 500))
    except Exception:
        return []
    return [(c[0], c[4]) for c in oh]


def _px_at(series, ts: datetime):
    ms = int(ts.timestamp() * 1000)
    prior = [c for _, c in [(t, c) for t, c in series if t <= ms]]
    return prior[-1] if prior else None


def measure(e, ex=None) -> dict | None:
    """한 이벤트의 자산별 반응(%) 계산. {'코스피': {'1h': .., '4h': ..}, ...}"""
    import quotes
    ex = ex or quotes.exchange()
    span = max(HORIZONS)
    out = {}
    for name, sym, _tv, _dp in quotes.INSTRUMENTS:
        s = _series(ex, sym, e["when"], span)
        if not s:
            continue
        base = _px_at(s, e["when"])
        if not base:
            continue
        d = {}
        for h in HORIZONS:
            p = _px_at(s, e["when"] + timedelta(hours=h))
            if p:
                d[f"{h}h"] = round((p / base - 1) * 100, 3)
        if d:
            out[name] = d
        time.sleep(0.05)
    return out or None


def log(events, now=None, limit: int = 6, vols=LOG_VOLS) -> int:
    """아직 기록 안 된, 이미 결과가 확정된 이벤트를 기록. 반환: 새로 기록한 수.

    limit 은 한 번 실행에서 처리할 상한 — 슬롯마다 도는 작업이라
    한 번에 다 하려 들면 알림이 늦어진다. 남은 건 다음 슬롯에서 처리된다.
    """
    now = now or datetime.now(KST)
    seen = {r["key"] for r in load_all()}
    ready = max(HORIZONS)
    todo = [e for e in events
            if e.get("actual") is not None
            and e.get("src") not in ("kr_ipo", "kr_expiry", "custom")
            and (not vols or e.get("vol") in vols)
            and e["when"] + timedelta(hours=ready) <= now
            and key_of(e) not in seen]
    todo.sort(key=lambda e: e["when"], reverse=True)

    rows = []
    for e in todo[:limit]:
        react = measure(e)
        if not react:
            continue
        rows.append({
            "key": key_of(e), "when": e["when"].isoformat(timespec="minutes"),
            "country": e.get("country"), "name": e.get("name_kr") or e.get("name"),
            "raw_name": e.get("name"), "vol": e.get("vol"),
            "tags": sorted(e.get("tags") or []),
            "actual": e.get("actual"), "consensus": e.get("consensus"),
            "dev": e.get("dev"), "better": e.get("better"), "react": react,
        })
    _append(rows)
    return len(rows)


def find(e, rows=None) -> dict | None:
    """이 이벤트의 실측 기록. 없으면 None(아직 +4h 가 안 지났거나 대상 밖)."""
    rows = load_all() if rows is None else rows
    k = key_of(e)
    return next((r for r in rows if r.get("key") == k), None)


def react_text(e, assets=("나스닥", "코스피"), horizon="1h", rows=None) -> str | None:
    """'1h 나스닥 +0.31% · 코스피 +0.12%' — 발표 직후 실제로 어떻게 움직였나."""
    r = find(e, rows)
    if not r:
        return None
    parts = [f"{a} {r['react'][a][horizon]:+.2f}%" for a in assets
             if (r.get("react") or {}).get(a, {}).get(horizon) is not None]
    return f"{horizon} " + " · ".join(parts) if parts else None


def _mean(v):
    return sum(v) / len(v) if v else None


def stats(name: str, asset: str, horizon: str = "1h", sign: int | None = None,
          rows=None, country: str | None = None) -> dict | None:
    """같은 지표의 과거 반응 통계.

    country 를 반드시 함께 건다 — '소비자물가'는 미국·일본·유로존·한국이 다 있고
    같은 이름이어도 시장 반응이 전혀 다르다. 이름만으로 묶으면 통계가 무의미해진다.
    sign: +1 예상 상회 / -1 하회 / None 전체.
    반환 {n, mean, up_ratio} — up_ratio 는 평균과 같은 방향으로 움직인 비율.
    """
    rows = load_all() if rows is None else rows
    vals = []
    for r in rows:
        if r.get("name") != name:
            continue
        if country and r.get("country") != country:
            continue
        if sign is not None:
            d = r.get("dev")
            if d is None or (d > 0) != (sign > 0):
                continue
        v = (r.get("react") or {}).get(asset, {}).get(horizon)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    m = _mean(vals)
    return {"n": len(vals), "mean": m,
            "up_ratio": sum(1 for v in vals if (v > 0) == (m > 0)) / len(vals)}


def summary_for(e, assets=("나스닥", "코스피"), horizon: str = "1h",
                rows=None, min_n: int = MIN_N) -> str | None:
    """예정 이벤트 한 줄 요약: '과거 12회 · 나스닥 +0.31% / 코스피 +0.12% (1h)'.

    표본이 min_n 미만이면 None — 3~4개로 평균을 말하면 오히려 오해를 부른다.
    """
    rows = load_all() if rows is None else rows
    name = e.get("name_kr") or e.get("name")
    parts, n = [], 0
    for a in assets:
        st = stats(name, a, horizon, rows=rows, country=e.get("country"))
        if not st or st["n"] < min_n:
            continue
        n = max(n, st["n"])
        parts.append(f"{a} {st['mean']:+.2f}%")
    if not parts:
        return None
    return f"과거 {n}회 · " + " / ".join(parts) + f" ({horizon})"


if __name__ == "__main__":
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="지표 반응 로그")
    ap.add_argument("--backfill", type=int, metavar="DAYS",
                    help="과거 N일 이벤트를 소급 기록")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--table", action="store_true", help="쌓인 통계 출력")
    a = ap.parse_args()

    if a.backfill:
        import events as ev_mod
        now = datetime.now(KST)
        got = 0
        # FXStreet 은 긴 구간을 한 번에 안 주므로 2주씩 끊어서 받는다
        cur = now - timedelta(days=a.backfill)
        while cur < now:
            nxt = min(cur + timedelta(days=14), now)
            evs = ev_mod.collect(cur, nxt, only={"fxstreet"})
            n = log(evs, now=now, limit=a.limit - got)
            got += n
            print(f"  {cur:%Y-%m-%d} ~ {nxt:%m-%d}  일정 {len(evs):>3} → 신규 {n}")
            if got >= a.limit:
                break
            cur = nxt
        print(f"\n총 {got}건 기록 · 누적 {len(load_all())}건")

    if a.table or not a.backfill:
        rows = load_all()
        print(f"경로 {PATH}\n누적 {len(rows)}건\n")
        from collections import Counter
        for (cc, name), c in Counter((r["country"], r["name"]) for r in rows).most_common(20):
            line = f"  {cc:<4}{name:<24} n={c:<3}"
            for asset in ("나스닥", "코스피"):
                st = stats(name, asset, "1h", rows=rows, country=cc)
                if st:
                    line += f"  {asset} {st['mean']:+.2f}% ({st['up_ratio']*100:.0f}%)"
            print(line)
