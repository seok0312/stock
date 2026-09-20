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


def _justify_label(s: str, n: int = 4, fill: str = "ㅤ") -> str:
    """라벨을 n칸에 '양쪽 정렬' — 채움을 글자 사이에 고르게 분산 (09-16 사용자).

    개인 → 개ㅤㅤ인 / 외국인 → 외ㅤ국인 / 기타법인 → 그대로.
    꼬리 패딩보다 시각적으로 안정적이고, 채움문자 폭이 폰트마다 조금 달라도
    오차가 가운데로 분산돼 끝단이 덜 어긋난다."""
    gap = n - len(s)
    if gap <= 0 or len(s) <= 1:
        return s + fill * max(0, gap)
    slots = len(s) - 1
    base, extra = divmod(gap, slots)
    out = []
    for i, ch in enumerate(s):
        out.append(ch)
        if i < slots:
            out.append(fill * (base + (1 if i < extra else 0)))
    return "".join(out)


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


# 종가베팅 신호 점수 v2 (09-16 데이터 적합 — 61일, 타깃=후보군 종가→익일시가 평균).
# 항목별 IC 와 표준화 β 에 비례한 정수 가중치. 규칙은 여기 한 곳에만.
#   EWY 퍼프 창변동  IC+0.64 β+1.54 → ±0.3% 에 ±2, ±1.0% 에 ±3  (최강 신호)
#   거래대금 수위     IC+0.32 β+0.98 → 평소 ±15% 에 ±2
#   QQQ 퍼프 창변동  IC+0.60 β+0.52 → ±0.5% 에 ±1 (EWY 와 공선이라 소폭만)
#   오버나이트 발표   이벤트밤 평균 +0.46% vs 없는 밤 +1.80% → -1, 금리/FOMC -2
#   수급 대량 매수/매도 ±1 — 시계열 미검증(같은시각 표본 부족), v1 유지
#   탈락: 코스피 본장 등락(IC+0.08, EWY 통제 시 잉여) · 오일(-0.16) · 금 · BTC
_SIG_LABEL = ((3, "우호"), (1, "약우호"), (0, "중립"), (-2, "신중"), (-99, "관망"))


def _perp_of(row):
    """행의 퍼프 창변동 % — perp_pct 가 없으면(본장 조회 실패로 chg_pct 가 퍼프인
    경우) chg_pct 로 폴백. 장중 지수행(idx_star, chg_pct=본장)은 None."""
    if row is None:
        return None
    if row.get("perp_pct") is not None:
        return row["perp_pct"]
    return None if row.get("idx_star") else row.get("chg_pct")


