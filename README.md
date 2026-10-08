# tailrisk

변동성 급등(테일리스크)을 며칠 앞서 감지하고, 그 신호로 포트폴리오 비중을 조절하는 **리스크 타겟팅 자산배분** 실험입니다.

> 질문: **LightGBM 이 HAR-RV·GARCH 보다 앞으로 20영업일 실현 변동성을 더 잘 맞히는가? 그 예측으로 목표 변동성 배분을 하면 고정 비중보다 최대낙폭이 줄어드는가?**
>
> 상태: 데이터 수집 완료. 다음: 피처·라벨 → 베이스라인(HAR-RV, GARCH) → LightGBM → 변동성 타겟 배분 → MLflow·Airflow.
> 기한: 2026-10-19. 일정과 범위: [docs/00_plan.md](docs/00_plan.md)

## 시작하기
```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"            # macOS/Linux: .venv/bin/pip
.venv/Scripts/pip install -e ".[ml,viz]"         # 모델링 단계에서 추가

.venv/Scripts/python pipelines/collect_data.py   # 야후·FRED -> data/raw, data/processed/daily.csv (약 1분)
.venv/Scripts/pytest -q
```
원자료와 가공 데이터는 Git 에 넣지 않으므로 위 명령으로 재생성합니다. API 키는 필요 없습니다.

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
달력은 S&P 500 거래일. 시작 2000-01.

| 열 | 내용 | 출처 | 시점 규칙 |
|---|---|---|---|
| `spx_*`, `kospi_*`, `tlt_*`, `gld_*`, `usdkrw_*` | 자산 5종 OHLC 와 로그수익률 `_ret` | Yahoo Finance | 당일 종가. 휴장일은 수준만 앞값 채움, 수익률은 NaN |
| `vix`, `move`, `dxy` | 주식·채권 내재변동성, 달러지수 | Yahoo Finance | 당일 종가 |
| `us10y`, `us3m` | 미국 10년·3개월 국채금리 (%) | Yahoo Finance (`^TNX`, `^IRX`) | 당일 종가 |
| `hy_oas`, `t10y2y`, `dgs10`, `dgs3mo` | 하이일드 스프레드, 10y-2y, 10y, 3m 금리 | FRED | **1영업일 지연** (발표 지연 반영) |

TLT 는 2002-07, GLD 는 2004-11, 달러원은 2003-12 부터 있습니다. 평가는 2010 년 이후로 잡을 예정이라 영향 없습니다.
금리는 야후(당일)와 FRED(1일 지연) 두 벌이 있는데, 피처에는 당일 값인 `us10y`·`us3m` 을 쓰고 FRED 는 교차 검증용입니다.

**FRED 수집 상태 (2026-10-08):** `fred.stlouisfed.org` 가 응답하지 않아 `hy_oas` 는 아직 없습니다. 나머지 3종은 2026-09-30 까지의 캐시로 채웠습니다. 복구되면 `--source fred` 로 다시 받습니다.
