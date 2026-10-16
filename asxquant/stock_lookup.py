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


def _observed_by(snapshot, cutoff):
    try:
        observed = dt.datetime.fromisoformat(snapshot["fetched_at"])
        return observed.utcoffset() is not None and observed <= cutoff
    except (KeyError, TypeError, ValueError):
        return False


def _complete_traded_bar(row):
    values = {k: _number(row.get(k)) for k in ("open", "high", "low", "close", "volume")}
    return all(v is not None and v > 0 for v in values.values()) and (
        values["low"] <= min(values["open"], values["close"])
        and values["high"] >= max(values["open"], values["close"]))


def _source_completed(snapshot, cutoff):
    if not _observed_by(snapshot, cutoff):
        return None
    observed = dt.datetime.fromisoformat(snapshot["fetched_at"])
    try:
        return latest_completed("AU", observed, _overrides(observed))["date"]
    except ValueError:
        return None


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
    market_price = _number(meta.get("regularMarketPrice"))
    # Yahoo appends its latest quote after the canonical daily bar. Its timestamp
    # equals regularMarketTime; its volume can be an earlier quote snapshot.
    # Its canonical intraday Close can still be null. Keep that null rather than
    # filling it from the quote; analyze() excludes unfinished sessions and will
    # reject a missing completed-session Close. Conflicting OHLC still fails.
    if (len(rows) > 1 and rows[-1]["date"] == rows[-2]["date"]
            and stamps[-1] == meta.get("regularMarketTime")
            and stamps[-1] > stamps[-2]
            and dt.datetime.fromtimestamp(stamps[-2], SYDNEY).time() == dt.time(10)
            and rows[-1]["close"] is not None
            and market_price is not None
            # Chart OHLC is float32-expanded; metadata may be decimal (e.g.
            # 59.369998931884766 vs 59.37). Accept only that encoding difference.
            and rows[-1]["close"] in (market_price, float(np.float32(market_price)))
            and all(rows[-1][k] is not None and rows[-1][k] == rows[-2][k]
                    for k in ("open", "high", "low"))
            and (rows[-2]["close"] is None or rows[-1]["close"] == rows[-2]["close"])):
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
            "events_status": "provider_reported",
            "normalization_notes": notes,
            "quote": {"price": _number(meta.get("regularMarketPrice")),
                      "as_of": dt.datetime.fromtimestamp(meta["regularMarketTime"],
                          dt.timezone.utc).isoformat() if meta.get("regularMarketTime") else None}}


