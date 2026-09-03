# -*- coding: utf-8 -*-
"""미국 섹터 강약(전일) + 한국시장 파급 매핑.

finviz map(t=sec_all)은 데이터를 JS로 지연 로딩해 HTML 파싱이 불가능하다.
(섹터명이 HTML에 0회 등장, /api/map.ashx 는 404)
→ 동일 정보를 주는 SPDR 섹터 ETF + 한국에 중요한 테마 ETF의 전일 등락률로 대체한다.
"""
from __future__ import annotations

import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

# GICS 11개 섹터 (SPDR)
GICS = [
    ("기술",       "XLK"), ("커뮤니케이션", "XLC"), ("경기소비재", "XLY"),
    ("필수소비재",  "XLP"), ("에너지",     "XLE"), ("금융",      "XLF"),
    ("헬스케어",    "XLV"), ("산업재",     "XLI"), ("소재",      "XLB"),
    ("유틸리티",    "XLU"), ("리츠",       "XLRE"),
]
# 한국시장 연동성이 특히 큰 테마
THEMES = [
    ("반도체",   "SMH"), ("2차전지",  "LIT"), ("방산",   "ITA"),
    ("바이오",   "XBI"), ("태양광",   "TAN"), ("조선/해운", "BOAT"),
]


# finviz 실시간 종목 등락률 (5,916개) — 섹터 주도주 산출에 쓴다
FINVIZ_PERF = "https://finviz.com/api/map_perf?t=sec_all&st=d1"
FINVIZ_GROUPS = "https://finviz.com/api/map_perf_groups?t=sec_all&st=d1"

# finviz 는 종목→섹터 매핑을 노출하지 않는다(/api/map*.json 전부 404).
# 그래서 섹터별 대표 종목을 정의해두고, finviz 등락률로 그중 상위를 뽑는다.
SECTOR_MEMBERS = {
    "기술":       ["AAPL","MSFT","NVDA","AVGO","ORCL","CRM","AMD","ADBE","CSCO","ACN","TXN","NOW"],
    "커뮤니케이션": ["GOOGL","META","NFLX","DIS","CMCSA","TMUS","VZ","T","EA"],
    "경기소비재":  ["AMZN","TSLA","HD","MCD","NKE","SBUX","LOW","BKNG","TJX","GM","F"],
    "필수소비재":  ["WMT","PG","KO","PEP","COST","PM","MO","MDLZ","CL","KMB"],
    "에너지":     ["XOM","CVX","COP","SLB","EOG","PSX","MPC","VLO","OXY","HAL","DVN"],
    "금융":       ["JPM","V","MA","BAC","WFC","GS","MS","SPGI","AXP","C","BLK"],
    "헬스케어":   ["LLY","UNH","JNJ","ABBV","MRK","TMO","ABT","PFE","AMGN","BMY","GILD"],
    "산업재":     ["GE","CAT","RTX","UNP","HON","BA","LMT","UPS","DE","ETN","EMR"],
    "소재":       ["LIN","SHW","APD","ECL","FCX","NEM","DOW","NUE","VMC","MLM"],
    "유틸리티":   ["NEE","SO","DUK","CEG","AEP","SRE","D","EXC","XEL","ED"],
    "리츠":       ["PLD","AMT","EQIX","WELL","SPG","O","CCI","PSA","DLR","VICI"],
    "반도체":     ["NVDA","TSM","AVGO","AMD","ASML","AMAT","LRCX","KLAC","MU","INTC","ADI","QCOM"],
    "2차전지":    ["TSLA","ALB","ENPH","PLUG","FSLR","QS"],
    "방산":       ["RTX","LMT","NOC","GD","BA","LHX","HII","TDG","LDOS"],
    "바이오":     ["VRTX","REGN","MRNA","BIIB","ALNY","INCY","BMRN","SRPT"],
    "태양광":     ["FSLR","ENPH","SEDG","RUN","NXT","ARRY"],
    "조선/해운":  ["ZIM","MATX","KEX","GNK","SBLK"],
}


def fetch_finviz_perf():
    """finviz 종목별 당일 등락률 {티커: %}. 실패 시 None."""
    try:
        r = requests.get(FINVIZ_PERF, headers=UA, timeout=25)
        if r.status_code != 200:
            return None
        n = r.json().get("nodes")
        return n if isinstance(n, dict) and n else None
    except Exception:
        return None


def sector_leaders(sector: str, perf: dict, top: int = 3):
    """섹터 대표 종목 중 finviz 등락률 상위. [{ticker, change_pct}]"""
    if not perf:
        return []
    rows = [{"ticker": t, "change_pct": perf[t]}
            for t in SECTOR_MEMBERS.get(sector, []) if t in perf]
    rows.sort(key=lambda x: x["change_pct"], reverse=True)
    return rows[:top]


def _prev_change(sym: str):
    """전일(최근 거래일) 등락률 %. 실패 시 None."""
    try:
        r = requests.get(CHART.format(sym=sym), headers=UA,
                         params={"range": "10d", "interval": "1d"}, timeout=20)
        if r.status_code != 200:
            return None, None
        res = r.json()["chart"]["result"][0]
        cl = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        if len(cl) < 2:
            return None, None
        return (cl[-1] / cl[-2] - 1) * 100, cl[-1]
    except Exception:
        return None, None


