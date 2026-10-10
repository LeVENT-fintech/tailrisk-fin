# tailrisk

변동성 급등(테일리스크)을 며칠 앞서 감지하고, 그 신호로 포트폴리오 비중을 조절하는 **리스크 타겟팅 자산배분** 실험입니다.

> 질문: **LightGBM 이 HAR-RV·GARCH 보다 앞으로 20영업일 실현 변동성을 더 잘 맞히는가? 그 예측으로 목표 변동성 배분을 하면 고정 비중보다 최대낙폭이 줄어드는가?**
>
> 상태: 데이터 수집·피처·라벨 완료. 다음: walk-forward 평가 틀과 베이스라인(지난 21일 변동성, HAR-RV, GARCH) → LightGBM → 변동성 타겟 배분 → MLflow·Airflow.
> 계획과 결정 기록: [docs/00_plan.md](docs/00_plan.md) · 데이터 카탈로그: [docs/01_data_catalog.md](docs/01_data_catalog.md)

## 시작하기
```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"            # macOS/Linux: .venv/bin/pip
.venv/Scripts/pip install -e ".[ml,viz]"         # 모델링 단계에서 추가

cp .env.example .env                             # ECOS_API_KEY (한국 데이터), FRED_API_KEY, KRX_API_KEY 입력
.venv/Scripts/python pipelines/collect_data.py   # 야후·FRED·ECOS·OFR·KRX -> data/raw, data/processed/daily.csv (첫 실행 15분)
.venv/Scripts/python pipelines/build_features.py # daily.csv -> data/processed/features.csv (라벨 + 1차·2차 피처, 약 10초)
.venv/Scripts/pytest -q
```
원자료와 가공 데이터는 Git 에 넣지 않으므로 위 명령으로 재생성합니다. 야후·OFR 은 키가 필요 없고, FRED 는 키가 없으면 CSV 엔드포인트로 받습니다(네트워크에 따라 막힐 수 있음).
일부 출처만 다시 받으려면 `--source yahoo|fred|ecos|ofr|krx|cot|events`, 받아 둔 원자료로 패널만 다시 만들려면 `--skip-download`.

## 구조
```
src/tailrisk/     라이브러리 (data.py: 수집·자산 구성·패널, events.py: 회의 일정·COT, features.py: 라벨·피처)
pipelines/        실행 스크립트 (collect_data.py, build_features.py)
tests/            패널 규칙 검증
data/raw/         원자료 CSV (Git 제외)
data/processed/   daily.csv (Git 제외)
results/          평가 결과 CSV·그림
docs/             계획, 데이터 설명, 결과 문서
```

## 데이터 (`data/processed/daily.csv`)
달력은 S&P 500 거래일. 2000-01-03 부터, 86열. 모든 열은 그 날짜의 미국 종가 시점에 알 수 있는 값입니다.

### 자산 5종 (`{asset}_open/high/low/close/adj_close/volume/ret`)
| 자산 | 가격 출처 | `adj_close` (배당 포함) | 비고 |
|---|---|---|---|
| `spx` S&P 500 | 야후 `^GSPC` | 총수익지수 `^SP500TR` 수준 | 거래량은 SPY |
| `kospi` KOSPI | 야후 `^KS11` | ECOS 월별 배당수익률을 일별 적립 (2004~, 그 전은 가격지수) | 야후에 빠진 거래일 6일을 ECOS 종가로 보완 |
| `tlt` 미국 장기채 ETF | 야후 `TLT` (2002-07~) | 야후 분배금 반영 종가 | |
| `gld` 금 ETF | 야후 `GLD` (2004-11~) | 야후 | 2000-08~2004-11 은 금 선물 `GC=F` 종가로 접합 (고저가 없음) |
| `usdkrw` 달러/원 | **ECOS 서울외국환중개 시가·고가·저가·15:30 종가** (2000~) | 종가와 같음 | 야후 `KRW=X` 는 고저가 불량이라 `usdkrw_yahoo` 열로만 둠 |

`_ret` 는 `adj_close` 의 로그 수익률입니다. 휴장일은 수준만 앞값 채움, 수익률·거래량은 NaN. 미국 휴장일에 거래된 움직임은 다음 미국 거래일 수익률에 합쳐집니다.

### 시장 지표 (당일 종가, 야후)
| 열 | 내용 | 시작 |
|---|---|---|
| `vix`, `vix9d`, `vix3m`, `vvix` | S&P 500 내재변동성 30일·9일·3개월, VIX 의 변동성 | 2000, 2011, 2006, 2007 |
| `move`, `gvz`, `ovx` | 미국 국채·금·원유 내재변동성 | 2002, 2008, 2007 |
| `dxy`, `usdjpy`, `wti` | 달러지수, 달러/엔, WTI 원유 선물 | 2000, 2000, 2000-08 |
| `us10y`, `us3m` | 미국 10년·3개월 국채금리 (%) | 2000 |
| `hyg`, `lqd`, `ief`, `ewy`, `spy` | 하이일드·투자등급 회사채, 7-10년 국채, 한국 주식, S&P 500 ETF (배당 반영 종가) | 2007, 2002, 2002, 2000, 2000 |
| `usdkrw_yahoo` | 야후 달러/원 종가 (교차 검증용) | 2003-12 |

