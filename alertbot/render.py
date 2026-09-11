# -*- coding: utf-8 -*-
"""텔레그램 메시지 조립. 각 섹션은 데이터가 없으면 통째로 생략된다."""
from __future__ import annotations

import unicodedata

from notify import esc

WD = ["월", "화", "수", "목", "금", "토", "일"]
ICON = {"0600": "☀️", "0750": "🌅", "0850": "🔔", "0930": "🟢",
        "1430": "🔔", "1530": "🏁", "1630": "🔁", "1800": "🌆",
        "1900": "🌙", "2000": "🌛"}


def _fmt_px(v, dp):
    return f"{v:,.{dp}f}" if v is not None else "—"


def header(win):
    e = win["end"]
    return (f"{ICON[win['slot']]} <b>종가베팅 브리핑</b>\n"
            f"· {e:%m/%d}({WD[e.weekday()]}) {e:%H:%M}\n"
            f"· <i>{_span(win)} 변동 ({win['start']:%m/%d %H:%M} → {win['end']:%m/%d %H:%M})</i>")


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
    """시황 — 본장 시세, 괄호는 퍼프 창변동%(제목에 Perp. 명시). ★=1% 이상."""
    lines = ["\n📊 <b>시황</b> <i>(Perp.)</i>"]
    for r in win["rows"]:
        if r["chg_pct"] is None:
            lines.append(f"  {esc(r['name'])} — 데이터 없음")
            continue
        star = "★" * r.get("stars", 1 if r["significant"] else 0)
        sign = "🔺" if r["chg_pct"] > 0 else ("🔽" if r["chg_pct"] < 0 else "▪️")
        px = _fmt_px(r['end_px'], r['decimals'])
        if r.get("kind") == "yield":
            px += "%"                     # 금리는 수치 자체가 %
        if r.get("perp_pct") is not None:
            tag = f" <i>({r['perp_pct']:+.2f}%)</i>"
        elif r.get("proxy"):
            tag = f" <i>({esc(r['proxy'])})</i>"
        else:
            tag = ""
        # 가격 등락률 (퍼프%)★ — Perp. 표기는 섹션 제목에 한 번만 (09-11 사용자 포맷)
        lines.append(f"  {sign} <b>{esc(r['name'])}</b> {px} "
                     f"<b>{esc(r.get('chg_label') or '')}</b>{tag}{star}")
    return "\n".join(lines)


def section_key_stocks(win):
    """주요 종목(SK하이닉스·삼성전자) — 시황 바로 다음.

    장중엔 실제 주가(전일比), 장외엔 바이낸스 퍼프로 밤사이 변동을 잰다(perp 표기).
    """
    rows = win.get("key_stocks") or []
    if not rows:
        return ""
    lines = ["\n📌 <b>주요 종목</b> <i>(Perp.)</i>"]
    for r in rows:
        if r.get("chg_pct") is None:
            lines.append(f"  {esc(r['name'])} — 데이터 없음")
            continue
        star = "★" * r.get("stars", 1 if r["significant"] else 0)
        sign = "🔺" if r["chg_pct"] > 0 else ("🔽" if r["chg_pct"] < 0 else "▪️")
        if r.get("perp_pct") is not None:
            # 앵커가 15:30(KRX 마감)이라 이 퍼프 % = '마감 이후 변동' = 괴리율 프록시
            tag = f" <i>({r['perp_pct']:+.2f}%)</i>"
        elif r.get("proxy"):
            tag = " <i>(perp)</i>"
        else:
            tag = ""
        lines.append(f"  {sign} <b>{esc(r['name'])}</b> "
                     f"{_fmt_px(r['end_px'], r.get('decimals', 0))} "
                     f"<b>{esc(r.get('chg_label') or '')}</b>{tag}{star}")
    return "\n".join(lines)


