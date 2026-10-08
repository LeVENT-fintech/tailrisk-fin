"""데이터 수집과 일별 패널 구성.

원칙
  * 달력은 S&P 500 거래일(미국 영업일)로 통일한다.
  * 가격·지수는 해당 일 종가를 그대로 쓴다. KOSPI 는 미국보다 먼저 마감하므로 같은 날짜 값이 t 시점에 알려져 있다.
  * FRED 지표는 발표가 하루 이상 늦으므로 1영업일 뒤로 민다. t 시점 행에는 t-1 값이 들어간다.
  * 수준(level) 열은 휴장일에 앞값으로 채우고, 수익률 열은 채우지 않는다(휴장일 NaN).
  * 자산이 미국 휴장일에 거래한 움직임은 다음 미국 거래일 수익률에 합쳐진다(앞값 채운 종가의 로그 차분).
"""
from __future__ import annotations

import io
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
    "vix": "^VIX",       # S&P 500 내재변동성
    "move": "^MOVE",     # 미국 국채 내재변동성
    "dxy": "DX-Y.NYB",   # 달러지수
    "us10y": "^TNX",     # 미국 10년 국채금리 (%). FRED dgs10 의 대체재
    "us3m": "^IRX",      # 미국 3개월 국채금리 (%). FRED dgs3mo 의 대체재
}
# 단위 환산이 필요한 지표만 적는다. 야후의 ^TNX·^IRX 는 이미 % 단위 (2007-06-12 ^TNX=5.25 로 확인).
INDICATOR_SCALE: dict[str, float] = {}
# FRED 일별 시계열 (API 키 불필요)
FRED = {
    "hy_oas": "BAMLH0A0HYM2",  # 미국 하이일드 신용스프레드 (%p)
    "t10y2y": "T10Y2Y",        # 10년-2년 금리차 (%p)
    "dgs10": "DGS10",          # 10년 국채금리 (%)
    "dgs3mo": "DGS3MO",        # 3개월 국채금리 (%)
}
OHLC = ["open", "high", "low", "close"]


# ---------------------------------------------------------------- 수집

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


def download_all(raw_dir: Path, start: str = START, sources: tuple[str, ...] = ("yahoo", "fred")) -> None:
    """원자료를 받아 raw_dir 에 CSV 로 저장한다. 하나가 실패해도 나머지는 계속."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    yahoo = {**ASSETS, **INDICATORS} if "yahoo" in sources else {}
    fred = FRED if "fred" in sources else {}
    for key, tk in yahoo.items():
        try:
            df = fetch_yahoo(tk, start)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {key} ({tk}) 실패: {e}")
            continue
        df.to_csv(raw_dir / f"yahoo_{key}.csv")
        print(f"{key:8s} {tk:12s} {df.index.min().date()} ~ {df.index.max().date()}  {len(df):6d} rows")
    for key, sid in fred.items():
        try:
            s = fetch_fred(sid, start)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {key} ({sid}) 실패: {e}")
            continue
        s.to_csv(raw_dir / f"fred_{key}.csv", header=True)
        print(f"{key:8s} {sid:12s} {s.index.min().date()} ~ {s.index.max().date()}  {len(s):6d} rows")


def load_raw(raw_dir: Path):
    """raw_dir 의 CSV 를 읽어 (assets, indicators, fred) 딕셔너리로 돌려준다. 없는 파일은 건너뛴다."""
    raw_dir = Path(raw_dir)

    def _read_df(p: Path) -> pd.DataFrame:
        return pd.read_csv(p, index_col="date", parse_dates=True)

    assets = {k: _read_df(raw_dir / f"yahoo_{k}.csv") for k in ASSETS if (raw_dir / f"yahoo_{k}.csv").exists()}
    inds = {k: _read_df(raw_dir / f"yahoo_{k}.csv") for k in INDICATORS if (raw_dir / f"yahoo_{k}.csv").exists()}
    fred = {k: _read_df(raw_dir / f"fred_{k}.csv").iloc[:, 0] for k in FRED if (raw_dir / f"fred_{k}.csv").exists()}
    return assets, inds, fred


# ---------------------------------------------------------------- 패널

def build_panel(assets: dict[str, pd.DataFrame], indicators: dict[str, pd.DataFrame],
                fred: dict[str, pd.Series], calendar_key: str = "spx",
                start: str | None = None) -> pd.DataFrame:
    """원자료를 하나의 일별 표로 합친다. 열 규칙은 모듈 docstring 참고.

    열 이름
      {asset}_open/high/low/close : 수준, 휴장일 앞값 채움
      {asset}_ret                 : 로그 수익률, 그 자산이 거래하지 않은 날은 NaN
      {indicator}                 : 수준, 앞값 채움
      {fred}                      : 수준, 1영업일 지연 후 앞값 채움
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
        lvl = df[OHLC].reindex(cal, method="ffill")
        for c in OHLC:
            out[f"{key}_{c}"] = lvl[c]
        traded = pd.Series(True, index=df.index).reindex(cal, fill_value=False)
        out[f"{key}_ret"] = np.log(lvl["close"]).diff().where(traded)

    for key, df in indicators.items():
        out[key] = df["close"].sort_index().reindex(cal, method="ffill") * INDICATOR_SCALE.get(key, 1.0)

    for key, s in fred.items():
        out[key] = s.sort_index().reindex(cal, method="ffill").shift(1)

    return out


def build_daily(raw_dir: Path, start: str | None = None) -> pd.DataFrame:
    assets, inds, fred = load_raw(raw_dir)
    return build_panel(assets, inds, fred, start=start)


def coverage(panel: pd.DataFrame) -> pd.DataFrame:
    """열별 첫 유효일, 마지막 유효일, 결측 수."""
    rows = []
    for c in panel.columns:
        s = panel[c]
        rows.append({"column": c, "first": s.first_valid_index(), "last": s.last_valid_index(),
                     "n_missing": int(s.isna().sum())})
    return pd.DataFrame(rows).set_index("column")
