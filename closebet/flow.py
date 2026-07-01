"""네이버 금융 외국인·기관 수급 크롤링 (종목별 최근 거래일 순매매).

finance.naver.com/item/frgn.naver?code=<6자리> 의 '외국인·기관 순매매' 표를 파싱한다.
- 비공식 크롤링이라 HTML 구조 변경 시 파서 수정이 필요할 수 있다.
- 개인 학습용, 요청 간 딜레이(sleep) 필수. 재배포/상업 이용 금지 권장.
- 순매매 단위는 '주(shares)'. 양수=순매수, 음수=순매도.
"""

from __future__ import annotations

import io
import time

import pandas as pd
import requests

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_URL = "https://finance.naver.com/item/frgn.naver?code={code}"


def _flatten(cols) -> list:
    """MultiIndex/일반 컬럼을 단일 문자열 리스트로 평탄화."""
    out = []
    for c in cols:
        if isinstance(c, tuple):
            a, b = str(c[0]), str(c[-1])
            out.append(a if a == b else f"{a}_{b}")
        else:
            out.append(str(c))
    return out


def _find_flow_table(tables) -> pd.DataFrame | None:
    """컬럼명으로 '외국인·기관 순매매' 표를 찾는다(표 순서 변경에 견고)."""
    for t in tables:
        joined = " ".join(_flatten(t.columns))
        if "기관" in joined and "외국인" in joined and "순매매" in joined:
            return t
    return None


def get_flow(code: str, session: requests.Session | None = None, timeout: float = 10.0) -> dict | None:
    """종목의 최근 거래일 외국인·기관 순매매(주). 실패 시 None.

    반환: {"날짜": str, "외국인순매매": int, "기관순매매": int}
    """
    code = str(code).zfill(6)
    getter = session or requests
    try:
        r = getter.get(_URL.format(code=code), headers=_UA, timeout=timeout)
        r.encoding = "euc-kr"
        tables = pd.read_html(io.StringIO(r.text))
    except Exception:
        return None

    t = _find_flow_table(tables)
    if t is None or t.empty:
        return None
    t = t.copy()
    t.columns = _flatten(t.columns)

    date_col = next((c for c in t.columns if c.startswith("날짜")), None)
    inst_col = next((c for c in t.columns if c.startswith("기관")), None)
    frgn_col = next((c for c in t.columns if c.startswith("외국인_순매매")), None)
    if not (date_col and inst_col and frgn_col):
        return None

    t = t.dropna(subset=[date_col])
    if t.empty:
        return None
    row = t.iloc[0]  # 최근 거래일

    frgn = pd.to_numeric(row[frgn_col], errors="coerce")
    inst = pd.to_numeric(row[inst_col], errors="coerce")
    return {
        "날짜": str(row[date_col]),
        "외국인순매매": int(frgn) if pd.notna(frgn) else 0,
        "기관순매매": int(inst) if pd.notna(inst) else 0,
    }


def get_flows(codes, sleep: float = 0.4) -> dict:
    """여러 종목 수급을 순차 수집(세션 재사용 + 딜레이). {code: flowdict|None}."""
    out = {}
    with requests.Session() as s:
        for code in codes:
            out[str(code).zfill(6)] = get_flow(code, session=s)
            time.sleep(sleep)
    return out
