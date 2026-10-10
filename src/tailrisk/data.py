"""데이터 수집과 일별 패널 구성.

원칙
  * 달력은 S&P 500 거래일(미국 영업일)로 통일한다.
  * 가격·지수는 해당 일 종가를 그대로 쓴다. 한국 데이터(KOSPI, ECOS)는 미국보다 먼저 마감하므로 같은 날짜 값이 t 시점에 알려져 있다.
  * FRED 지표는 발표가 하루 이상 늦으므로 1영업일 뒤로 민다. OFR 금융스트레스지수는 2영업일. ECOS 콜금리는 익일 공표라 1영업일.
  * 수준(level) 열은 휴장일에 앞값으로 채우고, 수익률·순매수·거래대금 같은 흐름(flow) 열은 채우지 않는다(휴장일 NaN).
  * 자산이 미국 휴장일에 거래한 움직임은 다음 미국 거래일에 합쳐진다(수익률은 앞값 채운 종가의 로그 차분, 흐름은 합산).
  * 수익률(_ret)은 배당 포함 종가(adj_close)의 로그 차분이다. 배분 백테스트와 변동성 계산 모두 이 열을 쓴다.

자산 구성 (assemble_assets)
  * spx    : ^GSPC OHLC, adj_close 는 S&P 500 총수익지수(^SP500TR) 수준, 거래량은 SPY.
  * kospi  : ^KS11 OHLC. 야후에 빠진 실제 거래일은 ECOS KOSPI 종가로 채움. adj_close 는 ECOS 월별 배당수익률을 일별로 적립.
  * tlt    : TLT OHLC + adj_close(분배금 반영).
  * gld    : GLD OHLC + adj_close. 상장 전(2000-08~2004-11)은 금 선물 GC=F 종가를 접합(고저가 없음).
  * usdkrw : ECOS 서울외국환중개 시가·고가·저가·15:30 종가. 야후 KRW=X 는 고저가 불량이라 교차 검증용 열(usdkrw_yahoo)로만 둔다.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

START = "2000-01-01"
OHLC = ["open", "high", "low", "close"]

# 야후 OHLCV 자산 (달러원은 ECOS 로 구성)
ASSETS = {
    "spx": "^GSPC",      # S&P 500 지수
    "kospi": "^KS11",    # KOSPI 지수
    "tlt": "TLT",        # 미국 20년+ 국채 ETF (2002-07~)
    "gld": "GLD",        # 금 ETF (2004-11~)
}
ASSET_KEYS = ["spx", "kospi", "tlt", "gld", "usdkrw"]
TOTAL_RETURN = {"spx": "^SP500TR"}   # 배당 포함 지수 -> {asset}_adj_close (1988~)
BACKFILL = {"gld": "GC=F"}           # 상장 전 구간을 선물 종가로 접합 (2000-08~)
VOLUME_FROM = {"spx": "spy"}         # ^GSPC 거래량 정의가 불명확 -> SPY 거래량 사용

# 시장 지표 (종가 수준만 사용)
INDICATORS = {
    "vix": "^VIX",            # S&P 500 내재변동성 (30일)
    "vix9d": "^VIX9D",        # 9일 내재변동성 (2011~)
    "vix3m": "^VIX3M",        # 3개월 내재변동성 (2006-07~)
    "vvix": "^VVIX",          # VIX 의 변동성 (2007~)
    "gvz": "^GVZ",            # 금 ETF 내재변동성 (2008-06~)
    "ovx": "^OVX",            # 원유 ETF 내재변동성 (2007-05~)
    "move": "^MOVE",          # 미국 국채 내재변동성 (2002-11~)
    "dxy": "DX-Y.NYB",        # 달러지수
    "usdjpy": "JPY=X",        # 달러/엔 (1996-10~). 엔 캐리 청산 전이 지표
    "wti": "CL=F",            # WTI 원유 선물 (2000-08~). 2020-04-20 에 음수 종가 있음
    "us10y": "^TNX",          # 미국 10년 국채금리 (%)
    "us3m": "^IRX",           # 미국 3개월 국채금리 (%)
    "usdkrw_yahoo": "KRW=X",  # 야후 달러/원 종가 (2003-12~). ECOS 종가의 교차 검증용
}
INDICATOR_SCALE: dict[str, float] = {}
# 배당 반영 종가를 수준으로 쓰는 ETF
ETF_PROXIES = {
    "hyg": "HYG",        # 하이일드 회사채 ETF (2007-04~)
    "lqd": "LQD",        # 투자등급 회사채 ETF (2002-07~)
    "ief": "IEF",        # 미국 7-10년 국채 ETF (2002-07~)
    "ewy": "EWY",        # 한국 주식 ETF (2000-05~). KOSPI 마감 뒤 미국 장중 정보
    "spy": "SPY",        # S&P 500 ETF (1993~). spx 거래량의 출처
}
# FRED 일별 시계열. 키(.env 의 FRED_API_KEY)가 있으면 API 서버, 없으면 CSV 엔드포인트
FRED = {
    "hy_oas": "BAMLH0A0HYM2",  # 하이일드 신용스프레드 (%p). 2026-04 부터 FRED 가 최근 3년만 제공
    "baa10y": "BAA10Y",        # 무디스 Baa 회사채 - 10년 국채 (%p). 1986~
    "aaa10y": "AAA10Y",        # 무디스 Aaa 회사채 - 10년 국채 (%p)
    "t10y2y": "T10Y2Y",        # 10년-2년 금리차 (%p)
    "dgs10": "DGS10",          # 10년 국채금리 (%)
    "dgs3mo": "DGS3MO",        # 3개월 국채금리 (%)
    "cp3m": "DCPF3M",          # AA 금융 CP 3개월 금리 (%). cp3m - dgs3mo 가 자금조달 스프레드 (1997~)
    "tips10": "DFII10",        # 10년 물가연동채 실질금리 (%). dgs10 - tips10 이 기대인플레이션 (2003~)
    "fedfunds": "DFF",         # 실효 연방기금금리 (%). 현금 레그·조달비용 기준
}
FRED_LAG = 1
# 한국은행 ECOS. (통계표, 항목, 종류)
#   level: 당일 값, 앞값 채움 | level_lag1: 익일 공표라 1영업일 지연 | flow: 미국 휴장일 분 합산, 없는 날 NaN
#   monthly: 월별 값을 2개월 뒤부터 일별로 사용 | asset: 자산 구성용, 패널 열로 직접 들어가지 않음
ECOS = {
    "kr_foreign_netbuy": ("802Y001", "0030000", "flow"),     # 외국인 순매수, 유가증권시장 (억원, 2003~)
    "kr_value": ("802Y001", "0088000", "flow"),              # 거래대금, 유가증권시장 (억원)
    "kr_mcap": ("802Y001", "0183000", "level"),              # 시가총액, 유가증권시장 (억원, 2003~)
    "kr_base_rate": ("722Y001", "0101000", "level"),         # 한국은행 기준금리 (%)
    "kr_cd91": ("817Y002", "010502000", "level"),            # CD 91일 (%)
    "kr_ktb3y": ("817Y002", "010200000", "level"),           # 국고채 3년 (%)
    "kr_ktb10y": ("817Y002", "010210000", "level"),          # 국고채 10년 (%, 2000-12~)
    "kr_call": ("817Y002", "010101000", "level_lag1"),       # 콜금리 1일물 (%). 현금 레그 기준
    "kospi_divyield": ("901Y014", "1100000", "monthly"),     # KOSPI 배당수익률 (%, 월별, 2004~)
    "kospi_ecos": ("802Y001", "0001000", "asset"),           # KOSPI 종가 (야후 결측 거래일 보완)
    "usdkrw_open": ("731Y003", "0000002", "asset"),          # 원/달러 시가 (서울외국환중개)
    "usdkrw_high": ("731Y003", "0000005", "asset"),          # 원/달러 고가
    "usdkrw_low": ("731Y003", "0000004", "asset"),           # 원/달러 저가
    "usdkrw_close": ("731Y003", "0000003", "asset"),         # 원/달러 종가 15:30
}
ECOS_MONTHLY_OFFSET_MONTHS = 2
# OFR 금융스트레스지수 (일별, 2000~). 2영업일 전 데이터 기준 공표
OFR_URL = "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv"
OFR_COLS = {"OFR FSI": "ofr_fsi", "Credit": "ofr_credit", "Funding": "ofr_funding",
            "Volatility": "ofr_volatility", "Emerging markets": "ofr_em"}
OFR_LAG = 2


# ---------------------------------------------------------------- 수집

def load_env(path: Path) -> None:
    """.env 의 KEY=VALUE 를 환경변수에 넣는다 (이미 있으면 유지). 외부 의존성 없는 최소 구현."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def fetch_yahoo(ticker: str, start: str = START) -> pd.DataFrame:
    """야후 일별 OHLCV. 열: open, high, low, close, adj_close, volume. 인덱스: date (tz 없음)."""
    import yfinance as yf

    df = yf.download(ticker, start=start, progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        raise RuntimeError(f"no data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                            "Adj Close": "adj_close", "Volume": "volume"})
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "date"
    return df[OHLC + ["adj_close", "volume"]].dropna(subset=["close"])


FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"


def _fetch_fred_api(series: str, start: str, key: str, timeout: int) -> pd.Series:
    """FRED API 서버 경로. 오류 메시지에 URL(키 포함)을 남기지 않는다."""
    import urllib.parse

    q = urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json",
                                "observation_start": start})
    try:
        with urllib.request.urlopen(f"{FRED_API_URL}?{q}", timeout=timeout) as r:
            j = json.load(r)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"FRED API {series}: {type(e).__name__} {getattr(e, 'code', '')}".strip()) from None
    obs = j.get("observations")
    if obs is None:
        raise RuntimeError(f"FRED API {series}: {j.get('error_message', 'unexpected response')}")
    s = pd.Series(pd.to_numeric([o["value"] for o in obs], errors="coerce"),   # '.' -> NaN
                  index=pd.to_datetime([o["date"] for o in obs]), name=series)
    s.index.name = "date"
    return s


