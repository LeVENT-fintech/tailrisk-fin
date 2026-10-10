"""야후·FRED·ECOS·OFR 원자료를 data/raw 에 받고 data/processed/daily.csv 를 만든다.

사용
  python pipelines/collect_data.py                  # 전체 수집 + 패널 구성
  python pipelines/collect_data.py --source fred    # FRED 만 다시 받고 패널 재구성
  python pipelines/collect_data.py --skip-download  # 받아 둔 원자료로 패널만 재구성

ECOS 는 .env 의 ECOS_API_KEY 가 필요하다 (.env.example 참고). 없으면 ECOS 만 건너뛴다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tailrisk import data as D  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=D.START)
    ap.add_argument("--skip-download", action="store_true", help="data/raw 를 다시 받지 않는다")
    ap.add_argument("--source", choices=["all", "yahoo", "fred", "ecos", "ofr", "krx", "cot", "events"],
                    default="all", help="일부 출처만 다시 받는다 (krx 는 날짜별 호출이라 처음엔 10분 이상)")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    a = ap.parse_args()

    D.load_env(ROOT / ".env")
    data_dir = Path(a.data_dir)
    raw_dir = data_dir / "raw"
    if not a.skip_download:
        sources = ("yahoo", "fred", "ecos", "ofr", "krx", "cot", "events") if a.source == "all" else (a.source,)
        D.download_all(raw_dir, start=a.start, sources=sources)

    panel = D.build_daily(raw_dir, start=a.start)
    out = data_dir / "processed" / "daily.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out, float_format="%.6f")

    with D.pd.option_context("display.width", 120, "display.max_rows", 200):
        print()
        print(D.coverage(panel).to_string())
    print(f"\n-> {out}  ({len(panel)} rows, {panel.index.min().date()} ~ {panel.index.max().date()})")


if __name__ == "__main__":
    main()
