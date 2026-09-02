# -*- coding: utf-8 -*-
"""텔레그램 메시지 조립. 각 섹션은 데이터가 없으면 통째로 생략된다."""
from __future__ import annotations

from notify import esc

WD = ["월", "화", "수", "목", "금", "토", "일"]
ICON = {"07": "🌅", "14": "🔔", "19": "🌙"}


def _fmt_px(v, dp):
    return f"{v:,.{dp}f}" if v is not None else "—"


def header(win):
    e = win["end"]
    return (f"{ICON[win['slot']]} <b>종가베팅 브리핑</b> · {e:%m-%d}({WD[e.weekday()]}) {e:%H:%M}\n"
            f"<i>{esc(win['label'])} | {win['start']:%m-%d %H:%M} → {win['end']:%m-%d %H:%M}</i>")


def section_quotes(win):
    hrs = round((win["end"] - win["start"]).total_seconds() / 3600)
    lines = [f"\n📊 <b>시황</b> <i>({hrs}시간 변동)</i>"]
    for r in win["rows"]:
        if r["chg_pct"] is None:
            lines.append(f"  {esc(r['name'])} — 데이터 없음"); continue
        star = " ★" if r["significant"] else ""
        sign = "🔺" if r["chg_pct"] > 0 else ("🔻" if r["chg_pct"] < 0 else "▪️")
        lines.append(f"  {sign} <b>{esc(r['name'])}</b> {_fmt_px(r['end_px'], r['decimals'])}"
                     f"  <b>{r['chg_pct']:+.2f}%</b>{star}")
    return "\n".join(lines)


def section_news(news_by_asset):
    """news_by_asset: {자산명: [{title,url,source,summary}]} — 유의미 변동 자산만"""
    if not news_by_asset:
        return ""
    lines = ["\n📰 <b>유의미 변동 뉴스</b> <i>(±0.5% 이상)</i>"]
    for asset, items in news_by_asset.items():
        if not items:
            continue
        lines.append(f"\n<b>· {esc(asset)}</b>")
        for it in items[:3]:
            t = esc(it.get("title", ""))[:110]
            u = it.get("url", "")
            src = esc(it.get("source", ""))
            lines.append(f"  <a href=\"{u}\">{t}</a>" + (f" <i>({src})</i>" if src else ""))
            if it.get("summary"):
                lines.append(f"    <i>{esc(it['summary'])[:200]}</i>")
    return "\n".join(lines) if len(lines) > 1 else ""


def section_us_sectors(sectors):
    """sectors: [{sector, change_pct}] 내림차순"""
    if not sectors:
        return ""
    up = [s for s in sectors if s["change_pct"] > 0][:4]
    dn = [s for s in sectors if s["change_pct"] < 0][-3:]
    lines = ["\n🇺🇸 <b>전일 미국 섹터</b>"]
    if up:
        lines.append("  강세 " + ", ".join(f"{esc(s['sector'])} <b>{s['change_pct']:+.2f}%</b>" for s in up))
    if dn:
        lines.append("  약세 " + ", ".join(f"{esc(s['sector'])} <b>{s['change_pct']:+.2f}%</b>" for s in dn))
    return "\n".join(lines)


def section_kr_impact(impacts):
    """impacts: [{kr_sector, driver, tickers:[{name,code}], note}]"""
    if not impacts:
        return ""
    lines = ["\n🇰🇷 <b>한국시장 영향 예상</b>"]
    for im in impacts[:5]:
        lines.append(f"  <b>{esc(im['kr_sector'])}</b> <i>← {esc(im['driver'])}</i>")
        if im.get("tickers"):
            names = ", ".join(f"{esc(t['name'])}({t['code']})" for t in im["tickers"][:5])
            lines.append(f"    {names}")
        if im.get("note"):
            lines.append(f"    <i>{esc(im['note'])[:160]}</i>")
    return "\n".join(lines)


def section_leaders(rows, title="🎯 <b>당일 주도주 후보</b>"):
    """closebet 스크리너 결과: [{종목명, 종목코드, 등락률, 거래대금, 점수}]"""
    if not rows:
        return ""
    lines = [f"\n{title}"]
    for r in rows[:8]:
        amt = r.get("거래대금", 0) / 1e8
        sc = f" · 점수 {r['점수']:.2f}" if r.get("점수") is not None else ""
        lines.append(f"  {esc(r['종목명'])}({r.get('종목코드','')}) "
                     f"<b>{r.get('등락률',0):+.2f}%</b> · {amt:,.0f}억{sc}")
    return "\n".join(lines)


def build(win, news=None, us_sectors=None, kr_impact=None, leaders=None, footer=None):
    parts = [header(win), section_quotes(win)]
    for s in (section_news(news or {}),
              section_us_sectors(us_sectors or []),
              section_kr_impact(kr_impact or []),
              section_leaders(leaders or [])):
        if s:
            parts.append(s)
    if footer:
        parts.append(f"\n<i>{esc(footer)}</i>")
    return "\n".join(parts)
