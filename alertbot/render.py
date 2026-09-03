# -*- coding: utf-8 -*-
"""텔레그램 메시지 조립. 각 섹션은 데이터가 없으면 통째로 생략된다."""
from __future__ import annotations

import unicodedata

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
        sign = "🔺" if r["chg_pct"] > 0 else ("🔽" if r["chg_pct"] < 0 else "▪️")
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


def section_us_sectors(sectors, leaders=None, topic_news=None):
    """전일 미국 섹터 — 시황과 같은 🔺🔽 마커.

    leaders:    {섹터명: [{ticker, change_pct}]}  finviz 종목 등락률 기반 주도주
    topic_news: {섹터명: [{title, url, source}]}  왜 올랐는지 원인 기사
    강세 섹터에만 주도주·뉴스를 붙인다(약세는 참고용이라 이름만).
    """
    if not sectors:
        return ""
    up = [s for s in sectors if s["change_pct"] > 0][:3]
    dn = [s for s in sectors if s["change_pct"] < 0][-3:]
    if not up and not dn:
        return ""
    leaders = leaders or {}
    topic_news = topic_news or {}
    lines = ["\n🇺🇸 <b>전일 미국 섹터</b>"]
    for s in up:
        nm = s["sector"]
        lines.append(f"  🔺 <b>{esc(nm)}</b> {s['change_pct']:+.2f}%")
        ld = leaders.get(nm) or []
        if ld:
            lines.append("      " + " · ".join(
                f"{esc(x['ticker'])} {x['change_pct']:+.1f}%" for x in ld))
        for it in (topic_news.get(nm) or [])[:1]:
            t = esc(it.get("title", ""))[:90]
            lines.append(f"      <a href=\"{it.get('url','')}\">{t}</a>")
    for s in reversed(dn):
        lines.append(f"  🔽 <b>{esc(s['sector'])}</b> {s['change_pct']:+.2f}%")
    return "\n".join(lines)


def section_kr_sectors(upjong, themes, when="장중", topic_news=None):
    """장중 한국 업종 강약 + 주도 테마(주도주·원인뉴스 포함).

    업종·테마 모두 상승 3 / 하락 3 만 보여준다. 그 이상은 노이즈에 가깝다.
    themes 는 {'up': [...], 'down': [...]} 구조.
    """
    lines = []
    topic_news = topic_news or {}

    def block(title, up, down, with_leaders=False):
        if not up and not down:
            return
        lines.append(f"\n{title}")
        for x in up:
            d3 = (f" <i>(3일 {x['d3_pct']:+.2f}%)</i>"
                  if x.get("d3_pct") is not None else "")
            lines.append(f"  🔺 <b>{esc(x['name'])}</b> {x['change_pct']:+.2f}%{d3}")
            if with_leaders and x.get("leaders"):
                lines.append(f"      {esc(', '.join(x['leaders']))}")
            for it in (topic_news.get(x["name"]) or [])[:1]:
                lines.append(f"      <a href=\"{it.get('url','')}\">"
                             f"{esc(it.get('title',''))[:90]}</a>")
        for x in down:
            d3 = (f" <i>(3일 {x['d3_pct']:+.2f}%)</i>"
                  if x.get("d3_pct") is not None else "")
            lines.append(f"  🔽 <b>{esc(x['name'])}</b> {x['change_pct']:+.2f}%{d3}")
            if with_leaders and x.get("leaders"):
                lines.append(f"      {esc(', '.join(x['leaders']))}")

    if upjong:
        block(f"🇰🇷 <b>한국 업종</b> <i>({esc(when)})</i>",
              upjong.get("up") or [], upjong.get("down") or [])
    if themes:
        block("🎯 <b>주도 테마</b>", themes.get("up") or [],
              themes.get("down") or [], with_leaders=True)
    return "\n".join(lines)


def section_kr_impact(impacts):
    """impacts: [{kr_sector, driver, tickers, note}] — '원인 → 결과' 순."""
    if not impacts:
        return ""
    lines = ["\n🇰🇷 <b>한국시장 영향 예상</b>"]
    for im in impacts[:5]:
        lines.append(f"  · <b>{esc(im['driver'])} → {esc(im['kr_sector'])}</b>")
        if im.get("tickers"):
            names = ", ".join(f"{esc(t['name'])}({t['code']})" for t in im["tickers"][:5])
            lines.append(f"      {names}")
        if im.get("note"):
            lines.append(f"      <i>{esc(im['note'])[:160]}</i>")
    return "\n".join(lines)