def section_quote_news(win, news):
    """유의미 변동 자산의 원인 뉴스 — 시황 바로 아래 독립 카테고리.

    검색 구간은 변동폭 계산 구간과 같다. 그 밖의 기사는 이 변동의 원인이 아니다.
    """
    news = news or {}
    lines = ["\n📰 <b>관련 뉴스</b>"]
    for r in list(win["rows"]) + list(win.get("key_stocks") or []):
        items = news.get(r["name"]) or []
        if not items:
            continue
        # % 는 ★ 판정과 같은 기준(퍼프 있으면 퍼프, 없으면 본장) — chg_pct 가 그 값
        pct = f"{r['chg_pct']:+.2f}%" if r.get("chg_pct") is not None else ""
        lines.append(f"  · <b>{esc(r['name'])}</b> <i>{pct}</i>")
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
    proxy = bool(sectors and sectors[0].get("kind") == "perp_proxy")
    title = ("\n🇺🇸 <b>미국 프록시</b> <i>(휴장일 · 퍼페추얼 기준)</i>" if proxy
             else "\n🇺🇸 <b>전일 미국 섹터</b>")
    lines = [title]
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
            amt = x.get("amt_eok")
            amt_s = ("" if not amt else
                     f" · {amt/1e4:.1f}조" if amt >= 10000 else f" · {amt:,.0f}억")
            lines.append(f"  {mark} <b>{esc(x['name'])}</b> {x['change_pct']:+.2f}%{amt_s}{d3}")
            if with_leaders and x.get("leaders"):
                lines.append(f"      {esc(', '.join(x['leaders']))}")

    if upjong:
        block(f"🇰🇷 <b>주도 섹터</b> <i>({esc(when)})</i>",
              upjong.get("up") or [], upjong.get("down") or [], with_leaders=True)
    if themes:
        block("🎯 <b>주도 테마</b>", themes.get("up") or [],
              themes.get("down") or [], with_leaders=True)
    return "\n".join(lines)


def section_nxt_premarket(pm):
    """NXT 프리마켓(08:00~08:50) 주도 섹터·주도주 — 08:50 알림 전용.

    09시 개장 전 유일한 실체결 데이터라 개장 방향의 선행 신호로 쓴다.
    등락률은 전일 종가 대비, 거래대금은 NXT 체결분만이다.
    """
    if not pm:
        return ""
    lines = [f"\n🌅 <b>NXT 프리마켓</b> <i>(08:00~08:50 · 총 {pm['total_eok']/1e4:,.1f}조)</i>"]
    if pm.get("sectors"):
        lines.append("  <b>주도 섹터</b>")
        for x in pm["sectors"]:
            mark = "🔺" if x["chg_pct"] > 0 else "🔽"
            lines.append(f"  {mark} <b>{esc(x['name'])}</b> {x['chg_pct']:+.2f}% · "
                         f"{x['amt_eok']:,.0f}억")
            lines.append(f"      {esc(', '.join(l['name'] for l in x['leaders']))}")
    if pm.get("stocks"):
        lines.append("  <b>주도주</b> <i>(NXT 거래대금순)</i>")
        for r in pm["stocks"]:
            mark = "🔺" if (r["chg_pct"] or 0) > 0 else "🔽"
            sec = f" · <i>{esc(r['sector'])}</i>" if r.get("sector") else ""
            lines.append(f"  {mark} <b>{esc(r['name'])}</b> {r['chg_pct']:+.2f}% · "
                         f"{r['amt_eok']:,.0f}억{sec}")
    return "\n".join(lines)


def section_us_movers(mv):
    """미국 개별주 독주 — 지수 대비 격차 + 원인 뉴스 + 한국 관련주."""
    if not mv:
        return ""
    lines = ["\n🎯 <b>미국 특이 종목</b> <i>(나스닥 대비 독주 · Perp.)</i>"]
    for m in mv:
        lines.append(f"  · <b>{esc(m['sym'])}({esc(m['kr'])})</b> {m['pct']:+.2f}%"
                     f" <i>(지수 {m['base']:+.2f}%)</i>")
        if m.get("news"):
            lines.append(f"      {_link(m['news'], cut=70)}")
        if m.get("related"):
            names = ", ".join(f"{esc(n)}({c})" for n, c in m["related"][:3])
            lines.append(f"      한국 관련: {names}")
    return "\n".join(lines)