def fetch_fred(series: str, start: str = START, tries: int = 2, timeout: int = 60,
               key: str | None = None) -> pd.Series:
    """FRED. 키(FRED_API_KEY)가 있으면 API 서버, 없으면 CSV 엔드포인트. 결측('.')은 NaN."""
    key = key or os.environ.get("FRED_API_KEY")
    if key:
        return _fetch_fred_api(series, start, key, timeout)
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
    last: Exception | None = None
    for i in range(tries):
        try:
            raw = _get(url, timeout * (i + 1))
            break
        except Exception as e:  # noqa: BLE001
            last = e
    else:
        raise RuntimeError(f"FRED {series}: {last}")
    df = pd.read_csv(io.StringIO(raw), na_values=".")
    s = pd.Series(df[series].astype(float).values, index=pd.to_datetime(df[df.columns[0]]), name=series)
    s.index.name = "date"
    return s


ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/{n}/{stat}/{cycle}/{start}/{end}/{item}"


def fetch_ecos(stat: str, item: str, start: str = START, end: str | None = None,
               key: str | None = None, cycle: str = "D", n: int = 30000) -> pd.Series:
    """한국은행 ECOS StatisticSearch. 키는 인자 또는 환경변수 ECOS_API_KEY. 일별(D) TIME 은 YYYYMMDD, 월별(M) 은 YYYYMM."""
    key = key or os.environ.get("ECOS_API_KEY")
    if not key:
        raise RuntimeError("ECOS_API_KEY 가 없습니다 (.env.example 참고)")
    width = 6 if cycle == "M" else 8
    end = (end or dt.date.today().strftime("%Y%m%d")).replace("-", "")[:width]
    url = ECOS_URL.format(key=key, n=n, stat=stat, cycle=cycle,
                          start=start.replace("-", "")[:width], end=end, item=item)
    with urllib.request.urlopen(url, timeout=60) as r:
        j = json.load(r)
    if "StatisticSearch" not in j:
        raise RuntimeError(f"ECOS {stat}/{item}: {j.get('RESULT', j)}")
    rows = j["StatisticSearch"]["row"]
    fmt = "%Y%m" if cycle == "M" else "%Y%m%d"
    idx = pd.to_datetime([x["TIME"] for x in rows], format=fmt)
    vals = pd.to_numeric([x["DATA_VALUE"] for x in rows], errors="coerce")
    s = pd.Series(vals, index=idx, name=f"{stat}/{item}")
    s.index.name = "date"
    return s[~s.index.duplicated()].sort_index()


