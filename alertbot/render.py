# -*- coding: utf-8 -*-
"""텔레그램 메시지 조립. 각 섹션은 데이터가 없으면 통째로 생략된다."""
from __future__ import annotations

from notify import esc

WD = ["월", "화", "수", "목", "금", "토", "일"]
ICON = {"0600": "☀️", "0750": "🌅", "0850": "🔔", "0930": "🟢",
        "1430": "🔔", "1600": "🏁", "1900": "🌙", "2000": "🌛"}


def _fmt_px(v, dp):
    return f"{v:,.{dp}f}" if v is not None else "—"


def header(win):
    e = win["end"]
    return (f"{ICON[win['slot']]} <b>종가베팅 브리핑</b> · {e:%m-%d}({WD[e.weekday()]}) {e:%H:%M}\n"
            f"<i>{esc(win['label'])} | {win['start']:%m-%d %H:%M} → {win['end']:%m-%d %H:%M}</i>")


def _span(win):
    """구간 길이 표기. 1시간 미만은 분, 그 외는 0.5시간 단위."""
    mins = round((win["end"] - win["start"]).total_seconds() / 60)
    if mins < 60:
        return f"{mins}분"
    h = mins / 60
    return f"{h:.0f}시간" if abs(h - round(h)) < 0.05 else f"{h:.1f}시간"


def section_quotes(win):
    lines = [f"\n📊 <b>시황</b> <i>({_span(win)} 변동)</i>"]
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
    """전일 미국 섹터 — 시황과 같은 🔺🔻 마커로 통일."""
    if not sectors:
        return ""
    up = [s for s in sectors if s["change_pct"] > 0][:4]
    dn = [s for s in sectors if s["change_pct"] < 0][-3:]
    if not up and not dn:
        return ""
    lines = ["\n🇺🇸 <b>전일 미국 섹터</b>"]
    for s in up:
        lines.append(f"  🔺 <b>{esc(s['sector'])}</b> {s['change_pct']:+.2f}%")
    for s in reversed(dn):
        lines.append(f"  🔻 <b>{esc(s['sector'])}</b> {s['change_pct']:+.2f}%")
    return "\n".join(lines)


def section_kr_sectors(upjong, themes, when="장중"):
    """장중 한국 업종 강약 + 주도 테마.

    14:30 / 19시 슬롯용. 미국장이 닫혀 있어 '전일 미국 섹터' 대신
    지금 움직이는 한국 데이터로 판단해야 하기 때문이다.
    """
    lines = []
    if upjong and (upjong.get("up") or upjong.get("down")):
        lines.append(f"\n🇰🇷 <b>한국 업종</b> <i>({esc(when)})</i>")
        for x in upjong.get("up", []):
            lines.append(f"  🔺 <b>{esc(x['name'])}</b> {x['change_pct']:+.2f}%")
        for x in upjong.get("down", []):
            lines.append(f"  🔻 <b>{esc(x['name'])}</b> {x['change_pct']:+.2f}%")
    if themes:
        lines.append("\n🎯 <b>주도 테마</b>")
        for t in themes:
            d3 = f" <i>(3일 {t['d3_pct']:+.2f}%)</i>" if t.get("d3_pct") is not None else ""
            lines.append(f"  · <b>{esc(t['name'])}</b> {t['change_pct']:+.2f}%{d3}")
            if t.get("leaders"):
                lines.append(f"      {esc(', '.join(t['leaders']))}")
    return "\n".join(lines)


def section_kr_impact(impacts):
    """impacts: [{kr_sector, driver, tickers, note}] — '원인 → 결과' 순."""
    if not impacts:
        return ""
    lines = ["\n🇰🇷 <b>한국시장 영향 예상</b>"]
    for im in impacts[:5]:
        lines.append(f"  · {esc(im['driver'])} <b>→ {esc(im['kr_sector'])}</b>")
        if im.get("tickers"):
            names = ", ".join(f"{esc(t['name'])}({t['code']})" for t in im["tickers"][:5])
            lines.append(f"      {names}")
        if im.get("note"):
            lines.append(f"      <i>{esc(im['note'])[:160]}</i>")
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


def _flow_fmt(v):
    """순매수 표기는 조 단위 소수 1자리로 통일.
    1,000억 = 0.1조. 그 미만은 반올림돼 0.1 또는 0.0 으로 표시된다."""
    return None if v is None else f"{v/1e4:+,.1f}조"


def _heat(p):
    return "🔥 과열" if p > 30 else ("🌿 활발" if p > 5 else
           ("💤 한산" if p < -20 else "▫️ 보통"))


