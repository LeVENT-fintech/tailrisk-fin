"""데이터 수집과 일별 패널 구성.

원칙
  * 달력은 S&P 500 거래일(미국 영업일)로 통일한다.
  * 가격·지수는 해당 일 종가를 그대로 쓴다. 한국 데이터(KOSPI, ECOS)는 미국보다 먼저 마감하므로 같은 날짜 값이 t 시점에 알려져 있다.
  * FRED 지표는 발표가 하루 이상 늦으므로 1영업일 뒤로 민다. t 시점 행에는 t-1 값이 들어간다.
  * 수준(level) 열은 휴장일에 앞값으로 채우고, 수익률·순매수 같은 흐름(flow) 열은 채우지 않는다(휴장일 NaN).
  * 자산이 미국 휴장일에 거래한 움직임은 다음 미국 거래일에 합쳐진다(수익률은 앞값 채운 종가의 로그 차분, 순매수는 합산).
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

# 자산 5종: 미국 주식, 한국 주식, 미국 장기채, 금, 달러원
ASSETS = {
    "spx": "^GSPC",      # S&P 500 지수
    "kospi": "^KS11",    # KOSPI 지수
    "tlt": "TLT",        # 미국 20년+ 국채 ETF (2002-07~)
    "gld": "GLD",        # 금 ETF (2004-11~)
    "usdkrw": "KRW=X",   # 달러/원 (2003-12~)
}
# 시장 지표 (종가 수준만 사용)
INDICATORS = {
    "vix": "^VIX",       # S&P 500 내재변동성 (30일)
    "vix9d": "^VIX9D",   # 9일 내재변동성 (2011~). VIX 기간구조의 단기 쪽
    "vix3m": "^VIX3M",   # 3개월 내재변동성 (2006-07~). VIX 기간구조의 장기 쪽
    "move": "^MOVE",     # 미국 국채 내재변동성 (2002-11~)
    "dxy": "DX-Y.NYB",   # 달러지수
    "us10y": "^TNX",     # 미국 10년 국채금리 (%). FRED dgs10 의 대체재
    "us3m": "^IRX",      # 미국 3개월 국채금리 (%). FRED dgs3mo 의 대체재
}
# 단위 환산이 필요한 지표만 적는다. 야후의 ^TNX·^IRX 는 이미 % 단위 (2007-06-12 ^TNX=5.25 로 확인).
INDICATOR_SCALE: dict[str, float] = {}
# 신용 스프레드 대용 ETF (배당 반영 종가 사용). hyg/ief 가격비가 하이일드 스프레드의 대체 신호
ETF_PROXIES = {
    "hyg": "HYG",        # 하이일드 회사채 ETF (2007-04~)
    "lqd": "LQD",        # 투자등급 회사채 ETF (2002-07~)
    "ief": "IEF",        # 미국 7-10년 국채 ETF (2002-07~)
}
# FRED 일별 시계열 (API 키 불필요)
FRED = {
    "hy_oas": "BAMLH0A0HYM2",  # 미국 하이일드 신용스프레드 (%p)
    "t10y2y": "T10Y2Y",        # 10년-2년 금리차 (%p)
    "dgs10": "DGS10",          # 10년 국채금리 (%)
    "dgs3mo": "DGS3MO",        # 3개월 국채금리 (%)
}
# 한국은행 ECOS (키 필요: .env 의 ECOS_API_KEY). (통계표, 항목, 종류) 종류: level=앞값 채움, flow=합산
ECOS = {
    "kr_foreign_netbuy": ("802Y001", "0030000", "flow"),   # 외국인 순매수, 유가증권시장 (억원)
    "kr_base_rate": ("722Y001", "0101000", "level"),       # 한국은행 기준금리 (%)
    "kr_cd91": ("817Y002", "010502000", "level"),          # CD 91일 (%)
    "kr_ktb3y": ("817Y002", "010200000", "level"),         # 국고채 3년 (%)
    "kr_ktb10y": ("817Y002", "010210000", "level"),        # 국고채 10년 (%)
    "usdkrw_1530": ("731Y003", "0000003", "level"),        # 원/달러 종가 15:30, 서울외국환중개
}
OHLC = ["open", "high", "low", "close"]


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


def fetch_fred(series: str, start: str = START, tries: int = 2, timeout: int = 60) -> pd.Series:
    """FRED CSV 엔드포인트. 결측('.')은 NaN. 실패하면 타임아웃을 늘려 재시도."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout * (i + 1)) as r:
                raw = r.read().decode("utf-8")
            break
        except Exception as e:  # noqa: BLE001
            last = e
    else:
        raise RuntimeError(f"FRED {series}: {last}")
    df = pd.read_csv(io.StringIO(raw), na_values=".")
    date_col = df.columns[0]  # observation_date (구버전은 DATE)
    s = pd.Series(df[series].astype(float).values, index=pd.to_datetime(df[date_col]), name=series)
    s.index.name = "date"
    return s


ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/{n}/{stat}/{cycle}/{start}/{end}/{item}"