def section_program(fl):
    """프로그램 매매(차익/비차익).

    투자자별(개인·외국인·기관·기타법인)과 '독립된 5번째 주체'가 아니다.
    투자자 분류는 거래 주체, 프로그램은 거래 방식(15종목 이상 바스켓 주문) 기준이라
    서로 직교한다 — 외국인이 낸 비차익 매수는 외국인 순매수에도, 비차익에도 잡힌다.
    비차익은 주로 외국인·기관의 대량 바스켓이라 '큰손이 쓸어담나'의 별도 신호로 본다.
    """
    if not fl or not fl.get("rows"):
        return ""
    parts = []
    for m in fl["rows"]:
        p = m.get("program_eok") or {}
        if p.get("비차익") is None:
            continue
        seg = f"{esc(m['label'])} 비차익 {_flow_fmt(p['비차익'])}"
        if p.get("차익") is not None:
            seg += f" · 차익 {_flow_fmt(p['차익'])}"
        parts.append(seg)
    if not parts:
        return ""
    return ("\n🤖 <b>프로그램 매매</b>\n  · " + "\n  · ".join(parts) +
            "\n  <i>투자자별 분류와 별개 축 — 같은 거래가 양쪽에 중복 집계됨</i>")


def section_leaders(ld, title="🎯 <b>당일 주도주 후보</b>"):
    """leaders.fetch_leaders() 결과."""
    if not ld or not ld.get("rows"):
        return ""
    lines = [f"\n{title} <i>({esc(ld.get('source') or '')})</i>"]
    for r in ld["rows"]:
        amt = r.get("거래대금")
        seg = f"  · <b>{esc(r['종목명'])}</b>({r.get('종목코드','')}) {r.get('등락률',0):+.2f}%"
        if amt is not None:
            seg += f" · {amt:,.0f}억"
        lines.append(seg)
        sub = []
        for k, lab in (("외국인", "외국인"), ("기관", "기관")):
            v = r.get(k)
            if v is not None:
                sub.append(f"{lab} {v:+,.0f}억")
            elif r.get(k + "주") is not None:       # 금액 조회 실패 시 수량으로 대체
                sub.append(f"{lab} {r[k + '주']:+,.0f}주")
        if r.get("프로그램") is not None:
            sub.append(f"프로그램 {r['프로그램']:+,.0f}억")
        if sub:
            lines.append("      " + " · ".join(sub))
    return "\n".join(lines)