def section_summary(win, fl=None, cmp=None, events=None, kr_upjong=None,
                    us_movers=None, kr_impact=None):
    """맨 윗줄 요약 — 본문 섹션 순서(발표→시황→거래대금→수급→주도→미국)대로 압축.

    첫 줄은 종가베팅 신호 점수(규칙은 위 주석). 소재 없으면 그 줄 생략."""
    cmp = cmp or {}
    bits = []
    score = 0

    # 1) 발표 — 다음 시가 전 오버나이트 이벤트
    ov = [x for x in (events or {}).get("ahead") or [] if x.get("overnight")]
    if ov:
        x = ov[0]
        flag, _, nm = (x["label"] or "").partition(" ")
        bits.append(f"다음 시가 전: {flag} {esc(nm)} ({x['when']:%H:%M})")
        # 감점은 그 밤 이벤트 전체 중 최고 강도로 (첫 줄이 소매판매여도 FOMC 가 겹치면 -2)
        score -= 2 if any(w in (e["label"] or "") for e in ov
                          for w in ("금리", "FOMC")) else 1

    # 2) 시황·주요종목 — ★2개 이상 이례 변동 + 지수 방향
    big = [r for r in (win.get("rows") or []) if (r.get("stars") or 0) >= 2]
    big += [r for r in (win.get("key_stocks") or []) if (r.get("stars") or 0) >= 2]
    big.sort(key=lambda r: (-(r.get("stars") or 0), -abs(r.get("chg_pct") or 0)))
    if big:
        seg = " · ".join(f"{esc(r['name'])} {r['chg_pct']:+.1f}%{'★' * r['stars']}"
                         for r in big[:3])
        bits.append(f"변동: {seg}")
    # EWY 퍼프(주 신호) + QQQ 퍼프 — 창 기준, v2 가중치
    ewy = _perp_of(next((r for r in win.get("rows") or [] if r["name"] == "코스피"), None))
    if ewy is not None:
        score += (3 if ewy >= 1.0 else 2 if ewy >= 0.3 else 0) \
            - (3 if ewy <= -1.0 else 2 if ewy <= -0.3 else 0)
    qqq = _perp_of(next((r for r in win.get("rows") or [] if r["name"] == "나스닥"), None))
    if qqq is not None:
        score += 1 if qqq >= 0.5 else (-1 if qqq <= -0.5 else 0)

    # 3) 거래대금
    a = (cmp.get("amount") or (fl or {}).get("ref") or {})
    p = a.get("pct_short")
    if p is not None:
        bits.append(f"거래대금 평소 {p:+.0f}%")
        score += 2 if p >= 15 else (-2 if p <= -15 else 0)

    # 4) 수급
    fs = []
    for key, name in (("foreign", "외국인"), ("inst", "기관"), ("nonarb", "비차익")):
        c = cmp.get(key)
        z = (c or {}).get("z")
        if z is None or abs(z) < 1.2:
            continue
        buy = c["today"] > c.get("avg_long", 0)
        stars = "★" * (3 if abs(z) >= 2.0 else 2 if abs(z) >= 1.5 else 1)
        fs.append(f"{name} 대량 {'매수' if buy else '매도'}{stars}")
        if key in ("foreign", "inst"):
            score += 1 if buy else -1
    if fs:
        bits.append("수급: " + " · ".join(fs))

    # 5) 주도 섹터
    up = (kr_upjong or {}).get("up") or []
    if up:
        x = up[0]
        bits.append(f"주도: {esc(x['name'])} {x['change_pct']:+.1f}%")

    # 6) 미국 특이종목 / 한국 영향
    if us_movers:
        m = us_movers[0]
        bits.append(f"미국: {esc(m['sym'])} 독주 {m['pct']:+.1f}% (지수 {m['base']:+.1f}%)")
        if m["pct"] < 0:
            score -= 1
    elif kr_impact:
        im = kr_impact[0]
        d = im.get("direction")
        tag = " 상승" if d == "up" else (" 하락" if d == "down" else "")
        bits.append(f"미국발: {esc(im['kr_sector'])}{tag} 예상")

    # 진입 타이밍 (1430/1900 전용, 09-16 퍼프 30분봉 68일 실측):
    # 마감 전 구간은 '되돌림'이 지배 — 흐름 강세(+0.3%↑)면 마감까지 평균 -0.2~-0.3%p
    # 반납(먼저 매수 손해), 중립(±0.3%)일 때만 마감 앞 상승(+0.26~+0.36%p, 밤 승률 81%).
    # 약세는 낮엔 지속(대기), 밤엔 반등 경향(매수 무방).
    timing = None
    slot = win.get("slot")
    if slot in ("1430", "1900"):
        kr_row = next((r for r in win.get("rows") or [] if r["name"] == "코스피"), None)
        f = (kr_row or {}).get("chg_pct")
        if f is not None:
            if slot == "1430":
                timing = ("지금 매수 우위 — 중립 흐름은 마감 앞 상승 경향" if abs(f) < 0.3
                          else "15:20 대기 — 강세 흐름은 마감 전 되돌림" if f >= 0.3
                          else "대기/관망 — 약세 흐름은 마감까지 지속 경향")
            else:
                timing = ("지금 매수 우위 — 중립 흐름 (실측 승률 81%)" if abs(f) < 0.3
                          else "20시 근처 대기 — 강세 흐름은 되돌림 경향" if f >= 0.3
                          else "지금 매수 무방 — 약세는 막판 반등 경향")

    if not bits:
        return ""
    lab = next(l for th, l in _SIG_LABEL if score >= th)
    head = [f"  · <b>종가베팅 신호: {score:+d} ({lab})</b>"]
    if timing:
        head.append(f"  · <b>타이밍: {esc(timing)}</b>")
    return "\n🧭 <b>요약</b>\n" + "\n".join(head + [f"  · {b}" for b in bits])


def _fmt_after(p):
    """괄호(마감 후 변동) 라벨 — 장중(정확히 0)은 부호 없이 0.00%."""
    return "0.00%" if p == 0 else f"{p:+.2f}%"


def section_quotes(win):
    """시황 — 본장 등락률 + 괄호는 본장 '마감 후' 변동만(after).
    본장이 장중이면 괄호는 항상 0% (09-20 사용자)."""
    lines = ["\n📊 <b>시황</b> <i>(after)</i>"]
    for r in win["rows"]:
        if r["chg_pct"] is None:
            lines.append(f"  {esc(r['name'])} — 데이터 없음")
            continue
        star = "★" * r.get("stars", 1 if r["significant"] else 0)
        sign = "🔺" if r["chg_pct"] > 0 else ("🔽" if r["chg_pct"] < 0 else "▪️")
        px = _fmt_px(r['end_px'], r['decimals'])
        if r.get("kind") == "yield":
            px += "%"                     # 금리는 수치 자체가 %
        lab = esc(r.get("chg_label") or "")
        if r.get("after_pct") is not None:
            body = f"<b>{lab}</b> ({_fmt_after(r['after_pct'])})"
        elif r.get("proxy"):
            body = f"<b>{lab}</b> <i>({esc(r['proxy'])})</i>"
        else:
            body = f"<b>{lab}</b>"
        lines.append(f"  {sign} <b>{esc(r['name'])}</b> {px} {body}{star}")
    return "\n".join(lines)


