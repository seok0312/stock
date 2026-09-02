# -*- coding: utf-8 -*-
"""종가베팅 브리핑 봇 진입점.

사용:
  py -3.11 cli.py --slot 07 --dry-run     # 전송 없이 콘솔 출력
  py -3.11 cli.py --slot auto             # 현재 시각에서 슬롯 자동 판정
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import quotes
import render
import notify

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))


def pick_slot(now: datetime) -> str:
    """현재 시각에서 가장 가까운(이미 지난) 슬롯."""
    h = now.hour
    hm = now.hour * 60 + now.minute
    if hm >= 19 * 60:
        return "19"
    if hm >= 14 * 60 + 30:
        return "1430"
    return "07"


def is_kr_trading_day(d: datetime) -> tuple[bool, str]:
    """한국 증시 개장일 여부. (열림, 사유)"""
    if d.weekday() >= 5:
        return False, f"{'토' if d.weekday()==5 else '일'}요일"
    try:
        import FinanceDataReader as fdr
        ks = fdr.DataReader("KS11", (d - timedelta(days=12)).strftime("%Y-%m-%d"))
        if len(ks) == 0:
            return True, "판정불가(기본 개장)"
        last = ks.index[-1].date()
        # 오늘 데이터가 이미 있으면 확실히 개장일
        if last == d.date():
            return True, "당일 데이터 확인"
        # 직전 거래일이 3영업일 이상 전이면 연휴 가능성
        gap = (d.date() - last).days
        if gap >= 4:
            return False, f"직전 거래일 {last} (연휴 추정)"
    except Exception:
        pass
    return True, "개장 추정"


def main(argv=None):
    ap = argparse.ArgumentParser(description="종가베팅 시간대별 브리핑")
    ap.add_argument("--slot", default="auto", choices=["auto", "07", "1430", "19"])
    ap.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 출력만")
    ap.add_argument("--force", action="store_true", help="휴장일에도 실행")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-sectors", action="store_true")
    ap.add_argument("--no-flows", action="store_true")
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    notify.load_env(os.path.join(HERE, ".env"),
                    os.path.join(HERE, "..", ".env"),
                    "/opt/upbit_bot/.env")

    now = datetime.now(KST)
    slot = pick_slot(now) if args.slot == "auto" else args.slot

    ok, why = is_kr_trading_day(now)
    if not ok and not args.force:
        print(f"[skip] 한국 증시 휴장 ({why}) — 알림 생략. 강제하려면 --force")
        return 0

    print(f"[{now:%Y-%m-%d %H:%M} KST] 슬롯 {slot} · 개장 {ok}({why})")

    win = quotes.fetch_window(slot, now)
    news = {}
    us_sectors, kr_impact = [], []
    fl = None

    if not args.no_flows:
        try:
            import flows as flows_mod
            fl = flows_mod.summary()
        except Exception as e:
            print(f"  거래대금 수집 실패(계속 진행): {type(e).__name__}: {e}")

    if not args.no_news:
        try:
            import news as news_mod
            news = news_mod.news_for_window(win)
        except Exception as e:
            print(f"  뉴스 수집 실패(계속 진행): {type(e).__name__}: {e}")

    if slot == "07" and not args.no_sectors:
        try:
            import sectors as sec_mod
            us_sectors = sec_mod.fetch_us_sectors()
            kr_impact = sec_mod.kr_impact(us_sectors, win["rows"])
        except Exception as e:
            print(f"  섹터 수집 실패(계속 진행): {type(e).__name__}: {e}")

    sig = sum(1 for r in win["rows"] if r["significant"])
    msg = render.build(win, news=news, us_sectors=us_sectors, kr_impact=kr_impact,
                       flows=fl, footer=f"유의미 변동 {sig}/5종 · 자동수집")
    notify.send(msg, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
