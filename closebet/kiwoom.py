"""키움증권 신규 REST API 클라이언트 (OAuth 토큰 + 요청 + ka00198 조회순위).

인증/요청 포맷은 openapi.kiwoom.com 및 오픈소스 래퍼(younghwan91/kiwoom-rest-api,
dongbin300/KiwoomRestApi.Net) 소스로 교차확인함(2026-07).
  - 토큰: POST /oauth2/token, body {grant_type, appkey, secretkey}, 응답 필드 'token'
  - 요청: POST /api/dostk/{group}, 헤더 api-id / authorization: Bearer / cont-yn / next-key
  - 도메인: 실전 api.kiwoom.com, 모의 mockapi.kiwoom.com

앱키/시크릿은 .env(KIWOOM_APPKEY / KIWOOM_SECRETKEY / KIWOOM_ENV)에서 로드하며 절대 커밋 금지.
실계좌 전에 반드시 모의(mock)로 검증하세요.
"""

from __future__ import annotations

import os

import pandas as pd
import requests

PROD_URL = "https://api.kiwoom.com"
MOCK_URL = "https://mockapi.kiwoom.com"
_CT = "application/json;charset=UTF-8"

# 안전장치: 이 프로그램은 '조회 전용'. 주문/신용주문 엔드포인트는 절대 호출 금지.
# (실전 앱키로도 매매가 나가지 않도록 코드 레벨에서 차단)
_BLOCKED_ENDPOINTS = ("/ordr", "/crdordr")


class KiwoomError(RuntimeError):
    pass


class KiwoomOrderBlockedError(KiwoomError):
    """주문 계열 호출 시도 시 발생 — 매매는 코드 레벨에서 금지되어 있음."""


