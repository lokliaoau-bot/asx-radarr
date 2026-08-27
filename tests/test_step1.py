# -*- coding: utf-8 -*-
"""升级第一步（纯 bug 修复）的回归测试 —— 不联网、不依赖 yfinance，纯合成数据。

    python -m tests.test_step1

每一条断言都对应一个已修复的缺陷。它们的作用是：以后任何人（包括未来的你自己）
把某个修复改回去，这里会立刻红掉，而不是等到实盘里慢慢亏出来。

范围说明：本文件**只覆盖第一步**（时区对齐、RSI/MFI、广度闸门、Spearman、
覆盖率归一化、借券费、停牌处理、特征指纹）。建模层的改动（近因权重、专家聚合、
显著性收缩、新因子）属于第二步，其测试不在此处。
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from asxquant import crossmarket as XM          # noqa: E402
from asxquant import features as F              # noqa: E402
from asxquant import indicators as I            # noqa: E402
from asxquant import model as M                 # noqa: E402
from asxquant import picks as PK                # noqa: E402
from asxquant import validate as V              # noqa: E402
from asxquant.config import BENCHMARK, all_stock_tickers   # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


# ----------------------------------------------------------------------
def test_rsi_mfi():
    print("\n[1] RSI / MFI 退化情形")
    idx = pd.bdate_range("2024-01-01", periods=80)
    up = pd.Series(np.cumprod(1 + np.full(80, 0.01)) * 100, index=idx)
    dn = pd.Series(np.cumprod(1 - np.full(80, 0.01)) * 100, index=idx)
    flat = pd.Series(np.full(80, 100.0), index=idx)
    check("单调上涨 RSI = 100（旧版给 50）", abs(I.rsi(up).iloc[-1] - 100) < 1e-6,
          "实测 %.4f" % I.rsi(up).iloc[-1])
    check("单调下跌 RSI = 0", abs(I.rsi(dn).iloc[-1] - 0) < 1e-6)
    check("价格不动 RSI = 50", abs(I.rsi(flat).iloc[-1] - 50) < 1e-6)
    check("暖机期为 NaN 而非伪造的 50", not np.isfinite(I.rsi(up).iloc[3]))

    v = pd.Series(np.full(80, 1e6), index=idx)
    check("单调上涨 MFI = 100（旧版给 50）",
          abs(I.mfi(up * 1.01, up * .99, up, v).iloc[-1] - 100) < 1e-6)

    # 正常区间必须与旧版数值一致（这是纯修 bug，不该改变任何正常读数）
    rng = np.random.default_rng(3)
    s = pd.Series(np.cumprod(1 + rng.normal(0, .012, 600)) * 100,
                  index=pd.bdate_range("2022-01-01", periods=600))
    d = s.diff()
    u = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dd = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    old = (100 - 100 / (1 + u / dd.replace(0, np.nan))).fillna(50)
    new = I.rsi(s, 14)
    m = new.notna()
    check("正常区间与旧版数值一致", float((old[m] - new[m]).abs().max()) < 1e-9)


# ----------------------------------------------------------------------
def test_crossmarket():
    print("\n[2] 跨市场时段对齐（时区前视）")
    asx = pd.bdate_range("2024-01-01", periods=12)
    s = pd.Series(range(12), index=asx, dtype=float)
    home = XM.align(s, asx, "^AXJO")
    away = XM.align(s, asx, "^GSPC")
    check("本土标的不滞后", float(home.iloc[5]) == 5.0)
    check("外盘标的滞后一个交易日", float(away.iloc[5]) == 4.0)
    check("外盘首日无可用数据 -> NaN", not np.isfinite(away.iloc[0]))
    stale = XM.align(s.iloc[:3], asx, "^GSPC", max_stale_days=8)
    check("外盘停更超阈值 -> NaN（不再 ffill 出假直线）", not np.isfinite(stale.iloc[-1]))
    check("未登记标的默认按最保守的 GLOBAL 处理", XM.lag_of("SOMETHING=F") == 1)
    check("*.AX 自动识别为本土", XM.lag_of("XYZ.AX") == 0)


def test_leak_magnitude():
    print("\n[3] 时区前视的量级（合成实验）")
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    rng = np.random.default_rng(11)
    n = 3000
    idx = pd.bdate_range("2012-01-01", periods=n)
    u = pd.Series(rng.normal(0, .011, n), index=idx)          # 美股当日收益
    asx_ret = 0.55 * u.shift(1) + rng.normal(0, .008, n)      # 澳股由昨夜美股驱动
    y = (asx_ret.shift(-1) > 0).astype(float)

    def oos_auc(x):
        d = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
        p = np.full(len(d), np.nan)
        X, Y = d[["x"]].values, d["y"].values
        for s in range(750, len(d), 21):
            e = min(s + 21, len(d))
            p[s:e] = LogisticRegression().fit(X[:s - 6], Y[:s - 6]).predict_proba(X[s:e])[:, 1]
        ok = np.isfinite(p)
        return roc_auc_score(Y[ok], p[ok])

    leak = oos_auc(u.rolling(20).sum())
    fixed = oos_auc(u.shift(1).rolling(20).sum())
    check("同日对齐会虚高 AUC（旧版的写法）", leak - fixed > 0.03,
          "泄漏 %.4f vs 修正 %.4f，虚高 %.1f 个 AUC 点" % (leak, fixed, (leak - fixed) * 100))


# ----------------------------------------------------------------------
def test_ic():
    print("\n[4] 横截面 IC 的秩计算")
    try:
        from scipy.stats import spearmanr
    except ImportError:
        print("  (skip: 无 scipy)")
        return
    rng = np.random.default_rng(7)
    nt, nd = 150, 200
    cols = ["S%d" % i for i in range(nt)]
    idx = pd.bdate_range("2023-01-01", periods=nd)
    truth = pd.DataFrame(rng.normal(size=(nd, nt)), index=idx, columns=cols)
    fwd = 0.06 * truth + pd.DataFrame(rng.normal(size=(nd, nt)), index=idx, columns=cols)
    score = truth.where(pd.DataFrame(rng.random((nd, nt)) > .45, index=idx, columns=cols))
    fwd = fwd.where(pd.DataFrame(rng.random((nd, nt)) > .05, index=idx, columns=cols))

    ic = V._ic(score, fwd)
    ref = []
    for d in idx:
        a, b = score.loc[d], fwd.loc[d]
        m = a.notna() & b.notna()
        if m.sum() >= 20:
            ref.append(spearmanr(a[m], b[m]).statistic)
    ref = pd.Series(ref, index=ic.index)
    check("与 scipy.spearmanr 逐日一致", float((ic - ref).abs().max()) < 1e-9,
          "最大差异 %.2e" % float((ic - ref).abs().max()))


# ----------------------------------------------------------------------
def test_picks_coverage():
    print("\n[5] 缺失成分的权重归一化")
    rng = np.random.default_rng(4)
    stocks = []
    for i in range(30):
        has = i >= 8
        stocks.append({
            "ticker": "S%02d.AX" % i, "code": "S%02d" % i, "px": 10.0,
            "cmf20": rng.normal(), "obv_slope": rng.normal(), "dollar_vol_z": rng.normal(),
            "ret_20d": rng.normal(0, .05), "ret_60d": rng.normal(0, .1),
            "dist_ma50": rng.normal(0, .05), "dist_ma200": rng.normal(0, .1),
            "rsi14": float(rng.uniform(30, 85)), "extension": rng.normal(),
            "short_pct": float(rng.uniform(.5, 8)) if has else None,
            "short_chg_20d": float(rng.normal(0, .5)) if has else None,
            "short_pctile_1y": .5,
            "days_to_cover": float(rng.uniform(1, 15)) if has else None,
            "adv_aud": 2e7, "vol20": .25, "atr_pct": .02})
    sec = {"a": {"name": "A", "stocks": stocks[:15], "flow": {"score": 1.0},
                 "short": {"score": .5}, "heat": {"score": 0.},
                 "stage": {"key": "early_in", "label": "x"}},
           "b": {"name": "B", "stocks": stocks[15:], "flow": {"score": -0.5},
                 "short": {"score": 1.2}, "heat": {"score": 0.},
                 "stage": {"key": "outflow", "label": "y"}}}
    rows = PK.score_stocks(sec)
    full = [r for r in rows if r["short_pct"] is not None]
    part = [r for r in rows if r["short_pct"] is None]
    check("覆盖率被如实报告",
          np.mean([r["long_coverage"] for r in full]) >
          np.mean([r["long_coverage"] for r in part]))
    check("缺证据的名字不进做空池", all(not r["liquid_short"] for r in part))
    check("每只票都记录了缺了哪几项", all("long_missing" in r for r in rows))


def test_blend_parity():
    """picks 与 validate 必须用同一套缺失处理 —— 否则验证的不是生产评分。

    这条铁律本项目已经踩过一次（validate 里 `squeeze_safe` 曾是全零占位，
    等于 t 值验的是一个跟生产不一样的组合）。第一步把 picks 改成按可得权重
    归一化，validate 必须同步，这个测试就是那道闸门。
    """
    print("\n[6] picks 与 validate 的评分口径一致（本项目的第 2 条铁律）")
    rng = np.random.default_rng(5)
    names = ["S%02d" % i for i in range(12)]
    idx = pd.bdate_range("2024-01-01", periods=4)
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}

    comp = {}
    for k in weights:
        v = pd.DataFrame(rng.normal(size=(len(idx), len(names))), index=idx, columns=names)
        if k == "c":
            v.iloc[:, :4] = np.nan          # 四只股票缺 c 这一项
        comp[k] = v

    frames = {}
    for k, v in comp.items():
        z = V._xz_raw(v)
        frames[k] = (z, z.notna())
    got_validate = V._blend_frames(frames, weights)

    worst = 0.0
    for d in idx:
        pk_comps = {k: PK._xz(list(v.loc[d].values)) for k, v in comp.items()}
        score, cov = PK._blend(pk_comps, weights)
        worst = max(worst, float(np.abs(score - got_validate.loc[d].values).max()))
    check("两边逐日逐股逐位一致", worst < 1e-9, "最大差异 %.2e" % worst)

    # 归一化确实生效：缺一项的股票，其分母应当是 0.8 而不是 1.0
    pk_comps = {k: PK._xz(list(v.loc[idx[0]].values)) for k, v in comp.items()}
    _, cov = PK._blend(pk_comps, weights)
    check("缺失成分退出分母而非投 0 票",
          abs(cov[0] - 0.8) < 1e-9 and abs(cov[-1] - 1.0) < 1e-9,
          "缺 c 的覆盖率 %.2f，齐全的 %.2f" % (cov[0], cov[-1]))


def test_coverage_variance_bias():
    """覆盖率不同不得让分数方差不同 —— 否则缺成分的股票被系统性推向排序两端。

    只做 Σwz/Σw 时，Var(score)=Σw²/(Σw)² 随可得成分减少而上升。实测本项目权重：
    多头缺 ASIC 两项 sd +10.8%，蒙特卡洛下无披露股票在极值名单里超配 15.7%，
    而 MIN_COVERAGE=0.70 拦不住（那种情况覆盖率是 0.80）。
    """
    print("\n[7] 覆盖率不同不得造成方差偏差")
    from asxquant.picks import LONG_WEIGHTS
    keys = list(LONG_WEIGHTS)
    w = np.array([LONG_WEIGHTS[k] for k in keys])
    miss_idx = [j for j, k in enumerate(keys) if k in ("short_cover", "short_low")]

    rng = np.random.default_rng(0)
    N, nmiss, trials = 150, 45, 400
    hit = tot = 0
    sds_full, sds_miss = [], []
    for _ in range(trials):
        z = rng.normal(size=(N, len(keys)))
        ok = np.ones((N, len(keys)), dtype=bool)
        for j in miss_idx:
            ok[:nmiss, j] = False
        comps = {k: (z[:, j], ok[:, j]) for j, k in enumerate(keys)}
        score, _ = PK._blend(comps, LONG_WEIGHTS)
        sds_miss.append(float(np.std(score[:nmiss])))
        sds_full.append(float(np.std(score[nmiss:])))
        o = np.argsort(score)
        ext = set(o[:15].tolist()) | set(o[-15:].tolist())
        hit += sum(1 for i in ext if i < nmiss)
        tot += len(ext)
    over = (hit / tot) / (nmiss / N) * 100 - 100
    ratio = float(np.mean(sds_miss) / np.mean(sds_full))
    check("缺成分与齐全成分的分数标准差一致", abs(ratio - 1.0) < 0.05,
          "sd 之比 %.4f（修复前理论值 1.108）" % ratio)
    check("极值名单里不再超配缺成分的股票", abs(over) < 3.0,
          "超配 %+.1f%%（修复前实测 +15.7%%）" % over)
    _ = w  # 保留权重向量以便调试时打印


# ----------------------------------------------------------------------
def _synthetic_px(n=1600, seed=1):
    """造一份形状与 datafeed.fetch_prices 一致的行情，用于端到端冒烟测试。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-08-25", periods=n)
    names = all_stock_tickers()[:60] + [BENCHMARK, "^AXVI", "^GSPC", "^VIX",
                                        "^TNX", "GC=F", "HG=F", "CL=F",
                                        "AUDUSD=X", "000001.SS"]
    close = {}
    for t in names:
        drift = 0.0002 if not t.startswith("^") else 0.00015
        r = rng.normal(drift, 0.013, n)
        close[t] = np.cumprod(1 + r) * (100 if t.endswith(".AX") else 7000)
    c = pd.DataFrame(close, index=idx)
    o = c.shift(1).fillna(c.iloc[0]) * (1 + rng.normal(0, .004, c.shape))
    h = np.maximum(c, o) * (1 + np.abs(rng.normal(0, .004, c.shape)))
    lo = np.minimum(c, o) * (1 - np.abs(rng.normal(0, .004, c.shape)))
    v = pd.DataFrame(np.abs(rng.normal(3e6, 8e5, c.shape)), index=idx, columns=c.columns)
    return {"close": c, "open": pd.DataFrame(o, index=idx, columns=c.columns),
            "high": pd.DataFrame(h, index=idx, columns=c.columns),
            "low": pd.DataFrame(lo, index=idx, columns=c.columns),
            "volume": v, "adjclose": c}


