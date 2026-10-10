"""data/processed/daily.csv -> data/processed/features.csv (긴 형식, 행 = date × asset).

사용: python pipelines/build_features.py [--h 20]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tailrisk.features import FEATURES, H, build_features  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=H)
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    a = ap.parse_args()
    data_dir = Path(a.data_dir)
    p = pd.read_csv(data_dir / "processed" / "daily.csv", index_col="date", parse_dates=True)
    f = build_features(p, h=a.h)
    out = data_dir / "processed" / "features.csv"
    f.to_csv(out, float_format="%.6f")

    ev = f.loc["2010-01-01":]
    print(f"rows {len(f)}  (2010~ {len(ev)}), features: stage1 {len(FEATURES['stage1'])}, stage2 {len(FEATURES['stage2'])}")
    print("\n[2010~ 자산별 라벨 수 / 결측 비율]")
    for asset, g in ev.groupby(level="asset"):
        print(f"  {asset:7s} y={g['y'].notna().sum():5d}  stage1 nan {g[FEATURES['stage1']].isna().mean().mean():5.1%}  "
              f"stage2 nan {g[FEATURES['stage2']].isna().mean().mean():5.1%}")
    print("\n[2010~ 피처별 결측 비율 상위]")
    print((ev[FEATURES["stage1"] + FEATURES["stage2"]].isna().mean().sort_values(ascending=False).head(10) * 100).round(1).to_string())
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
