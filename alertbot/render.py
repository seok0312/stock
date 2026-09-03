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


def _link(it, cut=90):
    t = esc(it.get("title", ""))[:cut]
    return f"<a href=\"{it.get('url','')}\">{t}</a>"


def section_quotes(win, news=None):
    """시황 5종을 먼저 순서대로 나열하고, 그 아래에 자산별 원인 뉴스를 묶는다.

    변동률 사이사이에 링크가 끼면 5종을 한눈에 비교하기 어렵다.
    목록을 먼저 붙여 훑게 하고, 근거 기사는 그 밑에 모은다.
    """
    news = news or {}
    lines = [f"\n📊 <b>시황</b> <i>({_span(win)} 변동)</i>"]
    for r in win["rows"]:
        if r["chg_pct"] is None:
            lines.append(f"  {esc(r['name'])} — 데이터 없음")
            continue
        star = " ★" if r["significant"] else ""
        sign = "🔺" if r["chg_pct"] > 0 else ("🔽" if r["chg_pct"] < 0 else "▪️")
        lines.append(f"  {sign} <b>{esc(r['name'])}</b> "
                     f"{_fmt_px(r['end_px'], r['decimals'])}"
                     f"  <b>{r['chg_pct']:+.2f}%</b>{star}")
    for r in win["rows"]:
        items = news.get(r["name"]) or []
        if not items:
            continue
        lines.append(f"  📰 <b>{esc(r['name'])}</b> <i>{r['chg_pct']:+.2f}%</i>")
        for it in items[:2]:
            lines.append(f"      {_link(it)}")
    return "\n".join(lines)


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
            lines.append(f"      {_link(it)}")
    for s in reversed(dn):
        lines.append(f"  🔽 <b>{esc(s['sector'])}</b> {s['change_pct']:+.2f}%")
    return "\n".join(lines)


def section_kr_sectors(upjong, themes, when="장중", topic_news=None):
    """장중 한국 업종 강약 + 주도 테마(주도주·원인뉴스 포함).

    업종·테마 모두 상승 3 / 하락 3 만 보여준다. 그 이상은 노이즈에 가깝다.
    upjong / themes 는 {'up': [...], 'down': [...]} 구조.
    """
    lines = []
    topic_news = topic_news or {}

    def block(title, up, down, with_leaders=False):
        if not up and not down:
            return
        lines.append(f"\n{title}")
        for x in list(up) + list(down):
            mark = "🔺" if x["change_pct"] > 0 else "🔽"
            d3 = (f" <i>(3일 {x['d3_pct']:+.2f}%)</i>"
                  if x.get("d3_pct") is not None else "")
            lines.append(f"  {mark} <b>{esc(x['name'])}</b> {x['change_pct']:+.2f}%{d3}")
            if with_leaders and x.get("leaders"):
                lines.append(f"      {esc(', '.join(x['leaders']))}")
            for it in (topic_news.get(x["name"]) or [])[:1]:
                lines.append(f"      {_link(it)}")

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


def section_leaders(ld, news=None, title="🎯 <b>당일 주도주 후보</b>"):
    """leaders.fetch_leaders() 결과 + 종목별 원인 뉴스 링크."""
    if not ld or not ld.get("rows"):
        return ""
    news = news or {}
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
        for it in (news.get(r["종목명"]) or [])[:1]:
            lines.append(f"      {_link(it)}")
    return "\n".join(lines)


