# -*- coding: utf-8 -*-
"""One ASX ticker -> provider snapshot -> completed-session facts and explanation.

This is a descriptive chart, not a new scoring model. It never changes the main
universe, trains a model, measures holdout performance or places an order.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import urllib.request
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import indicators as I
from .fileio import atomic_bytes
from .picks import MIN_ADV_LONG, MIN_ADV_SHORT
from short_radar import asx_calendar as AC
from short_radar.calendars import latest_completed, parse_override, session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "cache", "stock_lookup")
SYDNEY = ZoneInfo("Australia/Sydney")
BENCHMARK = "STW.AX"
_fetch_lock = threading.Lock()


def normalize_ticker(value):
    value = str(value or "").strip().upper()
    if value.startswith("ASX:"):
        value = value[4:]
    if value.endswith(".AX"):
        value = value[:-3]
    if not re.fullmatch(r"[A-Z0-9]{2,6}", value, flags=re.ASCII):
        raise ValueError("请输入澳股代码，例如 BHP、CSL、GYG 或 360；只支持 ASX。")
    return value + ".AX"


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _overrides(now):
    from .shortradar import OVERRIDE_DIR
    out = {}
    for name in sorted(os.listdir(OVERRIDE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(OVERRIDE_DIR, name), encoding="utf-8") as f:
                try:
                    raw = f.read()
                    row = {**parse_override(raw, now),
                           "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
                    out[(row["market"], row["session_date"])] = row
                except ValueError:
                    pass  # Future knowledge never changes today's session.
    return out


def parse_chart(raw, ticker):
    """Parse the saved HTTP response; missing values remain null, no filling."""
    result = json.loads(raw).get("chart", {}).get("result")
    if not result:
        raise ValueError("行情源没有返回这只股票，请核对代码或稍后重试。")
    obj = result[0]
    meta = obj.get("meta") or {}
    if (meta.get("symbol") != ticker or meta.get("currency") != "AUD"
            or meta.get("exchangeName") != "ASX"
            or meta.get("instrumentType") not in {"EQUITY", "ETF"}
            or meta.get("exchangeTimezoneName") != "Australia/Sydney"):
        raise ValueError("行情源返回的代码、币种或交易所不是所请求的 AUD/ASX 标的。")
    stamps = obj.get("timestamp") or []
    quote = (obj.get("indicators", {}).get("quote") or [{}])[0]
    fields = ("open", "high", "low", "close", "volume")
    if not stamps or any(len(quote.get(k, [])) != len(stamps) for k in fields):
        raise ValueError("行情字段不完整，不能生成单股结论。")
    rows = [{"date": dt.datetime.fromtimestamp(t, SYDNEY).date().isoformat(),
             **{k: _number(quote[k][i]) for k in fields}}
            for i, t in enumerate(stamps)]
    notes = []
    # Yahoo appends its latest quote after the canonical daily bar. Its timestamp
    # equals regularMarketTime; its volume can be an earlier quote snapshot.
    # Treat only this explicit, price-identical tail as a QUOTE, never as a second
    # session. Any other duplicate (including conflicting OHLC) still fails.
    if (len(rows) > 1 and rows[-1]["date"] == rows[-2]["date"]
            and stamps[-1] == meta.get("regularMarketTime")
            and stamps[-1] > stamps[-2]
            and dt.datetime.fromtimestamp(stamps[-2], SYDNEY).time() == dt.time(10)
            and all(rows[-1][k] == rows[-2][k] for k in ("open", "high", "low", "close"))):
        rows = rows[:-1]
        notes.append("来源在日线末尾追加了同日最新报价，已单独展示报价；日线保留原始日线成交量，没有相加。")
    if len({r["date"] for r in rows}) != len(rows):
        raise ValueError("行情出现重复交易日，不能静默覆盖。")
    events = []
    for kind, values in (obj.get("events") or {}).items():
        for value in values.values():
            events.append({"kind": kind, "date": dt.datetime.fromtimestamp(
                value["date"], SYDNEY).date().isoformat(),
                "amount": _number(value.get("amount")),
                "split_ratio": value.get("splitRatio")})
    return {"ticker": ticker, "name": meta.get("longName") or meta.get("shortName") or ticker,
            "bars": sorted(rows, key=lambda r: r["date"]), "events": events,
            "normalization_notes": notes,
            "quote": {"price": _number(meta.get("regularMarketPrice")),
                      "as_of": dt.datetime.fromtimestamp(meta["regularMarketTime"],
                          dt.timezone.utc).isoformat() if meta.get("regularMarketTime") else None}}


def fetch_snapshot(ticker, now=None, cache_dir=None):
    """60-second cache, immutable raw HTTP revisions + hash, explicit stale fallback."""
    ticker = normalize_ticker(ticker)
    supplied_now = now
    now = now or dt.datetime.now(dt.timezone.utc)
    folder = os.path.join(cache_dir or CACHE_DIR, "ticker-" + ticker)
    pointer = os.path.join(folder, "latest.json")
    cached = None
    try:
        with open(pointer, encoding="utf-8") as f:
            cached = json.load(f)
        with open(os.path.join(folder, cached["sha256"] + ".json"), "rb") as f:
            raw = f.read()
        if hashlib.sha256(raw).hexdigest() != cached["sha256"]:
            raise ValueError("snapshot hash mismatch")
        cached = {**parse_chart(raw, ticker), **cached}
        age = (now - dt.datetime.fromisoformat(cached["fetched_at"])).total_seconds()
        if 0 <= age < 60:
            return {**cached, "cached": True, "fetch_error": None}
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + ticker
           + "?range=2y&interval=1d&events=div%2Csplits")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("行情响应超出上限")
        observed_at = supplied_now or dt.datetime.now(dt.timezone.utc)
        sha = hashlib.sha256(raw).hexdigest()
        receipt = {"sha256": sha, "fetched_at": observed_at.isoformat(), "source_url": url,
                   "source": "Yahoo Finance", "snapshot_kind": "raw_http"}
        # Keep original bytes even if the provider later revises its prices.
        path = os.path.join(folder, sha + ".json")
        if not os.path.exists(path):
            atomic_bytes(path, raw)
            atomic_bytes(os.path.join(folder, sha + ".first.json"), _json_bytes(receipt))
        parsed = parse_chart(raw, ticker)
        atomic_bytes(pointer, _json_bytes(receipt))
        return {**parsed, **receipt, "cached": False, "fetch_error": None}
    except Exception as e:
        if cached:
            return {**cached, "cached": True,
                    "fetch_error": "最新行情抓取失败，下面保留上次数据（%s）。" % type(e).__name__}
        raise RuntimeError("暂时无法取得 %s 的行情（%s），未生成虚构数据。" %
                           (ticker, type(e).__name__)) from e


def analyze(stock, benchmark=None, now=None, mode="local"):
    """All measurements stop at a completed ASX session. No intraday backdating.

    MA: full 20/50/200 arithmetic means. RSI/ATR: existing I.wilder exponential
    recursion (first observation seed, NOT TA-Lib's initial simple mean). Returns
    and relative STW change use split-adjusted Close, NOT total/net returns.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    overrides = _overrides(now)
    expected = latest_completed("AU", now, overrides)["date"]
    all_bars = stock["bars"]
    bars = [r for r in all_bars if r["date"] <= expected]
    issues = []
    dates = [r["date"] for r in bars]
    if dates != sorted(set(dates)):
        issues.append("日线日期重复或顺序异常，暂不判断。")
    as_of = bars[-1]["date"] if bars else None
    if as_of != expected:
        issues.append("尚未拿到最近完整交易日 %s 的收盘行情。" % expected)
    if stock.get("fetch_error"):
        issues.append(stock["fetch_error"])
    if len(bars) < 60:
        issues.append("完整日线不足60根，先观察。")
    required = []
    day = dt.date.fromisoformat(expected)
    while len(required) < 60:
        s = session("AU", day, overrides)
        if s:
            if not s.get("phase_times_verified", True):
                issues.append("近期提前收市时段未核实。")
            required.append(day.isoformat())
        day -= dt.timedelta(days=1)
    by_date = {r["date"]: r for r in bars}
    if any(r["date"] >= required[-1] and r["date"] not in required for r in bars):
        issues.append("近期在休市日出现日线，暂不判断。")
    for day in required:
        r = by_date.get(day)
        if r is None:
            issues.append("近期日线漏日：%s；没有补价格或跳过后继续判断。" % day)
            break
        if (any(r.get(k) is None or not np.isfinite(r[k]) or r[k] <= 0
                for k in ("open", "high", "low", "close"))
                or r.get("volume") is None or r["volume"] <= 0
                or r["low"] > min(r["open"], r["close"])
                or r["high"] < max(r["open"], r["close"]) or r["low"] > r["high"]):
            issues.append("近期有缺失、无成交或异常日线：%s；暂不判断。" % day)
            break
    valid_until = None
    if as_of:
        day = dt.date.fromisoformat(as_of)
        for _ in range(40):
            day += dt.timedelta(days=1)
            try:
                s = session("AU", day, overrides)
            except ValueError:
                break
            if s:
                valid_until = s["close"].isoformat()
                break
    facts, chart = None, bars[-510:]
    if not issues:
        f = pd.DataFrame(bars).set_index("date")
        close = f["close"].astype(float)
        ma20, ma50, ma200 = (close.rolling(n, min_periods=n).mean() for n in (20, 50, 200))
        rsi, atr = I.rsi(close), I.atr(f["high"], f["low"], close)
        rs20 = None
        if benchmark and not benchmark.get("fetch_error"):
            bench = {r["date"]: r for r in benchmark["bars"]}
            dates = list(reversed(required[:21]))
            if all(d in bench and (_number(bench[d].get("close")) or 0) > 0
                   and (_number(bench[d].get("volume")) or 0) > 0 for d in dates):
                rs20 = (close.iloc[-1] / close.iloc[-21]
                        - bench[dates[-1]]["close"] / bench[dates[0]]["close"])
        facts = {"close": float(close.iloc[-1]), "change_1d": float(close.iloc[-1] / close.iloc[-2] - 1),
                 "return_20d": float(close.iloc[-1] / close.iloc[-21] - 1),
                 "relative_stw_20d": _number(rs20), "ma20": float(ma20.iloc[-1]),
                 "ma50": float(ma50.iloc[-1]), "ma200": _number(ma200.iloc[-1]),
                 "rsi14": _number(rsi.iloc[-1]), "atr14": _number(atr.iloc[-1]),
                 "adv20_aud": float((close * f["volume"]).tail(20).mean()),
                 "prior20_low": float(f["low"].iloc[-21:-1].min()),
                 "prior20_high": float(f["high"].iloc[-21:-1].max())}
        chart = [{**r, "ma20": _number(ma20.loc[r["date"]]),
                  "ma50": _number(ma50.loc[r["date"]]), "rsi14": _number(rsi.loc[r["date"]])}
                 for r in bars[-510:]]
    recent = [e for e in stock.get("events", []) if as_of and e["date"] <= as_of
              and e["date"] >= (dt.date.fromisoformat(as_of) - dt.timedelta(days=7)).isoformat()]
    blockers = list(issues)
    if recent:
        blockers.append("近期有除息或拆合股事件，跌幅可能包含机械调整，先核对公告。")
    if facts and facts["relative_stw_20d"] is None:
        blockers.append("缺少同日期 STW 对照，暂不作多空方向判断。")
    long_setup = bool(facts and not blockers and facts["close"] > facts["ma20"] > facts["ma50"]
                      and facts["relative_stw_20d"] > 0 and facts["adv20_aud"] >= MIN_ADV_LONG)
    short_setup = bool(facts and not blockers and facts["close"] < facts["ma20"] < facts["ma50"]
                       and facts["relative_stw_20d"] < 0 and facts["adv20_aud"] >= MIN_ADV_SHORT)
    reasons = list(blockers) + stock.get("normalization_notes", [])
    if facts:
        reasons.extend(["收盘价%s20日均线，20日均线%s50日均线。" %
                        ("高于" if facts["close"] > facts["ma20"] else "未高于",
                         "高于" if facts["ma20"] > facts["ma50"] else "未高于"),
                        "20日平均成交额 A$%s；买入观察门槛 A$%s，做空观察门槛 A$%s。" %
                        (format(facts["adv20_aud"], ",.0f"), format(MIN_ADV_LONG, ","), format(MIN_ADV_SHORT, ","))])
        if facts["relative_stw_20d"] is not None:
            reasons.append("过去20日价格涨跌幅比 STW %s %.2f 个百分点（不含股息和费用）。" %
                           ("高" if facts["relative_stw_20d"] > 0 else "低",
                            abs(facts["relative_stw_20d"]) * 100))
        if facts["adv20_aud"] < MIN_ADV_LONG:
            reasons.append("成交金额不足买入观察门槛，先不考虑入场。")
        elif facts["adv20_aud"] < MIN_ADV_SHORT:
            reasons.append("成交金额不足做空观察门槛，先不考虑做空。")
    return {"schema_version": 1, "ticker": stock["ticker"], "name": stock["name"],
            "market": "AU", "currency": "AUD", "mode": mode, "as_of": as_of,
            "expected_session": expected, "analyzed_at": now.isoformat(), "valid_until": valid_until,
            "source": {k: stock.get(k) for k in ("source", "source_url", "fetched_at", "sha256", "snapshot_kind")},
            "benchmark_source": {k: (benchmark or {}).get(k) for k in ("fetched_at", "sha256", "source")},
            "quote": {**stock.get("quote", {}), "delay": "unknown", "used_in_analysis": False},
            "quality": {"state": "unavailable" if issues else "single_source",
                        "issues": issues, "source_verified": False,
                        "adjustment_metadata_verified": False,
                        "calendar_version": AC.CALENDAR_VERSION,
                        "excluded_unfinished_bars": len(all_bars) - len(bars)},
            "facts": facts, "chart": chart, "recent_events": recent,
            "assessment": {"bias": "long_watch" if long_setup else "short_watch" if short_setup else "observe",
                           "headline": "偏多，可关注买入条件" if long_setup else
                                       "偏空，可关注做空条件" if short_setup else "先观察，当前条件不完整或方向不一致",
                           "long": "技术条件符合，下一交易时段再核对成交价" if long_setup else "暂不符合本页的买入观察条件",
                           "short": "技术条件符合；借券及费用未确认，尚不能确认可执行" if short_setup else "暂不符合本页的做空观察条件",
                           "reasons": reasons, "borrow_available": None, "cfd_available": None,
                           "net_expected_return": None, "predictive_accuracy": None,
                           "method": "仅描述均线排列、相对STW强弱及既有流动性门槛；没有训练或调参，不代表已验证的交易优势。",
                           "next_step": "先核对公告、财务和券商报价；日线结论最早用于下一交易时段，不能倒填为当日收盘成交。"}}


def lookup(ticker, now=None, cache_dir=None):
    ticker = normalize_ticker(ticker)
    if not _fetch_lock.acquire(blocking=False):
        raise BlockingIOError("另一只股票正在读取行情，请稍后重试。")
    try:
        stock = fetch_snapshot(ticker, now, cache_dir)
        try:
            bench = stock if ticker == BENCHMARK else fetch_snapshot(BENCHMARK, now, cache_dir)
        except RuntimeError:
            bench = None
        return analyze(stock, bench, now or dt.datetime.now(dt.timezone.utc))
    finally:
        _fetch_lock.release()


def cached_analyses(now=None, log=print):
    """Daily/offline snapshots from existing caches only; no downloads or retraining."""
    from . import datafeed as D
    from .shortradar import universe
    now = now or dt.datetime.now(dt.timezone.utc)
    snapshots = {}
    for filename in ("prices.pkl", "shortradar_extra.pkl"):
        px, timestamp = D._load(filename), D.cache_timestamp(filename)
        if not px or timestamp is None:
            continue
        with open(D._cache_path(filename), "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        for ticker, (name, _) in universe().items():
            if ticker not in px["close"]:
                continue
            s = px["close"][ticker].dropna().tail(510)
            bars = [{"date": str(d.date()), **{k: _number(px[k].at[d, ticker])
                     if k in px and ticker in px[k] and d in px[k].index else None
                     for k in ("open", "high", "low", "close", "volume")}} for d in s.index]
            snapshots[ticker] = {"ticker": ticker, "name": name, "bars": bars, "events": [],
                                 "source": "Yahoo Finance / 每日行情缓存", "source_url": None,
                                 "fetched_at": dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(),
                                 "sha256": sha, "snapshot_kind": "existing_cache_not_raw_http", "quote": {}}
    results = {}
    for ticker, stock in snapshots.items():
        try:
            result = analyze(stock, snapshots.get(BENCHMARK), now, mode="daily_snapshot")
            # Existing bulk caches do not contain verified corporate-action details.
            result["assessment"]["reasons"].append("每日缓存不含完整公司行动明细，需另外核对除息、配股和公告。")
            results[ticker] = result
        except ValueError as e:
            log("单股快照 %s 未生成: %s" % (ticker, e))
    return results


def build_site(out_dir, now=None, log=print):
    results = cached_analyses(now=now, log=log)
    for ticker, result in results.items():
        # ASX codes such as PRN are reserved device names on Windows, even with
        # extensions. A fixed prefix makes both cache and export paths portable.
        atomic_bytes(os.path.join(out_dir, "stocks", "ticker-" + ticker + ".json"), _json_bytes(result))
    log("单股查询: 已生成 %d 个每日快照；股票池及评分权重未改。" % len(results))
    return len(results)
