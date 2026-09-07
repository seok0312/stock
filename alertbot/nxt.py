# -*- coding: utf-8 -*-
"""NXT 프리마켓(08:00~08:50) 주도주·주도 섹터 — 08:50 알림용.

09시 개장 전에 유일하게 실체결이 있는 곳이 NXT 프리마켓이다. 여기서 어떤
종목·섹터에 돈이 몰리는지가 정규장 개장 방향의 선행 신호가 된다(사용자 전략).

소스:
  네이버 NXT 거래상위 nxt_sise_quant.naver — 종목/현재가/등락률/거래대금(백만원)
  섹터 매핑은 키움 ka20002 구성종목을 파일로 캐시(data/sector_map.json, 주 1회 갱신)
  — 매 슬롯마다 65개 업종을 다시 받으면 호출 낭비라서.

주의: 이 페이지의 등락률은 '전일 종가 대비'이고, 거래대금은 NXT 체결분만이다.
08:50 이후(메인마켓 중)에 호출하면 프리마켓이 아니라 당일 NXT 누적이 잡힌다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
MAP_PATH = os.path.join(HERE, "data", "sector_map.json")
MAP_MAX_AGE_DAYS = 7

QUANT = "https://finance.naver.com/sise/nxt_sise_quant.naver"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
      "Referer": "https://finance.naver.com/sise/"}


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("+", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def fetch_quant(pages: int = 1) -> list:
    """NXT 거래상위 [{code, name, price, chg_pct, amt_eok}] — 거래대금 내림차순."""
    out = []
    for p in range(1, pages + 1):
        try:
            r = requests.get(QUANT, headers=UA, params={"page": p}, timeout=20)
            r.encoding = "euc-kr"
        except Exception:
            break
        rows = re.findall(
            r'<a href="/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>(.*?)</tr>',
            r.text, re.S)
        for code, name, body in rows:
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]
            # 순서: 현재가, 전일비, 등락률, 거래량, 거래대금(백만), 매수호가, ...
            if len(cells) < 5:
                continue
            out.append({"code": code, "name": name.strip(),
                        "price": _num(cells[0]), "chg_pct": _num(cells[2]),
                        "amt_eok": (_num(cells[4]) or 0) / 100})
    return out


# ── 종목 → 섹터 캐시 ────────────────────────────────────────────
def build_sector_map(verbose: bool = True) -> dict:
    """키움 전 업종 구성종목 → {종목코드6: 섹터명}. 65콜이라 캐시로만 쓴다."""
    import kr_sectors as krs
    try:
        kc = krs._kc()
    except Exception:
        return {}
    out = {}
    for inds, mrkt in (("001", "0"), ("101", "1")):
        try:
            d, _ = kc.request("ka20003", {"inds_cd": inds}, endpoint="/api/dostk/sect")
        except Exception:
            continue
        for r in d.get("all_inds_idex") or []:
            nm = (r.get("stk_nm") or "").strip()
            if any(a in nm for a in krs._AGG):
                continue
            try:
                d2, _ = kc.request("ka20002",
                                   {"inds_cd": r.get("stk_cd"), "mrkt_tp": mrkt,
                                    "stex_tp": "3"}, endpoint="/api/dostk/sect")
            except Exception:
                continue
            n = 0
            for s in d2.get("inds_stkpc") or []:
                code = str(s.get("stk_cd") or "")[:6]
                if code and code not in out:
                    out[code] = nm
                    n += 1
            if verbose:
                print(f"  {nm}: {n}종목")
            time.sleep(0.1)
    if out:
        os.makedirs(os.path.dirname(MAP_PATH), exist_ok=True)
        with open(MAP_PATH + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"built_at": datetime.now(KST).isoformat(timespec="seconds"),
                       "map": out}, f, ensure_ascii=False)
        os.replace(MAP_PATH + ".tmp", MAP_PATH)
    return out


def load_sector_map(refresh_if_stale: bool = False) -> dict:
    try:
        with open(MAP_PATH, encoding="utf-8") as f:
            doc = json.load(f)
        built = datetime.fromisoformat(doc["built_at"])
        if refresh_if_stale and datetime.now(KST) - built > timedelta(days=MAP_MAX_AGE_DAYS):
            return build_sector_map(verbose=False) or doc["map"]
        return doc["map"]
    except Exception:
        return {}


# ── 프리마켓 요약 ────────────────────────────────────────────────
def premarket(top_stocks: int = 6, top_sectors: int = 3, min_amt_eok: float = 5.0):
    """{'stocks': [...], 'sectors': [...], 'total_eok': float} 또는 None.

    주도주 = NXT 거래대금 상위 중 |등락률| 기준 정렬이 아니라 **거래대금 순**
    (프리마켓은 절대 금액이 작아 등락률만 보면 노이즈가 위로 온다).
    주도 섹터 = 종목을 섹터로 묶어 상승에너지(Σ max(0,등락률)×거래대금) 순.
    """
    rows = [r for r in fetch_quant() if (r["amt_eok"] or 0) >= min_amt_eok
            and r["chg_pct"] is not None]
    if not rows:
        return None
    smap = load_sector_map()
    for r in rows:
        r["sector"] = smap.get(r["code"])

    by_sec = {}
    for r in rows:
        if not r["sector"]:
            continue
        d = by_sec.setdefault(r["sector"], {"name": r["sector"], "energy": 0.0,
                                            "amt_eok": 0.0, "leaders": []})
        d["amt_eok"] += r["amt_eok"]
        d["energy"] += max(0.0, r["chg_pct"]) * r["amt_eok"]
        d["leaders"].append(r)
    sectors = sorted(by_sec.values(), key=lambda x: x["energy"], reverse=True)[:top_sectors]
    for s in sectors:
        s["leaders"] = sorted(s["leaders"], key=lambda x: x["amt_eok"], reverse=True)[:2]
        # 섹터 대표 등락률 = 거래대금 가중
        w = sum(x["amt_eok"] for x in s["leaders"]) or 1
        s["chg_pct"] = sum(x["chg_pct"] * x["amt_eok"] for x in s["leaders"]) / w

    return {"stocks": rows[:top_stocks], "sectors": sectors,
            "total_eok": sum(r["amt_eok"] for r in rows)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "--build-map":
        m = build_sector_map()
        print(f"섹터 맵 {len(m)}종목 저장 → {MAP_PATH}")
        raise SystemExit(0)
    pm = premarket()
    if not pm:
        print("NXT 데이터 없음")
        raise SystemExit(1)
    print(f"NXT 거래대금 상위 (총 {pm['total_eok']:,.0f}억)")
    for r in pm["stocks"]:
        print(f"  {r['name']:<12} {r['chg_pct']:+6.2f}%  {r['amt_eok']:>8,.0f}억  {r.get('sector') or '-'}")
    print("주도 섹터(상승에너지)")
    for s in pm["sectors"]:
        print(f"  {s['name']:<12} {s['chg_pct']:+6.2f}%  {s['amt_eok']:>8,.0f}억  "
              + ", ".join(x["name"] for x in s["leaders"]))