def _load_dotenv() -> None:
    """저장소 루트의 .env를 간단 파싱해 os.environ에 주입(python-dotenv 없어도 동작)."""
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def parse_view_rank(data: dict) -> pd.DataFrame:
    """ka00198 응답(dict) → 표준 컬럼 DataFrame. 네트워크와 분리된 순수 함수(테스트 용이)."""
    rows = _extract_list(data, prefer="item_inq_rank")
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    rename = {
        "stk_cd": "종목코드",
        "stk_nm": "종목명",
        "bigd_rank": "조회순위",
        "rank_chg": "순위등락",
        "base_comp_chgr": "등락률",
        "past_curr_prc": "현재가",
    }
    keep = {k: v for k, v in rename.items() if k in df.columns}
    out = df[list(keep)].rename(columns=keep)
    if "종목코드" in out.columns:
        out["종목코드"] = out["종목코드"].astype(str).str.zfill(6)
    for c in ("조회순위", "순위등락", "등락률", "현재가"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    # 키움은 현재가에 등락 방향 부호를 붙여 반환 → 실제 가격은 절댓값
    if "현재가" in out.columns:
        out["현재가"] = out["현재가"].abs()
    return out


def _extract_list(data: dict, prefer: str = "") -> list:
    if isinstance(data, dict):
        if prefer and isinstance(data.get(prefer), list):
            return data[prefer]
        for v in data.values():  # fallback: 첫 list[dict] 필드
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _num(x):
    """키움 숫자 문자열 → float. 부호 관례 처리: '+123', '--123'(음수), '1,234', '' → None."""
    if x is None:
        return None
    s = str(x).strip().replace(",", "")
    if not s:
        return None
    if s.startswith("--"):  # 키움 이중 마이너스 = 음수
        s = "-" + s[2:]
    try:
        return float(s)
    except ValueError:
        return None


class KiwoomClient:
    def __init__(self, appkey=None, secretkey=None, env=None, timeout: float = 10.0):
        _load_dotenv()
        self.appkey = appkey or os.environ.get("KIWOOM_APPKEY")
        self.secretkey = secretkey or os.environ.get("KIWOOM_SECRETKEY")
        env = (env or os.environ.get("KIWOOM_ENV", "mock")).lower()
        self.base = PROD_URL if env == "real" else MOCK_URL
        self.timeout = timeout
        self._token = None
        if not self.appkey or not self.secretkey:
            raise KiwoomError("KIWOOM_APPKEY/KIWOOM_SECRETKEY가 없습니다. .env.example를 복사해 .env를 설정하세요.")

    # ---- 인증 ----
    def issue_token(self) -> str:
        r = requests.post(
            f"{self.base}/oauth2/token",
            headers={"Content-Type": _CT},
            json={"grant_type": "client_credentials", "appkey": self.appkey, "secretkey": self.secretkey},
            timeout=self.timeout,
        )
        try:
            data = r.json()
        except ValueError:
            raise KiwoomError(f"토큰 응답 파싱 실패 (status {r.status_code}): {r.text[:200]}")
        token = data.get("token") or data.get("access_token")
        if not token:
            raise KiwoomError(f"토큰 발급 실패 [{data.get('return_code')}]: {data.get('return_msg') or data}")
        self._token = token
        return token

    @property
    def token(self) -> str:
        return self._token or self.issue_token()

    # ---- 일반 요청 ----
    def request(self, api_id: str, params: dict | None = None,
                endpoint: str = "/api/dostk/stkinfo", cont_yn: str = "N", next_key: str = ""):
        """단건 요청. (응답 JSON dict, 응답 헤더) 반환. return_code!=0 이면 KiwoomError.

        안전장치: 주문/신용주문 엔드포인트는 호출 자체를 차단한다(조회 전용 프로그램).
        """
        if any(b in endpoint for b in _BLOCKED_ENDPOINTS):
            raise KiwoomOrderBlockedError(
                f"주문 계열 엔드포인트({endpoint})는 이 프로그램에서 차단되어 있습니다. "
                "이 도구는 조회 전용이며 매매를 하지 않습니다."
            )
        headers = {
            "Content-Type": _CT,
            "authorization": f"Bearer {self.token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }
        r = requests.post(f"{self.base}{endpoint}", headers=headers, json=params or {}, timeout=self.timeout)
        try:
            data = r.json()
        except ValueError:
            raise KiwoomError(f"{api_id} 응답 파싱 실패 (status {r.status_code}): {r.text[:200]}")
        rc = data.get("return_code")
        if rc not in (0, "0", None):
            raise KiwoomError(f"{api_id} 오류 [{rc}] {data.get('return_msg')}")
        return data, r.headers

    # ---- ka00198 실시간종목조회순위 (빅데이터 관심도) ----
    def view_rank(self, qry_tp: str = "4") -> pd.DataFrame:
        """조회순위. qry_tp: 1=1분, 2=10분, 3=1시간, 4=당일누적, 5=30초.

        반환 컬럼: [종목코드, 종목명, 조회순위, 순위등락, 등락률, 현재가]
        """
        data, _ = self.request("ka00198", {"qry_tp": str(qry_tp)}, endpoint="/api/dostk/stkinfo")
        return parse_view_rank(data)

    # ---- ka10059 종목별투자자기관별 (외국인·기관 순매수) ----
    def stock_flow(self, code: str, dt: str) -> dict | None:
        """최근 거래일 외국인·기관·개인 순매수(수량, 단주). dt=YYYYMMDD. 실패 시 None."""
        params = {"dt": dt, "stk_cd": str(code).zfill(6), "amt_qty_tp": "2", "trde_tp": "0", "unit_tp": "1"}
        data, _ = self.request("ka10059", params, endpoint="/api/dostk/stkinfo")
        rows = _extract_list(data, prefer="stk_invsr_orgn")
        if not rows:
            return None
        r = rows[0]  # 최근일
        return {
            "날짜": r.get("dt"),
            "외국인": _num(r.get("frgnr_invsr")),
            "기관": _num(r.get("orgn")),
            "개인": _num(r.get("ind_invsr")),
        }

    # ---- ka90013 종목일별프로그램매매추이 (프로그램 순매수) ----
    def stock_program(self, code: str, dt: str) -> dict | None:
        """최근 거래일 프로그램 순매수 금액(억원). dt=YYYYMMDD. 실패 시 None.

        ※ 프로그램 순매수 '합계'만 제공되며 차익/비차익 분해는 종목단위로 불가.
        """
        params = {"dt": dt, "stk_cd": str(code).zfill(6), "amt_qty_tp": "1"}
        data, _ = self.request("ka90013", params, endpoint="/api/dostk/mrkcond")
        rows = _extract_list(data, prefer="stk_daly_prm_trde_trnsn")
        if not rows:
            return None
        amt = _num(rows[0].get("prm_netprps_amt"))  # 백만원
        return {"날짜": rows[0].get("dt"), "프로그램순매수억": (amt / 100.0) if amt is not None else None}

    # ---- ka90001 테마그룹별요청 (테마 랭킹) ----
    def theme_groups(self, flu_pl_amt_tp: str = "3", date_tp: str = "1",
                     stex_tp: str = "1", qry_tp: str = "0", thema_nm: str = "", stk_cd: str = "") -> list:
        """테마 목록/랭킹. flu_pl_amt_tp: 3=상위등락률,1=상위기간수익률. 원시 dict 리스트 반환."""
        params = {
            "qry_tp": qry_tp, "date_tp": date_tp, "flu_pl_amt_tp": flu_pl_amt_tp,
            "stex_tp": stex_tp, "stk_cd": stk_cd, "thema_nm": thema_nm,
        }
        data, _ = self.request("ka90001", params, endpoint="/api/dostk/thme")
        return _extract_list(data, prefer="thema_grp")

    # ---- ka90002 테마구성종목요청 ----
    def theme_stocks(self, grp_cd: str, stex_tp: str = "1", date_tp: str = "1") -> list:
        """테마 구성종목. grp_cd=ka90001의 thema_grp_cd. 원시 dict 리스트 반환."""
        params = {"thema_grp_cd": str(grp_cd), "stex_tp": stex_tp, "date_tp": date_tp}
        data, _ = self.request("ka90002", params, endpoint="/api/dostk/thme")
        return _extract_list(data, prefer="thema_comp_stk")