### 미국 거시·신용 (FRED, **1영업일 지연**, 채권 휴장일 앞값 채움)
| 열 | 내용 | 시작 |
|---|---|---|
| `baa10y`, `aaa10y` | 무디스 Baa·Aaa 회사채 − 10년 국채 (%p) | 2000 |
| `hy_oas` | ICE BofA 하이일드 스프레드 (%p). FRED 가 2026-04 부터 최근 3년만 제공 | 2023-10 |
| `t10y2y`, `dgs10`, `dgs3mo` | 10y-2y, 10년, 3개월 금리 | 2000 |
| `cp3m` | AA 금융 CP 3개월 금리. `cp3m - dgs3mo` 가 자금조달 스프레드 | 2000 |
| `tips10` | 10년 물가연동채 실질금리. `dgs10 - tips10` 이 기대인플레이션 | 2003 |
| `fedfunds` | 실효 연방기금금리. 현금 레그·조달비용 기준 | 2000 |

### 한국 (ECOS, 당일 값. 한국이 먼저 마감)
| 열 | 내용 | 시작 | 규칙 |
|---|---|---|---|
| `kr_ktb3y`, `kr_ktb10y`, `kr_cd91`, `kr_base_rate` | 국고채 3년·10년, CD 91일, 기준금리 (%) | 2000 (10년은 2000-12) | 수준 |
| `kr_call` | 콜금리 1일물 (%). 원화 현금 레그 기준 | 2000 | 익일 공표라 **1영업일 지연** |
| `kr_foreign_netbuy`, `kr_value` | 외국인 순매수, 거래대금 (억원, 유가증권시장) | 2003, 2000 | 흐름. 미국 휴장일 분 합산, 한국 휴장일 NaN |
| `kr_mcap` | 시가총액 (억원). `kr_value / kr_mcap` 이 회전율 | 2003 | 수준 |
| `kospi_divyield` | KOSPI 배당수익률 (%, 월별) | 2004 | 월별 값을 2개월 뒤부터 사용 |

### 한국 내재변동성 (KRX Open API, 당일 값)
`vkospi` 코스피200 변동성지수 종가. 2010-01 부터 (Open API 제공 시작). `.env` 의 `KRX_API_KEY` 와 '파생상품지수 일별시세' 서비스 승인이 필요하며, 날짜별 호출이라 첫 수집에 10분 이상 걸리고 이후는 마지막 날짜 다음부터만 받습니다.

### 회의 일정 (사전 공표, 지연 없음)
`fomc_next_bdays`, `fomc_in_20d`, `bok_next_bdays`, `bok_in_20d`: 다음 정례 FOMC·금통위까지 영업일 수와 앞으로 20영업일 안 포함 여부(0/1). 당일 회의는 세지 않습니다(결정이 종가 전에 발표). 임시회의(2001-09, 2008-10, 2020-03)는 제외하고, 취소된 2020-03-18 FOMC 는 당시 예정이었으므로 포함. 출처: 연준 과거·현재 일정 페이지, 한국은행 통화정책방향 결정회의 목록.

### 투자자별 선물 포지션 (CFTC COT, 주별, **보고일 화요일 → 금요일부터 사용**)
`cot_vix_lev`, `cot_es_lev`, `cot_es_am`, `cot_tn_am`, `cot_tn_lev`, `cot_gold_mm`: (롱−숏)/미결제약정. VIX·E-mini S&P 500·10년 국채 선물의 레버리지펀드(lev)·자산운용사(am), 금 선물의 매니지드머니(mm). 2006-06 부터.

### 금융스트레스 (OFR, **2영업일 지연**)
`ofr_fsi`(종합), `ofr_credit`, `ofr_funding`, `ofr_volatility`, `ofr_em`. 2000 년부터 일별.

## 피처·라벨 (`data/processed/features.csv`)
행 = (date, asset). `src/tailrisk/features.py`.
- **라벨 `y`:** log(앞으로 20영업일 실현변동성, 연율). t+1~t+20 수익률로 계산하고 관측이 16개 미만이면 NaN. 마지막 20행은 NaN.
- **1차 피처 36개 (2000년부터):** 자산별 실현변동성 5·21·63일(로그)과 기울기, Parkinson 변동성, 수익률 5·21·63일, 1년 z-점수, 거래량 21/63일 비율, 당일 고저폭; 공통으로 VIX 수준·21일 변화·실현변동성 대비, 달러지수·달러엔·WTI 변화와 변동성, 미국 10년·커브, 무디스 스프레드와 변화, CP 스프레드, OFR 3종, 한국 3년·한미 금리차, FOMC·금통위 일정.
- **2차 피처 22개 (늦게 시작):** VIX 기간구조, VVIX/VIX, MOVE, GVZ, OVX, VKOSPI(수준·실현 대비), 신용 ETF 비율과 변화, TIPS·기대인플레이션 변화, 외국인 순매수/거래대금 21일, 회전율 5/63일, EWY 변동성·KOSPI 괴리, COT 6종, VIX9D/VIX.
- 미래 패널을 잘라내도 과거 피처와 완전한 창의 라벨이 변하지 않는 것을 테스트로 고정 (`tests/test_features.py`).

**수집 상태 (2026-10-11)**
- FRED 는 `.env` 의 `FRED_API_KEY` 로 API 서버에서 받습니다 (CSV 엔드포인트는 이 네트워크에서 불통).
- VKOSPI 는 KRX Open API 로 수집 (2010~). `--source krx` 로 증분 갱신.