def test_end_to_end():
    print("\n[8] 端到端冒烟测试")
    px = _synthetic_px()
    tks = [t for t in all_stock_tickers() if t in px["close"].columns]
    sp = pd.DataFrame(np.abs(np.random.default_rng(0).normal(3, 1.2, (len(px["close"]), len(tks)))),
                      index=px["close"].index, columns=tks)
    X = F.build_market_features(px, sp)
    check("因子矩阵构建成功", X.shape[1] >= 40, "%d 个因子 x %d 行" % (X.shape[1], X.shape[0]))
    check("不引入新因子（45 个：46 减掉重复的 rev_ret5）", X.shape[1] == 45,
          "实测 %d" % X.shape[1])
    c = np.array(X.corr().abs().values, copy=True)
    np.fill_diagonal(c, 0.0)
    worst = float(np.nanmax(c))
    ij = np.unravel_index(int(np.nanargmax(c)), c.shape)
    check("无重复因子（任意两列 |corr| < 0.999）", worst < 0.999,
          "最高的一对 %.4f: %s <-> %s" % (worst, X.columns[ij[0]], X.columns[ij[1]]))
    check("每个因子都有先验方向", set(X.columns) <= set(F.PRIOR_SIGN))
    check("每个因子都有中文标签", all(c in F.FEATURE_LABEL for c in X.columns))
    check("每个因子都归属某个模块", all(c in F.FEATURE_BLOCK for c in X.columns))
    check("没有 inf", not np.isinf(X.select_dtypes("number").to_numpy()).any())

    fp1 = F.feature_fingerprint(X)
    X2 = X.copy()
    X2.iloc[len(X2) // 2, 0] += 1e-4
    check("特征指纹能识别数值改动", fp1 != F.feature_fingerprint(X2))
    check("特征指纹对同一矩阵稳定", fp1 == F.feature_fingerprint(X))

    Xf = X.dropna(thresh=int(X.shape[1] * .7))
    bd = px["close"][BENCHMARK].reindex(Xf.index)
    y = (bd.shift(-5) / bd - 1 > 0).astype(float).where(bd.shift(-5).notna())
    Xa, ya = Xf.align(y, join="inner", axis=0)
    P = M.walk_forward(Xa, ya, horizon=5, min_train=750)
    check("走向前跑通", P["ensemble"].notna().sum() > 100,
          "%d 行样本外预测" % int(P["ensemble"].notna().sum()))

    P2 = M.walk_forward(Xa, ya, horizon=5, min_train=750,
                        cached=P[["logit", "gbm", "combo"]])
    same = np.allclose(P["ensemble"].dropna().values,
                       P2["ensemble"].reindex(P["ensemble"].dropna().index).values,
                       equal_nan=True)
    check("缓存复用逐位可复现", same)

    rows = M.signal_scoreboard(Xa, ya, F.PRIOR_SIGN, F.FEATURE_LABEL, F.FEATURE_BLOCK)
    check("信号明细表生成成功", len(rows) >= 40)
    check("模块归因生成成功", len(M.block_scores(rows)) >= 7)


def test_validation_guard():
    print("\n[9] 验证模块的边界保护与成本")
    px = _synthetic_px(n=400, seed=2)
    tks = [t for t in all_stock_tickers() if t in px["close"].columns][:10]
    sp = pd.DataFrame(np.nan, index=px["close"].index, columns=tks)   # 完全没有空头数据
    out = V.run_validation(px, sp, tks, {t: "materials" for t in tks},
                           horizon=20, n_side=3)
    check("横截面不足时显式报错而非静默乱跑", out.get("error") == "insufficient_cross_section",
          str(out.get("note", ""))[:40])
    check("借券费是一个真实存在的参数", V.DEFAULT_BORROW_BPS_PA > 0,
          "%.0f bps/年" % V.DEFAULT_BORROW_BPS_PA)
    check("多重检验修正函数可用",
          M.deflated_sharpe(0.05, 1000, 12) is not None
          and M.deflated_sharpe(0.05, 1000, 12) < M.probabilistic_sharpe(0.05, 1000),
          "DSR 必须低于未修正的 PSR")


def main():
    print("=" * 68)
    print("澳股雷达 升级第一步（纯 bug 修复）回归测试")
    print("=" * 68)
    for fn in (test_rsi_mfi, test_crossmarket, test_leak_magnitude, test_ic,
               test_picks_coverage, test_blend_parity, test_coverage_variance_bias,
               test_end_to_end, test_validation_guard):
        try:
            fn()
        except Exception as e:
            import traceback
            FAIL.append(fn.__name__)
            print("  [ERROR] %s: %s" % (fn.__name__, e))
            traceback.print_exc()
    print("\n" + "=" * 68)
    print("通过 %d 项，失败 %d 项" % (len(OK), len(FAIL)))
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
