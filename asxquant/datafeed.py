# -*- coding: utf-8 -*-
"""Data acquisition: Yahoo Finance OHLCV + ASIC daily short position disclosure.

Australia mandates daily short position reporting, which makes actual positioning
observable rather than inferred. ASIC publishes one year-to-date CSV per calendar
year holding every reported stock's short position for every trade day, so a handful
of requests reconstructs years of daily short interest for the whole market.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import io
import os
import pickle
import time
import warnings

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

from .config import CACHE_DIR, HISTORY_PERIOD, MACRO, SECTOR_INDEX, SHORT_YEARS, all_stock_tickers

_HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")}
_ASIC = "https://download.asic.gov.au/short-selling/RR%s-001-SSDailyYTD.csv"


def _cache_path(name):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(here, CACHE_DIR)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _save(name, obj):
    with open(_cache_path(name), "wb") as f:
        pickle.dump({"ts": time.time(), "obj": obj}, f)


def _load(name, max_age_sec=None):
    p = _cache_path(name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "rb") as f:
            blob = pickle.load(f)
    except Exception:
        return None
    if max_age_sec is not None and (time.time() - blob["ts"]) > max_age_sec:
        return None
    return blob["obj"]


def cache_age(name):
    p = _cache_path(name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "rb") as f:
            return time.time() - pickle.load(f)["ts"]
    except Exception:
        return None


# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------
def fetch_prices(force=False, log=print):
    if not force:
        cached = _load("prices.pkl", max_age_sec=3600 * 6)
        if cached is not None:
            log("行情数据: 使用本地缓存")
            return cached

    import yfinance as yf

    tickers = sorted(set(all_stock_tickers() + list(MACRO.keys()) + list(SECTOR_INDEX.values())))
    log("行情数据: 从 Yahoo Finance 下载 %d 个标的 (%s) ..." % (len(tickers), HISTORY_PERIOD))
    raw = yf.download(tickers, period=HISTORY_PERIOD, interval="1d",
                      progress=False, auto_adjust=False, threads=True, group_by="column")

    out = {}
    lvl0 = list(raw.columns.get_level_values(0))
    for field, key in [("Close", "close"), ("Open", "open"), ("High", "high"),
                       ("Low", "low"), ("Volume", "volume"), ("Adj Close", "adjclose")]:
        if field in lvl0:
            df = raw[field].copy()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            out[key] = df.sort_index()

    good = [c for c in out["close"].columns if out["close"][c].notna().sum() >= 200]
    for k in list(out.keys()):
        out[k] = out[k][[c for c in good if c in out[k].columns]]

    # Yahoo emits a phantom row for the current calendar day when a foreign venue is
    # open while the ASX is shut. Such a row has almost no cross-section: drop it.
    filled = out["close"].notna().sum(axis=1)
    keep = filled >= max(3, int(0.2 * out["close"].shape[1]))
    dropped = int((~keep).sum())
    if dropped:
        for k in list(out.keys()):
            out[k] = out[k][keep.reindex(out[k].index).fillna(False)]
        log("行情数据: 剔除 %d 个无效交易日" % dropped)

    log("行情数据: %d 个交易日 x %d 个标的" % (out["close"].shape[0], out["close"].shape[1]))
    _save("prices.pkl", out)
    return out



def trading_status(px, tickers):
    """Names that have stopped printing trades: halts, suspensions, delistings.

    A static universe rots silently. A suspended or delisted stock keeps its last
    close forever, and every downstream indicator then presents a stale price as if
    it were today's. QUB was carried this way for four sessions -- delisted, still
    quoted at its final price, still eligible for the pick lists -- before this
    check existed.

    Cheap and assumption-free: compare each name's last printed close against the
    market's own latest session. No announcement parsing, so it catches suspensions,
    delistings and plain data outages alike.
    """
    closes = px["close"]
    idx = closes.index
    out = {}
    for t in tickers:
        if t not in closes.columns:
            out[t] = {"last_trade": None, "stale_days": 10 ** 4}
            continue
        s = closes[t].dropna()
        if not len(s):
            out[t] = {"last_trade": None, "stale_days": 10 ** 4}
            continue
        out[t] = {"last_trade": str(s.index[-1].date()),
                  "stale_days": int((idx > s.index[-1]).sum())}
    return out


def halted_tickers(px, tickers, min_stale_days=1):
    """The subset of `tickers` not currently trading."""
    st = trading_status(px, tickers)
    return {t for t, v in st.items() if v["stale_days"] >= min_stale_days}

# --------------------------------------------------------------------------
# ASIC short positions
# --------------------------------------------------------------------------
_ASIC_V = "https://download.asic.gov.au/short-selling/RR%s-%s-SSDailyYTD.csv"
_ASIC_INDEX = "https://download.asic.gov.au/short-selling/short-selling-data.json"


def _asic_index(log=print):
    """ASIC's own index of published dates and revision numbers.

    One 139KB JSON of `{date, version}` for every report since 2010. Two things it
    buys over the old approach of probing candidate URLs:
      * the latest date is read, not guessed -- the old code walked back day by day,
        up to twenty 3MB downloads on a bad day;
      * ~345 dates have revisions (version 002/003/010). The hardcoded `-001-` URL
        always fetched the ORIGINAL file; the index names the corrected one.
    """
    try:
        r = requests.get(_ASIC_INDEX, headers=_HDR, timeout=60)
        if r.status_code == 200:
            rows = r.json()
            if isinstance(rows, list) and rows:
                return {int(x["date"]): str(x["version"]) for x in rows
                        if isinstance(x, dict) and "date" in x}
    except Exception as e:
        log("空头持仓: 索引获取失败(%s)，退回逐日试探" % e)
    return None


def _try_ytd(datestr, version="001", timeout=90):
    try:
        r = requests.get(_ASIC_V % (datestr, version), headers=_HDR, timeout=timeout)
        if r.status_code == 200 and len(r.content) > 50000:
            return r.content
    except Exception:
        pass
    return None


def _find_latest_ytd(index=None, log=print):
    """Newest YTD file: straight from the index, else walk back probing (fallback)."""
    if index:
        latest = max(index)
        d = dt.date(latest // 10000, latest // 100 % 100, latest % 100)
        blob = _try_ytd("%08d" % latest, index[latest], timeout=90)
        if blob is not None:
            return d, blob
    today = dt.date.today()
    for back in range(2, 22):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        blob = _try_ytd(d.strftime("%Y%m%d"), timeout=90)
        if blob is not None:
            return d, blob
    return None, None


def _find_year_end_ytd(year, index=None):
    """The final YTD file of a past year, at its latest published revision."""
    if index:
        in_year = [d for d in index if d // 10000 == year]
        if in_year:
            last = max(in_year)
            blob = _try_ytd("%08d" % last, index[last], timeout=90)
            if blob is not None:
                return blob
    for day in range(31, 18, -1):
        try:
            d = dt.date(year, 12, day)
        except ValueError:
            continue
        if d.weekday() >= 5:
            continue
        blob = _try_ytd(d.strftime("%Y%m%d"), timeout=90)
        if blob is not None:
            return blob
    return None


def _parse_ytd(blob):
    """Wide ASIC layout -> long frame (date, code, short_shares, short_pct).

    Row 0 repeats each trade date twice, row 1 names the two measures, and every
    later row is one product. Missing disclosures are '-' and must not become zero:
    a stock with no reported short position is genuinely different from one at 0.00%
    only in that both mean 'nothing reported', so both map to 0 after parsing, but
    a parse failure would silently look like a short position collapsing to nothing.
    """
    txt = None
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            t = blob.decode(enc)
            if t.count(",") > 100:
                txt = t
                break
        except Exception:
            continue
    if txt is None:
        return pd.DataFrame(columns=["date", "code", "short_shares", "short_pct"])

    raw = pd.read_csv(io.StringIO(txt), header=None, dtype=str, low_memory=False)
    if raw.shape[0] < 3 or raw.shape[1] < 4:
        return pd.DataFrame(columns=["date", "code", "short_shares", "short_pct"])

    dates = raw.iloc[0].tolist()
    body = raw.iloc[2:].reset_index(drop=True)
    codes = body.iloc[:, 1].astype(str).str.strip()

    def _n(col):
        return pd.to_numeric(col.astype(str).str.replace(",", "", regex=False)
                             .str.strip().replace({"-": np.nan, "": np.nan, "nan": np.nan}),
                             errors="coerce")

    frames = []
    for j in range(2, raw.shape[1], 2):
        d = dates[j]
        if not isinstance(d, str) or "/" not in d:
            continue
        try:
            ts = pd.to_datetime(d.strip(), dayfirst=True)
        except Exception:
            continue
        sh = _n(body.iloc[:, j])
        pc = _n(body.iloc[:, j + 1]) if (j + 1) < raw.shape[1] else pd.Series(np.nan, index=body.index)
        frames.append(pd.DataFrame({"date": ts, "code": codes,
                                    "short_shares": sh.values, "short_pct": pc.values}))
    if not frames:
        return pd.DataFrame(columns=["date", "code", "short_shares", "short_pct"])
    out = pd.concat(frames, ignore_index=True)
    return out[out["code"].str.len().between(2, 6)]


def fetch_shorts(force=False, log=print):
    """Long frame of daily ASIC short positions for the whole market."""
    if not force:
        cached = _load("shorts.pkl", max_age_sec=3600 * 6)
        if cached is not None:
            log("空头持仓: 使用本地缓存")
            return cached

    log("空头持仓: 从 ASIC 下载年度汇总文件 ...")
    index = _asic_index(log=log)
    asof, blob = _find_latest_ytd(index=index, log=log)
    if blob is None:
        log("空头持仓: ASIC 无法访问 (该组因子将被跳过)")
        empty = pd.DataFrame(columns=["date", "code", "short_shares", "short_pct"])
        _save("shorts.pkl", empty)
        return empty

    blobs = [blob]
    this_year = asof.year
    years = [this_year - k for k in range(1, SHORT_YEARS)]
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        for b in ex.map(lambda yr: _find_year_end_ytd(yr, index=index), years):
            if b is not None:
                blobs.append(b)

    parts = [_parse_ytd(b) for b in blobs]
    df = pd.concat([p for p in parts if len(p)], ignore_index=True)
    df = (df.dropna(subset=["date", "code"])
            .drop_duplicates(subset=["date", "code"], keep="first")
            .sort_values(["code", "date"])
            .reset_index(drop=True))
    log("空头持仓: %d 个交易日 x %d 只股票 (%s -> %s)" % (
        df["date"].nunique(), df["code"].nunique(),
        df["date"].min().date(), df["date"].max().date()))
    _save("shorts.pkl", df)
    return df


def shorts_panel(shorts, tickers, index):
    """Pivot to date x ticker frames of short % and short shares, aligned to price dates.

    ASIC reports with a four-business-day lag, so the value is shifted onto the date it
    actually became public. Using it on its own trade date would be lookahead.
    """
    from .config import asx_code
    empty = pd.DataFrame(index=index, columns=tickers, dtype=float)
    if shorts is None or len(shorts) == 0:
        return empty, empty.copy()

    code2tk = {asx_code(t): t for t in tickers}
    d = shorts[shorts["code"].isin(code2tk.keys())].copy()
    if d.empty:
        return empty, empty.copy()
    d["ticker"] = d["code"].map(code2tk)

    pct = d.pivot_table(index="date", columns="ticker", values="short_pct", aggfunc="last")
    sha = d.pivot_table(index="date", columns="ticker", values="short_shares", aggfunc="last")

    def _align(x):
        x = x.sort_index()
        x.index = x.index + pd.Timedelta(days=6)      # publication lag, calendar days
        return x.reindex(index, method="ffill").reindex(columns=tickers)

    return _align(pct), _align(sha)
