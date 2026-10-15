# -*- coding: utf-8 -*-
"""自动校准：拿实盘结果回来对账，按**事先写死的规则**调整系统自己。

用户要求（2026-10-15）：「拿实际数据从 App 里返回，比较准确度，从而调整代码」。

为什么不做「根据最近成绩自动改权重」：看空分 20 日 IC 约 -0.03，每段的离散度约 0.16
（tools/experiments/power_check.py）。这么弱的信号，每月大约只多一段独立证据；按最近成绩
调权重，调的几乎全是运气。所以这里只做三件事，规则全部在 2026-10-15 写定，之后不许看着结果改：

一、对账（每天）：系统自己的看空分（champion）和三个「对手」每天的评分都已当场写进存档
    （score_snapshots / challenger_snapshots / shortradar_snapshots，记录时刻早于次日开盘）。
    等 20 个交易日后，用真实价格算每个模型当天的横截面 IC（评分 vs 之后 20 日收益）。
    口径同 forward.py：信号日后第 1 个交易日收盘进场、再 20 个交易日收盘出场、复权价；
    只看信号日 20 日均成交额 > A$300 万、进场日有成交的股票。只用互不重叠的日子做检验。

二、把握刻度（每天自动更新，连续变化）：
    起点 = 回测 IC **打五折**（McLean & Pontiff 2016：因子发表后样本外平均衰减约一半），
    标准误用回测的不重叠标准误；实盘每多一段，就按精度加权把估计往实盘结果拉。
    输出 P(看空分真的有效) = P(IC < 0)，换成大白话标签显示在页面上。

三、开关动作（证据很强才动，有记录、可回退）：
    A 刹车：实盘 >= 10 段、看空分实盘 IC > 0（方向反了）且 p < 0.05 → 看空名单「暂停」。
    B 换将：实盘 >= 10 段、某个对手比看空分更准（逐段配对差值 t 检验，3 个对手 Bonferroni
       后 p < 0.05），且对手自己实盘 IC < 0、p < 0.05 → 看空名单改用这个对手的排序。
    C 回退：已暂停/已换将的，同一套门槛反过来成立才恢复（不会因为一两段来回跳）。
    每次状态改变写进存档 model_decisions（谁、哪天、凭什么），页面上能看到。
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
from scipy import stats

REGISTERED = "2026-10-15"
HORIZON = 20
MIN_SEGMENTS = 10
ALPHA = 0.05
MIN_ADV_AUD = 3e6
BACKTEST_HAIRCUT = 0.5
CHALLENGERS = ("neg_mom20", "short_build", "technical_v1")
# technical_v1 只参加评比、不能被换上场：它在主程序之后（看空雷达页生成时）才算出来，
# 主程序排名单时拿不到它当天的分数。2026-10-15 预先规定。
SWITCHABLE = ("neg_mom20", "short_build")
NAMES = {"champion": "系统看空分", "neg_mom20": "只看过去20天跌幅",
         "short_build": "只看空头仓位20天增加", "technical_v1": "ChatGPT 看空雷达分"}


# ----------------------------------------------------------------------------
# 读存档
# ----------------------------------------------------------------------------
def load_scores(con):
    """{model: DataFrame(index=asof, columns=ticker)} + {asof: 最早 recorded_at}。"""
    out, rec = {}, {}

    def put(model, rows):
        if not rows:
            return
        df = pd.DataFrame(rows, columns=["asof", "code", "score", "recorded_at"])
        for a, r in df.groupby("asof")["recorded_at"].min().items():
            rec.setdefault((model, a), r)
        out[model] = df.pivot_table(index="asof", columns="code", values="score", aggfunc="first")
        out[model].columns = [c + ".AX" for c in out[model].columns]

    put("champion", con.execute("SELECT asof, code, short_score, recorded_at FROM score_snapshots "
                                "WHERE short_score IS NOT NULL").fetchall())
    for m in ("neg_mom20", "short_build"):
        put(m, con.execute("SELECT asof, code, score, recorded_at FROM challenger_snapshots "
                           "WHERE model=? AND score IS NOT NULL", (m,)).fetchall())
    put("technical_v1", con.execute("SELECT asof, code, score, recorded_at FROM shortradar_snapshots").fetchall())
    return out, rec


# ----------------------------------------------------------------------------
# 对账：每个模型每天的前瞻 IC
# ----------------------------------------------------------------------------
def prospective_ic(px, scores, recorded, h=HORIZON):
    """{model: Series(asof -> IC)}；只算准时记录、已满期的日子。"""
    from .forward import _next_open_utc, _parse_recorded
    A = px.get("adjclose", px["close"])
    V = px.get("volume")
    idx = A.index
    pos = {d: i for i, d in enumerate(idx)}
    adv = (px["close"] * V).rolling(20, min_periods=5).mean() if V is not None else None
    out = {}
    for model, S in scores.items():
        ics = {}
        for asof, row in S.iterrows():
            i = pos.get(pd.Timestamp(asof))
            if i is None or i + 1 + h >= len(idx):
                continue
            rt = _parse_recorded(recorded.get((model, asof)))
            if rt is None or rt >= _next_open_utc(idx[i + 1]):
                continue                                     # 迟到的不算
            cols = [c for c in row.index if c in A.columns]
            e, x = A[cols].iloc[i + 1], A[cols].iloc[i + 1 + h]
            ok = e.notna() & (e > 0) & x.notna()
            if V is not None:
                ok &= ~(V[cols].iloc[i + 1] == 0)
                ok &= adv[cols].iloc[i] > MIN_ADV_AUD
            s = row[cols][ok].dropna()
            if len(s) < 20:
                continue
            f = (x / e - 1)[s.index]
            ics[asof] = float(s.rank().corr(f.rank()))
        out[model] = pd.Series(ics, dtype=float).sort_index()
    return out


def nonoverlap_dates(dates, px, h=HORIZON):
    """从最早一天起贪心取持有期互不重叠的日子（同 forward.summarize 的取法）。"""
    pos = {d: i for i, d in enumerate(px["close"].index)}
    sel, last_exit = [], -1
    for a in sorted(dates):
        i = pos.get(pd.Timestamp(a))
        if i is not None and i + 1 > last_exit:
            sel.append(a)
            last_exit = i + 1 + h
    return sel


def _t(x):
    x = np.asarray(x, float)
    n = len(x)
    if n < 3 or not x.std(ddof=1) > 0:
        return None, None, n
    t = float(x.mean() / (x.std(ddof=1) / np.sqrt(n)))
    return t, float(2 * stats.t.sf(abs(t), n - 1)), n


# ----------------------------------------------------------------------------
# 把握刻度 + 开关规则
# ----------------------------------------------------------------------------
def trust(backtest_ic, prosp):
    """回测打五折作起点，按精度加权并入实盘。返回 (估计 IC, 标准误, P(IC<0))。"""
    m0 = BACKTEST_HAIRCUT * backtest_ic["mean"]
    se0 = backtest_ic["se"]
    if prosp is not None and len(prosp) >= 3 and np.std(prosp, ddof=1) > 0:
        m1, se1 = float(np.mean(prosp)), float(np.std(prosp, ddof=1) / np.sqrt(len(prosp)))
        w0, w1 = 1 / se0 ** 2, 1 / se1 ** 2
        m, se = (w0 * m0 + w1 * m1) / (w0 + w1), (w0 + w1) ** -0.5
    else:
        m, se = m0, se0
    return m, se, float(stats.norm.cdf(-m / se))


def trust_label(p_right):
    if p_right >= 0.975:
        return "较有把握"
    if p_right >= 0.80:
        return "有一点把握"
    if p_right >= 0.50:
        return "没什么把握"
    return "方向可能是反的"


def decide(ics, px, previous=None):
    """按预先登记的规则给出当前状态。previous = 上一次的状态（来自 model_decisions）。"""
    champ = ics.get("champion", pd.Series(dtype=float))
    common = set(champ.index)
    sel = nonoverlap_dates(sorted(common), px)
    c_vals = [champ[a] for a in sel]
    tc, pc, n = _t(c_vals)
    state = dict(previous or {"active_model": "champion", "paused": False})
    evidence = {"segments": n, "champion": {"mean_ic": round(float(np.mean(c_vals)), 4) if c_vals else None,
                                            "t": tc and round(tc, 2), "p": pc and round(pc, 3)},
                "challengers": {}}
    actions = []
    # A 刹车 / C 解除刹车
    if n >= MIN_SEGMENTS and tc is not None:
        wrong = tc > 0 and pc < ALPHA
        right = tc < 0 and pc < ALPHA
        if wrong and not state.get("paused"):
            state["paused"] = True
            actions.append("pause")
        elif right and state.get("paused"):
            state["paused"] = False
            actions.append("resume")
    # B 换将 / C 换回
    best = None
    for m in CHALLENGERS:
        s = ics.get(m)
        if s is None or not len(s):
            continue
        both = nonoverlap_dates(sorted(set(s.index) & common), px)
        diff = [s[a] - champ[a] for a in both]               # 负 = 对手更「看空得准」
        td, pd_, nd = _t(diff)
        to, po, _ = _t([s[a] for a in both])
        p_adj = min(1.0, pd_ * len(CHALLENGERS)) if pd_ is not None else None
        evidence["challengers"][m] = {"segments": nd, "mean_ic": round(float(np.mean([s[a] for a in both])), 4) if both else None,
                                      "own_t": to and round(to, 2), "own_p": po and round(po, 3),
                                      "diff_t": td and round(td, 2), "diff_p_adj": p_adj and round(p_adj, 3)}
        if (m in SWITCHABLE and nd >= MIN_SEGMENTS and td is not None and td < 0 and p_adj < ALPHA
                and to is not None and to < 0 and po < ALPHA):
            if best is None or td < best[1]:
                best = (m, td)
    active = state.get("active_model", "champion")
    if best and active != best[0]:
        state["active_model"] = best[0]
        actions.append("switch:" + best[0])
    elif active != "champion":
        e = evidence["challengers"].get(active, {})
        # 换回：当初的对手不再显著优于看空分（配对差值 t 已变正且显著）
        if e.get("diff_t") is not None and e["diff_t"] > 0 and (e.get("diff_p_adj") or 1) < ALPHA:
            state["active_model"] = "champion"
            actions.append("switch_back")
    return state, evidence, actions


def run(px, backtest_short_ic, con=None, now=None):
    """每日调用：对账、更新把握、按规则决定状态，状态改变时写进 model_decisions。"""
    from . import archive as AR
    own = con is None
    con = con or AR._connect()
    try:
        scores, recorded = load_scores(con)
        ics = prospective_ic(px, scores, recorded)
        prev = con.execute("SELECT state_json FROM model_decisions ORDER BY decided_at DESC LIMIT 1").fetchone()
        previous = json.loads(prev[0]) if prev else None
        state, evidence, actions = decide(ics, px, previous)
        champ = ics.get("champion", pd.Series(dtype=float))
        sel = nonoverlap_dates(list(champ.index), px)
        bt = None
        b = backtest_short_ic or {}
        if b.get("mean_nonoverlap") is not None and b.get("se_nonoverlap"):
            # 起点用回测**判决那组不重叠样本**自己的均值和标准误——口径与实盘对账一致
            bt = {"mean": b["mean_nonoverlap"], "se": b["se_nonoverlap"]}
        elif b.get("mean_ic") is not None and b.get("t_stat"):
            bt = {"mean": b["mean_ic"], "se": abs(b["mean_ic"] / b["t_stat"])}   # 旧报告兜底
        m = se = p_right = None
        if bt:
            m, se, p_right = trust(bt, [champ[a] for a in sel])
        now = now or dt.datetime.now(dt.timezone.utc)
        if actions:
            con.execute("INSERT OR IGNORE INTO model_decisions VALUES (?,?,?,?)",
                        (now.isoformat(timespec="seconds"), ",".join(actions),
                         json.dumps(state, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False)))
            con.commit()
        history = [{"decided_at": a, "actions": b, "state": json.loads(c)}
                   for a, b, c in con.execute("SELECT decided_at, actions, state_json FROM model_decisions "
                                              "ORDER BY decided_at").fetchall()]
        first = min((s.index.min() for s in scores.values() if len(s)), default=None)
        return {"registered": REGISTERED, "horizon": HORIZON, "min_segments": MIN_SEGMENTS,
                "backtest_haircut": BACKTEST_HAIRCUT, "names": NAMES,
                "state": state, "actions_today": actions, "evidence": evidence,
                "trust": {"ic": m and round(m, 4), "se": se and round(se, 4),
                          "p_right": p_right and round(p_right, 3),
                          "label": trust_label(p_right) if p_right is not None else None,
                          "prospective_segments": len(sel)},
                "recording_since": first, "history": history}
    finally:
        if own:
            con.close()


def apply_to_rows(stock_rows, state, challenger_scores):
    """把状态作用到今天的名单上：换将时用对手的分数排序（z 分，与看空分同一尺度）。"""
    model = (state or {}).get("active_model", "champion")
    if model == "champion" or model not in challenger_scores:
        return stock_rows
    sc = challenger_scores[model]
    vals = np.array([sc.get(r["ticker"], np.nan) for r in stock_rows], float)
    ok = np.isfinite(vals)
    if ok.sum() < 10:
        return stock_rows
    z = (vals - np.nanmean(vals)) / (np.nanstd(vals) or 1.0)
    for r, v, k in zip(stock_rows, z, ok):
        r["short_score_original"] = r.get("short_score")
        r["short_score"] = round(float(v), 3) if k else None
        r["short_model"] = model
    return stock_rows