def section_kr_impact(impacts):
    """impacts: [{kr_sector, driver, tickers, note}] — '원인 → 결과' 순."""
    if not impacts:
        return ""
    lines = ["\n🇰🇷 <b>한국시장 영향 예상</b>"]
    for im in impacts[:5]:
        d = im.get("direction")
        tag = " (상승 예상)" if d == "up" else (" (하락 예상)" if d == "down" else "")
        lines.append(f"  · <b>{esc(im['driver'])} → {esc(im['kr_sector'])}</b>{esc(tag)}")
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
    lines = ["\n🗓 <b>발표 완료</b> <i>(24시간)</i>"]
    for d in done:
        v = f" : {esc(d['verdict'])}" if d.get("verdict") else ""
        # 국기를 맨 앞으로 (label 은 '🇺🇸 이름' 형태 — 첫 토큰이 항상 국기)
        flag, _, nm = (d["label"] or "").partition(" ")
        lines.append(f"  {flag} {d['when']:%H:%M} <b>{esc(nm)}</b>{v}")
        if d.get("nums"):
            lines.append(f"      ({esc(d['nums'])})")
        if d.get("react"):
            lines.append(f"      → 발표 후 {esc(d['react'])}")
        elif d.get("assets"):
            seg = " · ".join(f"{esc(n)} {p:+.2f}%" for n, p in d["assets"])
            lines.append(f"      → {seg}")
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
        mark = "⚠️ " if a.get("overnight") else ""
        v = f" {esc(a['value'])}" if a.get("value") else ""
        flag, _, nm = (a["label"] or "").partition(" ")
        lines.append(f"  {flag} {mark}{a['when']:%m-%d %H:%M} <b>{esc(nm)}</b>{v}")
        if a.get("stat"):
            lines.append(f"      <i>{esc(a['stat'])}</i>")
        if a.get("note"):
            lines.append(f"      <i>{esc(a['note'])[:120]}</i>")
    return "\n".join(lines)


def section_leaders(ld, title="🎯 <b>주도주</b>"):
    """leaders.fetch_leaders() 결과."""
    if not ld or not ld.get("rows"):
        return ""
    lines = [f"\n{title} <i>({esc(ld.get('source') or '')})</i>"]
    for r in ld["rows"]:
        amt = r.get("거래대금")
        seg = f"  · <b>{esc(r['종목명'])}</b> {r.get('등락률',0):+.2f}%"
        if amt is not None:
            seg += f" / {amt:,.0f}억"
        if r.get("섹터"):
            seg += f" / <i>{esc(r['섹터'])}</i>"
        lines.append(seg)
        # 거래원별 순매수 서브라인은 09-11 제거 — 변동률·거래대금만 (수급은 점수에만 반영)
    return "\n".join(lines)


def _flow_table(groups):
    """순매수 블록 — <pre> 표를 버리고 일반 텍스트 리스트로 그린다.

    <pre> 고정폭은 'PC 텔레그램의 고정폭 폰트에서 한글 폭 ≠ 영문 2칸'이라
    기기마다 표가 어긋났다(09-11 스크린샷). 거래대금 섹션과 같은 리스트 형식은
    비례 폰트라 한글끼리/숫자끼리 폭이 일정해 어디서든 같게 보인다.
    """
    KEYS = ("개인", "외국인", "기관", "기타법인", "비차익")

    # 단위 자동 선택. 프리마켓(08:50)엔 수백억 단위라 조원으로 찍으면 전부
    # -0.1/+0.0 으로 뭉개진다. 최대값이 1조 미만이면 억원으로 보여준다.
    vals = [abs(v) for _, acc in groups for v in (acc or {}).values() if v is not None]
    use_jo = (max(vals, default=0) >= 10000)
    unit = "조원" if use_jo else "억원"
    fmt = (lambda v: f"{v/1e4:+.1f}") if use_jo else (lambda v: f"{v:+,.0f}")

    out = [f"  <i>{esc(' / '.join(g[0] for g in groups))}</i>"]
    for k in KEYS:
        if k == "비차익":                  # 투자자별과 별개 집계(프로그램) — 선으로 분리
            out.append("   ────────────")
        cells = " / ".join(("-" if (acc or {}).get(k) is None else fmt(acc[k]))
                           for _, acc in groups)
        pad = "　" * (4 - len(k))          # 전각 패딩 — 비례 폰트에서도 폭 일정
        out.append(f"  · {esc(k)}{pad} <b>{esc(cells)}</b>")
    return "\n".join(out), unit


# 거래대금 표시 순서 — 선물이 규모가 가장 크고 방향을 먼저 보여주므로 앞에 둔다
MARKET_ORDER = ("선물", "코스피", "코스닥")

