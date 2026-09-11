# -*- coding: utf-8 -*-
"""미국 개별 종목 '독주' 감지 — 섹터/지수와 따로 노는 대형주 + 원인 뉴스 + 한국 관련주.

배경(사용자, 09-11): 미국장 전반 하락 속 AAPL 단독 강세(아이폰 출시) 같은 신호는
섹터 단위 감지(us_sectors)로는 안 잡힌다. 개별주 퍼프로 지수 대비 격차를 재고,
뉴스 풀에서 원인을 붙이고, 한국 밸류체인 종목으로 연결한다.

관련주 선정 주의(사용자): 어제까지 주목 못 받던 체인은 '기존 거래대금'이 낮다 —
거래대금으로 거르지 말고 시총 있는 대표 체인을 고정 매핑으로 둔다. 유동성 판단은
당일 장중에 실제 거래대금으로 하면 된다.
"""
from __future__ import annotations

import time

# (심볼, 한글명, 한국 관련주[(이름, 코드)]) — 시총 있는 대표 체인 위주
WATCH = [
    ("AAPL", "애플", [("LG이노텍", "011070"), ("LG디스플레이", "034220"),
                      ("비에이치", "090460")]),
    ("NVDA", "엔비디아", [("SK하이닉스", "000660"), ("삼성전자", "005930"),
                          ("한미반도체", "042700")]),
    ("AVGO", "브로드컴", [("SK하이닉스", "000660"), ("이수페타시스", "007660")]),
    ("MU",   "마이크론", [("SK하이닉스", "000660"), ("삼성전자", "005930")]),
    ("TSLA", "테슬라", [("LG에너지솔루션", "373220"), ("에코프로비엠", "247540"),
                        ("엘앤에프", "066970")]),
    ("MSFT", "마이크로소프트", []),
    ("GOOGL", "구글", []),
    ("META", "메타", []),
    ("AMZN", "아마존", []),
    ("ORCL", "오라클", []),
    ("NFLX", "넷플릭스", [("스튜디오드래곤", "253450"), ("콘텐트리중앙", "036420")]),
    ("LLY",  "일라이릴리", [("펩트론", "087010"), ("한미약품", "128940")]),
]

ABS_MIN = 1.5    # 자기 변동 최소 (%)
REL_MIN = 2.0    # 지수(QQQ) 대비 격차 최소 (%p)


def movers(start, end, abs_min: float = ABS_MIN, rel_min: float = REL_MIN, top: int = 4):
    """[{sym, kr, pct, base, gap, related, news}] — 지수 대비 격차 큰 순."""
    import quotes
    ex = quotes.exchange()

    def wchg(sym):
        p0 = quotes._close_at(sym, start, ex)
        p1 = quotes._close_at(sym, end, ex)
        return (p1 / p0 - 1) * 100 if (p0 and p1) else None

    base = wchg("QQQ/USDT:USDT")
    if base is None:
        return []
    out = []
    for sym, kr, rel in WATCH:
        pct = wchg(f"{sym}/USDT:USDT")
        if pct is None:
            continue
        gap = pct - base
        if abs(pct) >= abs_min and abs(gap) >= rel_min:
            item = {"sym": sym, "kr": kr, "pct": pct, "base": base, "gap": gap,
                    "related": rel, "news": None}
            try:
                import news
                hits = news._pool_match(keywords=(kr, sym), tickers=(sym,),
                                        limit=3, start=start, end=end)
                for h in hits:             # 시황 랩업(제목에 종목이 스치듯 언급)은 원인이 아니다
                    if not any(w in h["title"] for w in ("시황", "마감", "요약", "코스피")):
                        item["news"] = h
                        break
            except Exception:
                pass
            out.append(item)
        time.sleep(0.05)
    out.sort(key=lambda x: -abs(x["gap"]))
    return out[:top]


if __name__ == "__main__":
    import sys
    from datetime import datetime, timedelta, timezone
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, ".")
    KST = timezone(timedelta(hours=9))
    end = datetime.now(KST)
    start = (end - timedelta(days=1)).replace(hour=15, minute=30)
    for m in movers(start, end):
        print(f"{m['sym']}({m['kr']}) {m['pct']:+.2f}%  지수 {m['base']:+.2f}%  격차 {m['gap']:+.1f}%p")
        if m["news"]:
            print("   ", m["news"]["title"][:70])
        if m["related"]:
            print("   관련:", ", ".join(n for n, _ in m["related"]))
