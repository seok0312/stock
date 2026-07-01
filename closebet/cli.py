"""명령줄 진입점.

예시:
    python -m closebet                          # 최근 거래일 주도주 후보
    python -m closebet --market KOSDAQ --min-change 5
    python -m closebet --top 150 --min-value-eok 100
    python -m closebet --save                   # 결과를 data/ 에 CSV 저장
    python -m closebet --sectors                # 주도섹터 근사(실험적, pykrx 필요)
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace

from tabulate import tabulate

from .config import DEFAULT, EOK
from .screener import screen_leaders
from .sectors import leading_sectors


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="closebet",
        description="종가배팅 주도주/주도섹터 스크리너 (FinanceDataReader 기반, 최근 거래일 종가)",
    )
    p.add_argument("--market", default=DEFAULT.market, help="KRX(전체) / KOSPI / KOSDAQ")
    p.add_argument("--min-change", type=float, default=DEFAULT.min_change_pct, help="최소 등락률(퍼센트)")
    p.add_argument("--max-change", type=float, default=None, help="최대 등락률(퍼센트)")
    p.add_argument("--top", type=int, default=DEFAULT.top_by_value, help="거래대금 상위 N개로 압축")
    p.add_argument("--min-value-eok", type=float, default=DEFAULT.min_trading_value / EOK, help="최소 거래대금(억)")
    p.add_argument("--rows", type=int, default=DEFAULT.show_rows, help="화면 출력 행 수")
    p.add_argument("--flow", action="store_true", help="네이버 외인·기관 수급 결합 주도주 점수화")
    p.add_argument("--kiwoom", action="store_true", help="키움 공식 수급(ka10059)+프로그램(ka90013)으로 점수화 (.env 필요)")
    p.add_argument("--flow-top", type=int, default=DEFAULT.flow_top, help="수급 조회할 상위 종목 수")
    p.add_argument("--views", action="store_true", help="키움 ka00198 조회순위(관심도) 조회/결합 (.env 필요)")
    p.add_argument("--themes", action="store_true", help="키움 테마 랭킹 + 주도주 교집합으로 '오늘의 주도테마' 도출 (.env 필요)")
    p.add_argument("--sectors", action="store_true", help="주도섹터 근사 표시(실험적, pykrx 필요)")
    p.add_argument("--save", action="store_true", help="결과를 data/ 에 CSV로 저장")
    return p


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)

    cfg = replace(
        DEFAULT,
        market=args.market,
        min_change_pct=args.min_change,
        max_change_pct=args.max_change,
        top_by_value=args.top,
        min_trading_value=int(args.min_value_eok * EOK),
        flow_top=args.flow_top,
    )

    date, leaders = screen_leaders(cfg)

    print(
        f"\n📅 기준일(최근 거래일): {date}  |  시장: {cfg.market}  |  "
        f"조건: 거래대금 상위 {cfg.top_by_value}개 중 등락률 >= {cfg.min_change_pct}%"
    )
    print(f"🎯 주도주 후보 {len(leaders)}종목\n")
    if len(leaders):
        print(tabulate(leaders.head(args.rows), headers="keys", tablefmt="github", showindex=False))
    else:
        print("조건을 만족하는 종목이 없습니다. (--min-change 를 낮춰 보세요)")

    scored = None
    if (args.flow or args.kiwoom) and len(leaders):
        from .score import score_leaders

        source = "kiwoom" if args.kiwoom else "naver"
        n = min(cfg.flow_top, len(leaders))
        label = "키움 공식(수급+프로그램)" if source == "kiwoom" else "네이버 수급"
        per = cfg.kiwoom_sleep * 2 if source == "kiwoom" else cfg.flow_sleep
        print(f"\n💹 상위 {n}종목 {label} 수집 중… (약 {n * per:.0f}초)")
        try:
            scored = score_leaders(leaders, cfg, source=source, dt=date)
            print(f"\n🏆 주도주 점수 랭킹 ({label} 결합)\n")
            print(tabulate(scored.head(args.rows), headers="keys", tablefmt="github", showindex=False))
        except Exception as e:
            print(f"  (점수화 실패: {e})")

    if args.views:
        try:
            from .kiwoom import KiwoomClient

            vr = KiwoomClient().view_rank("4")  # 당일 누적 조회순위
            print(f"\n👁  키움 조회순위(ka00198) — {len(vr)}건")
            if scored is not None and len(vr):
                m = vr.set_index("종목코드")["조회순위"]
                scored = scored.copy()
                scored["조회순위"] = scored["종목코드"].map(m)
                print("   (아래 점수 랭킹에 조회순위 결합)\n")
                print(tabulate(scored.head(args.rows), headers="keys", tablefmt="github", showindex=False))
            elif len(vr):
                print(tabulate(vr.head(args.rows), headers="keys", tablefmt="github", showindex=False))
        except Exception as e:
            print(f"  (키움 조회순위 실패: {e})")

    themes = None
    if args.themes:
        try:
            from .kiwoom import KiwoomClient
            from .themes import derive_leading_themes

            print(f"\n🧩 키움 테마 랭킹 + 주도주 교집합으로 주도테마 도출 중… (상위 {cfg.theme_top}개 테마)")
            themes = derive_leading_themes(KiwoomClient(), scored=scored, cfg=cfg)
            if themes is None or themes.empty:
                print("  (테마 데이터를 가져오지 못했습니다)")
            else:
                print("\n🔥 오늘의 주도테마\n")
                print(tabulate(themes.head(cfg.theme_top), headers="keys", tablefmt="github", showindex=False))
        except Exception as e:
            print(f"  (주도테마 도출 실패: {e})")

    if args.save:
        os.makedirs("data", exist_ok=True)
        path = os.path.join("data", f"leaders_{date}.csv")
        leaders.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"\n💾 저장: {path}")
        if scored is not None:
            spath = os.path.join("data", f"scored_{date}.csv")
            scored.to_csv(spath, index=False, encoding="utf-8-sig")
            print(f"💾 저장: {spath}")
        if themes is not None and not themes.empty:
            tpath = os.path.join("data", f"themes_{date}.csv")
            themes.to_csv(tpath, index=False, encoding="utf-8-sig")
            print(f"💾 저장: {tpath}")

    if args.sectors:
        print("\n🧭 주도섹터 (KRX 지수 등락률, 실험적) 계산 중…")
        sectors = leading_sectors(date)
        if sectors is None or sectors.empty:
            print("  (섹터 데이터를 가져오지 못했습니다 — pykrx 미설치이거나 KRX 접속 차단. 건너뜀)")
        else:
            print(tabulate(sectors, headers="keys", tablefmt="github", showindex=False))


if __name__ == "__main__":
    main()