def _dw(s: str) -> int:
    """표시 폭. 한글·CJK는 2칸으로 센다(<pre> 고정폭 정렬용)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, width: int, right: bool = False) -> str:
    gap = max(0, width - _dw(s))
    return (" " * gap + s) if right else (s + " " * gap)


def _flow_table(groups, cmp_map):
    """순매수를 표로. Telegram <pre> 고정폭 사용.

    셀: '+0.2조 ▲0.3' = 오늘 +0.2조, 같은 시각 평균보다 0.3조 많음.
    비교값은 store 표본이 있을 때만 붙는다. 코스피+선물 열에만 적용
    (store 비교가 그 조합 기준으로 계산되므로).
    """
    KEYS = [("개인", "개인", "indiv"), ("외국인", "외국인", "foreign"),
            ("기관", "기관", "inst"), ("기타*", "기타법인", None),
            ("비차익", "비차익", "nonarb")]
    titles = [g[0] for g in groups]
    label_w = max(_dw(lab) for _, lab, _ in KEYS) + 1

    def cell(val, key, first):
        if val is None:
            return "-"
        txt = f"{val/1e4:+.1f}조"
        c = cmp_map.get(key) if (key and first) else None
        if c and c.get("avg") is not None:
            d = (val - c["avg"]) / 1e4
            txt += f" {'▲' if d >= 0 else '▼'}{abs(d):.1f}"
        return txt

    body = [(lab, [cell((acc or {}).get(k), ck, i == 0)
                   for i, (_, acc) in enumerate(groups)])
            for k, lab, ck in KEYS]
    col_w = [max([_dw(t)] + [_dw(r[1][i]) for r in body]) + 2
             for i, t in enumerate(titles)]
    out = [_pad("", label_w) + "".join(_pad(t, w, True) for t, w in zip(titles, col_w))]
    for lab, cells in body:
        out.append(_pad(lab, label_w) + "".join(_pad(c, w, True)
                                                for c, w in zip(cells, col_w)))
    return "<pre>" + esc("\n".join(out)) + "</pre>"


def _flow_fmt(v):
    """순매수 표기는 조 단위 소수 1자리로 통일.
    1,000억 = 0.1조. 그 미만은 반올림돼 0.1 또는 0.0 으로 표시된다."""
    return None if v is None else f"{v/1e4:+,.1f}조"


def _heat(p):
    return "🔥 과열" if p > 30 else ("🌿 활발" if p > 5 else
           ("💤 한산" if p < -20 else "▫️ 보통"))


# 표시 순서 — 선물이 규모가 가장 크고 방향을 먼저 보여주므로 앞에 둔다
MARKET_ORDER = ("선물", "코스피", "코스닥")


def section_flows(fl, cmp=None):
    """거래대금(시장별 20일 대비 증감 포함) + 순매수(비차익 포함).

    cmp: store.compare() 결과. 같은 슬롯(같은 시각) 과거와 비교한다.
         장중 값은 '그 시각까지의 누적'이라 완결된 하루 평균과 비교하면
         늘 작게 나온다. 표본이 쌓이기 전에는 종가평균 비교로 폴백한다.
    '기타법인*'은 -(개인+외국인+기관) 으로 유도. 순매수 총합은 항등적으로 0이라
    이 값 = 기타법인 + 기타외국인이고, 키움 실측 대조 오차는 0.4%였다.
    '비차익'은 주체가 아니라 거래 방식(바스켓) 축이라 위 4개와 중복 집계된다.
    """
    if not fl or not fl.get("rows"):
        return ""
    rows = {m.get("label"): m for m in fl["rows"] if not m.get("error")}
    cmp = cmp or {}
    per_slot = cmp.get("amount_market") or {}
    per_day = fl.get("ref_market") or {}

    lines = ["\n💰 <b>시장 거래대금</b>"]
    for lab in MARKET_ORDER:
        m = rows.get(lab)
        if not m:
            continue
        amt = (m.get("amount_won") or 0) / 1e12
        # 같은 시각 비교가 있으면 우선, 없으면 종가평균 비교
        d = per_slot.get(lab) or per_day.get(lab)
        tag = ""
        if d and d.get("pct") is not None:
            arrow = "🔺" if d["pct"] > 0 else ("🔽" if d["pct"] < 0 else "▪️")
            tag = f" <i>({arrow}{abs(d['pct']):.0f}%)</i>"
        lines.append(f"  · <b>{esc(lab)}</b> {amt:,.0f}조{tag}")
    for m in fl["rows"]:
        if m.get("error"):
            lines.append(f"  · {esc(m['label'])} — 조회 실패")
    lines.append(f"  ── <b>합계 {(fl.get('total_amount_jo') or 0):,.0f}조</b>")

    ca = cmp.get("amount")
    if ca and ca.get("pct") is not None:
        lines.append(f"  <i>같은 시각 {ca['n']}일평균 대비 {ca['pct']:+.0f}% "
                     f"{_heat(ca['pct'])}</i>")
    else:
        ref = fl.get("ref")
        if ref and ref.get("vs_avg_pct") is not None:
            p = ref["vs_avg_pct"]
            lines.append(f"  <i>{ref['days']}일 종가평균 대비 {p:+.0f}% {_heat(p)}</i>")

    KEYS = ("개인", "외국인", "기관", "기타*")
    LABEL = {"개인": "개인", "외국인": "외국인", "기관": "기관", "기타*": "기타법인*"}

    def merged(names):
        acc, seen = {k: 0.0 for k in KEYS}, False
        nonarb, has_p = 0.0, False
        for n in names:
            src = rows.get(n) or {}
            f = src.get("flow_eok") or {}
            for k in KEYS:
                if f.get(k) is not None:
                    acc[k] += f[k]; seen = True
            p = (src.get("program_eok") or {}).get("비차익")
            if p is not None:
                nonarb += p; has_p = True
        if not seen:
            return None
        acc["비차익"] = nonarb if has_p else None
        return acc

    groups = [("코스피+선물", merged(["코스피", "선물"])),
              ("코스닥", merged(["코스닥"]))]
    if any(g[1] for g in groups):
        lines.append("\n💵 <b>순매수</b> <i>(조원, ▲▼는 같은 시각 평균 대비)</i>")
        lines.append(_flow_table(groups, cmp))

        note = []
        for key, name in (("foreign", "외국인"), ("inst", "기관"),
                          ("indiv", "개인"), ("nonarb", "비차익")):
            c = cmp.get(key)
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
        lines.append("  <i>* 기타법인은 나머지 합으로 유도 (오차 0.4%) · "
                     "비차익은 거래방식 축이라 위 4개와 중복 집계</i>")
    return "\n".join(lines)


def build(win, news=None, us_sectors=None, kr_impact=None, leaders=None,
          flows=None, flows_cmp=None, kr_upjong=None, kr_themes=None, kr_when=None,
          us_leaders=None, topic_news=None, footer=None):
    parts = [header(win), section_quotes(win)]
    for s in (section_flows(flows, flows_cmp),
              section_news(news or {}),
              section_kr_sectors(kr_upjong, kr_themes, kr_when or "장중", topic_news),
              section_program(flows),
              section_leaders(leaders),
              section_us_sectors(us_sectors or [], us_leaders, topic_news),
              section_kr_impact(kr_impact or []),
              ):
        if s:
            parts.append(s)
    if footer:
        parts.append(f"\n<i>{esc(footer)}</i>")
    return "\n".join(parts)