def section_flows(fl, cmp=None):
    """거래대금(조 단위 정수) + 순매수(코스피+선물 / 코스닥).

    cmp: store.compare() 결과. 같은 슬롯(같은 시각)의 과거 평균과 비교한다.
         장중 값은 '그 시각까지의 누적'이라 완결된 하루 평균과 비교하면
         늘 작게 나온다. 같은 시각끼리 비교해야 의미가 있다.
         표본이 쌓이기 전에는 None 이라 기존 종가평균 비교로 폴백한다.
    '기타법인*'은 -(개인+외국인+기관) 으로 유도한 값. 순매수 총합은 항등적으로
    0이므로 이 값 = 기타법인 + 기타외국인이고, 키움 ka10061 실측 대조 결과
    기타법인 단독과의 오차는 0.4%였다.
    """
    if not fl or not fl.get("rows"):
        return ""
    rows = {m.get("label"): m for m in fl["rows"] if not m.get("error")}

    lines = ["\n💰 <b>시장 거래대금</b>"]
    for m in fl["rows"]:
        if m.get("error"):
            lines.append(f"  · {esc(m['label'])} — 조회 실패")
            continue
        lines.append(f"  · <b>{esc(m['label'])}</b> "
                     f"{(m.get('amount_won') or 0)/1e12:,.0f}조")
    lines.append(f"  ── <b>합계 {(fl.get('total_amount_jo') or 0):,.0f}조</b>")

    ca = (cmp or {}).get("amount")
    if ca and ca.get("pct") is not None:
        lines.append(f"  <i>같은 시각 {ca['n']}일평균 {ca['avg']/1e12:,.0f}조 대비 "
                     f"{ca['pct']:+.0f}% {_heat(ca['pct'])}</i>")
    else:
        ref = fl.get("ref")
        if ref and ref.get("vs_avg_pct") is not None:
            p = ref["vs_avg_pct"]
            scope = "선물포함" if ref.get("with_futures") else "현물만"
            lines.append(f"  <i>{ref['days']}일 종가평균 {ref['avg_jo']:,.0f}조({scope}) 대비 "
                         f"{p:+.0f}% {_heat(p)}</i>")

    KEYS = ("개인", "외국인", "기관", "기타*")
    LABEL = {"개인": "개인", "외국인": "외국인", "기관": "기관", "기타*": "기타법인*"}

    def merged(names):
        acc, seen = {k: 0.0 for k in KEYS}, False
        for n in names:
            f = (rows.get(n) or {}).get("flow_eok") or {}
            for k in KEYS:
                if f.get(k) is not None:
                    acc[k] += f[k]; seen = True
        return acc if seen else None

    groups = [("코스피+선물", merged(["코스피", "선물"])),
              ("코스닥", merged(["코스닥"]))]
    if any(g[1] for g in groups):
        lines.append("\n💵 <b>순매수</b>")
        for title, acc in groups:
            if not acc:
                continue
            parts = [f"{esc(LABEL[k])} {_flow_fmt(acc[k])}" for k in KEYS]
            lines.append(f"  · <b>{esc(title)}</b>")
            lines.append("      " + " · ".join(parts))

        note = []
        for key, name in (("foreign", "외국인"), ("inst", "기관"), ("indiv", "개인")):
            c = (cmp or {}).get(key)
            if not c:
                continue
            z = c.get("z") or 0
            if abs(z) < 1.0:
                continue
            verb = "대량 순매수" if c["today"] > c["avg"] else "대량 순매도"
            note.append(f"{esc(name)} <i>(같은 시각 평균 {_flow_fmt(c['avg'])}, "
                        f"{z:+.1f}σ {verb})</i>")
        if note:
            lines.append("  ⚡ " + " / ".join(note))
        lines.append("  <i>* 기타법인은 나머지 합으로 유도 (키움 실측 대조 오차 0.4%)</i>")
    return "\n".join(lines)


def build(win, news=None, us_sectors=None, kr_impact=None, leaders=None,
          flows=None, flows_cmp=None, kr_upjong=None, kr_themes=None, kr_when=None, footer=None):
    parts = [header(win), section_quotes(win)]
    for s in (section_flows(flows, flows_cmp),
              section_news(news or {}),
              section_kr_sectors(kr_upjong, kr_themes, kr_when or "장중"),
              section_us_sectors(us_sectors or []),
              section_kr_impact(kr_impact or []),
              section_leaders(leaders or [])):
        if s:
            parts.append(s)
    if footer:
        parts.append(f"\n<i>{esc(footer)}</i>")
    return "\n".join(parts)