def section_key_stocks(win):
    """주요 종목(SK하이닉스·삼성전자) — 시황 바로 다음.

    본장 시세(전일比) + 괄호는 본장 마감 후 변동만(after). 장중이면 항상 0%.
    """
    rows = win.get("key_stocks") or []
    if not rows:
        return ""
    lines = ["\n📌 <b>주요 종목</b> <i>(after)</i>"]
    for r in rows:
        if r.get("chg_pct") is None:
            lines.append(f"  {esc(r['name'])} — 데이터 없음")
            continue
        star = "★" * r.get("stars", 1 if r["significant"] else 0)
        sign = "🔺" if r["chg_pct"] > 0 else ("🔽" if r["chg_pct"] < 0 else "▪️")
        lab = esc(r.get("chg_label") or "")
        if r.get("after_pct") is not None:
            body = f"<b>{lab}</b> ({_fmt_after(r['after_pct'])})"
        elif r.get("proxy"):
            body = f"<b>{lab}</b> <i>(perp)</i>"
        else:
            body = f"<b>{lab}</b>"
        lines.append(f"  {sign} <b>{esc(r['name'])}</b> "
                     f"{_fmt_px(r['end_px'], r.get('decimals', 0))} {body}{star}")
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
    lines = ["\n🗓 <b>발표 완료</b>"]
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
    lines = ["\n⏳ <b>발표 예정</b>"]
    for a in ahead:
        v = f" {esc(a['value'])}" if a.get("value") else ""
        flag, _, nm = (a["label"] or "").partition(" ")
        lines.append(f"  {flag} {a['when']:%m-%d %H:%M} <b>{esc(nm)}</b>{v}")
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
        if r.get("주도일수") is not None:  # 연속성 태그 — 반짝 vs 지속 주도 구분 (09-15)
            seg += f" · <i>주도 {r['주도일수']:.0f}/20일</i>"
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

    # 우측정렬(09-15): 열별 최대 폭에 맞춰 숫자 왼쪽을 숫자폭 공백(U+2007)으로 채운다
    # — 비례 폰트에서도 숫자와 같은 폭이라 자릿수가 달라도 오른쪽 끝이 맞는다.
    rows_v = [[("-" if (acc or {}).get(k) is None else fmt(acc[k])) for _, acc in groups]
              for k in KEYS]
    w = [max(len(r[i]) for r in rows_v) for i in range(len(groups))]
    out = []
    for k, vals in zip(KEYS, rows_v):
        if k == "비차익":                  # 투자자별과 별개 집계(프로그램) — 선으로 분리
            out.append("   ────────────")
        cells = " / ".join(" " * (w[i] - len(c)) + c for i, c in enumerate(vals))
        out.append(f"  · {esc(_justify_label(k, 4))} <b>{esc(cells)}</b>")
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
        lines.append(f"  · <b>{esc(_justify_label(lab, 3))}</b> {amt:,.0f}조{_pct_tag(d)}")
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
        tbl, unit = _flow_table(groups)
        asof2 = f" · {esc(asof)}" if asof else ""
        markets = "/".join(t for t, _ in FLOW_GROUPS)
        lines.append(f"\n💵 <b>순매수</b> <i>({esc(markets)} · {esc(unit)}{asof2})</i>")
        lines.append(tbl)

        # ⚡ 별 사다리는 시황과 동일: z 1.2★ / 1.5★★ / 2.0★★★ (09-15 사용자)
        note = []
        for key, name in (("foreign", "외국인"), ("inst", "기관"),
                          ("indiv", "개인"), ("etc", "기타법인"),
                          ("nonarb", "비차익")):
            c = cmp.get(key)
            z = (c or {}).get("z")
            if z is None or abs(z) < 1.2:
                continue
            stars = "★" * (3 if abs(z) >= 2.0 else 2 if abs(z) >= 1.5 else 1)
            verb = "대량 순매수" if c["today"] > c.get("avg_long", 0) else "대량 순매도"
            note.append(f"  ⚡ {esc(name)} {verb}{stars}")
        if note:
            lines += note                 # 비차익 바로 아래 붙임 (09-16 사용자)

    return "\n".join(lines)


def build(win, news=None, us_sectors=None, kr_impact=None, leaders=None,
          flows=None, flows_cmp=None, kr_upjong=None, kr_themes=None, kr_when=None,
          us_leaders=None, events=None, nxt_pm=None, us_movers=None, footer=None):
    parts = [header(win)]
    for s in (section_summary(win, flows, flows_cmp, events, kr_upjong,
                              us_movers, kr_impact),
              section_events_ahead(events),       # 발표 예정을 맨 위로 (09-15 사용자)
              section_events_done(events),
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
              ):
        if s:
            parts.append(s)
    if footer:
        parts.append(f"\n<i>{esc(footer)}</i>")
    return "\n".join(parts)
