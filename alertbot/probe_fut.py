# -*- coding: utf-8 -*-
"""네이버 선물 투자자별 순매수가 '언제 확정되는지'를 실측하는 일회성 로거.

배경:
  2026-09-03 관측에서 값이 두 번 달랐다.
    ~16:05, ~16:40  개인 -1,889 / 외국인 +6,658 / 기관 -3,557   (잠정으로 추정)
    ~17:35          개인 -3,768 / 외국인 +8,076 / 기관 -3,229   (0780 확정치와 일치)
  사용자 화면의 계약수는 16:07에 이미 확정값이었다. 즉 화면(계약)과
  API(억원)의 확정 시점이 다를 수 있다. 마감 후 알림을 몇 시에 보낼지가
  여기에 달려 있어 추정 대신 측정한다.

사용: 크론에서 5분마다 호출. 값이 바뀌지 않기 시작하는 시각이 확정 시각이다.
  */5 6-8 * * 1-5  /usr/bin/python3 /opt/alertbot/probe_fut.py >> /opt/alertbot/data/fut_probe.log 2>&1
확인이 끝나면 이 크론 줄과 파일을 지운다.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def get(url):
    try:
        r = requests.get(url, headers=UA, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def row(d):
    if not d:
        return "조회실패"
    return (f"개인 {d.get('personalValue','?'):>8} / "
            f"외국인 {d.get('foreignValue','?'):>8} / "
            f"기관 {d.get('institutionalValue','?'):>8}")


def kiwoom_kospi():
    """키움 코스피 투자자별을 거래소별로. 정산 시간대에 값이 재배분되는지 본다.

    2026-09-03 18:13 관측: 17:55 에 기관 -2,149 / 기타법인 +15,936 이던 KRX 값이
    18:13 에 기관 +9,147 / 기타법인 -6 으로 바뀌었다(합은 보존). 같은 시각 조회한
    09-02·09-01·08-28 은 여전히 기타법인 +1.6조라, 당일 데이터가 저녁 정산 중
    불안정한 상태였을 가능성이 크다. 언제 안정되는지 실측한다.
    """
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for _p in (here, os.path.abspath(os.path.join(here, ".."))):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    try:
        import notify
        notify.load_env(os.path.join(here, ".env"),
                        os.path.abspath(os.path.join(here, "..", ".env")),
                        "/opt/upbit_bot/.env")
        from closebet.kiwoom import KiwoomClient
        kc = KiwoomClient()
    except Exception as e:
        return f"키움 초기화 실패: {type(e).__name__}"
    dt = datetime.now(KST).strftime("%Y%m%d")
    out = []
    for stex, lab in (("1", "KRX"), ("2", "NXT")):
        try:
            d, _ = kc.request("ka10051", {"mrkt_tp": "0", "amt_qty_tp": "0",
                                          "base_dt": dt, "stex_tp": stex},
                              endpoint="/api/dostk/sect")
            r = (d.get("inds_netprps") or [{}])[0]
            g = lambda k: float(r.get(k) or 0)
            out.append(f"{lab} 개인 {g('ind_invsr') or g('ind_netprps'):+,.0f} "
                       f"외국인 {g('frgnr_netprps'):+,.0f} 기관 {g('orgn_netprps'):+,.0f} "
                       f"기타법인 {g('etc_corp_netprps'):+,.0f}")
        except Exception as e:
            out.append(f"{lab} 실패({type(e).__name__})")
    return "  |  ".join(out)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(KST)
    t = get("https://m.stock.naver.com/api/index/FUT/trend")
    ig = get("https://m.stock.naver.com/api/index/FUT/integration") or {}
    d = ig.get("dealTrendInfo")
    info = {x.get("key"): x.get("value") for x in (ig.get("totalInfos") or [])}
    print(f"{now:%m-%d %H:%M}  /trend      {row(t)}")
    print(f"{now:%m-%d %H:%M}  dealTrend   {row(d)}   "
          f"거래량 {info.get('거래량','?')} 대금 {info.get('대금','?')}")
    print(f"{now:%m-%d %H:%M}  키움코스피   {kiwoom_kospi()}")


if __name__ == "__main__":
    main()
