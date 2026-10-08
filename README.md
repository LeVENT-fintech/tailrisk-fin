# tailrisk

변동성 급등(테일리스크)을 며칠 앞서 감지하고, 그 신호로 포트폴리오 비중을 조절하는 **리스크 타겟팅 자산배분** 실험입니다.

> 질문: **LightGBM 이 HAR-RV·GARCH 보다 앞으로 20영업일 실현 변동성을 더 잘 맞히는가? 그 예측으로 목표 변동성 배분을 하면 고정 비중보다 최대낙폭이 줄어드는가?**
>
> 상태: 데이터 수집 완료 (FRED 하이일드 스프레드만 서버 불통으로 보류). 다음: 피처·라벨 → 베이스라인(HAR-RV, GARCH) → LightGBM → 변동성 타겟 배분 → MLflow·Airflow.
> 계획과 결정 기록: [docs/00_plan.md](docs/00_plan.md)

## 시작하기
```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"            # macOS/Linux: .venv/bin/pip
.venv/Scripts/pip install -e ".[ml,viz]"         # 모델링 단계에서 추가

cp .env.example .env                             # ECOS_API_KEY 입력 (한국 데이터용. 없으면 ECOS 만 건너뜀)
.venv/Scripts/python pipelines/collect_data.py   # 야후·FRED·ECOS -> data/raw, data/processed/daily.csv (약 1분)
.venv/Scripts/pytest -q
```
원자료와 가공 데이터는 Git 에 넣지 않으므로 위 명령으로 재생성합니다. 야후와 FRED 는 키가 필요 없습니다.
일부 출처만 다시 받으려면 `--source yahoo|fred|ecos`, 받아 둔 원자료로 패널만 다시 만들려면 `--skip-download`.

## 구조
```
src/tailrisk/     라이브러리 (data.py: 수집·패널 구성)
pipelines/        실행 스크립트 (collect_data.py)
tests/            패널 규칙 검증
data/raw/         원자료 CSV (Git 제외)
data/processed/   daily.csv (Git 제외)
results/          평가 결과 CSV·그림
docs/             계획, 데이터 설명, 결과 문서
```

## 데이터 (`data/processed/daily.csv`)
달력은 S&P 500 거래일. 2000-01-03 부터, 약 50열.

| 열 | 내용 | 출처 | 시작 | 시점 규칙 |
|---|---|---|---|---|
| `spx_*`, `kospi_*`, `tlt_*`, `gld_*`, `usdkrw_*` | 자산 5종 OHLC, 배당 반영 종가 `_adj_close`, 로그수익률 `_ret` | Yahoo | SPX·KOSPI 2000, TLT 2002-07, USDKRW 2003-12, GLD 2004-11 | 당일 종가. 휴장일은 수준만 앞값 채움, 수익률은 NaN |
| `vix`, `vix9d`, `vix3m` | S&P 500 내재변동성 30일·9일·3개월 (기간구조) | Yahoo | 2000, 2011, 2006-07 | 당일 종가 |
| `move`, `dxy` | 미국 국채 내재변동성, 달러지수 | Yahoo | 2002-11, 2000 | 당일 종가 |
| `us10y`, `us3m` | 미국 10년·3개월 국채금리 (%) | Yahoo (`^TNX`, `^IRX`) | 2000 | 당일 종가 |
| `hyg`, `lqd`, `ief` | 하이일드·투자등급 회사채, 7-10년 국채 ETF (배당 반영 종가). `hyg/ief` 비율이 신용스프레드 대용 | Yahoo | 2007-04, 2002-07, 2002-07 | 당일 종가 |
| `hy_oas`, `t10y2y`, `dgs10`, `dgs3mo` | 하이일드 스프레드, 10y-2y, 10y, 3m 금리 | FRED | 2000 | **1영업일 지연**. 채권시장만 쉬는 날은 앞값 채움 |
| `kr_ktb3y`, `kr_ktb10y`, `kr_cd91`, `kr_base_rate` | 국고채 3년·10년, CD 91일, 기준금리 (%) | ECOS | 2000 (10년은 2000-12) | 당일 값 (한국이 먼저 마감) |
| `kr_foreign_netbuy` | 외국인 순매수, 유가증권시장 (억원) | ECOS | 2003-01 | 흐름. 미국 휴장일 분은 다음 거래일에 합산, 한국 휴장일은 NaN |
| `usdkrw_1530` | 원/달러 15:30 종가 (서울외국환중개) | ECOS | 2000 | 당일 값. 야후 `KRW=X` 의 교차 검증용 |

금리는 야후(당일)와 FRED(1일 지연) 두 벌이 있습니다. 피처에는 당일 값인 `us10y`·`us3m` 을 쓰고 FRED 는 교차 검증용입니다 (상관 0.999).

**수집 상태 (2026-10-08)**
- FRED: `fred.stlouisfed.org` 가 이 네트워크에서 응답하지 않아 `hy_oas` 는 아직 없고, 나머지 3종은 2026-09-30 까지의 캐시입니다. 복구되면 `--source fred`. 끝내 안 되면 `hyg/ief` 비율로 대체합니다.
- ECOS: 수집 완료. 외국인 순매수는 ECOS 일별 계열이 2003-01 부터라 그 이전은 비어 있고, 국고채 10년은 2000-12 부터입니다. 항목 코드는 ECOS 항목 목록으로 확인했습니다 (`010200000`=국고채 3년, `010210000`=국고채 10년).
