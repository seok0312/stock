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


# ── 표 정렬 유틸 ────────────────────────────────────────────────
def _dw(s: str) -> int:
    """표시 폭. 한글·CJK는 2칸으로 센다(<pre> 고정폭 정렬용)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, width: int, align: str = "l") -> str:
    gap = max(0, width - _dw(s))
    if align == "r":
        return " " * gap + s
    if align == "c":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def section_quotes(win):
    """시황 5종을 순서대로. 뉴스는 바로 아래 별도 섹션으로 뺀다."""
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
    return "\n".join(lines)


def section_quote_news(win, news):
    """유의미 변동 자산의 원인 뉴스 — 시황 바로 아래 독립 카테고리.

    검색 구간은 변동폭 계산 구간과 같다. 그 밖의 기사는 이 변동의 원인이 아니다.
    """
    news = news or {}
    lines = ["\n📰 <b>관련 뉴스</b>"]
    for r in win["rows"]:
        items = news.get(r["name"]) or []
        if not items:
            continue
        lines.append(f"  · <b>{esc(r['name'])}</b> <i>{r['chg_pct']:+.2f}%</i>")
        for it in items[:2]:
            lines.append(f"      {_link(it)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def section_us_sectors(sectors, leaders=None):
    """전일 미국 섹터 — 시황과 같은 🔺🔽 마커. 강세 섹터에만 주도주를 붙인다."""
    if not sectors:
        return ""
    up = [s for s in sectors if s["change_pct"] > 0][:3]
    dn = [s for s in sectors if s["change_pct"] < 0][-3:]
    if not up and not dn:
        return ""
    leaders = leaders or {}
    lines = ["\n🇺🇸 <b>전일 미국 섹터</b>"]
    for s in up:
        nm = s["sector"]
        lines.append(f"  🔺 <b>{esc(nm)}</b> {s['change_pct']:+.2f}%")
        ld = leaders.get(nm) or []
        if ld:
            lines.append("      " + " · ".join(
                f"{esc(x['ticker'])} {x['change_pct']:+.1f}%" for x in ld))
    for s in reversed(dn):
        lines.append(f"  🔽 <b>{esc(s['sector'])}</b> {s['change_pct']:+.2f}%")
    return "\n".join(lines)


def section_kr_sectors(upjong, themes, when="장중"):
    """장중 한국 업종 강약 + 주도 테마. 상승 3 / 하락 3 만."""
    lines = []

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


def section_events_done(ev):
    """구간 안에 발표된 최중요 일정 — 브리핑 맨 위.
    시황을 읽기 전에 '무엇이 나왔나'를 알아야 아래 숫자들이 해석된다."""
    done = (ev or {}).get("done") or []
    if not done:
        return ""
    lines = ["\n🗓 <b>발표 완료</b>"]
    for d in done:
        v = f" {esc(d['value'])}" if d.get("value") else ""
        lines.append(f"  · {d['when']:%H:%M} <b>{esc(d['label'])}</b>{v}")
        if d.get("assets"):
            seg = " · ".join(f"{esc(n)} {p:+.2f}%" for n, p in d["assets"])
            lines.append(f"      → {seg}")
        if d.get("sectors"):
            lines.append(f"      → {esc(', '.join(d['sectors']))}")
        if d.get("note"):
            lines.append(f"      <i>{esc(d['note'])[:120]}</i>")
    return "\n".join(lines)


def section_events_ahead(ev):
    """앞으로의 최중요 일정 — 브리핑 맨 아래.

    종가베팅은 시가매도라 다음 시가까지의 노출이 손익을 좌우한다.
    stat 은 같은 지표의 과거 반응 실측치.
    """
    ahead = (ev or {}).get("ahead") or []
    if not ahead:
        return ""
    lines = ["\n⏳ <b>예정</b> <i>(⚠️ 는 다음 시가 전)</i>"]
    for a in ahead:
        mark = "⚠️" if a.get("overnight") else "·"
        v = f" {esc(a['value'])}" if a.get("value") else ""
        lines.append(f"  {mark} {a['when']:%m-%d %H:%M} <b>{esc(a['label'])}</b>{v}")
        if a.get("stat"):
            lines.append(f"      <i>{esc(a['stat'])}</i>")
        if a.get("note"):
            lines.append(f"      <i>{esc(a['note'])[:120]}</i>")
    return "\n".join(lines)


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


def _flow_table(groups):
    """순매수 표. Telegram 은 HTML 표를 지원하지 않아 <pre> 고정폭으로 그린다.

    셀 안에 '평균 대비 ▲0.3' 까지 넣으면 열 폭이 들쭉날쭉해 정렬이 무너진다.
    표는 값만 가운데 정렬로 두고, 평상시 대비 이상치는 아래 ⚡ 줄이 맡는다.
    """
    KEYS = ("개인", "외국인", "기관", "기타법인", "비차익")

    def lab(s, n=4):
        """라벨을 항상 n개의 전각 글자 폭으로 맞춘다.

        공백(반각)으로 채우면 텔레그램 고정폭 글꼴에서 한글:영문 폭이 정확히
        2:1이 아닐 때 줄마다 열 시작점이 어긋난다. 전각 공백(U+3000)으로 채우면
        라벨 칸이 '한글 n글자'로 고정돼 폰트 비율과 무관하게 정렬이 유지된다.
        """
        gap = n - len(s)
        left = gap // 2
        return "　" * left + s + "　" * (gap - left)

    titles = [lab("구분")] + [g[0] for g in groups]
    rows = [[lab(k)] + [("-" if (acc or {}).get(k) is None else f"{acc[k]/1e4:+.1f}조")
                        for _, acc in groups] for k in KEYS]
    w = [max(_dw(r[i]) for r in [titles] + rows) + 2 for i in range(len(titles))]
    out = ["".join(_pad(t, w[i], "c") for i, t in enumerate(titles)),
           "-" * sum(w)]
    out += ["".join(_pad(c, w[i], "c") for i, c in enumerate(r)) for r in rows]
    return "<pre>" + esc("\n".join(out)) + "</pre>"


def _heat(p):
    return "🔥 과열" if p > 30 else ("🌿 활발" if p > 5 else
           ("💤 한산" if p < -20 else "▫️ 보통"))


# 거래대금 표시 순서 — 선물이 규모가 가장 크고 방향을 먼저 보여주므로 앞에 둔다
MARKET_ORDER = ("선물", "코스피", "코스닥")

# 순매수 표 열. 코스피·코스닥은 키움(KRX+NXT 통합), 선물은 네이버 —
# 키움 국내주식 REST 에는 시장 단위 선물 투자자별 TR 이 없다(ka10051 은 현물만,
# ka10063 은 종목별). 그래서 선물만 소스가 다르고 KRX 거래분만 담긴다.
FLOW_MARKETS = ("코스피", "선물", "코스닥")


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
    ref_src = "같은 시각" if cmp.get("amount") else "종가"
    for lab, key in (("  5일", "pct_short"), ("20일", "pct_long")):
        p = a.get(key)
        if p is not None:
            lines.append(f"  · <i>{ref_src}평균 대비 · {lab} {p:+.0f}% {_heat(p)}</i>")

    KEYS = ("개인", "외국인", "기관", "기타법인")

    def one(name):
        m = rows.get(name) or {}
        f = m.get("flow_eok") or {}
        acc = {k: f[k] for k in KEYS if f.get(k) is not None}
        if not acc:
            return None
        acc["비차익"] = (m.get("program_eok") or {}).get("비차익")
        return acc

    groups = [(m, one(m)) for m in FLOW_MARKETS if m in rows]
    if any(g[1] for g in groups):
        fsrc = "키움 KRX+NXT" if fl.get("flow_src") == "kiwoom" else "네이버 KRX"
        lines.append(f"\n💵 <b>순매수</b> <i>(조원 · {esc(fsrc)})</i>")
        lines.append(_flow_table(groups))

        note = []
        for key, name in (("foreign", "외국인"), ("inst", "기관"),
                          ("indiv", "개인"), ("etc", "기타법인"),
                          ("nonarb", "비차익")):
            c = cmp.get(key)
            if not c or c.get("z") is None or abs(c["z"]) < 1.0:
                continue
            verb = "대량 순매수" if c["today"] > c.get("avg_long", 0) else "대량 순매도"
            note.append(f"{esc(name)} <i>({c['z']:+.1f}σ {verb})</i>")
        if note:
            lines.append("  ⚡ " + " / ".join(note) +
                         f" <i>· 코스피 {cmp.get('n_long', 0)}일 기준</i>")
        lines.append("  · <i>개인+외국인+기관+기타법인 = 0</i>")
        lines.append("  · <i>비차익은 거래방식 축이라 위 4개와 중복 집계</i>")
        lines.append("  · <i>선물만 네이버(KRX 거래분)</i>")
    return "\n".join(lines)


def build(win, news=None, us_sectors=None, kr_impact=None, leaders=None,
          flows=None, flows_cmp=None, kr_upjong=None, kr_themes=None, kr_when=None,
          us_leaders=None, events=None, footer=None):
    parts = [header(win)]
    for s in (section_events_done(events),        # 오늘 나온 근거를 먼저
              section_quotes(win),
              section_quote_news(win, news),
              section_flows(flows, flows_cmp),
              section_kr_sectors(kr_upjong, kr_themes, kr_when or "장중"),
              section_leaders(leaders),
              section_us_sectors(us_sectors or [], us_leaders),
              section_kr_impact(kr_impact or []),
              section_events_ahead(events),      # 오버나이트 노출은 맨 끝에
              ):
        if s:
            parts.append(s)
    if footer:
        parts.append(f"\n<i>{esc(footer)}</i>")
    return "\n".join(parts)