def _revision_summary(previous, current, observed_at):
    summary = {"state": "first_observation", "previous_sha256": None,
               "changed_sessions": None, "examples": []}
    if not previous:
        return summary
    summary.update(state="not_comparable", previous_sha256=previous["sha256"])
    if not _observed_by(previous, observed_at):
        return summary
    try:
        prior_at = dt.datetime.fromisoformat(previous["fetched_at"])
        completed = latest_completed("AU", prior_at, _overrides(prior_at))["date"]
    except ValueError:
        return summary
    old = {r["date"]: r for r in previous["bars"] if r["date"] <= completed}
    changes = []
    for row in current["bars"]:
        prior = old.get(row["date"])
        if prior:
            fields = [k for k in ("open", "high", "low", "close", "volume") if prior[k] != row[k]]
            if fields: changes.append({"date": row["date"], "fields": fields})
    summary.update(state="compared", changed_sessions=len(changes), examples=changes[:5])
    return summary


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
        if not isinstance(cached, dict) or not isinstance(cached.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", cached["sha256"]):
            raise ValueError("invalid snapshot hash")
        with open(os.path.join(folder, cached["sha256"] + ".json"), "rb") as f:
            raw = f.read()
        if hashlib.sha256(raw).hexdigest() != cached["sha256"]:
            raise ValueError("snapshot hash mismatch")
        # A receipt supplies provenance, never bars or identity. Hash-checked raw
        # bytes remain authoritative even if a pointer contains extra fields.
        metadata = {k: cached[k] for k in ("sha256", "fetched_at", "source_url", "source", "snapshot_kind", "revision") if k in cached}
        cached = {**parse_chart(raw, ticker), **metadata}
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
        receipt["revision"] = _revision_summary(cached, parsed, observed_at)
        atomic_bytes(pointer, _json_bytes(receipt))
        return {**parsed, **receipt, "cached": False, "fetch_error": None}
    except Exception as e:
        if cached:
            return {**cached, "cached": True,
                    "fetch_error": "最新行情抓取失败，下面保留上次数据（%s）。" % type(e).__name__}
        raise RuntimeError("暂时无法取得 %s 的行情（%s），未生成虚构数据。" %
                           (ticker, type(e).__name__)) from e


def _condition_scores(facts, blockers, as_of):
    """Count the four EXISTING observation checks; no fitted or new weights.

    These are independent long/short checklists, not probabilities, the frozen
    cross-sectional picks scores, or technical-v1. Unknown/blocked is not zero.
    """
    result = {"method": "existing-observation-conditions-v1", "as_of": as_of,
              "state": "paused", "max": 4, "long": None, "short": None,
              "blockers": list(blockers),
              "meaning": "每满足一个既有观察条件记1项，满分4项；不是上涨/下跌概率，也不是全市场排名。"}
    if facts is None or blockers:
        return result
    f = facts
    checks = {
        "long": [("close_ma20", "收盘价高于20日均线", f["close"] > f["ma20"]),
                 ("ma20_ma50", "20日均线高于50日均线", f["ma20"] > f["ma50"]),
                 ("relative_stw", "20日价格表现强于STW", f["relative_stw_20d"] > 0),
                 ("liquidity", "20日日均成交额至少A$300万", f["adv20_aud"] >= MIN_ADV_LONG)],
        "short": [("close_ma20", "收盘价低于20日均线", f["close"] < f["ma20"]),
                  ("ma20_ma50", "20日均线低于50日均线", f["ma20"] < f["ma50"]),
                  ("relative_stw", "20日价格表现弱于STW", f["relative_stw_20d"] < 0),
                  ("liquidity", "20日日均成交额至少A$800万", f["adv20_aud"] >= MIN_ADV_SHORT)]}
    for side, rows in checks.items():
        result[side] = {"value": sum(bool(met) for _, _, met in rows),
                        "checks": [{"key": key, "label": label, "met": bool(met)}
                                   for key, label, met in rows]}
    result["state"] = "ready"
    return result


def analyze(stock, benchmark=None, now=None, mode="local"):
    """All measurements stop at a completed ASX session. No intraday backdating.

    MA: full 20/50/200 arithmetic means. RSI/ATR: existing I.wilder exponential
    recursion (first observation seed, NOT TA-Lib's initial simple mean). Returns
    and relative STW change use split-adjusted Close, NOT total/net returns.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("分析时刻必须带时区。")
    overrides = _overrides(now)
    expected = latest_completed("AU", now, overrides)["date"]
    all_bars = stock["bars"]
    bars = [r for r in all_bars if r["date"] <= expected]
    issues = []
    if not _observed_by(stock, now):
        issues.append("行情抓取时间缺失、无时区或晚于分析时刻，暂停指标和评分。")
    dates = [r["date"] for r in bars]
    if dates != sorted(set(dates)):
        issues.append("日线日期重复或顺序异常，暂不判断。")
    as_of = bars[-1]["date"] if bars else None
    source_completed = _source_completed(stock, now)
    if as_of and (source_completed is None or as_of > source_completed):
        issues.append("快照抓取时该日收盘整理尚未结束，不能因时间过去就当成完整日线；请重新获取来源数据。")
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
        if not _complete_traded_bar(r):
            issues.append("来源尚未提供 %s 的日线收盘价；报价另列，不能替代收盘。" % day if r.get("close") is None
                          else "近期有缺失、无成交或异常日线：%s；暂不判断。" % day)
            break
    latest_complete = next((r["date"] for r in reversed(bars) if _complete_traded_bar(r)), None)
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
    calculation_warnings = []
    facts, chart, diagnostics = None, bars[-510:], None
    if not issues:
        f = pd.DataFrame(bars).set_index("date")
        close = f["close"].astype(float)
        ma20, ma50, ma200 = (close.rolling(n, min_periods=n).mean() for n in (20, 50, 200))
        empty = pd.Series(np.nan, index=f.index)
        close_history_ok = bool(close.notna().all() and np.isfinite(close).all() and (close > 0).all())
        range_history_ok = bool(close_history_ok and all(
            all((_number(r.get(k)) or 0) > 0 for k in ("open", "high", "low"))
            and r["low"] <= min(r["open"], r["close"])
            and r["high"] >= max(r["open"], r["close"]) for r in bars))
        rsi = I.rsi(close) if close_history_ok else empty
        atr = I.atr(f["high"], f["low"], close) if range_history_ok else empty
        macd, macd_signal, macd_hist = I.macd(close) if close_history_ok else (empty, empty, empty)
        if not close_history_ok:
            calculation_warnings.append("较早历史收盘缺失或异常，RSI/MACD暂停；没有跨缺口递推。")
        if not range_history_ok:
            calculation_warnings.append("较早历史价格区间缺失或异常，ATR暂停；近期均线与价格条件另行检查。")
        volume_ref = f["volume"].iloc[-21:-1].mean()
        rs20 = None
        if benchmark and not benchmark.get("fetch_error") and (_source_completed(benchmark, now) or "") >= expected:
            benchmark_bars = [r for r in benchmark["bars"] if r["date"] <= expected]
            benchmark_dates = [r["date"] for r in benchmark_bars]
            bench = {r["date"]: r for r in benchmark_bars} if benchmark_dates == sorted(set(benchmark_dates)) else {}
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
                 "atr_pct": _number(atr.iloc[-1] / close.iloc[-1]),
                 "macd": _number(macd.iloc[-1]), "macd_signal": _number(macd_signal.iloc[-1]),
                 "macd_hist": _number(macd_hist.iloc[-1]),
                 "volume_ratio20": _number(f["volume"].iloc[-1] / volume_ref),
                 "volatility20_annualized": _number(I.realized_vol(close).iloc[-1]),
                 "adv20_aud": float((close * f["volume"]).tail(20).mean()),
                 "prior20_low": float(f["low"].iloc[-21:-1].min()),
                 "prior20_high": float(f["high"].iloc[-21:-1].max())}
        from .stock_diagnostics import describe
        extra, diagnostics = describe(f, facts, range_history_ok)
        facts.update(extra)
        chart = [{**r, "ma20": _number(ma20.loc[r["date"]]),
                  "ma50": _number(ma50.loc[r["date"]]), "rsi14": _number(rsi.loc[r["date"]])}
                 for r in bars[-510:]]
    recent = [e for e in stock.get("events", []) if as_of and e["date"] <= as_of
              and e["date"] >= required[20]]
    benchmark_events = [e for e in (benchmark or {}).get("events", []) if as_of
                        and required[20] <= e["date"] <= as_of]
    blockers = list(issues)
    if recent:
        blockers.append("近期有除息或拆合股事件，跌幅可能包含机械调整，先核对公告。")
    if facts and facts["relative_stw_20d"] is None:
        blockers.append("缺少同日期 STW 对照，暂不作多空方向判断。")
    scores = _condition_scores(facts, blockers, as_of)
    long_setup = bool(scores["long"] and scores["long"]["value"] == scores["max"])
    short_setup = bool(scores["short"] and scores["short"]["value"] == scores["max"])
    reasons = list(blockers) + calculation_warnings + stock.get("normalization_notes", [])
    if stock.get("events_status") == "not_supplied":
        reasons.append("本快照未提供公司行动明细，条件分只作价格观察，需核对除息、配股和公告。")
    if benchmark_events:
        reasons.append("STW在比较窗口内有来源报告的公司行动；相对强弱只比价格，未消除股息影响。")
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
        if facts["macd_hist"] is not None:
            reasons.append("MACD线%s信号线，柱值 %.4f；只描述当前动能，不单独决定方向。" %
                           ("高于" if facts["macd_hist"] > 0 else "低于" if facts["macd_hist"] < 0 else "等于",
                            facts["macd_hist"]))
        if facts["rsi14"] is not None:
            rsi_note = ("最近上涨较集中" if facts["rsi14"] >= 70 else
                        "最近下跌较集中" if facts["rsi14"] <= 30 else "尚未进入常用的30/70极端区")
            reasons.append("RSI %.1f：%s；不据此保证反转。" % (facts["rsi14"], rsi_note))
        reasons.append("当日成交量为此前20个交易日平均的 %.2f 倍。" % facts["volume_ratio20"])
        if facts["atr_pct"] is not None:
            reasons.append("ATR约为收盘价的 %.2f%%，用于了解日常波动幅度。" % (facts["atr_pct"] * 100))
        if facts["adv20_aud"] < MIN_ADV_LONG:
            reasons.append("成交金额不足买入观察门槛，先不考虑入场。")
        elif facts["adv20_aud"] < MIN_ADV_SHORT:
            reasons.append("成交金额不足做空观察门槛，先不考虑做空。")
    quote = dict(stock.get("quote", {}))
    quote_issue = None
    if quote.get("price") is not None:
        try:
            quote_at = dt.datetime.fromisoformat(quote["as_of"])
            observed_at = dt.datetime.fromisoformat(stock["fetched_at"])
            if quote_at.utcoffset() is None or observed_at.utcoffset() is None or not quote_at <= observed_at <= now:
                raise ValueError("quote time inconsistent")
        except (KeyError, TypeError, ValueError):
            quote_issue = "报价时刻缺失或晚于抓取/分析时刻；原文保留，页面不显示该报价。"
            quote["price"] = None
    return {"schema_version": 1, "ticker": stock["ticker"], "name": stock["name"],
            "market": "AU", "currency": "AUD", "mode": mode, "as_of": as_of,
            "expected_session": expected, "analyzed_at": now.isoformat(), "valid_until": valid_until,
            "source": {k: stock.get(k) for k in ("source", "source_url", "fetched_at", "sha256", "snapshot_kind")},
            "benchmark_source": {k: (benchmark or {}).get(k) for k in ("fetched_at", "sha256", "source")},
            "quote": {**quote, "delay": "unknown", "used_in_analysis": False},
            "quality": {"state": "unavailable" if issues else "single_source",
                        "issues": issues, "source_verified": False,
                        "calculation_warnings": calculation_warnings,
                        "quote_issue": quote_issue,
                        "history_revision": stock.get("revision"),
                        "latest_complete_traded_session": latest_complete,
                        "source_completed_session": source_completed,
                        "corporate_actions_state": stock.get("events_status", "not_supplied"),
                        "benchmark_events_in_window": benchmark_events,
                        "adjustment_metadata_verified": False,
                        "calendar_version": AC.CALENDAR_VERSION,
                        "excluded_unfinished_bars": len(all_bars) - len(bars)},
            "facts": facts, "chart": chart, "recent_events": recent, "scores": scores, "diagnostics": diagnostics,
            "assessment": {"bias": "long_watch" if long_setup else "short_watch" if short_setup else "observe",
                           "headline": "资料待核对，暂停方向判断" if blockers else "偏多，可关注买入条件" if long_setup else
                                       "偏空，可关注做空条件" if short_setup else "先观察，当前条件不完整或方向不一致",
                           "long": "资料待核对，暂停判断" if blockers else "技术条件符合，下一交易时段再核对成交价" if long_setup else "暂不符合本页的买入观察条件",
                           "short": "资料待核对，暂停判断" if blockers else "技术条件符合；借券及费用未确认，尚不能确认可执行" if short_setup else "暂不符合本页的做空观察条件",
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
                                 "events_status": "not_supplied",
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