# 순매수 표 열. 코스피 현물 + 코스피200 선물을 한 열로 본다 — 외국인이 현물을
# 팔면서 선물을 사는 헤지가 흔해 나눠 보면 방향을 놓친다.
# 현물은 키움 KRX+NXT 통합, 선물은 네이버(KRX). 선물은 NXT 에서 거래되지 않으므로
# 둘 다 '해당 상품의 시장 전체'다 — 소스는 달라도 범위는 어긋나지 않는다.
FLOW_GROUPS = (("코스피+선물", ("코스피", "선물")), ("코스닥", ("코스닥",)))


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

    def _pct_tag(d):
        """(5일 +14% / 20일 +37%) — 같은 시각평균 대비, 있는 것만."""
        parts = [f"{nm} {d[k]:+.0f}%" for nm, k in (("5일", "pct_short"), ("20일", "pct_long"))
                 if d.get(k) is not None]
        return f" <i>({' / '.join(parts)})</i>" if parts else ""

    asof = fl.get("asof_label")
    asof_tag = f" <i>({esc(asof)})</i>" if asof else ""
    lines = ["\n💰 <b>시장 거래대금</b>" + asof_tag]
    n_amt = 0
    for lab in MARKET_ORDER:
        m = rows.get(lab)
        if not m or m.get("amount_won") is None:
            continue                      # 개장 전엔 오늘 거래대금이 없다 — 행 생략
        n_amt += 1
        amt = (m.get("amount_won") or 0) / 1e12
        d = per_slot.get(lab) or per_day.get(lab) or {}
        pad = "　" * (3 - len(lab))   # 선물(2자)도 코스피/코스닥과 금액 열 맞춤
        lines.append(f"  · <b>{esc(lab)}</b>{pad} {amt:,.0f}조{_pct_tag(d)}")
    for m in fl["rows"]:
        if m.get("error"):
            lines.append(f"  · {esc(m['label'])} — 조회 실패")
    if n_amt == 0:
        lines = []                        # 거래대금 블록 통째로 생략
    else:
        a = cmp.get("amount") or fl.get("ref") or {}
        lines.append(f"   ─ <b>합계 {(fl.get('total_amount_jo') or 0):,.0f}조</b>{_pct_tag(a)}")

    KEYS = ("개인", "외국인", "기관", "기타법인")

    def merged(names):
        acc, seen = {k: 0.0 for k in KEYS}, False
        nonarb, has_p = 0.0, False
        for n in names:
            m = rows.get(n) or {}
            f = m.get("flow_eok") or {}
            for k in KEYS:
                if f.get(k) is not None:
                    acc[k] += f[k]; seen = True
            p = (m.get("program_eok") or {}).get("비차익")
            if p is not None:
                nonarb += p; has_p = True
        if not seen:
            return None
        acc["비차익"] = nonarb if has_p else None
        return acc

    groups = [(title, merged(names)) for title, names in FLOW_GROUPS]
    if any(g[1] for g in groups):
        fsrc = "KRX+NXT" if fl.get("flow_src") == "kiwoom" else "네이버 KRX"
        tbl, unit = _flow_table(groups)
        asof2 = f" · {esc(asof)}" if asof else ""
        lines.append(f"\n💵 <b>순매수</b> <i>({esc(unit)} · {esc(fsrc)}{asof2})</i>")
        lines.append(tbl)

        note = []
        for key, name in (("foreign", "외국인"), ("inst", "기관"),
                          ("indiv", "개인"), ("etc", "기타법인"),
                          ("nonarb", "비차익")):
            c = cmp.get(key)
            if not c or c.get("z") is None or abs(c["z"]) < 1.0:
                continue
            verb = "대량 순매수" if c["today"] > c.get("avg_long", 0) else "대량 순매도"
            note.append(f"  ⚡ {esc(name)} <i>({c['z']:+.1f}σ {verb})</i>")
        if note:
            lines += note
            lines.append(f"    · <i>코스피+선물 {cmp.get('n_long', 0)}일 기준</i>")

    return "\n".join(lines)


def build(win, news=None, us_sectors=None, kr_impact=None, leaders=None,
          flows=None, flows_cmp=None, kr_upjong=None, kr_themes=None, kr_when=None,
          us_leaders=None, events=None, nxt_pm=None, us_movers=None, footer=None):
    parts = [header(win)]
    for s in (section_events_done(events),        # 오늘 나온 근거를 먼저
              section_quotes(win),
              section_key_stocks(win),
              section_quote_news(win, news),
              section_flows(flows, flows_cmp),
              section_kr_sectors(kr_upjong, kr_themes, kr_when or "장중"),
              section_leaders(leaders),
              section_nxt_premarket(nxt_pm),
              section_us_sectors(us_sectors or [], us_leaders),
              section_us_movers(us_movers),
              section_kr_impact(kr_impact or []),
              section_events_ahead(events),      # 오버나이트 노출은 맨 끝에
              ):
        if s:
            parts.append(s)
    if footer:
        parts.append(f"\n<i>{esc(footer)}</i>")
    return "\n".join(parts)
