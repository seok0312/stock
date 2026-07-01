# HANDOFF — closebet (종가배팅 데이터/스크리닝 프로젝트)

> 다음 세션에서 바로 이어서 작업하기 위한 인수인계 문서. **작업 재개 시 이 파일부터 읽으세요.**
> 최종 업데이트: **2026-07-01**

---

## 0. 30초 요약

- **목적**: 종가배팅용 — 거래대금·상승률·수급·프로그램·조회수로 **주도주**를 뽑고, 이를 묶어 **오늘의 주도테마**를 도출.
- **언어/실행**: Python 3.11, `python -m closebet ...` (작업 폴더 `c:\dev\stock`)
- **현재 상태**: **1~4단계 완료 + 라이브 검증 끝**. 다음은 **5단계 백테스팅**.
- **데이터 소스**: FinanceDataReader(무료) + 키움 REST API(사용자 실전 앱키, **조회 전용**) + 네이버 크롤링(보조).
- **안전**: 이 도구는 **매매하지 않음**. 키움 클라이언트가 주문 엔드포인트를 코드로 차단.

---

## 1. 진행 상태 (체크리스트)

- [x] **1단계** FDR 스냅샷 기반 마감 후 주도주 스크리너 — `python -m closebet`
- [x] **2단계** 네이버 외인·기관 수급 결합 점수화 — `--flow`
- [x] **3-1** 키움 `ka00198` 조회순위(관심도) — `--views`
- [x] **3-2** 키움 `ka10059`(공식 수급)·`ka90013`(프로그램 순매수) — `--kiwoom`
- [x] **4단계** 키움 `ka90001`/`ka90002` + 주도주 교집합 → **오늘의 주도테마** — `--themes`
- [ ] **5단계** 백테스팅(backtrader/vectorbt) — "종가 매수 → 익일 청산" 등
- [ ] **6단계** Streamlit 대시보드 + parquet/SQLite 이력 축적 + 가중치 사후조정

## 2. 명령어 치트시트

```powershell
cd c:\dev\stock
python -m closebet                        # 최근 거래일 주도주 후보(무료, 앱키 불필요)
python -m closebet --flow                 # + 네이버 수급 결합 점수
python -m closebet --kiwoom               # + 키움 공식 수급·프로그램 결합 점수(.env 필요)
python -m closebet --kiwoom --views       # + 조회순위(관심도)
python -m closebet --kiwoom --themes      # + 오늘의 주도테마 도출  ← 핵심 산출물
python -m closebet --kiwoom --themes --save   # 결과 CSV 저장(data/)
python -m closebet.kiwoom_probe           # 키움 연결 스모크 테스트
python -m closebet -h                     # 전체 옵션
```

## 3. 모듈 맵 (`closebet/`)

| 파일 | 역할 |
|---|---|
| `config.py` | 임계치·점수 가중치(`DEFAULT_WEIGHTS`, `THEME_WEIGHTS`) |
| `market.py` | FinanceDataReader — 전종목 스냅샷·과거 일봉·최근 거래일 |
| `screener.py` | 거래대금·등락률 주도주 후보 필터 |
| `flow.py` | 네이버 외인·기관 수급 크롤링(무인증) |
| `score.py` | 수급/프로그램 결합 주도주 점수화(`source="naver"|"kiwoom"`) |
| `kiwoom.py` | 키움 REST 클라이언트(토큰·조회순위·수급·프로그램·테마, **주문 차단**) |
| `kiwoom_probe.py` | 키움 연결 스모크 테스트 |
| `themes.py` | 주도테마 도출(theme-first + 주도주 교집합) |
| `sectors.py` | (선택·실험적) pykrx 업종지수 — 현재 KRX 차단으로 대개 skip |
| `cli.py` | 명령줄 진입점(플래그 조합) |

## 4. 환경 설정 (.env)

`c:\dev\stock\.env` (git 제외됨) 에 키움 자격증명:
```
KIWOOM_APPKEY=...
KIWOOM_SECRETKEY=...
KIWOOM_ENV=real      # 앱키가 실전용이면 real, 모의용이면 mock (불일치 시 오류 8030)
```
- 코드는 **OS 환경변수를 우선** 읽고 없으면 `.env`를 읽음(`kiwoom.py:_load_dotenv`).
- 더 안전하게 하려면 키를 `.env` 대신 **Windows 사용자 환경변수**로 넣어도 동일 동작(파일에 비밀 없음).

## 5. 검증된 핵심 사실 & 함정 (다시 조사하지 말 것)

