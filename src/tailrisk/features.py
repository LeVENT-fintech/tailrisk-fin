"""라벨(앞으로 H영업일 실현변동성)과 피처 생성.

원칙
  * 시점 t 의 피처는 t 까지의 패널 값만 쓴다 (패널이 이미 발표 지연을 반영하므로 여기서는 추가 지연 없음).
  * 라벨은 t+1 ~ t+H 수익률로 계산한다. 그 구간에 관측이 MIN_OBS 개 미만이면 NaN.
  * 거래 없는 날(수익률 NaN)은 롤링 계산에서 건너뛴다 (min_periods 로 하한).
  * 모든 변동성은 연율, 로그 스케일. 피처는 수준 대신 변화·비율을 우선한다 (추세 계열 과적합 방지).

출력: 긴 형식 표. 행 = (date, asset). 열 = y, stage1 피처, stage2 피처.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import ASSET_KEYS

H = 20            # 예측 지평 (영업일)
MIN_OBS = 16      # 라벨 창 안 최소 관측 수
ANN = 252.0

STAGE1_ASSET = ["log_rv5", "log_rv21", "log_rv63", "rv_slope", "log_pk21", "ret5", "ret21", "ret63",
                "z252", "vol_ratio21", "range_pct"]
STAGE1_COMMON = ["log_vix", "d_log_vix21", "vix_minus_spx_rv", "d_log_dxy21", "usdjpy_rv21", "usdjpy_ret21",
                 "wti_rv21", "wti_ret21", "us10y", "d_us10y21", "us_curve", "baa10y", "d_baa10y21", "d_baa10y63",
                 "cp_spread", "ofr_fsi", "ofr_funding", "ofr_credit", "kr_ktb3y", "d_kr_ktb3y21", "kr_us_spread",
                 "fomc_next_bdays", "fomc_in_20d", "bok_next_bdays", "bok_in_20d"]
STAGE2 = ["vix_term", "vvix_ratio", "log_move", "log_gvz", "log_ovx", "log_vkospi", "vkospi_minus_kospi_rv",
          "credit_etf", "d_credit_etf21", "d_tips21", "d_breakeven21", "kr_flow21", "kr_turnover_ratio",
          "ewy_rv21", "ewy_kospi_gap21", "cot_vix_lev", "cot_es_lev", "cot_es_am", "cot_tn_lev", "cot_tn_am",
          "cot_gold_mm", "vix9d_ratio"]
FEATURES = {"stage1": STAGE1_ASSET + STAGE1_COMMON, "stage2": STAGE2}


# ---------------------------------------------------------------- 도우미

def _mp(w: int) -> int:
    return max(2, int(np.ceil(w * 0.8)))


def realized_vol(ret: pd.Series, w: int) -> pd.Series:
    """과거 w일(오늘 포함) 수익률의 연율 실현변동성. 관측 부족이면 NaN."""
    return np.sqrt(ANN * (ret ** 2).rolling(w, min_periods=_mp(w)).mean())


def forward_realized_vol(ret: pd.Series, h: int = H, min_obs: int = MIN_OBS) -> pd.Series:
    """t+1 ~ t+h 수익률의 연율 실현변동성 (라벨). 마지막 h 행은 창이 짧아 NaN 또는 부분값이 되므로
    관측 수가 min_obs 미만이면 NaN 으로 둔다."""
    r2 = (ret ** 2)
    fwd_mean = r2[::-1].rolling(h, min_periods=min_obs).mean()[::-1].shift(-1)
    # 창이 패널 끝을 넘어가는 행은 min_periods 를 만족해도 '완전한 창'이 아니므로 NaN
    n_ahead = pd.Series(np.arange(len(ret))[::-1], index=ret.index)
    return np.sqrt(ANN * fwd_mean).where(n_ahead >= h)


def parkinson_vol(high: pd.Series, low: pd.Series, w: int) -> pd.Series:
    x = np.log(high / low) ** 2 / (4 * np.log(2))
    return np.sqrt(ANN * x.rolling(w, min_periods=_mp(w)).mean())


def _log(s: pd.Series) -> pd.Series:
    return np.log(s.where(s > 0))


# ---------------------------------------------------------------- 피처

def asset_features(p: pd.DataFrame, a: str) -> pd.DataFrame:
    r = p[f"{a}_ret"]
    close = p[f"{a}_close"]
    f = pd.DataFrame(index=p.index)
    f["log_rv5"] = _log(realized_vol(r, 5))
    f["log_rv21"] = _log(realized_vol(r, 21))
    f["log_rv63"] = _log(realized_vol(r, 63))
    f["rv_slope"] = f["log_rv5"] - f["log_rv63"]
    hi, lo = p.get(f"{a}_high"), p.get(f"{a}_low")
    f["log_pk21"] = _log(parkinson_vol(hi, lo, 21)) if hi is not None else np.nan
    f["range_pct"] = _log(hi / lo) if hi is not None else np.nan
    for w in (5, 21, 63):
        f[f"ret{w}"] = r.rolling(w, min_periods=_mp(w)).sum()
    lc = _log(close)
    f["z252"] = (lc - lc.rolling(252, min_periods=200).mean()) / lc.rolling(252, min_periods=200).std()
    vol = p.get(f"{a}_volume")
    if vol is not None and vol.notna().any():
        v = vol.where(vol > 0)
        f["vol_ratio21"] = _log(v.rolling(21, min_periods=_mp(21)).mean() / v.rolling(63, min_periods=_mp(63)).mean())
    else:
        f["vol_ratio21"] = np.nan
    return f


def common_features(p: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=p.index)
    spx_rv21 = _log(realized_vol(p["spx_ret"], 21))
    f["log_vix"] = _log(p["vix"])
    f["d_log_vix21"] = f["log_vix"].diff(21)
    f["vix_minus_spx_rv"] = _log(p["vix"] / 100.0) - spx_rv21
    f["d_log_dxy21"] = _log(p["dxy"]).diff(21)
    for k in ("usdjpy", "wti"):
        rr = _log(p[k]).diff()
        f[f"{k}_rv21"] = _log(realized_vol(rr, 21))
        f[f"{k}_ret21"] = rr.rolling(21, min_periods=_mp(21)).sum()
    f["us10y"] = p["us10y"]
    f["d_us10y21"] = p["us10y"].diff(21)
    f["us_curve"] = p["us10y"] - p["us3m"]
    f["baa10y"] = p["baa10y"]
    f["d_baa10y21"] = p["baa10y"].diff(21)
    f["d_baa10y63"] = p["baa10y"].diff(63)
    f["cp_spread"] = p["cp3m"] - p["dgs3mo"]
    for k in ("ofr_fsi", "ofr_funding", "ofr_credit"):
        f[k] = p[k]
    f["kr_ktb3y"] = p["kr_ktb3y"]
    f["d_kr_ktb3y21"] = p["kr_ktb3y"].diff(21)
    f["kr_us_spread"] = p["kr_ktb3y"] - p["us3m"]
    for k in ("fomc_next_bdays", "fomc_in_20d", "bok_next_bdays", "bok_in_20d"):
        f[k] = p[k]
    # ---- 2차
    f["vix_term"] = _log(p["vix"] / p["vix3m"])
    f["vvix_ratio"] = _log(p["vvix"] / p["vix"])
    f["log_move"] = _log(p["move"])
    f["log_gvz"] = _log(p["gvz"])
    f["log_ovx"] = _log(p["ovx"])
    f["log_vkospi"] = _log(p["vkospi"])
    f["vkospi_minus_kospi_rv"] = _log(p["vkospi"] / 100.0) - _log(realized_vol(p["kospi_ret"], 21))
    f["credit_etf"] = _log(p["hyg"] / p["ief"])
    f["d_credit_etf21"] = f["credit_etf"].diff(21)
    f["d_tips21"] = p["tips10"].diff(21)
    f["d_breakeven21"] = (p["dgs10"] - p["tips10"]).diff(21)
    f["kr_flow21"] = (p["kr_foreign_netbuy"].fillna(0).rolling(21).sum()
                      / p["kr_value"].fillna(0).rolling(21).sum().where(lambda s: s > 0))
    turn = (p["kr_value"] / p["kr_mcap"])
    f["kr_turnover_ratio"] = _log(turn.rolling(5, min_periods=4).mean() / turn.rolling(63, min_periods=_mp(63)).mean())
    ewy_r = _log(p["ewy"]).diff()
    f["ewy_rv21"] = _log(realized_vol(ewy_r, 21))
    f["ewy_kospi_gap21"] = ewy_r.rolling(21, min_periods=_mp(21)).sum() - p["kospi_ret"].rolling(21, min_periods=_mp(21)).sum()
    for k in ("cot_vix_lev", "cot_es_lev", "cot_es_am", "cot_tn_lev", "cot_tn_am", "cot_gold_mm"):
        f[k] = p[k]
    f["vix9d_ratio"] = _log(p["vix9d"] / p["vix"])
    return f


def build_features(p: pd.DataFrame, assets: list[str] | None = None, h: int = H) -> pd.DataFrame:
    """긴 형식 (date, asset) 표. y = log(앞으로 h일 실현변동성)."""
    assets = assets or ASSET_KEYS
    common = common_features(p)
    parts = []
    for a in assets:
        f = asset_features(p, a)
        f["y"] = _log(forward_realized_vol(p[f"{a}_ret"], h))
        f["asset"] = a
        parts.append(pd.concat([f, common], axis=1))
    out = pd.concat(parts)
    out.index.name = "date"
    out = out.reset_index().set_index(["date", "asset"]).sort_index()
    cols = ["y"] + FEATURES["stage1"] + FEATURES["stage2"]
    return out[cols]