def _dw(s: str) -> int:
    """표시 폭. 한글·CJK는 2칸으로 센다(<pre> 고정폭 정렬용)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, width: int, right: bool = False) -> str:
    gap = max(0, width - _dw(s))
    return (" " * gap + s) if right else (s + " " * gap)


def _flow_table(groups, cmp_map):
    """순매수를 표로. Telegram <pre> 고정폭 사용.

    셀: '+0.2조 ▲0.3' = 오늘 +0.2조, 같은 시각 5일평균보다 0.3조 많음.
    비교값은 store 표본이 있을 때만, 그리고 첫 열(코스피)에만 붙는다
    (store 비교가 코스피 기준으로 계산되므로).
    """
    KEYS = [("개인", "개인", "indiv"), ("외국인", "외국인", "foreign"),
            ("기관", "기관", "inst"), ("기타법인", "기타법인", "etc"),
            ("비차익", "비차익", "nonarb")]
    titles = [g[0] for g in groups]
    label_w = max(_dw(lab) for _, lab, _ in KEYS) + 1

    def cell(val, key, first):
        if val is None:
            return "-"
        txt = f"{val/1e4:+.1f}조"
        c = cmp_map.get(key) if (key and first) else None
        if c and c.get("avg_short") is not None:
            d = (val - c["avg_short"]) / 1e4
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


# 거래대금 표시 순서 — 선물이 규모가 가장 크고 방향을 먼저 보여주므로 앞에 둔다
MARKET_ORDER = ("선물", "코스피", "코스닥")

# 순매수 표에 쓰는 시장. 키움 ka10051 이 커버하는 현물 두 시장이다.
# 선물은 키움 국내주식 REST 에 투자자별 TR 이 없고, 네이버 FUT 값은 단위(계약/억원)가
# 문서화돼 있지 않아 검증할 수 없어 뺐다. 거래대금 쪽 선물은 실측 검증돼 그대로 둔다.
FLOW_MARKETS = ("코스피", "코스닥")


def section_flows(fl, cmp=None):
    """거래대금 + 순매수 표.

    비교 기준 두 개를 함께 쓴다:
      5일  — 증감률(%). 종가베팅은 1일 지평이라 최근 국면이 기준이 된다.
      20일 — z-score. 표준편차를 5개로 추정하면 오차가 커 이상치 판정은 표본이 필요.
    store 의 같은 시각 표본이 있으면 그쪽을, 없으면 종가 완결일 평균으로 폴백한다.
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
        d = per_slot.get(lab) or per_day.get(lab) or {}
        p = d.get("pct_short")
        tag = ""
        if p is not None:
            tag = f" <i>({'🔺' if p > 0 else '🔽'}{abs(p):.0f}%)</i>"
        lines.append(f"  · <b>{esc(lab)}</b> {amt:,.0f}조{tag}")
    for m in fl["rows"]:
        if m.get("error"):
            lines.append(f"  · {esc(m['label'])} — 조회 실패")
    lines.append(f"  ── <b>합계 {(fl.get('total_amount_jo') or 0):,.0f}조</b>")

    a = cmp.get("amount") or fl.get("ref") or {}
    seg = []
    if a.get("pct_short") is not None:
        seg.append(f"5일 {a['pct_short']:+.0f}% {_heat(a['pct_short'])}")
    if a.get("pct_long") is not None:
        seg.append(f"20일 {a['pct_long']:+.0f}% {_heat(a['pct_long'])}")
    if seg:
        src = "같은 시각" if cmp.get("amount") else "종가"
        lines.append(f"  <i>{src}평균 대비 · " + " · ".join(seg) + "</i>")

    KEYS = ("개인", "외국인", "기관", "기타법인")

    def one(name):
        src = rows.get(name) or {}
        f = src.get("flow_eok") or {}
        acc = {k: f[k] for k in KEYS if f.get(k) is not None}
        if not acc:
            return None
        acc["비차익"] = (src.get("program_eok") or {}).get("비차익")
        return acc

    groups = [(m, one(m)) for m in FLOW_MARKETS]
    if any(g[1] for g in groups):
        fsrc = "키움 KRX+NXT" if fl.get("flow_src") == "kiwoom" else "네이버 KRX"
        lines.append(f"\n💵 <b>순매수</b> <i>(조원 · {esc(fsrc)} · "
                     f"▲▼는 코스피 5일평균 대비)</i>")
        lines.append(_flow_table(groups, cmp))

        note = []
        for key, name in (("foreign", "외국인"), ("inst", "기관"),
                          ("indiv", "개인"), ("etc", "기타법인"),
                          ("nonarb", "비차익")):
            c = cmp.get(key)
            if not c or c.get("z") is None:
                continue
            if abs(c["z"]) < 1.0:
                continue
            verb = "대량 순매수" if c["today"] > c.get("avg_long", 0) else "대량 순매도"
            note.append(f"{esc(name)} <i>({c['z']:+.1f}σ {verb})</i>")
        if note:
            lines.append("  ⚡ " + " / ".join(note) +
                         f" <i>· {cmp.get('n_long', 0)}일 기준</i>")
        lines.append("  <i>* 개인+외국인+기관+기타법인 = 0 · "
                     "비차익은 거래방식 축이라 위 4개와 중복 집계</i>")
    return "\n".join(lines)


def build(win, news=None, us_sectors=None, kr_impact=None, leaders=None,
          flows=None, flows_cmp=None, kr_upjong=None, kr_themes=None, kr_when=None,
          us_leaders=None, topic_news=None, leader_news=None, footer=None):
    parts = [header(win), section_quotes(win, news)]
    for s in (section_flows(flows, flows_cmp),
              section_kr_sectors(kr_upjong, kr_themes, kr_when or "장중", topic_news),
              section_leaders(leaders, leader_news),
              section_us_sectors(us_sectors or [], us_leaders, topic_news),
              section_kr_impact(kr_impact or []),
              ):
        if s:
            parts.append(s)
    if footer:
        parts.append(f"\n<i>{esc(footer)}</i>")
    return "\n".join(parts)
