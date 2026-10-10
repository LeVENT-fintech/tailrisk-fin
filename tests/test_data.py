"""패널 구성 규칙 검증: 달력 정렬, 휴장일 처리, FRED·OFR 지연, ECOS 정렬, 자산 구성, look-ahead 없음."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tailrisk.data import (  # noqa: E402
    accrue_dividends, assemble_assets, backfill_with, build_panel, fill_missing_days, fx_from_ecos,
)


def _ohlc(dates: pd.DatetimeIndex, base: float = 100.0, adj_factor: float = 0.9) -> pd.DataFrame:
    close = base + np.arange(len(dates), dtype=float)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close,
                       "adj_close": close * adj_factor, "volume": 1000.0}, index=dates)
    df.index.name = "date"
    return df


US = pd.bdate_range("2020-01-02", "2020-01-31").drop(pd.Timestamp("2020-01-20"))   # 미국 휴장 1/20
KR = pd.bdate_range("2020-01-02", "2020-01-31").drop(pd.Timestamp("2020-01-24"))   # 한국 휴장 1/24


@pytest.fixture
def raw():
    late = pd.bdate_range("2020-01-15", "2020-01-31")
    assets = {"spx": _ohlc(US), "kospi": _ohlc(KR, 2000.0), "gld": _ohlc(late, 150.0)}
    inds = {"vix": _ohlc(US, 15.0)}
    proxies = {"hyg": _ohlc(US, 80.0)}
    days = pd.bdate_range("2020-01-02", "2020-01-31")
    hy = pd.Series(days.day.astype(float), index=days, name="hy_oas")
    hy.loc["2020-01-13"] = np.nan                                                        # 채권시장 휴장일 결측
    fred = {"hy_oas": hy}
    ecos = {
        "kr_foreign_netbuy": pd.Series(KR.day.astype(float), index=KR),               # flow
        "kr_cd91": pd.Series(1.0 + KR.day / 100.0, index=KR),                          # level
        "kr_call": pd.Series(2.0 + KR.day / 100.0, index=KR),                          # level_lag1
        "kospi_divyield": pd.Series([1.2, 2.4], index=pd.to_datetime(["2019-11-01", "2019-12-01"])),
    }
    ofr = pd.DataFrame({"ofr_fsi": days.day.astype(float)}, index=days)
    return assets, inds, fred, proxies, ecos, ofr


def test_calendar_is_us_trading_days(raw):
    p = build_panel(*raw)
    assert pd.Timestamp("2020-01-20") not in p.index
    assert len(p) == 21


def test_fred_is_lagged_one_row_and_gap_ffilled(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-22", "hy_oas"] == 21.0   # 1/22 행에는 1/21 값
    assert np.isnan(p.loc["2020-01-02", "hy_oas"])
    assert p.loc["2020-01-14", "hy_oas"] == 10.0   # 1/13 결측 -> 1/10 값으로 채운 뒤 지연


def test_ofr_is_lagged_two_rows(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-06", "ofr_fsi"] == 2.0    # 1/6 행에는 두 행 전(1/2) 값


def test_return_uses_adj_close_and_holiday_rules(raw):
    assets = raw[0]
    p = build_panel(*raw)
    k = assets["kospi"]["adj_close"]
    assert p.loc["2020-01-10", "kospi_ret"] == pytest.approx(np.log(k.loc["2020-01-10"] / k.loc["2020-01-09"]))
    assert np.isnan(p.loc["2020-01-24", "kospi_ret"])                                   # 한국 휴장
    assert p.loc["2020-01-24", "kospi_close"] == p.loc["2020-01-23", "kospi_close"]
    assert p.loc["2020-01-27", "kospi_ret"] == pytest.approx(np.log(k.loc["2020-01-27"] / k.loc["2020-01-23"]))
    assert p.loc["2020-01-21", "kospi_ret"] == pytest.approx(np.log(k.loc["2020-01-21"] / k.loc["2020-01-17"]))  # 1/20 포함


def test_volume_column_is_nan_when_not_traded(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-10", "kospi_volume"] == 1000.0
    assert np.isnan(p.loc["2020-01-24", "kospi_volume"])


def test_proxy_uses_adj_close(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-10", "hyg"] == pytest.approx(raw[3]["hyg"].loc["2020-01-10", "adj_close"])


def test_ecos_kinds(raw):
    p = build_panel(*raw)
    assert p.loc["2020-01-21", "kr_foreign_netbuy"] == 20.0 + 21.0   # flow: 1/20 분 합산
    assert np.isnan(p.loc["2020-01-24", "kr_foreign_netbuy"])
    assert p.loc["2020-01-23", "kr_cd91"] == pytest.approx(1.23)      # level: 지연 없음
    assert p.loc["2020-01-24", "kr_cd91"] == pytest.approx(1.23)      # level: 휴장일 앞값
    assert p.loc["2020-01-24", "kr_call"] == pytest.approx(2.23)      # level_lag1: 1/24 행 = 1/23 값
    assert p.loc["2020-01-23", "kr_call"] == pytest.approx(2.22)
    assert p.loc["2020-01-31", "kospi_divyield"] == 1.2               # monthly: 11월 값은 1월부터, 12월 값은 2월부터


def test_late_asset_is_nan_before_start(raw):
    p = build_panel(*raw)
    assert np.isnan(p.loc["2020-01-14", "gld_close"])
    assert np.isnan(p.loc["2020-01-15", "gld_ret"])
    assert not np.isnan(p.loc["2020-01-16", "gld_ret"])


def test_no_lookahead(raw):
    """미래 원자료를 잘라내도 과거 행은 그대로여야 한다."""
    assets, inds, fred, proxies, ecos, ofr = raw
    full = build_panel(assets, inds, fred, proxies, ecos, ofr)
    cut = pd.Timestamp("2020-01-17")
    trim = lambda d: {k: v.loc[:cut] for k, v in d.items()}  # noqa: E731
    part = build_panel(trim(assets), trim(inds), trim(fred), trim(proxies), trim(ecos), ofr.loc[:cut])
    pd.testing.assert_frame_equal(full.loc[:cut], part)


# ---------------------------------------------------------------- 일정·COT

def test_event_features_count_only_future_meetings():
    from tailrisk.events import event_features
    ev = event_features(US, pd.DatetimeIndex(["2020-01-10", "2020-01-29"]), "fomc", horizon=5)
    assert ev.loc["2020-01-09", "fomc_next_bdays"] == 1 and ev.loc["2020-01-09", "fomc_in_5d"] == 1.0
    assert ev.loc["2020-01-10", "fomc_next_bdays"] == 12            # 당일 회의는 세지 않음 (1/10 -> 1/29, 미국 휴장 1/20 제외)
    assert ev.loc["2020-01-10", "fomc_in_5d"] == 0.0
    assert np.isnan(ev.loc["2020-01-30", "fomc_next_bdays"])         # 이후 회의 없음


def test_cot_is_available_from_friday():
    from tailrisk.events import cot_to_daily
    cot = pd.DataFrame({"cot_vix_lev": [0.1, 0.2]}, index=pd.to_datetime(["2020-01-07", "2020-01-14"]))  # 화요일
    d = cot_to_daily(cot, US)
    assert np.isnan(d.loc["2020-01-09", "cot_vix_lev"])
    assert d.loc["2020-01-10", "cot_vix_lev"] == 0.1                  # 금요일부터
    assert d.loc["2020-01-16", "cot_vix_lev"] == 0.1 and d.loc["2020-01-17", "cot_vix_lev"] == 0.2


# ---------------------------------------------------------------- 자산 구성

def test_fill_missing_days_uses_official_close():
    df = _ohlc(KR.drop(pd.Timestamp("2020-01-10")), 2000.0)                     # 야후에 1/10 누락
    official = pd.Series(1234.5, index=KR)                                         # 한국 거래일 달력
    official.loc["2020-01-11"] = 1234.5                                            # 토요일 행 (ECOS 버릇)
    out = fill_missing_days(df, official)
    assert pd.Timestamp("2020-01-10") in out.index and pd.Timestamp("2020-01-11") not in out.index
    assert out.loc["2020-01-10", "close"] == 1234.5 and np.isnan(out.loc["2020-01-10", "volume"])
    assert len(out) == len(df) + 1


def test_backfill_scales_proxy_and_blanks_high_low():
    gld = _ohlc(pd.bdate_range("2020-01-15", "2020-01-31"), 150.0)
    fut = _ohlc(pd.bdate_range("2020-01-02", "2020-01-31"), 1500.0)              # 접합일 1/15 선물 종가 1509
    out = backfill_with(gld, fut)
    assert out.index.min() == pd.Timestamp("2020-01-02")
    assert out.loc["2020-01-15", "close"] == 150.0                                 # 접합일은 GLD 값
    assert out.loc["2020-01-14", "close"] == pytest.approx(1508.0 * 150.0 / 1509.0)
    assert np.isnan(out.loc["2020-01-14", "high"]) and not np.isnan(out.loc["2020-01-15", "high"])


def test_accrue_dividends_adds_daily_yield():
    df = _ohlc(KR, 2000.0)
    dy = pd.Series([2.52], index=pd.to_datetime(["2019-11-01"]))                   # 1월부터 적용, 연 2.52% -> 일 0.01%
    out = accrue_dividends(df, dy)
    r_px = np.log(df["close"].iloc[1] / df["close"].iloc[0])
    r_tr = np.log(out["adj_close"].iloc[1] / out["adj_close"].iloc[0])
    assert r_tr - r_px == pytest.approx(0.0252 / 252)
    assert out["adj_close"].iloc[0] == df["close"].iloc[0]


def test_fx_from_ecos_drops_weekends_and_builds_ohlc():
    days = pd.bdate_range("2020-01-02", "2020-01-10").append(pd.DatetimeIndex(["2020-01-11"]))
    s = lambda v: pd.Series(v, index=days)  # noqa: E731
    fx = fx_from_ecos({"usdkrw_open": s(1150.0), "usdkrw_high": s(1160.0), "usdkrw_low": s(1140.0), "usdkrw_close": s(1155.0)})
    assert pd.Timestamp("2020-01-11") not in fx.index
    assert list(fx.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert fx["adj_close"].equals(fx["close"])


def test_assemble_assets_end_to_end():
    us = pd.bdate_range("2020-01-02", "2020-01-31")
    g = {
        "assets": {"spx": _ohlc(us), "kospi": _ohlc(KR.drop(pd.Timestamp("2020-01-10")), 2000.0)},
        "tr": {"spx": _ohlc(us, 5000.0)},
        "backfill": {},
        "proxies": {"spy": _ohlc(us, 300.0)},
        "ecos": {
            "kospi_ecos": pd.Series(1999.0, index=KR),
            "usdkrw_open": pd.Series(1150.0, index=KR), "usdkrw_high": pd.Series(1160.0, index=KR),
            "usdkrw_low": pd.Series(1140.0, index=KR), "usdkrw_close": pd.Series(1155.0, index=KR),
        },
    }
    a = assemble_assets(g)
    assert set(a) == {"spx", "kospi", "usdkrw"}
    assert a["spx"].loc["2020-01-10", "adj_close"] == g["tr"]["spx"].loc["2020-01-10", "close"]   # 총수익지수
    assert a["spx"].loc["2020-01-10", "volume"] == 1000.0                                         # SPY 거래량
    assert a["kospi"].loc["2020-01-10", "close"] == 1999.0                                        # 누락일 보완
    assert a["usdkrw"].loc["2020-01-10", "high"] == 1160.0