def fetch_ecos(stat: str, item: str, start: str = START, end: str | None = None,
               key: str | None = None, cycle: str = "D", n: int = 30000) -> pd.Series:
    """한국은행 ECOS StatisticSearch. 키는 인자 또는 환경변수 ECOS_API_KEY. 일별(D) TIME 은 YYYYMMDD."""
    key = key or os.environ.get("ECOS_API_KEY")
    if not key:
        raise RuntimeError("ECOS_API_KEY 가 없습니다 (.env.example 참고)")
    end = end or dt.date.today().strftime("%Y%m%d")
    url = ECOS_URL.format(key=key, n=n, stat=stat, cycle=cycle,
                          start=start.replace("-", ""), end=end.replace("-", ""), item=item)
    with urllib.request.urlopen(url, timeout=60) as r:
        j = json.load(r)
    if "StatisticSearch" not in j:
        raise RuntimeError(f"ECOS {stat}/{item}: {j.get('RESULT', j)}")
    rows = j["StatisticSearch"]["row"]
    fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
    idx = pd.to_datetime([x["TIME"] for x in rows], format=fmt)
    vals = pd.to_numeric([x["DATA_VALUE"] for x in rows], errors="coerce")
    s = pd.Series(vals, index=idx, name=f"{stat}/{item}")
    s.index.name = "date"
    return s[~s.index.duplicated()].sort_index()


def download_all(raw_dir: Path, start: str = START,
                 sources: tuple[str, ...] = ("yahoo", "fred", "ecos")) -> None:
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
    if "fred" in sources:
        for key, sid in FRED.items():
            _save(key, sid, lambda: fetch_fred(sid, start), raw_dir / f"fred_{key}.csv")
    if "ecos" in sources:
        if not os.environ.get("ECOS_API_KEY"):
            print("[warn] ECOS_API_KEY 가 없어 ECOS 수집을 건너뜁니다 (.env.example 참고)")
        else:
            for key, (stat, item, _) in ECOS.items():
                _save(key, f"{stat}/{item}", lambda: fetch_ecos(stat, item, start), raw_dir / f"ecos_{key}.csv")


def load_raw(raw_dir: Path) -> dict[str, dict]:
    """raw_dir 의 CSV 를 묶음별 딕셔너리로 읽는다. 없는 파일은 건너뛴다."""
    raw_dir = Path(raw_dir)

    def _group(prefix: str, keys, as_series: bool) -> dict:
        out = {}
        for k in keys:
            p = raw_dir / f"{prefix}_{k}.csv"
            if p.exists():
                d = pd.read_csv(p, index_col="date", parse_dates=True)
                out[k] = d.iloc[:, 0] if as_series else d
        return out

    return {
        "assets": _group("yahoo", ASSETS, False),
        "indicators": _group("yahoo", INDICATORS, False),
        "proxies": _group("yahoo", ETF_PROXIES, False),
        "fred": _group("fred", FRED, True),
        "ecos": _group("ecos", ECOS, True),
    }


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


def build_panel(assets: dict[str, pd.DataFrame], indicators: dict[str, pd.DataFrame],
                fred: dict[str, pd.Series], proxies: dict[str, pd.DataFrame] | None = None,
                ecos: dict[str, pd.Series] | None = None, calendar_key: str = "spx",
                start: str | None = None) -> pd.DataFrame:
    """원자료를 하나의 일별 표로 합친다. 열 규칙은 모듈 docstring 참고.

    열 이름
      {asset}_open/high/low/close/adj_close : 수준, 휴장일 앞값 채움
      {asset}_ret                           : 로그 수익률, 그 자산이 거래하지 않은 날은 NaN
      {indicator}, {proxy}                  : 수준, 앞값 채움 (proxy 는 배당 반영 종가)
      {fred}                                : 수준, 채권 휴장일 결측 보정 후 1영업일 지연
      {ecos level}                          : 수준, 앞값 채움 (같은 날짜 값, 지연 없음)
      {ecos flow}                           : 흐름, 미국 휴장일 분은 다음 거래일에 합산, 없는 날 NaN
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
        traded = pd.Series(True, index=df.index).reindex(cal, fill_value=False)
        out[f"{key}_ret"] = np.log(lvl["close"]).diff().where(traded)

    for key, df in indicators.items():
        out[key] = df["close"].sort_index().reindex(cal, method="ffill") * INDICATOR_SCALE.get(key, 1.0)

    for key, df in (proxies or {}).items():
        col = "adj_close" if "adj_close" in df.columns else "close"
        out[key] = df[col].sort_index().reindex(cal, method="ffill")

    for key, s in fred.items():
        # 채권시장만 쉬는 날(콜럼버스 데이 등)은 값이 비어 있으므로 앞값으로 채운 뒤 1영업일 지연
        out[key] = s.sort_index().reindex(cal, method="ffill").ffill().shift(1)

    for key, s in (ecos or {}).items():
        kind = ECOS[key][2] if key in ECOS else "level"
        s = s.sort_index()
        out[key] = _fold_flow(s, cal) if kind == "flow" else s.reindex(cal, method="ffill").ffill()

    return out


def build_daily(raw_dir: Path, start: str | None = None) -> pd.DataFrame:
    g = load_raw(raw_dir)
    return build_panel(g["assets"], g["indicators"], g["fred"], proxies=g["proxies"], ecos=g["ecos"], start=start)


def coverage(panel: pd.DataFrame) -> pd.DataFrame:
    """열별 첫 유효일, 마지막 유효일, 결측 수."""
    rows = []
    for c in panel.columns:
        s = panel[c]
        rows.append({"column": c, "first": s.first_valid_index(), "last": s.last_valid_index(),
                     "n_missing": int(s.isna().sum())})
    return pd.DataFrame(rows).set_index("column")
