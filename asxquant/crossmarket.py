# -*- coding: utf-8 -*-
"""跨市场序列的「交易时段对齐」—— 本项目最严重的一个前视偏差的修复点。

问题
----
Yahoo 按**日历日**给每个标的打标签，本项目此前直接把外盘 D 日的收盘价
`reindex` 到澳股 D 日这一行。但按 UTC 排一下收盘时刻：

    澳交所 ASX        06:00 UTC (悉尼 16:00)      <-- 我们下单的时刻
    上证 / 恒生        07:00 / 08:00 UTC          <-- 比 ASX 晚 1-2 小时
    伦敦金/铜/原油     18:30-19:30 UTC
    标普500 / VIX / 美债 20:00-21:00 UTC          <-- 比 ASX 晚 14 小时
    AUDUSD 日线        21:00-22:00 UTC

也就是说：**标普500 D 日的收盘价，要到悉尼 D+1 日凌晨才存在。**
用它去预测「澳股 D 日收盘 → D+1 日收盘」的涨跌，等于提前知道了驱动
D+1 日澳股开盘跳空的那个变量。合成数据实测：一个 20 日窗口里只泄漏
最新一天，样本外 AUC 就从 0.511 虚高到 0.567。

更糟的是**训练/实盘分布不一致**：报告在悉尼时间傍晚生成，那时美股当日还没开盘，
Yahoo 的 `^GSPC` 最后一行必然是 D-1 日。于是模型训练时吃的是「同日」口径、
实盘打分时吃的却是「滞后一日」口径，两者根本不是同一个特征。

修复
----
所有非澳洲本土标的，一律取「**严格早于**该澳股交易日的最后一根日线」。
另加陈旧度上限：外盘停更超过 `max_stale_days` 日历日就置 NaN，避免退市/
停更的指数被 ffill 出一条永远不变的假直线（`^AXJR` 就有这个风险）。

本土标的（`.AX`、`^AX*`）与澳股同场收盘，滞后 0。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 相对澳股交易时段需要滞后几个「澳股交易日」才算可得。
# 0 = 与 ASX 同场收盘；1 = 收盘晚于 ASX，必须用上一根。
VENUE_LAG = {
    "ASX": 0,       # ^AXJO ^AXKO ^AXJR ^AXVI ^AXMJ... 以及全部 *.AX
    "ASIA": 1,      # 000001.SS ^HSI ^N225 ^KS11  (07:00-08:00 UTC 收盘)
    "GLOBAL": 1,    # 美股、美债、伦敦金属、原油、FX (18:00-22:00 UTC 收盘)
}

# 显式登记；未登记的一律按最保守的 GLOBAL 处理（滞后 1）。
TICKER_VENUE = {
    "^AXJO": "ASX", "^AXKO": "ASX", "^AXJR": "ASX", "^AXVI": "ASX",
    "^AXMJ": "ASX", "^AXFJ": "ASX", "^AXEJ": "ASX", "^AXHJ": "ASX",
    "^AXNJ": "ASX", "^AXDJ": "ASX", "^AXSJ": "ASX", "^AXIJ": "ASX",
    "^AXTJ": "ASX", "^AXUJ": "ASX", "^AXPJ": "ASX",
    "000001.SS": "ASIA", "^HSI": "ASIA", "^N225": "ASIA", "^KS11": "ASIA",
    "^GSPC": "GLOBAL", "^VIX": "GLOBAL", "^TNX": "GLOBAL", "^IRX": "GLOBAL",
    "^DJI": "GLOBAL", "^IXIC": "GLOBAL",
    "GC=F": "GLOBAL", "HG=F": "GLOBAL", "CL=F": "GLOBAL", "SI=F": "GLOBAL",
    "AUDUSD=X": "GLOBAL", "DX-Y.NYB": "GLOBAL",
}

MAX_STALE_DAYS = 8          # 外盘停更超过这么多日历日就当作没有数据


def venue_of(ticker: str) -> str:
    if ticker in TICKER_VENUE:
        return TICKER_VENUE[ticker]
    if ticker.endswith(".AX") or ticker.startswith("^AX"):
        return "ASX"
    return "GLOBAL"


def lag_of(ticker: str) -> int:
    return VENUE_LAG[venue_of(ticker)]


def align(series: pd.Series, asx_index: pd.DatetimeIndex, ticker: str,
          max_stale_days: int = MAX_STALE_DAYS) -> pd.Series:
    """把任意标的的日线对齐到澳股交易日，且**保证澳股收盘时点已可得**。

    本土标的：取同日（缺失则前向填充，但受陈旧度上限约束）。
    外盘标的：取**严格早于**该澳股交易日的最后一根日线。

    这不是「稳妥起见多滞后一天」，而是按收盘时刻推出来的唯一正确口径：
    悉尼 16:00 时纽约还没开盘，那根 K 线在物理上并不存在。
    """
    s = pd.Series(series, dtype=float).dropna()
    asx_index = pd.DatetimeIndex(asx_index)
    if s.empty:
        return pd.Series(np.nan, index=asx_index)
    s = s.sort_index()
    src = s.index

    if lag_of(ticker) == 0:
        # 同场：本日若有值就用本日，否则用最近一根（含本日）
        pos = src.searchsorted(asx_index, side="right") - 1
    else:
        # 外盘：只能用**严格早于**本澳股交易日的那一根
        pos = src.searchsorted(asx_index, side="left") - 1

    ok = pos >= 0
    out = np.full(len(asx_index), np.nan)
    if ok.any():
        out[ok] = s.values[pos[ok]]
        # 陈旧度闸门：外盘退市/停更时不再 ffill 出一条假直线
        age = (asx_index[ok] - src[pos[ok]]).days
        out[np.where(ok)[0][age > max_stale_days]] = np.nan
    return pd.Series(out, index=asx_index)


def align_frame(frame: pd.DataFrame, asx_index: pd.DatetimeIndex,
                max_stale_days: int = MAX_STALE_DAYS) -> pd.DataFrame:
    """逐列按各自场地规则对齐。"""
    return pd.DataFrame(
        {c: align(frame[c], asx_index, c, max_stale_days) for c in frame.columns},
        index=pd.DatetimeIndex(asx_index))


def leak_audit(frame: pd.DataFrame, asx_index: pd.DatetimeIndex) -> list:
    """自检：列出所有「同日对齐会造成前视」的标的，供报告页展示。

    在 CI 里跑一次，任何新加进 MACRO 的标的都会自动被检查到，
    不会因为忘了登记而悄悄退回旧的错误口径。
    """
    rows = []
    for c in frame.columns:
        v = venue_of(c)
        rows.append({"ticker": c, "venue": v, "lag_sessions": VENUE_LAG[v],
                     "leaks_if_same_day": VENUE_LAG[v] > 0})
    return rows
