"""예정된 통화정책 회의 일정 (FOMC, 한국은행 금통위)과 CFTC 투자자별 포지션(COT).

시점 규칙
  * 회의 일정은 전년에 공표되므로 '다음 회의까지 영업일 수', '앞으로 20영업일 안에 회의가 있는가' 는 t 시점에 알 수 있다.
    회의 당일은 결정이 미국 종가 전(14:00 ET)·한국 종가 전(약 10:00 KST)에 나오므로 '다음 회의' 는 t 보다 뒤의 날짜만 센다.
  * COT 는 화요일 기준 포지션을 금요일 15:30 ET 에 공표한다. 보고일 +3일(금요일)부터 사용하고 앞값으로 채운다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str, timeout: int = 60) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


# ---------------------------------------------------------------- FOMC

FOMC_HIST_URL = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm"   # ~2019
FOMC_CAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"       # 2020~
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], 1)}


def _last_day(day_text: str) -> int:
    """'26-27' -> 27, '16' -> 16, '28-29*' -> 29. 결정 발표는 회의 마지막 날."""
    nums = re.findall(r"\d+", day_text)
    return int(nums[-1])


def fetch_fomc_dates(start_year: int = 2000, end_year: int | None = None) -> pd.DatetimeIndex:
    """정례 FOMC 회의의 결정 발표일. 컨퍼런스콜·비정례 회의는 제외."""
    end_year = end_year or dt.date.today().year + 1
    dates: list[pd.Timestamp] = []
    covered: set[int] = set()
    for y in range(start_year, end_year):
        try:
            h = _get(FOMC_HIST_URL.format(y=y))
        except Exception:  # noqa: BLE001  (아직 과거 페이지가 없는 해)
            continue
        found = 0
        for title in re.findall(r"<h5[^>]*>(.*?)</h5>", h, flags=re.S):
            title = re.sub(r"\s+", " ", title).strip()
            # 'January 26-27 Meeting', 'Jan/Feb 31-1 Meeting', 'April/May 30-1 Meeting' (월을 넘기면 뒤쪽 달·날)
            m = re.match(r"([A-Za-z]+(?:/[A-Za-z]+)?)\s+([\d\-–]+)\s+Meeting", title)
            m2 = re.match(r"[A-Za-z]+\s+\d+\s*[-–]\s*([A-Za-z]+)\s+(\d+)\s+Meeting", title)   # 'July 31-August 1 Meeting'
            if m2:
                mon, day = m2.group(1)[:3].title(), int(m2.group(2))
            elif m:
                mon, day = m.group(1).split("/")[-1][:3].title(), _last_day(m.group(2))
            else:
                continue
            mi = next((v for k, v in MONTHS.items() if k[:3] == mon), None)
            if mi:
                dates.append(pd.Timestamp(y, mi, day))
                found += 1
        if found:
            covered.add(y)
    # 사전 공표됐다가 취소된 회의: t 시점에는 '예정' 이었으므로 포함 (2020-03-17/18 은 3/15 긴급회의로 대체)
    dates += [pd.Timestamp("2020-03-18")]
    h = _get(FOMC_CAL_URL)
    for block in re.split(r'<div class="panel panel-default">', h)[1:]:
        ym = re.search(r"(20\d\d) FOMC Meetings", block)
        if not ym:
            continue
        y = int(ym.group(1))
        if y < start_year or y >= end_year or y in covered:
            continue
        rows = re.findall(r'fomc-meeting__month[^>]*>\s*(?:<strong>)?([A-Za-z/]+)(?:</strong>)?\s*</div>\s*'
                          r'<div class="fomc-meeting__date[^>]*>([^<]+)<', block)
        for month, day in rows:
            if "unscheduled" in day.lower() or "notation" in day.lower():
                continue
            mon = month.split("/")[-1]      # 'Jan/Feb' 처럼 걸친 회의는 뒤쪽 달
            if mon[:3] in {k[:3] for k in MONTHS}:
                mi = next(v for k, v in MONTHS.items() if k[:3] == mon[:3])
                dates.append(pd.Timestamp(y, mi, _last_day(day)))
    return pd.DatetimeIndex(sorted(set(dates)))


# ---------------------------------------------------------------- 한국은행 금통위

BOK_URL = "https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755&pYear={y}"


def fetch_bok_dates(start_year: int = 2000, end_year: int | None = None) -> pd.DatetimeIndex:
    """통화정책방향 결정회의 날짜 (연도별 페이지의 'MM월 DD일'). 2016년까지 월 1회, 2017년부터 연 8회."""
    end_year = end_year or dt.date.today().year + 1
    dates: list[pd.Timestamp] = []
    for y in range(start_year, end_year + 1):
        h = _get(BOK_URL.format(y=y))
        for mm, dd in re.findall(r"(\d{1,2})월\s*(\d{1,2})일", h):
            try:
                dates.append(pd.Timestamp(y, int(mm), int(dd)))
            except ValueError:
                continue
    # 임시회의(사전에 알 수 없음)는 제외: 9·11 직후, 금융위기, 코로나
    unscheduled = {pd.Timestamp("2001-09-19"), pd.Timestamp("2008-10-27"), pd.Timestamp("2020-03-16")}
    return pd.DatetimeIndex(sorted(set(dates) - unscheduled))


# ---------------------------------------------------------------- CFTC COT

COT_TFF_URL = ("https://publicreporting.cftc.gov/resource/gpe5-46if.json?cftc_contract_market_code={code}"
               "&$order=report_date_as_yyyy_mm_dd%20ASC&$limit=5000")
COT_DISAGG_URL = ("https://publicreporting.cftc.gov/resource/72hh-3qpy.json?cftc_contract_market_code={code}"
                  "&$order=report_date_as_yyyy_mm_dd%20ASC&$limit=5000")
# 열 이름: (데이터셋, 종목코드, 롱 필드, 숏 필드). 값은 (롱-숏)/미결제약정
COT = {
    "cot_vix_lev": ("tff", "1170E1", "lev_money_positions_long", "lev_money_positions_short"),     # VIX 선물, 레버리지펀드
    "cot_es_lev": ("tff", "13874A", "lev_money_positions_long", "lev_money_positions_short"),      # E-mini S&P 500, 레버리지펀드
    "cot_es_am": ("tff", "13874A", "asset_mgr_positions_long", "asset_mgr_positions_short"),       # E-mini S&P 500, 자산운용사
    "cot_tn_am": ("tff", "043602", "asset_mgr_positions_long", "asset_mgr_positions_short"),       # 10년 국채 선물, 자산운용사
    "cot_tn_lev": ("tff", "043602", "lev_money_positions_long", "lev_money_positions_short"),      # 10년 국채 선물, 레버리지펀드
    "cot_gold_mm": ("disagg", "088691", "m_money_positions_long_all", "m_money_positions_short_all"),  # 금 선물, 매니지드머니
}
COT_LAG_DAYS = 3   # 화요일 기준 -> 금요일 공표


def fetch_cot() -> pd.DataFrame:
    """COT 순포지션 비율(주별, 보고일=화요일 인덱스)."""
    out: dict[str, pd.Series] = {}
    cache: dict[tuple[str, str], list[dict]] = {}
    for col, (ds, code, lf, sf) in COT.items():
        if (ds, code) not in cache:
            url = (COT_TFF_URL if ds == "tff" else COT_DISAGG_URL).format(code=code)
            cache[(ds, code)] = json.loads(_get(url))
        rows = cache[(ds, code)]
        idx = pd.to_datetime([r["report_date_as_yyyy_mm_dd"][:10] for r in rows])
        num = lambda k: pd.to_numeric([r.get(k) for r in rows], errors="coerce")  # noqa: E731
        s = pd.Series((num(lf) - num(sf)) / num("open_interest_all"), index=idx, name=col)
        out[col] = s[~s.index.duplicated(keep="last")].sort_index()
    df = pd.DataFrame(out)
    df.index.name = "date"
    return df


# ---------------------------------------------------------------- 패널 열

def event_features(cal: pd.DatetimeIndex, dates: pd.DatetimeIndex, prefix: str, horizon: int = 20) -> pd.DataFrame:
    """'다음 회의까지 영업일 수' 와 '앞으로 horizon 영업일 안에 회의가 있는가(0/1)'. t 보다 뒤의 회의만 센다."""
    dates = pd.DatetimeIndex(sorted(set(dates)))
    pos_next = dates.searchsorted(cal, side="right")          # cal[i] 보다 뒤인 첫 회의
    nxt = np.where(pos_next < len(dates), dates[np.minimum(pos_next, len(dates) - 1)].values,
                   np.datetime64("NaT", "ns"))
    nxt = pd.DatetimeIndex(nxt)
    # 영업일 수: 달력 기준 위치 차이 (회의일이 미국 휴장일이면 다음 달력일 기준)
    cal_pos = cal.searchsorted(nxt.fillna(cal[-1] + pd.Timedelta(days=1)))
    bdays = cal_pos - np.arange(len(cal))
    bdays = pd.Series(bdays, index=cal, dtype="float64").where(nxt.notna())
    return pd.DataFrame({f"{prefix}_next_bdays": bdays,
                         f"{prefix}_in_{horizon}d": (bdays <= horizon).astype(float).where(bdays.notna())},
                        index=cal)


def cot_to_daily(cot: pd.DataFrame, cal: pd.DatetimeIndex) -> pd.DataFrame:
    c = cot.sort_index().copy()
    c.index = c.index + pd.Timedelta(days=COT_LAG_DAYS)
    return c.reindex(cal, method="ffill")


def load_event_dates(path: Path) -> pd.DatetimeIndex:
    """한 줄에 날짜 하나(YYYY-MM-DD). '#' 로 시작하는 줄은 주석."""
    lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()]
    return pd.DatetimeIndex(sorted(pd.Timestamp(ln) for ln in lines if ln and not ln.startswith("#")))
