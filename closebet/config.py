"""스크리닝 기본 설정값.

CLI 옵션(python -m closebet ...)으로 대부분 덮어쓸 수 있습니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EOK: int = 100_000_000  # 1억 (원)

# 주도주 점수 가중치 (합 1.0 권장). 순매수/프로그램은 양수만 신호로 사용.
DEFAULT_WEIGHTS = {
    "거래대금": 0.25,
    "등락률": 0.20,
    "외국인": 0.20,
    "기관": 0.15,
    "프로그램": 0.15,  # 키움 소스일 때만 존재 (네이버 소스는 0)
    "동반매수": 0.05,  # 외국인·기관 동반 순매수 시 가점(0/1)
}

# 주도테마 점수 가중치 (합 1.0). 테마 등락률·거래대금 + 우리 주도주와의 교집합.
THEME_WEIGHTS = {
    "테마등락률": 0.30,  # ka90001 flu_rt
    "거래대금": 0.25,    # 구성종목 거래대금 합(근사)
    "상승비율": 0.15,    # rising_stk_num / stk_num
    "주도주수": 0.15,    # 이 테마에 속한 우리 주도주 후보 수(hit)
    "주도주점수": 0.15,  # 그 주도주들의 점수 합
}


@dataclass(frozen=True)
class Settings:
    market: str = "KRX"                 # 대상 시장: KRX(전체) / KOSPI / KOSDAQ
    min_trading_value: int = 30 * EOK   # 최소 거래대금(원). 기본 30억
    top_by_value: int = 100             # 거래대금 상위 N개로 1차 압축
    min_change_pct: float = 3.0         # 최소 등락률(퍼센트)
    max_change_pct: float | None = None # 최대 등락률(퍼센트). None이면 상한 없음
    exclude_preferred: bool = True      # 우선주 제외 (보통주=코드 끝자리 0만 유지)
    show_rows: int = 30                 # 화면 출력 행 수
    # 수급 점수화(--flow / --kiwoom)
    flow_top: int = 30                  # 상위 몇 종목의 수급을 조회할지
    flow_sleep: float = 0.4             # 네이버 요청 간 딜레이(초)
    kiwoom_sleep: float = 0.3           # 키움 요청 간 딜레이(초, rate limit 대비)
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    # 주도테마 도출(--themes)
    theme_top: int = 20                 # 상위 몇 개 테마의 구성종목을 조회할지
    theme_sleep: float = 0.25           # 테마 구성종목 요청 간 딜레이(초)
    theme_min_rising: float = 0.0       # 상승종목비율 하한(0=필터 없음)
    theme_weights: dict = field(default_factory=lambda: dict(THEME_WEIGHTS))


DEFAULT = Settings()
