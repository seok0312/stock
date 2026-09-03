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

# 한국 정규장 데이터가 아직 전일치인 이른 시간대는 미국 섹터를 근거로 삼는다.
# 그 이후는 장중 한국 업종·테마로 판단한다(미국장이 닫혀 있으므로).
US_SECTOR_SLOTS = {"0600", "0750", "0850"}

# 주도주는 장중·마감 이후에만 의미가 있다(개장 전엔 전일 데이터).
LEADER_SLOTS = {"0930", "1430", "1600", "1900", "2000"}

ENV_PATHS = (
    os.path.join(HERE, ".env"),
    os.path.join(HERE, "..", ".env"),
    os.path.join(HERE, "..", "..", "upbit_bot", ".env"),   # 로컬: 텔레그램 토큰이 여기 있다
    "/opt/upbit_bot/.env",                                  # 서버
)


def pick_slot(now: datetime) -> str:
    """현재 시각에서 이미 지난 슬롯 중 가장 최근 것. 하나도 없으면 마지막(2000)."""
    hm = now.hour * 60 + now.minute
    passed = [(h * 60 + m, k) for k, c in quotes.SLOTS.items()
              for h, m in (c["at"],) if h * 60 + m <= hm]
    return max(passed)[1] if passed else list(quotes.SLOTS)[-1]


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
    ap.add_argument("--slot", default="auto",
                    choices=["auto"] + list(quotes.SLOTS))
    ap.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 출력만")
    ap.add_argument("--force", action="store_true", help="휴장일에도 실행")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-sectors", action="store_true")
    ap.add_argument("--no-flows", action="store_true")
    ap.add_argument("--no-leaders", action="store_true")
    ap.add_argument("--no-events", action="store_true")
    ap.add_argument("--no-store", action="store_true",
                    help="스냅샷을 파일에 기록하지 않음(테스트용)")
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    notify.load_env(*ENV_PATHS)

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

    fl_cmp = None
    if not args.no_flows:
        try:
            import flows as flows_mod
            fl = flows_mod.summary()
        except Exception as e:
            print(f"  거래대금 수집 실패(계속 진행): {type(e).__name__}: {e}")
        if fl:
            try:
                import store
                # 같은 시각 과거와 비교 → 저장은 비교 뒤에(오늘 값이 표본에 섞이지 않게)
                fl_cmp = store.compare(slot, fl)
                if fl_cmp:
                    print(f"  같은 시각 비교 표본 {fl_cmp.get('n_short', 0)}"
                          f"/{fl_cmp.get('n_long', 0)}일")
                else:
                    print("  같은 시각 비교 표본 없음 — 종가평균으로 폴백")
                if not args.no_store:
                    store.append(store.build_record(slot, fl, now))
            except Exception as e:
                print(f"  스냅샷 저장/비교 실패(계속 진행): {type(e).__name__}: {e}")

    if not args.no_news:
        try:
            import news as news_mod
            news = news_mod.news_for_window(win)
        except Exception as e:
            print(f"  뉴스 수집 실패(계속 진행): {type(e).__name__}: {e}")

    kr_upjong, kr_themes, kr_when = None, None, None
    us_leaders = {}
    if not args.no_sectors:
        if slot in US_SECTOR_SLOTS:
            # 미국장이 막 끝났고 한국 데이터는 아직 전일치 → 미국 섹터가 근거
            try:
                import sectors as sec_mod
                us_sectors = sec_mod.fetch_us_sectors()
                kr_impact = sec_mod.kr_impact(us_sectors, win["rows"])
                # 강세 섹터에만 주도주·원인뉴스 (뉴스 호출 수를 아끼려 상위 3개)
                perf = sec_mod.fetch_finviz_perf()
                strong = [x["sector"] for x in us_sectors if x["change_pct"] > 0][:3]
                for nm in strong:
                    ld = sec_mod.sector_leaders(nm, perf)
                    if ld:
                        us_leaders[nm] = ld
            except Exception as e:
                print(f"  미국섹터 수집 실패(계속 진행): {type(e).__name__}: {e}")
        else:
            # 14:30 / 19시 → 미국장이 닫혀 있으므로 장중 한국 데이터로 판단
            try:
                import kr_sectors as krs
                kr_upjong = krs.fetch_upjong()
                kr_themes = krs.fetch_themes()
                # 정규장(09:00~15:30) 안이면 '장중', 그 뒤면 '종가 기준'
                kr_when = "장중" if slot in ("0930", "1430") else "종가 기준"
            except Exception as e:
                print(f"  한국섹터 수집 실패(계속 진행): {type(e).__name__}: {e}")

    ld = None
    if slot in LEADER_SLOTS and not args.no_leaders:
        try:
            import leaders as ld_mod
            ld = ld_mod.fetch_leaders(top=8)
        except Exception as e:
            print(f"  주도주 수집 실패(계속 진행): {type(e).__name__}: {e}")

    ev_ctx, evs = None, []
    if not args.no_events:
        # 일정은 뉴스와 달리 '변동 전에 이미 알던 사실'이라 근거로서 더 강하다.
        # 창 시작 전부터 다음날까지 훑어 (발표된 것, 앞으로 올 것)으로 나눈다.
        try:
            import events as ev_mod
            names = [x["name"] for x in (kr_upjong or {}).get("up", [])] +                     [x["name"] for x in (kr_themes or {}).get("up", [])]
            # 실적은 며칠 전부터 알아야 의미가 있어 앞쪽 지평을 넉넉히 잡는다.
            # 실제 표시 범위는 events.brief() 가 종류별로 다시 자른다.
            evs = ev_mod.collect(win["start"] - timedelta(hours=2),
                                 now + timedelta(days=8))
            ev_ctx = ev_mod.brief(evs, win, quote_rows=win["rows"],
                                  sector_names=names, now=now)
            print(f"  일정 {len(evs)}건 · 구간내 {ev_ctx['n_done']} · "
                  f"예정 {len(ev_ctx['ahead'])}")
        except Exception as e:
            print(f"  일정 수집 실패(계속 진행): {type(e).__name__}: {e}")

    sig = sum(1 for r in win["rows"] if r["significant"])
    msg = render.build(win, news=news, us_sectors=us_sectors, kr_impact=kr_impact,
                       leaders=ld, us_leaders=us_leaders,
                       events=ev_ctx,
                       flows=fl, flows_cmp=fl_cmp, kr_upjong=kr_upjong, kr_themes=kr_themes, kr_when=kr_when,
                       footer=f"유의미 변동 {sig}/5종 · 자동수집")
    notify.send(msg, dry_run=args.dry_run)

    # 반응 로그는 전송 뒤에 — 자산당 봉 조회가 붙어 알림이 늦어지면 안 된다.
    if evs and not args.no_events:
        try:
            import reactions
            n = reactions.log(evs, now=now)
            if n:
                print(f"  반응 로그 {n}건 기록 (누적 {len(reactions.load_all())})")
        except Exception as e:
            print(f"  반응 로그 실패(무시): {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
