# -*- coding: utf-8 -*-
"""Descriptive ASX price/volume diagnostics; never a score or trading model."""
from math import isfinite

from . import indicators as I


def _value(x):
    return float(x) if x is not None and isfinite(x) else None


def describe(frame, facts, recursive_ranges_ok):
    """CMF and up/down volume use 20 completed bars, neutral days excluded.

    ADX/DI reuse the existing Wilder implementation; malformed old ranges make
    them null. No thresholds, weights, future labels or fitted parameters.
    """
    close, high, low, volume = (frame[k].astype(float) for k in ("close", "high", "low", "volume"))
    change = close.diff().tail(20)
    up = float(volume.tail(20)[change > 0].sum())
    down = float(volume.tail(20)[change < 0].sum())
    adx = plus = minus = None
    if recursive_ranges_ok:
        a, p, m = I.adx(high, low, close)
        adx, plus, minus = (_value(s.iloc[-1]) for s in (a, p, m))
    extra = {"adx14": adx, "di_plus14": plus, "di_minus14": minus,
             "cmf20": _value(I.cmf(high, low, close, volume).iloc[-1]),
             "up_volume20": up, "down_volume20": down,
             "down_up_volume20": down / up if up > 0 else None,
             "gap_1d": float(frame["open"].iloc[-1] / close.iloc[-2] - 1)}
    f = facts
    trend = "long" if f["close"] > f["ma20"] > f["ma50"] else "short" if f["close"] < f["ma20"] < f["ma50"] else "mixed"
    trend_note = {"long": "价格、20日线和50日线向上排列。", "short": "价格、20日线和50日线向下排列。",
                  "mixed": "价格和20/50日均线没有排成同一方向。"}[trend]
    if f["ma200"] is not None:
        trend_note += " 收盘价比200日均线%s %.2f%%。" % ("高" if f["close"] >= f["ma200"] else "低", abs(f["close"] / f["ma200"] - 1) * 100)
    if adx is not None:
        trend_note += " ADX %.1f描述趋势强度；DI+ %.1f、DI− %.1f描述方向。" % (adx, plus, minus)
    else:
        trend_note += " ADX/DI暂不可用。"
    hist = f["macd_hist"]
    momentum_note = "MACD暂不可用。" if hist is None else "MACD柱为%s，快线%s信号线；它与均线看的是不同时间尺度。" % (
        "正" if hist > 0 else "负" if hist < 0 else "零", "高于" if hist > 0 else "低于" if hist < 0 else "等于")
    if f["rsi14"] is not None:
        momentum_note += " RSI %.1f；涨跌集中不等于马上反转。" % f["rsi14"]
    ratio = extra["down_up_volume20"]
    volume_note = "CMF20 %.3f是用日线价格和成交量估算的收盘位置，不能当作真实资金流。" % extra["cmf20"]
    volume_note += " 下跌日/上涨日成交量比%s，平盘不计；当日量比%.2f。" % (
        "未知（没有上涨日）" if ratio is None else "%.2f" % ratio, f["volume_ratio20"])
    position = "收盘已低于此前20日最低价" if f["close"] < f["prior20_low"] else "收盘已高于此前20日最高价" if f["close"] > f["prior20_high"] else "收盘仍在此前20日价格区间内"
    risk_note = position + "；这些是历史价位。"
    if f["atr_pct"] is not None:
        risk_note += " ATR约占股价%.2f%%。" % (f["atr_pct"] * 100)
    risk_note += " 当日开盘相对前收跳空%+.2f%%，无法据日线保证止损成交。" % (extra["gap_1d"] * 100)
    conflicts = []
    if hist is not None and ((trend == "short" and hist > 0) or (trend == "long" and hist < 0)):
        conflicts.append("均线排列与MACD柱方向不同：中期趋势和短线动能有冲突，不能把它们当作一致确认。")
    cards = [{"key": "trend", "title": "趋势结构", "summary": trend_note},
             {"key": "momentum", "title": "短线动能", "summary": momentum_note},
             {"key": "volume", "title": "价量表现", "summary": volume_note},
             {"key": "risk", "title": "波动与价位", "summary": risk_note}]
    return extra, {"cards": cards, "conflicts": conflicts,
                   "scope": "仅解读完整日线，不参与0–4条件计数；没有胜率、净收益或可执行性验证。"}
