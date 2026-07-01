"""키움 연결 스모크 테스트: 토큰 발급 + ka00198(실시간종목조회순위).

실행:
    python -m closebet.kiwoom_probe

사전 준비: .env.example 을 .env 로 복사하고 KIWOOM_APPKEY/KIWOOM_SECRETKEY/KIWOOM_ENV 설정.
먼저 KIWOOM_ENV=mock(모의)로 검증하세요.
"""

from __future__ import annotations

from tabulate import tabulate

from .kiwoom import KiwoomClient, KiwoomError


def main() -> None:
    try:
        client = KiwoomClient()
    except KiwoomError as e:
        print(f"❌ {e}")
        return

    print(f"🔗 도메인: {client.base}")
    try:
        client.issue_token()
        print("✅ 토큰 발급 성공")
    except Exception as e:
        print(f"❌ 토큰 발급 실패: {e}")
        print("→ 앱키/시크릿, KIWOOM_ENV(mock/real), 사용신청 상태를 확인하세요.")
        return

    try:
        df = client.view_rank(qry_tp="4")  # 당일 누적
    except Exception as e:
        print(f"❌ ka00198 호출 실패: {e}")
        return

    print(f"\n📊 실시간종목조회순위 (ka00198) — {len(df)}건")
    if len(df):
        print(tabulate(df.head(20), headers="keys", tablefmt="github", showindex=False))
    else:
        print("(빈 응답 — 장중이 아니거나 응답 필드 확인 필요)")


if __name__ == "__main__":
    main()
