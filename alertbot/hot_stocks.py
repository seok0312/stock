# -*- coding: utf-8 -*-
"""주요 종목 정보 — 종가베팅 브리핑과 **별개**의 독립 알림 (09-23 사용자).

1430/1900 슬롯에서 별도 메시지로 발송한다. 브리핑과 포맷·코드를 결합하지 말 것
(사용자가 이 알림만 따로 발전시킬 예정).

  ① 당일 상한가 종목
  ② 거래대금 1,000억↑ 중 상승률 상위 10
  ③ 주도주 10 (시총구간 문턱 × 거래대금 — leaders A안, top=10)

각 종목에 회사 한 줄 소개(네이버 integration.description 축약)와
상승 이유(뉴스 풀: 네이버 증권뉴스+스탁허브, 최근 26시간)를 붙인다.
이미 위 섹션에 나온 종목은 이름·등락률만 재표기(메시지 길이 절약).

단독 실행: python3 hot_stocks.py --dry-run
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.abspath(os.path.join(HERE, "..")), HERE):
    if os.path.isdir(os.path.join(_p, "closebet")) and _p not in sys.path:
        sys.path.insert(0, _p)

UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1",
      "Referer": "https://m.stock.naver.com/"}
INTEGRATION = "https://m.stock.naver.com/api/stock/{code}/integration"

EOK = 1e8
UPPER_PCT = 29.5          # 상한가 근사(30% ± 호가 반올림)
BIG_AMT = 1000 * EOK      # 1,000억


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_desc_cache: dict = {}
_sector_map: dict | None = None


def _sector(code: str) -> str | None:
    """업종 — nxt.py 가 주간 갱신하는 sector_map.json (키움 업종구성 기반)."""
    global _sector_map
    if _sector_map is None:
        try:
            import json
            doc = json.load(open(os.path.join(HERE, "data", "sector_map.json"),
                                 encoding="utf-8"))
            _sector_map = doc.get("map") or {}
        except Exception:
            _sector_map = {}
    return _sector_map.get(code)


def _company(code: str) -> str | None:
    """회사 한 줄 — 업종(sector_map) + 네이버 integration.description 첫 문장.
    description 은 대부분 비어 있어(09-23 실측) 업종이 사실상의 본문이다."""
    if code in _desc_cache:
        return _desc_cache[code]
    bits = []
    sec = _sector(code)
    if sec:
        bits.append(sec)
    try:
        j = requests.get(INTEGRATION.format(code=code), headers=UA, timeout=10).json()
        d = (j.get("description") or "").strip()
        if d:
            first = d.split(". ")[0].split(".\n")[0].strip()
            bits.append((first[:60] + "…") if len(first) > 60 else first)
        time.sleep(0.05)
    except Exception:
        pass
    txt = " · ".join(bits) if bits else None
    _desc_cache[code] = txt
    return txt


def _reason(name: str, code: str, now) -> dict | None:
    """상승 이유 후보 — 뉴스 풀(주요뉴스 우선)에서 종목명/티커 매칭 최신 1건.
    스탁허브 자동 알림('종목 알림' 요약)은 이유가 아니므로 제외."""
    try:
        import news
        hits = news._pool_match(keywords=(name,), tickers=(code,), limit=3,
                                start=now - timedelta(hours=26), end=now)
        for h in hits:
            if "종목 알림" in h["title"] or h["url"].rstrip("/").endswith("/markets"):
                continue
            return h
    except Exception:
        pass
    return None


def _row_line(r, now, seen: set, brief: bool = False) -> str:
    code, name = r["code"], r["name"]
    head = f"  · <b>{esc(name)}</b> {r['chg']:+.1f}% / {r['amt']/EOK:,.0f}억"
    mc = r.get("mc_jo")
    if mc is not None:
        head += f" / 시총 {mc:,.1f}조" if mc >= 1 else f" / 시총 {mc*1e4:,.0f}억"
    if brief or code in seen:
        return head + " <i>(위 참조)</i>" if code in seen else head
    seen.add(code)
    lines = [head]
    comp = _company(code)
    if comp:
        lines.append(f"    {esc(comp)}")
    nw = _reason(name, code, now)
    if nw:
        lines.append(f"    ↳ <a href=\"{nw['url']}\">{esc(nw['title'])}</a>")
    return "\n".join(lines)


def build(now=None) -> str | None:
    now = now or datetime.now(KST)
    from closebet import market
    snap = market.get_snapshot()
    if snap is None or snap.empty:
        return None
    df = snap.reset_index()
    code_col = next((c for c in ("종목코드", "index", "Code") if c in df.columns), None)
    if code_col is None:
        return None

    def rows_of(sub):
        out = []
        for _, x in sub.iterrows():
            out.append({"code": str(x[code_col]), "name": x["종목명"],
                        "chg": float(x["등락률"]), "amt": float(x["거래대금"] or 0),
                        "mc_jo": (float(x["시가총액"]) / 1e12
                                  if x.get("시가총액") == x.get("시가총액") else None)})
        return out

    # 거래대금 10억 미만 상한가는 정리매매·초저유동성 노이즈 — 제외
    up = df[(df["등락률"] >= UPPER_PCT) & (df["거래대금"] >= 10 * EOK)] \
        .sort_values("거래대금", ascending=False).head(10)
    big = df[(df["거래대금"] >= BIG_AMT) & (df["등락률"] > 0)] \
        .sort_values("등락률", ascending=False).head(10)

    seen: set = set()
    parts = [f"📋 <b>주요 종목 정보</b>\n· {now:%m/%d %H:%M} · 당일 정규장 기준"]

    sec = [f"\n🔺 <b>상한가</b> ({len(up)}종목)"]
    if len(up):
        sec += [_row_line(r, now, seen) for r in rows_of(up)]
    else:
        sec += ["  · 없음"]
    parts.append("\n".join(sec))

    sec = [f"\n💰 <b>거래대금 1,000억↑ 상승률 상위 10</b>"]
    sec += [_row_line(r, now, seen) for r in rows_of(big)] or ["  · 없음"]
    parts.append("\n".join(sec))

    sec = ["\n🎯 <b>주도주 10</b> <i>(시총구간 문턱 × 거래대금)</i>"]
    try:
        import leaders
        ld = leaders.fetch_leaders(top=10)
        for x in (ld or {}).get("rows") or []:
            r = {"code": x.get("종목코드"), "name": x.get("종목명"),
                 "chg": float(x.get("등락률") or 0), "amt": float(x.get("거래대금") or 0) * EOK,
                 "mc_jo": x.get("시가총액조")}
            line = _row_line(r, now, seen)
            extra = []
            if x.get("주도일수") is not None:
                extra.append(f"주도 {x['주도일수']:.0f}/20일")
            if x.get("수급태그"):
                from leaders import TAG_ACT
                extra.append(f"<b>{esc(x['수급태그'])}"
                             f"({esc(TAG_ACT.get(x['수급태그'], ''))})</b>")
            if extra:
                first, *rest = line.split("\n")
                line = "\n".join([first + " · " + " · ".join(extra)] + rest)
            sec.append(line)
    except Exception as e:
        sec.append(f"  · 주도주 계산 실패: {type(e).__name__}")
    parts.append("\n".join(sec))
    # 태그 범례 — 맨 아래 (09-23 사용자: 태그명만으론 헷갈림)
    if "수급태그" in "".join(parts) or any(
            k in "".join(parts) for k in ("갭(", "손바뀜(", "회피(")):
        try:
            from leaders import TAG_LEGEND
            parts.append(f"\n<i>{esc(TAG_LEGEND)}</i>")
        except ImportError:
            pass
    return "\n".join(parts)


def send(now=None, dry_run: bool = False) -> bool:
    msg = build(now)
    if not msg:
        print("  주요종목: 스냅샷 없음 — 생략")
        return False
    import notify
    return notify.send(msg, dry_run=dry_run)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    import notify as _n
    _n.load_env(os.path.join(HERE, ".env"),
                os.path.abspath(os.path.join(HERE, "..", ".env")),
                "/opt/upbit_bot/.env")
    send(dry_run=a.dry_run)
