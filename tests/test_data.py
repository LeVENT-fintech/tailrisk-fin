"""패널 구성 규칙 검증: 달력 정렬, 휴장일 처리, FRED 지연, look-ahead 없음."""
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
                       "adj_close": close, "volume": 1.0}, index=dates)
    df.index.name = "date"
    return df


@pytest.fixture
def raw():
    us = pd.bdate_range("2020-01-02", "2020-01-31").drop(pd.Timestamp("2020-01-20"))   # 미국 휴장 1/20
    kr = pd.bdate_range("2020-01-02", "2020-01-31").drop(pd.Timestamp("2020-01-24"))   # 한국 휴장 1/24
    late = pd.bdate_range("2020-01-15", "2020-01-31")                                  # 늦게 시작하는 자산
    assets = {"spx": _ohlc(us), "kospi": _ohlc(kr, 2000.0), "gld": _ohlc(late, 150.0)}
    inds = {"vix": _ohlc(us, 15.0)}
    days = pd.bdate_range("2020-01-02", "2020-01-31")
    fred = {"hy_oas": pd.Series(days.day.astype(float), index=days, name="hy_oas")}
    return assets, inds, fred


def test_calendar_is_us_trading_days(raw):
    p = build_panel(*raw)
    assert pd.Timestamp("2020-01-20") not in p.index
    assert len(p) == 21


def test_fred_is_lagged_one_row(raw):
    p = build_panel(*raw)
    # 1/22 행에는 1/21 값(=21)이 들어간다
    assert p.loc["2020-01-22", "hy_oas"] == 21.0
    assert np.isnan(p.loc["2020-01-02", "hy_oas"])


def test_asset_holiday_return_is_nan_and_level_is_ffilled(raw):
    p = build_panel(*raw)
    assert np.isnan(p.loc["2020-01-24", "kospi_ret"])
    assert p.loc["2020-01-24", "kospi_close"] == p.loc["2020-01-23", "kospi_close"]
    # 다음 거래일 수익률은 1/23 -> 1/27 전체 움직임
    c23, c27 = p.loc["2020-01-23", "kospi_close"], p.loc["2020-01-27", "kospi_close"]
    assert p.loc["2020-01-27", "kospi_ret"] == pytest.approx(np.log(c27 / c23))


def test_us_holiday_move_folds_into_next_day(raw):
    assets, _, _ = raw
    p = build_panel(*raw)
    kr = assets["kospi"]["close"]
    expect = np.log(kr.loc["2020-01-21"] / kr.loc["2020-01-17"])  # 1/20 한국 거래분 포함
    assert p.loc["2020-01-21", "kospi_ret"] == pytest.approx(expect)


def test_late_asset_is_nan_before_start(raw):
    p = build_panel(*raw)
    assert np.isnan(p.loc["2020-01-14", "gld_close"])
    assert np.isnan(p.loc["2020-01-15", "gld_ret"])
    assert not np.isnan(p.loc["2020-01-16", "gld_ret"])


def test_no_lookahead(raw):
    """미래 원자료를 잘라내도 과거 행은 그대로여야 한다."""
    assets, inds, fred = raw
    full = build_panel(assets, inds, fred)
    cut = pd.Timestamp("2020-01-17")
    assets_c = {k: v.loc[:cut] for k, v in assets.items()}
    inds_c = {k: v.loc[:cut] for k, v in inds.items()}
    fred_c = {k: v.loc[:cut] for k, v in fred.items()}
    part = build_panel(assets_c, inds_c, fred_c)
    pd.testing.assert_frame_equal(full.loc[:cut], part)