def fetch_ofr() -> pd.DataFrame:
    """OFR 금융스트레스지수 CSV. 열 이름은 OFR_COLS 로 바꾼다."""
    df = pd.read_csv(io.StringIO(_get(OFR_URL)))
    df["date"] = pd.to_datetime(df["Date"])
    df = df.set_index("date").sort_index()
    return df[list(OFR_COLS)].rename(columns=OFR_COLS).astype(float)


def download_all(raw_dir: Path, start: str = START,
                 sources: tuple[str, ...] = ("yahoo", "fred", "ecos", "ofr")) -> None:
    """원자료를 받아 raw_dir 에 CSV 로 저장한다. 하나가 실패해도 나머지는 계속."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    def _save(key: str, label: str, fn, path: Path) -> None:
        try:
            obj = fn()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {key} ({label}) 실패: {e}")
            return
        obj.to_csv(path, header=True)
        print(f"{key:18s} {label:18s} {obj.index.min().date()} ~ {obj.index.max().date()}  {len(obj):6d} rows")

    if "yahoo" in sources:
        for key, tk in {**ASSETS, **INDICATORS, **ETF_PROXIES}.items():
            _save(key, tk, lambda: fetch_yahoo(tk, start), raw_dir / f"yahoo_{key}.csv")
        for key, tk in TOTAL_RETURN.items():
            _save(f"{key}_tr", tk, lambda: fetch_yahoo(tk, start), raw_dir / f"yahoo_tr_{key}.csv")
        for key, tk in BACKFILL.items():
            _save(f"{key}_backfill", tk, lambda: fetch_yahoo(tk, start), raw_dir / f"yahoo_backfill_{key}.csv")
    if "fred" in sources:
        for key, sid in FRED.items():
            _save(key, sid, lambda: fetch_fred(sid, start), raw_dir / f"fred_{key}.csv")
    if "ecos" in sources:
        if not os.environ.get("ECOS_API_KEY"):
            print("[warn] ECOS_API_KEY 가 없어 ECOS 수집을 건너뜁니다 (.env.example 참고)")
        else:
            for key, (stat, item, kind) in ECOS.items():
                cycle = "M" if kind == "monthly" else "D"
                _save(key, f"{stat}/{item}", lambda: fetch_ecos(stat, item, start, cycle=cycle),
                      raw_dir / f"ecos_{key}.csv")
    if "ofr" in sources:
        _save("ofr_fsi", "OFR FSI", fetch_ofr, raw_dir / "ofr_fsi.csv")


def load_raw(raw_dir: Path) -> dict[str, dict]:
    """raw_dir 의 CSV 를 묶음별 딕셔너리로 읽는다. 없는 파일은 건너뛴다."""
    raw_dir = Path(raw_dir)

    def _read(p: Path, as_series: bool):
        d = pd.read_csv(p, index_col="date", parse_dates=True)
        return d.iloc[:, 0] if as_series else d

    def _group(prefix: str, keys, as_series: bool) -> dict:
        return {k: _read(raw_dir / f"{prefix}_{k}.csv", as_series) for k in keys
                if (raw_dir / f"{prefix}_{k}.csv").exists()}

    return {
        "assets": _group("yahoo", ASSETS, False),
        "tr": _group("yahoo_tr", TOTAL_RETURN, False),
        "backfill": _group("yahoo_backfill", BACKFILL, False),
        "indicators": _group("yahoo", INDICATORS, False),
        "proxies": _group("yahoo", ETF_PROXIES, False),
        "fred": _group("fred", FRED, True),
        "ecos": _group("ecos", ECOS, True),
        "ofr": _group("ofr", ["fsi"], False).get("fsi"),
    }


# ---------------------------------------------------------------- 자산 구성

def _weekdays(s: pd.Series) -> pd.Series:
    """ECOS 에 섞인 주말 행(2000년 토요일 10개 등)을 지운다."""
    return s[s.index.dayofweek < 5]


def fx_from_ecos(ecos: dict[str, pd.Series]) -> pd.DataFrame | None:
    """ECOS 서울외국환중개 시가·고가·저가·종가로 달러원 자산 표를 만든다."""
    if "usdkrw_close" not in ecos:
        return None
    df = pd.DataFrame({c: _weekdays(ecos[f"usdkrw_{c}"]) for c in OHLC if f"usdkrw_{c}" in ecos})
    df = df.dropna(subset=["close"]).sort_index()
    df["adj_close"] = df["close"]
    df["volume"] = np.nan
    df.index.name = "date"
    return df[OHLC + ["adj_close", "volume"]] if all(c in df for c in OHLC) else None


def fill_missing_days(df: pd.DataFrame, official_close: pd.Series) -> pd.DataFrame:
    """야후에 빠진 실제 거래일을 공식 종가로 채운다 (OHLC 모두 종가, 거래량 NaN)."""
    oc = _weekdays(official_close.dropna())
    missing = oc.index.difference(df.index)
    missing = missing[(missing >= df.index.min()) & (missing <= df.index.max())]
    if len(missing) == 0:
        return df
    add = pd.DataFrame({c: oc.loc[missing] for c in OHLC + ["adj_close"]}, index=missing)
    add["volume"] = np.nan
    return pd.concat([df, add]).sort_index()


def backfill_with(df: pd.DataFrame, proxy: pd.DataFrame) -> pd.DataFrame:
    """상장 전 구간을 대리 계열(선물) 종가로 이어 붙인다. 접합일 종가 비율로 수준을 맞추고 고저가는 비운다."""
    first = df.index.min()
    p = proxy.sort_index()
    before = p[p.index < first]
    if before.empty:
        return df
    anchor = p["close"].asof(first)
    if not np.isfinite(anchor) or anchor <= 0:
        return df
    scale = float(df["close"].iloc[0]) / float(anchor)
    add = pd.DataFrame(index=before.index)
    add["open"] = np.nan
    add["high"] = np.nan
    add["low"] = np.nan
    add["close"] = before["close"] * scale
    add["adj_close"] = add["close"] * float(df["adj_close"].iloc[0]) / float(df["close"].iloc[0])
    add["volume"] = np.nan
    return pd.concat([add, df]).sort_index()


def accrue_dividends(df: pd.DataFrame, divyield_monthly: pd.Series,
                     offset_months: int = ECOS_MONTHLY_OFFSET_MONTHS) -> pd.DataFrame:
    """가격지수에 월별 배당수익률(%)을 일별로 적립해 adj_close 를 만든다. 수익률이 없는 구간은 적립 0."""
    dy = divyield_monthly.dropna().sort_index()
    dy.index = dy.index + pd.DateOffset(months=offset_months)   # m월 값은 m+2월 초부터 사용
    daily = dy.reindex(df.index, method="ffill").fillna(0.0) / 100.0 / 252.0
    ret = np.log(df["close"]).diff().fillna(0.0) + daily
    ret.iloc[0] = 0.0
    out = df.copy()
    out["adj_close"] = float(df["close"].iloc[0]) * np.exp(ret.cumsum())
    return out


def assemble_assets(g: dict) -> dict[str, pd.DataFrame]:
    """원자료 묶음에서 자산 5종의 OHLC·adj_close·volume 표를 만든다 (모듈 docstring 참고)."""
    assets = {k: v.sort_index().copy() for k, v in g.get("assets", {}).items()}
    ecos = g.get("ecos", {}) or {}
    tr, bf, px = g.get("tr", {}) or {}, g.get("backfill", {}) or {}, g.get("proxies", {}) or {}

    if "kospi" in assets and "kospi_ecos" in ecos:
        assets["kospi"] = fill_missing_days(assets["kospi"], ecos["kospi_ecos"])
    if "kospi" in assets and "kospi_divyield" in ecos:
        assets["kospi"] = accrue_dividends(assets["kospi"], ecos["kospi_divyield"])
    for key, t in tr.items():
        if key in assets:
            assets[key]["adj_close"] = t["close"].sort_index().reindex(assets[key].index, method="ffill")
    for key, b in bf.items():
        if key in assets:
            assets[key] = backfill_with(assets[key], b)
    for key, src in VOLUME_FROM.items():
        if key in assets and src in px:
            assets[key]["volume"] = px[src]["volume"].sort_index().reindex(assets[key].index)
    fx = fx_from_ecos(ecos)
    if fx is not None:
        assets["usdkrw"] = fx
    return assets


# ---------------------------------------------------------------- 패널

def _fold_flow(s: pd.Series, cal: pd.DatetimeIndex) -> pd.Series:
    """달력에 없는 날의 흐름 값을 다음 달력일에 합산한다. 달력일에 흐름이 없으면 NaN.
    달력 마지막 날 이후 값은 버린다(미래 값이 마지막 행에 섞이지 않게)."""
    s = s.dropna().sort_index()
    s = s[(s.index >= cal[0]) & (s.index <= cal[-1])]
    if s.empty:
        return pd.Series(np.nan, index=cal)
    pos = cal.searchsorted(s.index)
    return s.groupby(cal[pos]).sum().reindex(cal)


def _monthly_to_daily(s: pd.Series, cal: pd.DatetimeIndex,
                      offset_months: int = ECOS_MONTHLY_OFFSET_MONTHS) -> pd.Series:
    s = s.dropna().sort_index()
    s.index = s.index + pd.DateOffset(months=offset_months)
    return s.reindex(cal, method="ffill")


def build_panel(assets: dict[str, pd.DataFrame], indicators: dict[str, pd.DataFrame],
                fred: dict[str, pd.Series], proxies: dict[str, pd.DataFrame] | None = None,
                ecos: dict[str, pd.Series] | None = None, ofr: pd.DataFrame | None = None,
                calendar_key: str = "spx", start: str | None = None) -> pd.DataFrame:
    """자산·지표 표를 하나의 일별 표로 합친다. 열 규칙은 모듈 docstring 참고.

    열 이름
      {asset}_open/high/low/close/adj_close : 수준, 휴장일 앞값 채움
      {asset}_volume                        : 거래량, 거래 없는 날 NaN
      {asset}_ret                           : adj_close 로그 수익률, 거래 없는 날 NaN
      {indicator}, {proxy}                  : 수준, 앞값 채움 (proxy 는 배당 반영 종가)
      {fred}                                : 수준, 결측 보정 후 1영업일 지연
      {ecos level / level_lag1 / flow / monthly}, {ofr}: 종류별 규칙 (ECOS 설명 참고)
    """
    if calendar_key not in assets:
        raise KeyError(f"calendar asset '{calendar_key}' missing")
    cal = assets[calendar_key].index.sort_values()
    if start is not None:
        cal = cal[cal >= pd.Timestamp(start)]
    out = pd.DataFrame(index=cal)
    out.index.name = "date"

    for key, df in assets.items():
        df = df.sort_index()
        cols = OHLC + (["adj_close"] if "adj_close" in df.columns else [])
        lvl = df[cols].reindex(cal, method="ffill")
        for c in cols:
            out[f"{key}_{c}"] = lvl[c]
        if "volume" in df.columns and df["volume"].notna().any():
            out[f"{key}_volume"] = df["volume"].reindex(cal)
        traded = pd.Series(True, index=df.index).reindex(cal, fill_value=False)
        basis = lvl["adj_close"] if "adj_close" in cols else lvl["close"]
        out[f"{key}_ret"] = np.log(basis).diff().where(traded)

    for key, df in indicators.items():
        out[key] = df["close"].sort_index().reindex(cal, method="ffill") * INDICATOR_SCALE.get(key, 1.0)

    for key, df in (proxies or {}).items():
        col = "adj_close" if "adj_close" in df.columns else "close"
        out[key] = df[col].sort_index().reindex(cal, method="ffill")

    for key, s in fred.items():
        out[key] = s.sort_index().reindex(cal, method="ffill").ffill().shift(FRED_LAG)

    for key, s in (ecos or {}).items():
        kind = ECOS[key][2] if key in ECOS else "level"
        if kind == "asset":
            continue
        s = _weekdays(s.sort_index())
        if kind == "flow":
            out[key] = _fold_flow(s, cal)
        elif kind == "monthly":
            out[key] = _monthly_to_daily(s, cal)
        elif kind == "level_lag1":
            out[key] = s.reindex(cal, method="ffill").ffill().shift(1)
        else:
            out[key] = s.reindex(cal, method="ffill").ffill()

    if ofr is not None:
        o = ofr.sort_index().reindex(cal, method="ffill").ffill().shift(OFR_LAG)
        for c in o.columns:
            out[c] = o[c]

    return out


def build_daily(raw_dir: Path, start: str | None = None) -> pd.DataFrame:
    g = load_raw(raw_dir)
    assets = assemble_assets(g)
    return build_panel(assets, g["indicators"], g["fred"], proxies=g["proxies"], ecos=g["ecos"],
                       ofr=g["ofr"], start=start)


def coverage(panel: pd.DataFrame) -> pd.DataFrame:
    """열별 첫 유효일, 마지막 유효일, 결측 수."""
    rows = []
    for c in panel.columns:
        s = panel[c]
        rows.append({"column": c, "first": s.first_valid_index(), "last": s.last_valid_index(),
                     "n_missing": int(s.isna().sum())})
    return pd.DataFrame(rows).set_index("column")
