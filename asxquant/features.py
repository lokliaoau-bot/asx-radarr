# -*- coding: utf-8 -*-
"""Market-level feature construction for the ASX.

Grouped into economically motivated blocks so a forecast can be attributed to a
readable driver. Two blocks are Australia-specific:

* `xa_` carries commodities and the AUD. The ASX is a commodity-levered, rate-sensitive
  index; iron ore, copper, gold and the currency are not decoration here.
* `sh_` carries ASIC short-position disclosure. Short sellers are informed on average
  (Asquith-Pathak-Ritter 2005; Boehmer-Jones-Zhang 2008), so rising short interest
  carries a negative prior for forward returns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import crossmarket as XM
from . import indicators as ind
from .config import BENCHMARK, all_stock_tickers

BLOCKS = {
    "trend": "趋势", "momentum": "动量", "reversion": "均值回归",
    "volatility": "波动率", "breadth": "市场广度", "flow": "资金流",
    "short": "空头持仓", "crossasset": "跨市场/大宗商品",
}

PRIOR_SIGN = {
    "trend_ma50": +1, "trend_ma200": +1, "trend_macd": +1, "trend_adx_dir": +1,
    "trend_donchian": +1, "trend_golden": +1,
    "mom_5": +1, "mom_20": +1, "mom_60": +1, "mom_tsmom": +1, "mom_rsi14": +1,
    # ⚠️ 曾经这里还有一个 "rev_ret5": -1，它的实现是 ind.roc(bench, 5) ——
    # 与 "mom_5" **逐位相同**（实测相关系数 1.0000），却被赋了相反的先验符号。
    # 后果有三：combo（逐因子等权平均）里这个变量拿到双倍票数；L2 会把系数
    # 在两列间一分为二、等于对它单独放松正则；signal_scoreboard 里同一个数字
    # 出现两次且 stance 一正一负互相抵消，让 block 归因失真。已删除。
    # 不要重新加回来 —— 若想表达「5日收益的极端程度」，那是另一个量。
    "rev_z20": -1, "rev_rsi2": -1,
    "vol_rv20_z": -1, "vol_expansion": -1, "vol_axvi_z": -1, "vol_axvi_chg": -1,
    "vol_rv20_level": -1, "vol_rv20_pctile": -1, "vol_rv_ratio": -1,
    "vol_park_z": -1, "vol_dd": +1, "vol_vix_z": -1,
    "brd_ma50": +1, "brd_ma200": +1, "brd_mcclellan": +1, "brd_adratio": +1,
    "brd_nhnl": +1, "brd_updownvol": +1, "brd_corr": -1,
    "flow_cmf": +1, "flow_mfi": +1, "flow_obv": +1, "flow_dollarvol_z": +1,
    "sh_level_z": -1, "sh_chg20": -1, "sh_breadth_rising": -1, "sh_crowded": -1,
    "xa_spx20": +1, "xa_aud20": +1, "xa_gold20": +1, "xa_copper20": +1,
    "xa_oil20": +1, "xa_ust10": -1, "xa_china20": +1,
}

FEATURE_BLOCK = {}
for _f in PRIOR_SIGN:
    for _p, _b in (("trend_", "trend"), ("mom_", "momentum"), ("rev_", "reversion"),
                   ("vol_", "volatility"), ("brd_", "breadth"), ("flow_", "flow"),
                   ("sh_", "short"), ("xa_", "crossasset")):
        if _f.startswith(_p):
            FEATURE_BLOCK[_f] = _b
            break

FEATURE_LABEL = {
    "trend_ma50": "价格相对50日均线", "trend_ma200": "价格相对200日均线",
    "trend_macd": "MACD柱(波动率标准化)", "trend_adx_dir": "ADX方向强度",
    "trend_donchian": "唐奇安通道位置(252日)", "trend_golden": "50/200日均线金叉状态",
    "mom_5": "5日动量", "mom_20": "20日动量", "mom_60": "60日动量",
    "mom_tsmom": "时序动量(12-1月)", "mom_rsi14": "RSI(14)",
    "rev_z20": "20日价格Z分(反转)", "rev_rsi2": "RSI(2)超买超卖",
    "vol_rv20_z": "20日已实现波动率Z分", "vol_expansion": "波动率扩张(20/60)",
    "vol_rv20_level": "波动率绝对水平(对数)", "vol_rv20_pctile": "波动率历史百分位(扩张窗)",
    "vol_rv_ratio": "短期/中期波动率比(5/20)",
    "vol_axvi_z": "ASX波动率指数Z分", "vol_axvi_chg": "ASX波动率指数5日变化",
    "vol_park_z": "Parkinson波动率Z分", "vol_dd": "距252日高点回撤", "vol_vix_z": "美股VIX Z分",
    "brd_ma50": "50日线上方个股占比", "brd_ma200": "200日线上方个股占比",
    "brd_mcclellan": "麦克莱伦振荡器", "brd_adratio": "涨跌家数比(10日)",
    "brd_nhnl": "新高减新低占比", "brd_updownvol": "上涨/下跌成交额比",
    "brd_corr": "个股平均相关性(拥挤度)",
    "flow_cmf": "蔡金资金流(20日)", "flow_mfi": "资金流量指标MFI(14)",
    "flow_obv": "OBV能量潮斜率", "flow_dollarvol_z": "全市场成交额Z分",
    "sh_level_z": "全市场空头持仓水平Z分", "sh_chg20": "空头持仓20日变动",
    "sh_breadth_rising": "空头增仓个股占比", "sh_crowded": "高空头拥挤度",
    "xa_spx20": "标普500 20日涨跌", "xa_aud20": "澳元20日涨跌",
    "xa_gold20": "黄金20日涨跌", "xa_copper20": "铜20日涨跌", "xa_oil20": "原油20日涨跌",
    "xa_ust10": "美债10年收益率20日变动", "xa_china20": "上证综指20日涨跌",
}


def build_market_features(px, short_pct):
    """Return (features DataFrame, benchmark close Series)."""
    closes, highs, lows, vols = px["close"], px["high"], px["low"], px["volume"]
    bench = closes[BENCHMARK]
    bh, bl, bv = highs[BENCHMARK], lows[BENCHMARK], vols[BENCHMARK]

    # Breadth is measured across days, so it uses the dividend-adjusted series:
    # on the ASX a raw close puts a cluster of names "below their MA" every
    # February and August purely because they went ex-dividend.
    adj = px.get("adjclose", closes)
    uni = [t for t in all_stock_tickers() if t in closes.columns]
    sc = adj[uni] if all(t in adj.columns for t in uni) else closes[uni]
    sv = vols[uni]

    f = pd.DataFrame(index=closes.index)

    # ---- trend ----
    f["trend_ma50"] = ind.dist_from_ma(bench, 50)
    f["trend_ma200"] = ind.dist_from_ma(bench, 200)
    _, _, hist = ind.macd(bench)
    f["trend_macd"] = hist / ind.atr(bh, bl, bench, 14).replace(0, np.nan)
    adx_v, pdi, mdi = ind.adx(bh, bl, bench, 14)
    f["trend_adx_dir"] = adx_v * np.sign(pdi - mdi) / 100.0
    f["trend_donchian"] = ind.donchian_pos(bench, 252) - 0.5
    f["trend_golden"] = (bench.rolling(50).mean() > bench.rolling(200).mean()).astype(float) - 0.5

    # ---- momentum ----
    f["mom_5"] = ind.roc(bench, 5)
    f["mom_20"] = ind.roc(bench, 20)
    f["mom_60"] = ind.roc(bench, 60)
    f["mom_tsmom"] = ind.tsmom(bench, 252, 21)
    f["mom_rsi14"] = (ind.rsi(bench, 14) - 50) / 50.0

    # ---- mean reversion ----
    f["rev_z20"] = ind.zscore(bench, 20)
    f["rev_rsi2"] = (ind.rsi(bench, 2) - 50) / 50.0

    # ---- volatility ----
    rv20, rv60 = ind.realized_vol(bench, 20), ind.realized_vol(bench, 60)
    f["vol_rv20_z"] = ind.zscore(rv20, 252)
    f["vol_expansion"] = rv20 / rv60.replace(0, np.nan) - 1.0

    # Volatility LEVEL, not just its 252-day z-score. Everything above is relative to
    # a rolling window, so the model could never see "vol is low in absolute terms" --
    # which is precisely the one-line predictor that beats the whole ensemble on
    # vol_up_20d (AUC 0.775 vs 0.713). The percentile uses an EXPANDING window so it
    # stays a level statistic instead of collapsing back into a relative one.
    f["vol_rv20_level"] = np.log(rv20.clip(lower=1e-4))
    f["vol_rv20_pctile"] = rv20.expanding(min_periods=252).rank(pct=True)
    f["vol_rv_ratio"] = ind.realized_vol(bench, 5) / rv20.replace(0, np.nan)
    f["vol_park_z"] = ind.zscore(ind.parkinson_vol(bh, bl, 20), 252)
    f["vol_dd"] = ind.max_drawdown(bench, 252)

    # ---- 跨市场序列：一律经交易时段对齐（修掉时区前视偏差）----
    # 外盘的「D 日收盘」发生在澳股 D 日收盘**之后**（标普500 晚约 14 小时），
    # 把它对齐到澳股 D 日等于提前知道了驱动 D+1 日跳空的变量。详见 crossmarket.py。
    def _x(tk):
        if tk not in closes.columns:
            return pd.Series(np.nan, index=closes.index)
        return XM.align(closes[tk], closes.index, tk)

    axvi = _x("^AXVI")                       # 本土，滞后 0
    f["vol_axvi_z"] = ind.zscore(axvi, 252)
    f["vol_axvi_chg"] = axvi / axvi.shift(5) - 1.0
    f["vol_vix_z"] = ind.zscore(_x("^VIX"), 252)     # 美国，滞后 1

    # ---- breadth ----
    f["brd_ma50"] = ind.pct_above_ma(sc, 50) - 0.5
    f["brd_ma200"] = ind.pct_above_ma(sc, 200) - 0.5
    osc, _ = ind.mcclellan(sc)
    f["brd_mcclellan"] = osc / 100.0
    f["brd_adratio"] = ind.ad_ratio(sc).rolling(10, min_periods=3).mean()
    f["brd_nhnl"] = ind.new_high_low(sc, 252)
    f["brd_updownvol"] = ind.up_down_volume(sc, sv).rolling(5, min_periods=2).mean()
    # 取样改为「历史完整度 + 成交额」，不再是按字母序的前 40 只
    f["brd_corr"] = ind.avg_correlation(sc, 60, sample=60, volumes=sv)

    # ---- money flow ----
    f["flow_cmf"] = ind.cmf(bh, bl, bench, bv, 20)
    f["flow_mfi"] = (ind.mfi(bh, bl, bench, bv, 14) - 50) / 50.0
    o = ind.obv(bench, bv)
    f["flow_obv"] = (o - o.shift(20)) / bv.rolling(60, min_periods=20).mean().replace(0, np.nan) / 20.0
    mkt_dv = (sc * sv).sum(axis=1, min_count=10)
    f["flow_dollarvol_z"] = ind.zscore(mkt_dv.rolling(5, min_periods=2).mean(), 252)

    # ---- short positioning (ASIC) ----
    if short_pct is not None and short_pct.notna().any().any():
        sp = short_pct.reindex(closes.index)
        agg = sp.median(axis=1)
        f["sh_level_z"] = ind.zscore(agg, 252)
        f["sh_chg20"] = agg - agg.shift(20)
        rising = (sp - sp.shift(20)) > 0
        valid = sp.notna() & sp.shift(20).notna()
        f["sh_breadth_rising"] = ((rising & valid).sum(axis=1) /
                                  valid.sum(axis=1).replace(0, np.nan)) - 0.5
        f["sh_crowded"] = (sp > 5.0).sum(axis=1) / sp.notna().sum(axis=1).replace(0, np.nan)
    else:
        for c in ("sh_level_z", "sh_chg20", "sh_breadth_rising", "sh_crowded"):
            f[c] = np.nan

    # ---- cross-asset / commodities ----
    def _roc_of(tk, n=20):
        s = _x(tk)
        return s / s.shift(n) - 1.0

    f["xa_spx20"] = _roc_of("^GSPC")
    f["xa_aud20"] = _roc_of("AUDUSD=X")
    f["xa_gold20"] = _roc_of("GC=F")
    f["xa_copper20"] = _roc_of("HG=F")
    f["xa_oil20"] = _roc_of("CL=F")
    f["xa_china20"] = _roc_of("000001.SS")
    t10 = _x("^TNX")
    f["xa_ust10"] = t10 - t10.shift(20)

    f = f[[c for c in PRIOR_SIGN.keys() if c in f.columns]]
    return f.replace([np.inf, -np.inf], np.nan)


def feature_fingerprint(X: pd.DataFrame) -> str:
    """特征矩阵指纹，用来判断走向前缓存是否还能复用。

    只按 MODEL_VERSION 判定失效是不够的：改动 config 里的股票池、或改动某个指标的
    实现，都会让**同名特征的数值**变掉，而 MODEL_VERSION 不变。于是新旧两代预测
    会被拼接进同一条序列，而且永远不会自愈（后续每次运行都跳过「已有值」的区块）。
    """
    import hashlib
    h = hashlib.sha1()
    h.update(",".join(map(str, X.columns)).encode())
    h.update(str(X.shape).encode())
    if len(X):
        # 取三个锚点行的数值：改动实现几乎必然改变其中之一
        for pos in (0, len(X) // 2, len(X) - 1):
            row = X.iloc[pos].to_numpy(dtype=float)
            h.update(np.nan_to_num(row, nan=-9.87e9).round(8).tobytes())
            h.update(str(X.index[pos]).encode())
    return h.hexdigest()[:16]
