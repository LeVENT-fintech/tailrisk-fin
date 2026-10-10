"""피처·라벨 규칙 검증: 라벨 창, 롤링 하한, look-ahead 없음."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tailrisk.data import ASSET_KEYS  # noqa: E402
from tailrisk.features import FEATURES, build_features, forward_realized_vol, realized_vol  # noqa: E402


def _panel(n: int = 400, seed: int = 0) -> pd.DataFrame:
    """실제 패널과 같은 열 이름을 가진 합성 패널."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    p = pd.DataFrame(index=idx)
    for a in ASSET_KEYS:
        r = pd.Series(rng.normal(0, 0.01, n), index=idx)
        close = 100 * np.exp(r.cumsum())
        p[f"{a}_close"] = close
        p[f"{a}_adj_close"] = close
        p[f"{a}_high"] = close * 1.01
        p[f"{a}_low"] = close * 0.99
        p[f"{a}_volume"] = rng.integers(1000, 2000, n).astype(float)
        p[f"{a}_ret"] = r
    for c in ["vix", "vix3m", "vvix", "move", "gvz", "ovx", "vkospi", "vix9d"]:
        p[c] = 20 + rng.normal(0, 1, n)
    for c in ["dxy", "usdjpy", "wti", "hyg", "ief", "ewy", "kr_mcap"]:
        p[c] = 100 * np.exp(rng.normal(0, 0.005, n).cumsum())
    for c in ["us10y", "us3m", "baa10y", "cp3m", "dgs3mo", "dgs10", "tips10", "kr_ktb3y", "ofr_fsi", "ofr_funding",
              "ofr_credit", "kr_foreign_netbuy", "kr_value", "cot_vix_lev", "cot_es_lev", "cot_es_am", "cot_tn_lev",
              "cot_tn_am", "cot_gold_mm", "fomc_next_bdays", "fomc_in_20d", "bok_next_bdays", "bok_in_20d"]:
        p[c] = rng.normal(1, 0.1, n)
    p["kr_value"] = p["kr_value"].abs() * 1000
    return p


def test_label_window_and_tail():
    r = pd.Series(0.01, index=pd.bdate_range("2020-01-01", periods=50))
    y = forward_realized_vol(r, h=20)
    assert y.iloc[0] == pytest.approx(np.sqrt(252 * 0.01 ** 2))   # 일정한 수익률이면 연율 변동성 = 0.01*sqrt(252)
    assert y.iloc[-20:].isna().all() and y.iloc[-21:-20].notna().all()   # 마지막 20행은 창이 안 차서 NaN


def test_label_skips_missing_days_but_requires_min_obs():
    r = pd.Series(0.01, index=pd.bdate_range("2020-01-01", periods=60))
    r.iloc[10:13] = np.nan                       # 휴장 3일
    y = forward_realized_vol(r, h=20)
    assert y.iloc[0] == pytest.approx(np.sqrt(252 * 0.01 ** 2))   # 17개 관측으로 계산
    r.iloc[10:16] = np.nan                       # 휴장 6일 -> 14개 관측 < 16
    y2 = forward_realized_vol(r, h=20)
    assert np.isnan(y2.iloc[0])


def test_realized_vol_min_periods():
    r = pd.Series([0.01] * 3 + [np.nan] * 10 + [0.01] * 10)
    rv = realized_vol(r, 21)
    assert rv.isna().all()                        # 21일 창에 관측 13개 < 17


def test_build_features_shape_and_columns():
    f = build_features(_panel())
    assert f.index.names == ["date", "asset"]
    assert set(f.index.get_level_values("asset")) == set(ASSET_KEYS)
    assert list(f.columns) == ["y"] + FEATURES["stage1"] + FEATURES["stage2"]
    assert f["y"].notna().sum() > 0 and f["log_rv21"].notna().sum() > 0


def test_no_lookahead_in_features_and_label():
    p = _panel()
    full = build_features(p)
    cut = p.index[300]
    part = build_features(p.loc[:cut])
    feats = FEATURES["stage1"] + FEATURES["stage2"]
    pd.testing.assert_frame_equal(full.loc[:cut, feats], part[feats])                 # 피처는 t 까지만 사용
    safe = p.index[300 - 20]
    pd.testing.assert_series_equal(full.loc[:safe, "y"], part.loc[:safe, "y"])       # 라벨은 창이 완전한 구간만 동일
    assert part.loc[p.index[281]:, "y"].isna().all()                                 # 잘린 뒤 창이 불완전한 라벨은 NaN
