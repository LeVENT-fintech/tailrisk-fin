"""패널 구성 규칙 검증: 달력 정렬, 휴장일 처리, FRED 지연·결측 보정, ECOS 정렬, look-ahead 없음."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tailrisk.data import build_panel  # noqa: E402


def _ohlc(dates: pd.DatetimeIndex, base: float = 100.0) -> pd.DataFrame:
    close = base + np.arange(len(dates), dtype=float)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close,
                       "adj_close": close * 0.9, "volume": 1.0}, index=dates)
    df.index.name = "date"
    return df


@pytest.fixture
def raw():
    us = pd.bdate_range("2020-01-02", "2020-01-31").drop(pd.Timestamp("2020-01-20"))   # 미국 휴장 1/20
    kr = pd.bdate_range("2020-01-02", "2020-01-31").drop(pd.Timestamp("2020-01-24"))   # 한국 휴장 1/24
    late = pd.bdate_range("2020-01-15", "2020-01-31")                                  # 늦게 시작하는 자산
    assets = {"spx": _ohlc(us), "kospi": _ohlc(kr, 2000.0), "gld": _ohlc(late, 150.0)}
    inds = {"vix": _ohlc(us, 15.0)}
    proxies = {"hyg": _ohlc(us, 80.0)}
    days = pd.bdate_range("2020-01-02", "2020-01-31")
    hy = pd.Series(days.day.astype(float), index=days, name="hy_oas")
    hy.loc["2020-01-13"] = np.nan                                                        # 채권시장 휴장일 결측
    fred = {"hy_oas": hy}
    ecos = {
        "kr_foreign_netbuy": pd.Series(kr.day.astype(float), index=kr),               # flow
        "kr_cd91": pd.Series(1.0 + kr.day / 100.0, index=kr),                          # level
    }
    return assets, inds, fred, proxies, ecos


def test_calendar_is_us_trading_days(raw):
    p = build_panel(*raw)
    assert pd.Timestamp("2020-01-20") not in p.index
    assert len(p) == 21


def test_fred_is_lagged_one_row(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-22", "hy_oas"] == 21.0   # 1/22 행에는 1/21 값
    assert np.isnan(p.loc["2020-01-02", "hy_oas"])


def test_fred_gap_is_ffilled_before_lag(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-14", "hy_oas"] == 10.0   # 1/13 결측 -> 1/10 값으로 채운 뒤 지연
    assert p.loc["2020-01-15", "hy_oas"] == 14.0


def test_asset_holiday_return_is_nan_and_level_is_ffilled(raw):
    p = build_panel(*raw)
    assert np.isnan(p.loc["2020-01-24", "kospi_ret"])
    assert p.loc["2020-01-24", "kospi_close"] == p.loc["2020-01-23", "kospi_close"]
    c23, c27 = p.loc["2020-01-23", "kospi_close"], p.loc["2020-01-27", "kospi_close"]
    assert p.loc["2020-01-27", "kospi_ret"] == pytest.approx(np.log(c27 / c23))


def test_us_holiday_move_folds_into_next_day(raw):
    assets = raw[0]
    p = build_panel(*raw)
    kr = assets["kospi"]["close"]
    expect = np.log(kr.loc["2020-01-21"] / kr.loc["2020-01-17"])  # 1/20 한국 거래분 포함
    assert p.loc["2020-01-21", "kospi_ret"] == pytest.approx(expect)


def test_adj_close_and_proxy_columns(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-10", "kospi_adj_close"] == pytest.approx(p.loc["2020-01-10", "kospi_close"] * 0.9)
    assert p.loc["2020-01-10", "hyg"] == pytest.approx(raw[3]["hyg"].loc["2020-01-10", "adj_close"])


def test_ecos_flow_folds_us_holiday_and_nan_on_kr_holiday(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-21", "kr_foreign_netbuy"] == 20.0 + 21.0   # 1/20 분이 1/21 에 합산
    assert np.isnan(p.loc["2020-01-24", "kr_foreign_netbuy"])        # 한국 휴장
    assert p.loc["2020-01-27", "kr_foreign_netbuy"] == 27.0


def test_ecos_level_is_same_day_and_ffilled(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-23", "kr_cd91"] == pytest.approx(1.23)      # 지연 없음
    assert p.loc["2020-01-24", "kr_cd91"] == pytest.approx(1.23)      # 휴장일 앞값


def test_late_asset_is_nan_before_start(raw):
    p = build_panel(*raw)
    assert np.isnan(p.loc["2020-01-14", "gld_close"])
    assert np.isnan(p.loc["2020-01-15", "gld_ret"])
    assert not np.isnan(p.loc["2020-01-16", "gld_ret"])


def test_no_lookahead(raw):
    """미래 원자료를 잘라내도 과거 행은 그대로여야 한다."""
    assets, inds, fred, proxies, ecos = raw
    full = build_panel(assets, inds, fred, proxies, ecos)
    cut = pd.Timestamp("2020-01-17")
    trim = lambda d: {k: v.loc[:cut] for k, v in d.items()}  # noqa: E731
    part = build_panel(trim(assets), trim(inds), trim(fred), trim(proxies), trim(ecos))
    pd.testing.assert_frame_equal(full.loc[:cut], part)
