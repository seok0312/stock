# -*- coding: utf-8 -*-
"""신생 테마 자동 탐지 v1 — '탈모 테마' 같은 새 재료의 형성을 감지한다.

원리(사용자 논의, 09-11): 테마의 탄생 = 뉴스(공통 재료) × 동반 급등(상승률+거래대금).
  1) 오늘 급등주(등락률 5%↑ & 거래대금 300억↑) 추출 — closebet 스냅샷
  2) 뉴스 풀(news._fetch_pool: 네이버 증권뉴스+스탁허브)에서 급등주가 2개 이상
     함께 등장하는 기사 → 종목들을 같은 클러스터로 묶음 (스탁허브는 티커 태깅 활용)
  3) 클러스터 기사 제목들에서 공통 키워드 추출 (불용어·종목명 제외)
  4) 키움 테마 목록(ka90001, 100개 명명 테마)과 대조 — 겹치면 '기존', 없으면 '🆕 신생 후보'
  5) 결과는 LOG 채널(개인)로만 발송 — 오탐 관찰 후 공개 브리핑 편입 여부 결정

크론(UTC): 0 2,5 * * 1-5  (KST 11:00, 14:00)
실행: python3 theme_scout.py --scan [--send]
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for _p in (os.path.abspath(os.path.join(HERE, "..")), HERE):
    if os.path.isdir(os.path.join(_p, "closebet")) and _p not in sys.path:
        sys.path.insert(0, _p)

CHG_MIN = 5.0
AMT_MIN = 300e8            # 300억
MAX_GAINERS = 60

# 키워드 추출에서 버릴 일반어 (기사 제목 단골)
_STOP = {"특징주", "급등", "급락", "상한가", "장중", "마감", "주가", "코스피", "코스닥",
         "이유", "속보", "오늘", "종목", "상승", "하락", "강세", "약세", "돌파", "출발",
         "전망", "분석", "관련주", "테마주", "수혜주", "장초반", "개장", "증시", "시장",
         "억원", "투자", "매수", "매도", "외국인", "기관", "개인", "연속", "신고가"}


# 시황 랩업 기사 판별 — 이런 기사는 무관한 급등주 여럿을 한 문장에 담아
# 클러스터를 한 덩어리로 붙여버린다 (실측: '네 마녀의 날…7천피 지켰다')
_WRAP = ("코스피", "코스닥", "증시", "지수", "마감", "마녀", "특징주 요약", "장 초반",
         "시황", "인기 검색", "데이터랩")


def _is_wrap(title: str, n_hits: int) -> bool:
    return n_hits >= 4 or any(w in title for w in _WRAP)


def _tokens(title: str):
    """제목에서 한글 2~8자 토큰 추출. 조사·어미 조각(…의/…다)은 버린다."""
    out = []
    for t in re.findall(r"[가-힣]{2,8}", title):
        if t in _STOP or t.endswith("다") or (len(t) > 2 and t.endswith("의")):
            continue
        out.append(t)
    return out


def scan(now=None):
    now = now or datetime.now(KST)
    from notify import load_env
    load_env(os.path.join(HERE, ".env"), "/opt/upbit_bot/.env")
    from closebet import market as cbm
    import news

    snap = cbm.get_snapshot("KRX")
    g = snap[(snap["등락률"] >= CHG_MIN) & (snap["거래대금"] >= AMT_MIN)]
    g = g.sort_values("거래대금", ascending=False).head(MAX_GAINERS)
    gainers = {r["종목명"]: {"code": code, "chg": r["등락률"], "amt": r["거래대금"] / 1e8}
               for code, r in g.iterrows()}
    if len(gainers) < 2:
        return []
    code2name = {v["code"]: k for k, v in gainers.items()}

    floor = now - timedelta(hours=24)
    arts = []                              # [(hit_names(set), title)] — 공용 풀(거시+스탁허브)
    for it in news._fetch_pool():
        p = it.get("published")
        if p is None or p < floor:
            continue
        hits = {n for n in gainers if n in it["title"]}
        hits |= {code2name[t] for t in it.get("tickers") or () if t in code2name}
        if hits:
            arts.append((hits, it["title"]))

    # 종목별 뉴스 보강 — 공용 풀은 거시 기사 위주라 개별종목 기사가 없다 (실측 0건).
    # 같은 기사가 두 종목 피드에 뜨면 동시등장, 각자 기사라도 제목 키워드가 겹치면
    # 같은 재료다(탈모 사례: JW신약 기사와 현대약품 기사가 서로 다른 기사여도 '탈모' 공유).
    import time as _t

    import requests as _rq
    stock_tokens = {}                      # 종목명 -> {토큰}
    art_map = {}                           # (oid, aid) -> {종목명}
    art_title = {}
    for name, info in gainers.items():
        try:
            r = _rq.get(f"https://m.stock.naver.com/api/news/stock/{info['code']}",
                        params={"pageSize": 20, "page": 1}, headers=news.UA,
                        timeout=12).json()
        except Exception:
            continue
        toks = set()
        for cl in r if isinstance(r, list) else []:
            for it in cl.get("items") or []:
                try:
                    p = datetime.strptime(it.get("datetime") or "",
                                          "%Y%m%d%H%M").replace(tzinfo=KST)
                except ValueError:
                    continue
                if p < floor:
                    continue
                title = it.get("title") or ""
                key = (it.get("officeId"), it.get("articleId"))
                art_map.setdefault(key, set()).add(name)
                art_title.setdefault(key, title)
                # 키워드는 제목에 종목명이 직접 등장하는 기사에서만 뽑는다 —
                # 종목 피드엔 그 종목이 언급 안 된 시황·업황 기사가 섞여 들어와서
                # (실측: 주성엔지 피드의 유가 기사) 키워드가 오염된다.
                if name in title:
                    toks |= set(_tokens(title))
        if toks:
            stock_tokens[name] = toks
        _t.sleep(0.1)

    # 판별력 있는 키워드만 간선으로: 그날 2~4개 종목이 공유하는 토큰
    # ('반도체'처럼 급등주 절반에 등장하는 단어는 기존 대세이지 신생 재료가 아니다)
    df = Counter(tok for toks in stock_tokens.values() for tok in toks)
    disc = {tok for tok, c in df.items() if 2 <= c <= 4}

    parent = {n: n for n in gainers}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    edge_kw = {}                           # frozenset({a,b}) -> {키워드}
    for hits, title in arts:               # 공용 풀 동시등장 (시황 랩업 제외)
        if _is_wrap(title, len(hits)):
            continue
        hs = sorted(hits)
        for other in hs[1:]:
            union(hs[0], other)
    for key, ns in art_map.items():        # 같은 기사가 여러 종목 피드에
        title = art_title.get(key, "")
        if _is_wrap(title, len(ns)):
            continue
        hs = sorted(ns)
        for other in hs[1:]:
            union(hs[0], other)
        if len(ns) >= 2:
            arts.append((set(ns), title))
    names_l = sorted(stock_tokens)
    for i, a in enumerate(names_l):        # 키워드 겹침
        for b in names_l[i + 1:]:
            shared = stock_tokens[a] & stock_tokens[b] & disc
            # 단어 1개 겹침은 우연('출렁' 같은 표현 조각)이 많다 — 2개 이상만 간선.
            # union-find 는 간선 하나로 클러스터가 통째로 붙으니 정밀도를 우선한다.
            if len(shared) >= 2:
                union(a, b)
                edge_kw.setdefault(frozenset((a, b)), set()).update(shared)

    groups = {}
    for n in gainers:
        groups.setdefault(find(n), set()).add(n)
    clusters = [ns for ns in groups.values() if len(ns) >= 2]
    if not clusters:
        return []

    # 키움 명명 테마 목록 — 신생 여부 판단용
    theme_names = []
    try:
        import kr_sectors as krs
        kc = krs._kc()
        d, _ = kc.request("ka90001", {"qry_tp": "0", "date_tp": "1",
                                      "flu_pl_amt_tp": "1", "stex_tp": "3"},
                          endpoint="/api/dostk/thme")
        theme_names = [(t.get("thema_nm") or "") for t in d.get("thema_grp") or []]
    except Exception:
        pass

    out = []
    for ns in clusters:
        titles = [t for hits, t in arts if hits & ns]
        cnt = Counter(tok for t in titles for tok in _tokens(t)
                      if not any(tok in n or n in tok for n in ns))
        for pair, kws_ in edge_kw.items():          # 키워드 간선이 근거면 그 단어가 곧 재료
            if pair & ns:
                for k in kws_:
                    cnt[k] += 2
        kws = [w for w, c in cnt.most_common(6) if c >= 2][:3]
        known = [tn for tn in theme_names
                 if any(k in tn or tn in k for k in kws)]
        members = sorted(ns, key=lambda n: -gainers[n]["amt"])
        out.append({
            "stocks": [(n, gainers[n]["chg"], gainers[n]["amt"]) for n in members],
            "keywords": kws, "known_theme": known[:2],
            "n_articles": len(titles), "sample": titles[0][:60] if titles else "",
        })
    out.sort(key=lambda c: -sum(a for _, _, a in c["stocks"]))
    return out


def render_msg(clusters) -> str:
    lines = [f"🧪 테마 스캔 {datetime.now(KST):%m/%d %H:%M} — 동반급등×뉴스 클러스터 {len(clusters)}개"]
    for c in clusters:
        tag = ("기존: " + ", ".join(c["known_theme"])) if c["known_theme"] else "🆕 신생 후보"
        kw = " ".join("#" + k for k in c["keywords"]) or "#키워드미상"
        lines.append(f"\n{kw} · {tag} · 기사 {c['n_articles']}건")
        for n, chg, amt in c["stocks"][:5]:
            lines.append(f"  · {n} +{chg:.1f}% / {amt:,.0f}억")
        if c["sample"]:
            lines.append(f"  「{c['sample']}」")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cs = scan()
    if not cs:
        print(f"[{datetime.now(KST):%m-%d %H:%M}] 클러스터 없음")
        raise SystemExit(0)
    msg = render_msg(cs)
    print(msg)
    if "--send" in sys.argv:
        from notify import esc, send
        chat = os.environ.get("LOG_CHAT_ID")
        if chat:
            send(esc(msg), chat_id=chat)
            print("→ LOG 채널 발송")
