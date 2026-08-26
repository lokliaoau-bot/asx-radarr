# -*- coding: utf-8 -*-
"""个股多空评分的样本外验证（v2）。

一条从来没有被历史检验过的推荐是断言，不是信号。本模块把 `picks.py` 里的
同一套评分成分重建成完整时间序列，然后跑标准的横截面因子检验：

  * 信息系数 IC —— 评分与前瞻收益的每日秩相关。前瞻收益在**每个交易日**上都
    测一次 `horizon` 天，所以相邻观测重叠，朴素 t 值会大 3-5 倍。这里报告的每个
    t 都是 Newey-West 修正过的（见 `_overlap_stats`），朴素值仅作对照保留。
  * 分位价差 —— 评分最高与最低五分位的前瞻收益。
  * 多空组合 —— 多头前 N、空头前 N，定期再平衡，扣成本。

全部滞后：评分用到截至 t 日（含）的数据，收益从 t+1 开始测。ASIC 空头数据已在
`datafeed` 里按披露滞后前移。

v2 修正
-------
1. **Spearman IC 算错了**。v1 先对**全横截面**求秩，再用有效性掩码筛掉一部分。
   剩下的秩不再是 1..m，于是算出来的既不是 Spearman 也不是 Pearson。
   合成数据实测单日绝对误差最大 0.030，均值 IC 偏低 0.6%，更要命的是它给
   逐日 IC 序列注入了额外方差，从而**压低 t 值**。现在先掩码后求秩。
2. **做空腿加上借券成本**。v1 只收 15bps 单边交易成本，完全没有融券费。
   澳股高做空拥挤度的名字年化借券费常在 2-8%，对一个持有 20 天的空头篮子，
   这不是可以忽略的项。新增 `borrow_bps_pa`，并对空头腿按持有天数计提。
3. **`start` 的兜底**。`mask.sum(axis=1).ge(30).idxmax()` 在**永远不满足**时会
   返回第一行，于是整个验证悄悄从一段没有空头数据的历史开始跑。现在显式判定。
4. **停牌/退市不再按 0 收益持有**。v1 `rr.fillna(0)` 把停牌股票当成一条水平线
   继续持有。现在停止交易的名字会被移出组合并把权重摊回其余持仓。
5. **多重检验修正**。新增 deflated Sharpe：这个项目试过的变体不少，
   「最好的那个」的 Sharpe 必然向上有偏。
6. **分期稳健性**。新增逐年 IC 表 —— 一个只在 2020-2021 有效的因子和一个
   稳定有效的因子，全样本 t 值可以完全一样。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import model as M
from .picks import LONG_WEIGHTS, SHORT_WEIGHTS

# 澳股做空的真实摩擦：ASX 高空头持仓名字的年化借券费通常 2-8%，取 300bps 为
# 保守中值。忽略它会让空头腿的回测系统性偏好。
DEFAULT_BORROW_BPS_PA = 300.0


def _xz_raw(df, clip=3.0):
    """横截面 z 分，**不填缺失** —— 缺失位置保持 NaN 以便下游知道哪些不可得。"""
    mu = df.mean(axis=1)
    sd = df.std(axis=1, ddof=0).replace(0, np.nan)
    return df.sub(mu, axis=0).div(sd, axis=0).clip(-clip, clip)


def _xz_frame(df, clip=3.0):
    """横截面 z 分（缺失填 0）。仅用于不参与覆盖率归一化的场合。"""
    return _xz_raw(df, clip).fillna(0.0)


def _blend_frames(components, weights):
    """按**可得成分**重新归一化的加权和 —— 必须与生产端 `picks._blend` 同口径。

    ⚠️ 这个函数存在的唯一理由：`picks.py` 的评分是 `Σwᵢzᵢ / Σwᵢ`（只对可得的 i 求和），
    如果这里仍然用「缺失投 0 票」的 `Σwᵢzᵢ`，验证的就不是生产评分。
    本项目已经在 `squeeze_safe` 上踩过一次这个坑（验证里那一项曾是全零占位）。
    **改 picks.py 的权重或缺失处理，必须同步改这里。**

    components: {name: (z_frame, ok_frame)}
    """
    num = den = None
    for name, w in weights.items():
        z, ok = components[name]
        okf = ok.astype(float)
        zz = z.fillna(0.0) * okf
        num = w * zz if num is None else num + w * zz
        den = w * okf if den is None else den + w * okf
    return (num / den.where(den > 1e-9)).fillna(0.0)


def _nw_variance(x, lag):
    """均值估计量的 Newey-West 长期方差（Bartlett 核）。"""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return np.nan, np.nan
    d = x - x.mean()
    g0 = float((d * d).sum() / n)
    if g0 <= 0:
        return np.nan, np.nan
    s = g0
    for k in range(1, min(lag, n - 1) + 1):
        s += 2.0 * (1.0 - k / (lag + 1.0)) * float((d[k:] * d[:-k]).sum() / n)
    s = max(s, g0 * 1e-6)          # 核求和为负是小样本假象
    return s, float(np.sqrt(s / g0))


def _overlap_stats(series, horizon):
    """对建立在**重叠**前瞻收益上的日频统计量做诚实推断。

    本项目在每个交易日都测一次 `horizon` 日前瞻收益，相邻观测共享
    (horizon-1)/horizon 的收益窗口。教科书 t = mean/(sd/sqrt(n)) 假设独立抽样，
    在这里严重放大：实测日频 IC 序列的一阶自相关约 +0.9，朴素标准误小 3-5 倍。

    返回朴素 t，外加 Newey-West t（Bartlett 核，lag = horizon-1），以及一个
    不依赖任何假设的交叉验证：把序列拆成 `horizon` 个交错子样本 —— 每一个都是
    **不重叠**的 —— 并报告它们 t 值的范围。请读 Newey-West 那个，不要读朴素那个。
    """
    s = pd.Series(series).dropna()
    n = len(s)
    if n < 60:
        return None
    m, sd = float(s.mean()), float(s.std(ddof=0))
    naive = m / (sd / np.sqrt(n)) if sd > 0 else np.nan
    lrv, infl = _nw_variance(s.values, max(1, horizon - 1))
    nw = m / np.sqrt(lrv / n) if np.isfinite(lrv) and lrv > 0 else np.nan

    sub = []
    for i in range(horizon):
        w = s.iloc[i::horizon]
        if len(w) > 30 and w.std(ddof=0) > 0:
            sub.append(float(w.mean() / (w.std(ddof=0) / np.sqrt(len(w)))))

    def _r(x, d=2):
        return round(float(x), d) if x is not None and np.isfinite(x) else None

    return {
        "mean": round(m, 5), "sd": round(sd, 5), "n_days": int(n),
        "t_naive": _r(naive), "t_stat": _r(nw),      # t_stat 就是 Newey-West 那个
        "se_inflation": _r(infl, 1),
        "autocorr_1": _r(s.autocorr(1) if n > 2 else np.nan),
        "t_nonoverlap_median": _r(np.median(sub)) if sub else None,
        "t_nonoverlap_lo": _r(min(sub)) if sub else None,
        "t_nonoverlap_hi": _r(max(sub)) if sub else None,
        "verdict": _verdict(nw),
    }


def _verdict(t):
    if t is None or not np.isfinite(t):
        return "insufficient"
    a = abs(t)
    if a >= 3.0:
        return "strong"
    if a >= 2.0:
        return "marginal"
    if a >= 1.6:
        return "weak"
    return "none"


VERDICT_CN = {
    "strong": "统计显著",
    "marginal": "勉强达标（边缘显著）",
    "weak": "微弱，达不到常规门槛",
    "none": "无统计显著性",
    "insufficient": "样本不足",
}


def build_factor_panel(px, short_pct, tickers, sector_map, short_shares=None):
    """把每个评分成分做成 date x ticker 的时间序列。"""
    c = px["close"][tickers]
    h, l, v = px["high"][tickers], px["low"][tickers], px["volume"][tickers]
    a = px.get("adjclose", px["close"])[tickers]

    rng = (h - l).replace(0, np.nan)
    mfv = (((c - l) - (h - c)) / rng).fillna(0) * v
    cmf = mfv.rolling(20, min_periods=10).sum() / v.rolling(20, min_periods=10).sum().replace(0, np.nan)

    obv = (np.sign(a.diff().fillna(0)) * v).cumsum()
    obv_slope = (obv - obv.shift(20)) / v.rolling(60, min_periods=20).mean().replace(0, np.nan) / 20.0

    dv = c * v
    dv5 = dv.rolling(5, min_periods=2).mean()
    dv_z = (dv5 - dv5.rolling(120, min_periods=40).mean()) / \
        dv5.rolling(120, min_periods=40).std(ddof=0).replace(0, np.nan)

    mom20 = a / a.shift(20) - 1.0
    mom60 = a / a.shift(60) - 1.0
    ma50 = a / a.rolling(50, min_periods=25).mean() - 1.0
    ma200 = a / a.rolling(200, min_periods=100).mean() - 1.0

    # RSI：与 indicators.rsi 同一口径（全涨 100 / 全跌 0 / 暖机 NaN），
    # v1 这里分母为 0 时得到 NaN，随后被当成「不超买」，与生产端不一致。
    d = a.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    tot = up + dn
    rsi = (100.0 * up / tot.replace(0, np.nan)).where(tot != 0, 50.0).where(up.notna() & dn.notna())

    hi = a.rolling(252, min_periods=100).max()
    lo = a.rolling(252, min_periods=100).min()
    pos52 = (a - lo) / (hi - lo).replace(0, np.nan)
    extension = ((pos52 - 0.5) * 2 + (ma200 / 0.20).clip(-2, 2) + (rsi - 50) / 20.0) / 3.0

    sp = short_pct.reindex(index=c.index, columns=tickers)
    sp_chg = sp - sp.shift(20)
    advol = v.rolling(20, min_periods=5).mean().replace(0, np.nan)

    # days-to-cover，对齐生产端 picks.py 的 `squeeze_safe` 成分（权重 0.08）。
    if short_shares is not None:
        dtc = short_shares.reindex(index=c.index, columns=tickers) / advol
    else:
        dtc = pd.DataFrame(np.nan, index=c.index, columns=tickers)

    sect = pd.Series({t: sector_map.get(t, "na") for t in tickers})

    def _sector_mean(frame):
        out = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
        for s in sect.unique():
            cols = [t for t in tickers if sect[t] == s]
            if not cols:
                continue
            m = frame[cols].mean(axis=1)
            for t in cols:
                out[t] = m
        return out

    sector_flow = _xz_frame(_sector_mean(cmf) * 0.6 + _sector_mean(mom20) * 0.4)
    sector_short = _xz_frame(_sector_mean(sp_chg).fillna(0) * 0.6 - _sector_mean(cmf) * 0.4)

    return {
        "cmf": cmf, "obv": obv_slope, "turnover": dv_z, "mom20": mom20, "mom60": mom60,
        "above_ma50": ma50, "above_ma200": ma200, "rsi": rsi, "extension": extension,
        "short_pct": sp, "short_chg": sp_chg,
        "sector_flow": sector_flow, "sector_short": sector_short,
        "adv": advol * c, "adj": a, "dtc": dtc,
    }


def composite_scores(F):
    """用生产端权重把多空评分重建成时间序列。

    ⚠️ 与 `picks.score_stocks` 严格同口径：**按可得成分归一化**，缺失的成分退出
    加权，而不是投一张 0 票。两边不一致时，这里算出来的 t 值验的就不是生产评分。
    """
    def _allok(d):
        return pd.DataFrame(True, index=d.index, columns=d.columns)

    def _zo(key):
        z = _xz_raw(F[key])
        return z, z.notna()          # z 为 NaN 处即为不可得（原值缺失或该行无方差）

    def _neg(pair):
        z, ok = pair
        return -z, ok

    # 超买惩罚：RSI 缺失时不假装它是 50，而是标为缺失（与生产端一致）
    ob_z = _xz_raw((F["rsi"] - 72).clip(lower=0))

    lc = {
        "sector_flow":  (F["sector_flow"], _allok(F["sector_flow"])),
        "cmf":          _zo("cmf"),
        "obv":          _zo("obv"),
        "turnover":     _zo("turnover"),
        "mom20":        _zo("mom20"),
        "mom60":        _zo("mom60"),
        "above_ma50":   _zo("above_ma50"),
        "above_ma200":  _zo("above_ma200"),
        "short_cover":  _neg(_zo("short_chg")),
        "short_low":    _neg(_zo("short_pct")),
        "not_extended": _neg((ob_z, ob_z.notna())),
    }
    sc = {
        "sector_short": (F["sector_short"], _allok(F["sector_short"])),
        "short_build":  _zo("short_chg"),
        "short_level":  _zo("short_pct"),
        "cmf_neg":      _neg(_zo("cmf")),
        "obv_neg":      _neg(_zo("obv")),
        "mom_neg":      _neg(_zo("mom20")),
        "below_ma":     _neg(_zo("above_ma50")),
        "extension":    _zo("extension"),
        "squeeze_safe": _neg(_zo("dtc")),
    }
    return _blend_frames(lc, LONG_WEIGHTS), _blend_frames(sc, SHORT_WEIGHTS)


def _ic(score, fwd, min_names=20):
    """逐日横截面 Spearman IC。

    **先掩码，再求秩** —— v1 反过来做，算出来的不是 Spearman。
    """
    valid = score.notna() & fwd.notna()
    s = score.where(valid).rank(axis=1)
    f = fwd.where(valid).rank(axis=1)
    n = valid.sum(axis=1)
    sm, fm = s.mean(axis=1), f.mean(axis=1)
    cov = ((s.sub(sm, axis=0)) * (f.sub(fm, axis=0))).sum(axis=1)
    den = np.sqrt((s.sub(sm, axis=0) ** 2).sum(axis=1) * (f.sub(fm, axis=0) ** 2).sum(axis=1))
    return (cov / den.replace(0, np.nan)).where(n >= min_names).dropna()


def _ic_by_year(ic):
    """逐年 IC —— 暴露只在某一段有效的因子。"""
    if ic is None or not len(ic):
        return []
    g = ic.groupby(ic.index.year)
    return [{"year": int(y), "mean_ic": round(float(v.mean()), 5),
             "n_days": int(len(v)), "hit_rate": round(float((v > 0).mean()), 3)}
            for y, v in g if len(v) >= 30]


def run_validation(px, short_pct, tickers, sector_map, horizon=20,
                   n_side=3, cost_bps=15.0, min_adv=3e6, short_shares=None,
                   borrow_bps_pa=DEFAULT_BORROW_BPS_PA, n_trials=12):
    """多头与空头评分的完整横截面成绩单。"""
    F = build_factor_panel(px, short_pct, tickers, sector_map, short_shares=short_shares)
    long_s, short_s = composite_scores(F)

    a = F["adj"]
    fwd = a.shift(-horizon) / a - 1.0
    r1 = a.pct_change()

    liquid = F["adv"] > min_adv
    have = F["short_pct"].notna()          # ASIC 空头数据 2022 才开始
    mask = liquid & have & a.notna()

    enough = mask.sum(axis=1).ge(30)
    if not bool(enough.any()):
        # v1 在这里会被 idxmax 悄悄返回第一行，于是从一段没有空头数据的历史开跑
        return {"error": "insufficient_cross_section",
                "note": "满足流动性与空头披露条件的股票从未达到 30 只，无法做横截面验证",
                "max_names": int(mask.sum(axis=1).max()) if len(mask) else 0}
    start = enough.idxmax()

    L = long_s.where(mask).loc[start:]
    S = short_s.where(mask).loc[start:]
    FW = fwd.loc[start:]

    ic_l, ic_s = _ic(L, FW), _ic(S, FW)

    def _ic_stats(ic):
        st = _overlap_stats(ic, horizon)
        if st is None:
            return None
        st["mean_ic"] = st.pop("mean")
        st["ic_std"] = st.pop("sd")
        st["hit_rate"] = round(float((ic > 0).mean()), 3)
        st["verdict_cn"] = VERDICT_CN.get(st["verdict"], st["verdict"])
        st["by_year"] = _ic_by_year(ic)
        return st

    def _quintile(score):
        q = score.rank(axis=1, pct=True)
        top = FW.where(q >= 0.8).mean(axis=1)
        bot = FW.where(q <= 0.2).mean(axis=1)
        d = pd.concat([top.rename("top"), bot.rename("bot")], axis=1).dropna()
        if len(d) < 60:
            return None
        st = _overlap_stats(d["top"] - d["bot"], horizon) or {}
        return {"top": round(float(d["top"].mean()), 5),
                "bottom": round(float(d["bot"].mean()), 5),
                "spread": round(float((d["top"] - d["bot"]).mean()), 5),
                "t_stat": st.get("t_stat"), "t_naive": st.get("t_naive"),
                "verdict": st.get("verdict"),
                "verdict_cn": VERDICT_CN.get(st.get("verdict"), None),
                "n": int(len(d))}

    # ---- 组合，每 `horizon` 天再平衡 -----------------------------------
    # 权重写在评分日**之后**的那些日子上，本身已经滞后正确。不要再 shift。
    dates = L.index
    tradable = a.loc[start:].notna() & r1.loc[start:].notna()
    posL = pd.DataFrame(0.0, index=dates, columns=L.columns)
    posS = posL.copy()
    for i in range(0, len(dates), horizon):
        d0 = dates[i]
        d1 = dates[min(i + horizon, len(dates) - 1)]
        lrow, srow = L.loc[d0].dropna(), S.loc[d0].dropna()
        if len(lrow) < 20 or len(srow) < 20:
            continue
        lw = list(lrow.nlargest(n_side).index)
        sw = [t for t in srow.nlargest(n_side).index if t not in lw]
        if not lw or not sw:
            continue
        seg = (dates > d0) & (dates <= d1)
        for t in lw:
            posL.loc[seg, t] = 1.0 / len(lw)
        for t in sw:
            posS.loc[seg, t] = 1.0 / len(sw)

    # 停牌/退市：v1 用 fillna(0) 把它们当成一条水平线继续持有。
    # 现在把不能交易的名字从持仓里剔除，剩余仓位按比例摊回，保持总敞口不变。
    posL = posL.where(tradable, 0.0)
    posS = posS.where(tradable, 0.0)
    posL = posL.div(posL.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    posS = posS.div(posS.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

    rr = r1.reindex(index=dates, columns=L.columns).where(tradable).fillna(0.0)
    mkt = rr.where(mask.loc[start:]).mean(axis=1).fillna(0.0)

    gL = (posL * rr).sum(axis=1)
    gS = (posS * rr).sum(axis=1)
    turn = (posL.diff().abs().sum(axis=1) + posS.diff().abs().sum(axis=1)).fillna(0.0)

    ls_gross = 0.5 * gL - 0.5 * gS
    trade_cost = 0.5 * turn * (cost_bps / 1e4)
    # 借券费：只对空头腿、只在实际持有的日子计提
    borrow = 0.5 * posS.sum(axis=1) * (borrow_bps_pa / 1e4) / 252.0
    ls_net = ls_gross - trade_cost - borrow

    def _stats(r, label, trials=None):
        r = pd.Series(r).fillna(0.0)
        eq = (1 + r).cumprod()
        yrs = max(len(r) / 252.0, 1e-9)
        vol = float(r.std(ddof=0) * np.sqrt(252))
        last = float(eq.iloc[-1])
        cagr = float(last ** (1 / yrs) - 1) if last > 0 else -1.0
        sr_daily = float(r.mean() / r.std(ddof=0)) if r.std(ddof=0) > 0 else np.nan
        out = {"label": label, "cagr": round(cagr, 4), "vol": round(vol, 4),
               "sharpe": round(cagr / vol, 2) if vol > 1e-9 else None,
               "max_dd": round(float((eq / eq.cummax() - 1).min()), 4),
               "final_equity": round(last, 3),
               "psr": M.probabilistic_sharpe(sr_daily, len(r),
                                             skew=float(r.skew()),
                                             kurt=float(r.kurt() + 3.0))}
        if trials:
            out["dsr"] = M.deflated_sharpe(sr_daily, len(r), trials,
                                           skew=float(r.skew()),
                                           kurt=float(r.kurt() + 3.0))
            out["n_trials_assumed"] = trials
        return out

    eq_ls = (1 + ls_net).cumprod()
    eq_mkt = (1 + mkt).cumprod()
    yrs = max(len(dates) / 252.0, 1e-9)

    return {
        "horizon": horizon, "n_side": n_side, "cost_bps": cost_bps,
        "borrow_bps_pa": borrow_bps_pa,
        "start": str(dates[0].date()), "end": str(dates[-1].date()),
        "years": round(yrs, 1),
        "universe_median": int(mask.loc[start:].sum(axis=1).median()),
        "ic_long": _ic_stats(ic_l),
        "ic_short": _ic_stats(ic_s),
        "quintile_long": _quintile(L),
        "quintile_short": _quintile(S),
        "legs": {
            "long": _stats(gL, "做多篮子"),
            "short_basket": _stats(gS, "做空篮子(其自身涨跌)"),
            "market": _stats(mkt, "等权市场"),
            "long_short_net": _stats(ls_net, "市场中性多空(扣交易成本+借券费)", trials=n_trials),
            "long_short_gross": _stats(ls_gross, "市场中性多空(毛)"),
        },
        "cost_drag_pa": {
            "trading": round(float(trade_cost.mean() * 252), 4),
            "borrow": round(float(borrow.mean() * 252), 4),
        },
        "turnover_pa": round(float(turn.mean() * 252), 1),
        "survivorship_warning": (
            "股票池是**当前**的 ASX 名单回填历史，退市/被并购的公司不在其中。"
            "多头腿的历史收益因此系统性偏高，空头腿偏低。请把多空净值当作上界读。"),
        "curve": {
            "dates": [str(d.date()) for d in dates[::5]],
            "ls": [round(float(x), 4) for x in eq_ls.values[::5]],
            "ew": [round(float(x), 4) for x in eq_mkt.values[::5]],
            "lng": [round(float(x), 4) for x in (1 + gL).cumprod().values[::5]],
            "sht": [round(float(x), 4) for x in (1 + gS).cumprod().values[::5]],
        },
    }