- **pykrx(1.2.8)는 이 PC에서 KRX 로그인/차단으로 빈 응답** → 1단계는 **FinanceDataReader**로 구현. (`fdr.StockListing('KRX')` = 거래대금 `Amount`·등락률 `ChagesRatio`)
- **조회수는 증권사 공식 API에 원시 카운트 없음.** 키움 `ka00198`은 순위(`bigd_rank`=빅데이터 순위)만 제공. HTS 0198과 동일.
- **종목단위 프로그램 "비차익" 순매수는 어떤 API로도 불가.** `ka90013`은 프로그램 순매수 '합계'만(백만원). 차익/비차익은 시장 전체(`ka90005`/`ka90010`)만.
- **시장 "테마" 공식 표준분류 없음** → `themes.py`는 키움 테마 태그(정적) 위에 오늘 신호를 얹어 동적 도출.
- **키움 부호 quirk**: 음수를 `--6800`(이중 마이너스)로 반환 → `kiwoom._num()`이 처리.
- **키움 통합거래소 코드 접미사**: `ka90002` 응답 `stk_cd`가 `112040_AL` 형태 → 6자리로 절단(`themes._code6`).
- **역매핑 안 됨**: `ka90001 qry_tp='2', stk_cd=...`는 라이브에서 **0건** → 종목→테마 역조회 대신 **theme-first** 사용.
- **실전/모의 앱키 별개**: 불일치 호출 시 `[8030: 투자구분...]`. 조회는 `real`이어도 안전(주문 안 함).
- 상세 API 비교·근거는 메모리 `kr-stock-api-findings` 및 커밋 히스토리 참고.

### 검증된 키움 TR 스펙 (조회 전용)
| TR | 엔드포인트 | 핵심 파라미터 | 핵심 응답 필드 |
|---|---|---|---|
| `ka00198` 조회순위 | `/api/dostk/stkinfo` | `qry_tp`(4=당일누적) | `item_inq_rank[]`: `stk_cd·stk_nm·bigd_rank` |
| `ka10059` 수급 | `/api/dostk/stkinfo` | `dt·stk_cd·amt_qty_tp(2=수량)·trde_tp(0=순매수)·unit_tp(1)` | `stk_invsr_orgn[]`: `frgnr_invsr·orgn·ind_invsr` |
| `ka90013` 프로그램 | `/api/dostk/mrkcond` | `dt·stk_cd·amt_qty_tp(1=금액)` | `stk_daly_prm_trde_trnsn[]`: `prm_netprps_amt`(백만원) |
| `ka90001` 테마랭킹 | `/api/dostk/thme` | `qry_tp(0)·flu_pl_amt_tp(3=상위등락률)·stex_tp(1)·date_tp` | `thema_grp[]`: `thema_grp_cd·thema_nm·flu_rt·rising_stk_num·stk_num·main_stk` |
| `ka90002` 테마구성종목 | `/api/dostk/thme` | `thema_grp_cd·stex_tp·date_tp` | `thema_comp_stk[]`: `stk_cd·stk_nm·cur_prc·flu_rt·acc_trde_qty` |

## 6. 알려진 한계 / 미해결(UNCERTAIN)

- `ka90001 date_tp` 허용범위/단위(달력일 vs 영업일) 미확정 — 공식 로그인 가이드로 확인 필요.
- `flu_sig`(등락기호) 코드값 매핑 미확정 → 상승/하락은 `flu_rt` 부호로 판정 중.
- 테마 "주도주수"(hit)는 `--flow-top` 안의 주도주만 집계 → 값이 작으면 `--flow-top`을 키울 것.
- FDR 스냅샷은 **최근 거래일 종가** 기준(실시간 아님). 임의 과거일 전종목 조회 미지원(백테스팅은 개별종목 `DataReader`).

## 7. 다음 작업 (재개 지점)

1. **5단계 백테스팅**: `market.get_price_history(code, start, end)`로 일봉 수집 → "거래대금·수급 상위 종목 종가 매수 → 익일/N일 청산" 룰을 backtrader/vectorbt로 검증. 수수료·거래정지 반영.
2. **다듬기(선택)**: 테마 대장주에 수급(`ka10059`) 붙이기 / 조회순위를 주도주 점수에 반영 / 가중치 튜닝.
3. **6단계**: Streamlit 대시보드 + 매일 결과 parquet/SQLite 적재 → 가중치 사후조정 루프.

## 8. 재개 체크리스트

- [ ] `cd c:\dev\stock` 후 `python -m closebet --kiwoom --themes` 로 정상 동작 확인
- [ ] 안 되면: `.env` 키/`KIWOOM_ENV` 확인 → `python -m closebet.kiwoom_probe`
- [ ] `pip install -r requirements.txt` (환경 새로 만들었을 때)
- [ ] 이 문서 §5(함정)와 §7(다음 작업) 확인 후 진행

---
*이 프로젝트는 조회·분석 전용이며 자동매매를 하지 않습니다. 투자 판단·손실 책임은 본인에게 있습니다.*