def fetch_us_sectors(include_themes: bool = True):
    """[{sector, ticker, change_pct, price, kind}] 등락률 내림차순."""
    out = []
    for kind, group in (("sector", GICS), ("theme", THEMES if include_themes else [])):
        for name, sym in group:
            chg, px = _prev_change(sym)
            if chg is None:
                continue
            out.append({"sector": name, "ticker": sym, "change_pct": round(chg, 2),
                        "price": px, "kind": kind})
            time.sleep(0.05)
    return sorted(out, key=lambda x: x["change_pct"], reverse=True)


# 미국 섹터/테마 → 한국 업종 + 대표 종목 (코드는 FDR로 검증됨)
US_TO_KR = {
    "반도체":    ("반도체", [("삼성전자","005930"),("SK하이닉스","000660"),
                          ("한미반도체","042700"),("이오테크닉스","039030")]),
    "기술":      ("IT/플랫폼", [("네이버","035420"),("카카오","035720"),
                             ("삼성SDS","018260")]),
    "2차전지":   ("2차전지", [("LG에너지솔루션","373220"),("삼성SDI","006400"),
                           ("POSCO홀딩스","005490"),("에코프로비엠","247540")]),
    "방산":      ("방산", [("한화에어로스페이스","012450"),("현대로템","064350"),
                        ("LIG넥스원","079550"),("한국항공우주","047810")]),
    "바이오":    ("제약/바이오", [("삼성바이오로직스","207940"),("셀트리온","068270"),
                             ("알테오젠","196170")]),
    "에너지":    ("정유/화학", [("S-Oil","010950"),("SK이노베이션","096770"),
                            ("GS","078930"),("롯데케미칼","011170")]),
    "조선/해운": ("조선/해운", [("HD한국조선해양","009540"),("한화오션","042660"),
                            ("삼성중공업","010140"),("HMM","011200")]),
    "금융":      ("은행/증권", [("KB금융","105560"),("신한지주","055550"),
                             ("삼성증권","016360")]),
    "산업재":    ("기계/건설", [("현대건설","000720"),("두산에너빌리티","034020"),
                            ("HD현대일렉트릭","267260")]),
    "소재":      ("철강/소재", [("POSCO홀딩스","005490"),("고려아연","010130")]),
    "경기소비재": ("자동차/유통", [("현대차","005380"),("기아","000270")]),
    "태양광":    ("태양광", [("한화솔루션","009830"),("OCI홀딩스","010060")]),
    "헬스케어":  ("제약/바이오", [("삼성바이오로직스","207940"),("유한양행","000100")]),
}

# 원자재/지수 변동 → 한국 업종 (시황 5종에서 직접 유도)
QUOTE_TO_KR = {
    "오일":   [("정유/화학", "up",   [("S-Oil","010950"),("SK이노베이션","096770")], "유가 상승 → 정제마진·재고이익 개선"),
             ("항공/운송", "down", [("대한항공","003490"),("진에어","272450")],   "유가 상승 → 연료비 부담")],
    "금":    [("금 관련",   "up",   [("고려아연","010130"),("LS ELECTRIC","010120")], "금값 상승 → 귀금속·제련 수혜")],
    "나스닥": [("반도체/IT", "up",   [("삼성전자","005930"),("SK하이닉스","000660")], "나스닥 강세 → 기술주 동조")],
    "비트코인":[("가상자산",  "up",   [("두나무 관련주","")], "BTC 강세 → 거래소/블록체인 테마")],
}


def kr_impact(us_sectors, quote_rows, top_n: int = 5):
    """미국 섹터 상위 + 시황 유의미 변동 → 한국 파급 예상 리스트."""
    out, seen = [], set()

    # 1) 미국 섹터/테마 상위에서 유도
    for s in us_sectors:
        if s["change_pct"] < 1.0:      # 1% 미만은 신호로 보지 않음
            continue
        hit = US_TO_KR.get(s["sector"])
        if not hit or hit[0] in seen:
            continue
        kr, tickers = hit
        seen.add(kr)
        out.append({"kr_sector": kr,
                    "driver": f"미국 {s['sector']}({s['ticker']}) {s['change_pct']:+.2f}%",
                    "tickers": [{"name": n, "code": c} for n, c in tickers],
                    "note": ""})

    # 2) 시황 유의미 변동에서 직접 유도
    for r in quote_rows:
        if not r["significant"]:
            continue
        for kr, direction, tickers, note in QUOTE_TO_KR.get(r["name"], []):
            good = (direction == "up" and r["chg_pct"] > 0) or \
                   (direction == "down" and r["chg_pct"] > 0)
            if not good or kr in seen:
                continue
            seen.add(kr)
            out.append({"kr_sector": kr,
                        "driver": f"{r['name']} {r['chg_pct']:+.2f}%",
                        "tickers": [{"name": n, "code": c} for n, c in tickers if c],
                        "note": note})
    return out[:top_n]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ss = fetch_us_sectors()
    print(f"미국 섹터/테마 {len(ss)}개 (전일 등락률)\n")
    for s in ss:
        tag = "테마" if s["kind"] == "theme" else "섹터"
        print(f"   [{tag}] {s['sector']:<10}({s['ticker']:<5}) {s['change_pct']:>+7.2f}%")
