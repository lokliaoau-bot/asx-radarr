# -*- coding: utf-8 -*-
"""自我记录 · 自我复盘 · 改进提案。

用户要求（2026-10-15）：「深化结果回传，然后自我记录、自我思考、自我调整代码的逻辑和功能」。

分工（越往下越需要人确认）：
  1. 自我记录（run_log / component_snapshots）：每次运行记下用的哪版代码、哪天的数据、当时的
     把握和状态；看空分 9 个成分每天单独入档。以后任何一个预测都能追到是哪版代码、哪批数据做的。
  2. 自我复盘（reflection_log，每天）：今天有哪些预测满期、结果如何；大失误逐只找可能原因
     （空头在回补 / 单日暴涨 / 整个板块一起涨）；把握怎么变了；自动规则离触发还有多远；
     数据健不健康。全部是**描述**，每条都带样本量，不在小样本上讲故事。
  3. 自我调整：只在预先登记的规则内自动做——calibrate.py 的暂停/换将，加本文件的
     「数据过期」守卫（ASIC 空头数据比平常晚太多 → 看空名单当天挂「暂停参考」）。
  4. 改进提案（proposals）：复盘发现了规律、但没有规则授权自动处理的，写成一条提案：
     假设、证据、需要什么检验、最早什么时候能检验。**不自动改评分**——交给人或编码助手
     按提案做预先登记的检验后再改代码。

为什么不让程序看到失误就改公式：power_check.py 算过，看空分这么弱的信号，真实有效时
用 60 段也只有约 30% 的机会测得出来。几次失误里的「规律」绝大多数是运气。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess

import numpy as np
import pandas as pd

REGISTERED = "2026-10-15"
ASIC_NORMAL_LAG = 4            # ASIC T+4 个交易日公布
ASIC_STALE_LAG = 8             # 超过 8 个交易日没更新 → 数据过期
BIG_MISS = 0.05                # 看空名单里某只持有期比同池多涨 ≥5% = 大失误
COVER_PP = 0.5                 # 持有期内空头占比下降 ≥0.5 个百分点 = 空头在回补
JUMP = 0.10                    # 持有期内某天单日涨 ≥10% = 单日暴涨（多半有消息）
SECTOR_MOVE = 0.03             # 所在板块持有期比同池多涨 ≥3% = 整个板块一起涨
PATTERN_MIN = 5                # 至少 5 次大失误才谈「规律」
PATTERN_SHARE = 0.6            # 其中 ≥60% 有同一特征才写提案
MIN_SEGMENTS = 10
COMPONENTS = ("sector_short", "short_build", "short_level", "cmf_neg", "obv_neg",
              "mom_neg", "below_ma", "extension", "squeeze_safe")
COMPONENT_CN = {"sector_short": "板块做空压力", "short_build": "空头仓位增加", "short_level": "空头仓位水平",
                "cmf_neg": "资金流出", "obv_neg": "量能走弱", "mom_neg": "近期下跌", "below_ma": "跌破均线",
                "extension": "涨得过头", "squeeze_safe": "回补天数（防轧空）"}
TAG_CN = {"covering": "持有期间空头在回补", "jump": "持有期间有一天暴涨 10% 以上",
          "sector": "整个板块一起涨"}


# ----------------------------------------------------------------------------
# 自我记录
# ----------------------------------------------------------------------------
def code_identity(root=None):
    """这次运行用的是哪版代码。云端用 GITHUB_SHA；本地用 git，并标出有没有未提交的改动。"""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return {"commit": sha, "dirty": False, "where": "github-actions"}
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "asxquant", "web", "run.py"],
                                    cwd=root, capture_output=True, text=True).stdout.strip())
        return {"commit": sha, "dirty": dirty, "where": "local"}
    except Exception:
        return {"commit": None, "dirty": None, "where": "unknown"}


def record_run(report, con, now=None):
    from . import datafeed as D
    now = now or dt.datetime.now(dt.timezone.utc)
    cal = report.get("calibration") or {}
    ts = D.cache_timestamp("prices.pkl")
    row = {"code": code_identity(), "as_of": report.get("as_of"), "short_as_of": report.get("short_as_of"),
           "generated_at": report.get("generated_at"),
           "prices_fetched_at": dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat() if ts else None,
           "model_version": (report.get("coverage") or {}).get("model_version"),
           "trust": (cal.get("trust") or {}).get("p_right"), "state": cal.get("state")}
    con.execute("INSERT OR IGNORE INTO run_log VALUES (?,?,?)",
                (now.isoformat(timespec="seconds"), report.get("as_of"), json.dumps(row, ensure_ascii=False)))
    con.commit()
    return row


# ----------------------------------------------------------------------------
# 数据健康 + 数据过期守卫（预先登记的自动调整）
# ----------------------------------------------------------------------------
def asic_lag(px_index, short_as_of, as_of):
    """ASIC 空头数据比价格晚了几个交易日（正常是 4）。"""
    if not short_as_of or not as_of:
        return None
    s, a = pd.Timestamp(short_as_of), pd.Timestamp(as_of)
    return int(((px_index > s) & (px_index <= a)).sum())


def data_health(px, report, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    lag = asic_lag(px["close"].index, report.get("short_as_of"), report.get("as_of"))
    sydney_today = now.astimezone(dt.timezone(dt.timedelta(hours=10))).date()
    price_age = (sydney_today - pd.Timestamp(report["as_of"]).date()).days if report.get("as_of") else None
    halted = len(((report.get("data_health") or {}).get("halted")) or [])
    return {"asic_lag_trading_days": lag, "asic_normal_lag": ASIC_NORMAL_LAG,
            "asic_stale": bool(lag is not None and lag > ASIC_STALE_LAG),
            "price_age_calendar_days": price_age,
            "price_stale": bool(price_age is not None and price_age > 4),
            "halted_names": halted}


# ----------------------------------------------------------------------------
# 复盘：满期结果、大失误逐只找原因
# ----------------------------------------------------------------------------
def review_misses(px, spct, sector_of, records, h=20):
    """看空名单里已满期的每一只：比同池多涨/少涨多少；大失误打上可能原因的标签。"""
    from .forward import score_records
    A = px.get("adjclose", px["close"])
    idx = A.index
    uni = list(sector_of)
    rows = score_records(px, records, uni, h, "short")
    picks_out, matured_today = [], []
    last = str(idx[-1].date())
    for r, rec in zip(rows, sorted(records, key=lambda x: x["asof"])):
        if r["status"] != "matured":
            continue
        if r.get("exit") == last:
            matured_today.append({"asof": r["asof"], "excess": r["excess"]})
        e, x = r["_e"], r["_x"]
        pool = r["pool"]
        for p in rec["picks"]:
            if p not in A.columns or not (A[p].iloc[e] > 0):
                continue
            ret = float(A[p].iloc[x] / A[p].iloc[e] - 1)
            ex = ret - pool
            item = {"asof": r["asof"], "code": p.replace(".AX", ""), "excess": round(ex, 4), "tags": []}
            if ex >= BIG_MISS:
                if spct is not None and p in spct.columns:
                    s0, s1 = spct[p].iloc[e], spct[p].iloc[x]
                    if pd.notna(s0) and pd.notna(s1) and (s1 - s0) <= -COVER_PP:
                        item["tags"].append("covering")
                daily = A[p].iloc[e:x + 1].pct_change().dropna()
                if len(daily) and daily.max() >= JUMP:
                    item["tags"].append("jump")
                peers = [t for t, s in sector_of.items() if s == sector_of.get(p) and t != p and t in A.columns]
                if peers:
                    pr = (A[peers].iloc[x] / A[peers].iloc[e] - 1).mean()
                    if pd.notna(pr) and pr - pool >= SECTOR_MOVE:
                        item["tags"].append("sector")
            picks_out.append(item)
    big = [p for p in picks_out if p["excess"] >= BIG_MISS]
    # 独立事件：同一只股票连续多天都在名单上，持有期互相重叠，是**同一次**失误，不能算多次。
    # （2026-10-15 自查：第一次真实运行时，11 次「大失误」里 5 次是同一只 MP1 连着几天，
    #  系统差点据此写出「多数大失误是单日暴涨」的提案——那是同一件事被数了好几遍。）
    pos = {str(d.date()): i for i, d in enumerate(idx)}
    episodes = []
    for p in sorted(big, key=lambda q: (q["code"], q["asof"])):
        i = pos.get(p["asof"])
        last_ep = episodes[-1] if episodes else None
        if last_ep and last_ep["code"] == p["code"] and i is not None and i - last_ep["_last_i"] <= h:
            last_ep["days"] += 1
            last_ep["tags"] |= set(p["tags"])
            last_ep["excess"] = max(last_ep["excess"], p["excess"])
            last_ep["_last_i"] = i
        else:
            episodes.append({"code": p["code"], "first": p["asof"], "days": 1, "tags": set(p["tags"]),
                             "excess": p["excess"], "_last_i": i if i is not None else -10 ** 6})
    pattern = {}
    for tag in TAG_CN:
        k = sum(tag in e["tags"] for e in episodes)
        pattern[tag] = {"count": k, "share": round(k / len(episodes), 3) if episodes else None}
    eps = [{"code": e["code"], "first": e["first"], "days": e["days"], "max_excess": e["excess"],
            "tags": sorted(e["tags"])} for e in sorted(episodes, key=lambda e: e["first"])]
    return {"horizon": h, "picks_scored": len(picks_out), "big_misses": len(big),
            "miss_episodes": len(episodes),
            "hit_rate": round(float(np.mean([p["excess"] < 0 for p in picks_out])), 3) if picks_out else None,
            "recent_episodes": eps[-8:], "pattern": pattern, "matured_today": matured_today}


# ----------------------------------------------------------------------------
# 成分拆解：哪个成分在实盘里拖后腿（只描述 + 提案，不自动改权重）
# ----------------------------------------------------------------------------
def component_review(px, con):
    from . import calibrate as CAL
    rows = con.execute("SELECT asof, recorded_at, json FROM component_snapshots ORDER BY asof").fetchall()
    if not rows:
        return {"segments": 0, "components": {}}
    scores, recorded = {}, {}
    for asof, rec, js in rows:
        d = json.loads(js)
        for comp in COMPONENTS:
            vals = {c + ".AX": v.get(comp) for c, v in d.items() if v.get(comp) is not None}
            if vals:
                scores.setdefault(comp, {})[asof] = vals
                recorded[(comp, asof)] = rec
    frames = {c: pd.DataFrame.from_dict(v, orient="index") for c, v in scores.items()}
    ics = CAL.prospective_ic(px, frames, recorded)
    out, segs = {}, 0
    for comp, s in ics.items():
        sel = CAL.nonoverlap_dates(list(s.index), px)
        vals = [s[a] for a in sel]
        t, p, n = CAL._t(vals)
        segs = max(segs, n)
        # 成分值越大 = 越看空，所以「对」是 IC < 0
        out[comp] = {"name": COMPONENT_CN[comp], "segments": n,
                     "mean_ic": round(float(np.mean(vals)), 4) if vals else None,
                     "t": t and round(t, 2), "p": p and round(p, 3),
                     "p_adj": p and round(min(1.0, p * len(COMPONENTS)), 3)}
    return {"segments": segs, "components": out}


# ----------------------------------------------------------------------------
# 改进提案（预先登记的生成规则；只写提案，不自动改代码）
# ----------------------------------------------------------------------------
def make_proposals(misses, components, calibration, health):
    props = []
    big = misses.get("miss_episodes", 0)          # 按独立事件数，不按「只次」
    for tag, v in (misses.get("pattern") or {}).items():
        if big >= PATTERN_MIN and (v.get("share") or 0) >= PATTERN_SHARE:
            props.append({"key": "miss-pattern-" + tag,
                          "title": "看空名单的大失误多数「%s」" % TAG_CN[tag],
                          "hypothesis": "排除「%s」的股票后，看空名单会更准" % TAG_CN[tag],
                          "evidence": "%d 个独立的大失误事件里有 %d 个（%.0f%%）" % (big, v["count"], v["share"] * 100),
                          "test": ("只用 EVAL_END 之前的历史数据设计排除条件并回测；之后的前瞻记录只用来检验，"
                                   "满 10 段不重叠前瞻样本、配对比较 p<0.05 才上线"),
                          "earliest": "设计可立即开始；检验要等前瞻记录满 10 段"})
    for comp, c in (components.get("components") or {}).items():
        if (c.get("segments") or 0) >= MIN_SEGMENTS and c.get("t") is not None and c["t"] > 0 \
                and (c.get("p_adj") or 1) < 0.05:
            props.append({"key": "component-wrong-" + comp,
                          "title": "成分「%s」在实盘里方向反了" % c["name"],
                          "hypothesis": "去掉该成分后看空分更准",
                          "evidence": "实盘 %d 段，IC %+.3f，多重检验后 p=%.3f" % (c["segments"], c["mean_ic"], c["p_adj"]),
                          "test": "把「去掉该成分」作为新的对手加入自动校准，按同一套规则比较",
                          "earliest": "立即可加为对手"})
    ev = (calibration or {}).get("evidence") or {}
    for m, e in (ev.get("challengers") or {}).items():
        if (e.get("segments") or 0) >= 6 and e.get("diff_t") is not None and e["diff_t"] < -1.5:
            props.append({"key": "challenger-lead-" + m,
                          "title": "对手「%s」实盘暂时领先" % ((calibration.get("names") or {}).get(m, m)),
                          "hypothesis": "该对手比系统看空分更准",
                          "evidence": "实盘 %d 段，配对 t=%.2f（还没到自动换将的门槛）" % (e["segments"], e["diff_t"]),
                          "test": "什么都不用做——自动校准会继续比较，满 10 段且显著才换",
                          "earliest": "自动"})
    tr = (calibration or {}).get("trust") or {}
    if tr.get("p_right") is not None and tr["p_right"] < 0.5 and (tr.get("prospective_segments") or 0) >= 3:
        props.append({"key": "trust-below-half", "title": "把握跌破一半",
                      "hypothesis": "看空分在实盘里可能已经失效",
                      "evidence": "综合把握 %.0f%%（实盘 %d 段）" % (tr["p_right"] * 100, tr["prospective_segments"]),
                      "test": "复查数据管道和成分；满 10 段后自动校准会决定是否暂停",
                      "earliest": "立即复查"})
    if health.get("asic_stale"):
        props.append({"key": "asic-stale", "title": "ASIC 空头数据停更",
                      "hypothesis": "ASIC 网站或文件格式变了，抓取失败",
                      "evidence": "比价格晚了 %d 个交易日（正常 %d）" % (health["asic_lag_trading_days"], ASIC_NORMAL_LAG),
                      "test": "检查 datafeed.fetch_shorts 的日志和 ASIC 下载地址", "earliest": "立即"})
    return props


def upsert_proposals(con, props, now):
    """新提案入库；已有的刷新证据；今天不再成立的「open」提案自动标为 resolved（不删，留痕）。"""
    stamp = now.isoformat(timespec="seconds")
    today = {p["key"] for p in props}
    for (k,) in con.execute("SELECT key FROM proposals WHERE status='open'").fetchall():
        if k not in today:
            con.execute("UPDATE proposals SET status='resolved', updated_at=? WHERE key=?", (stamp, k))
    for p in props:
        old = con.execute("SELECT created_at FROM proposals WHERE key=?", (p["key"],)).fetchone()
        if old:
            con.execute("UPDATE proposals SET updated_at=?, json=?, status='open' WHERE key=?",
                        (stamp, json.dumps(p, ensure_ascii=False), p["key"]))
        else:
            con.execute("INSERT INTO proposals VALUES (?,?,?,?,?)",
                        (p["key"], stamp, stamp, "open", json.dumps(p, ensure_ascii=False)))
    con.commit()
    return [dict(json.loads(js), key=k, created_at=c, updated_at=u, status=s)
            for k, c, u, s, js in con.execute(
                "SELECT key, created_at, updated_at, status, json FROM proposals "
                "WHERE status='open' ORDER BY updated_at DESC").fetchall()]


# ----------------------------------------------------------------------------
# 每日复盘
# ----------------------------------------------------------------------------
def _notes(health, misses, misses5, components, calibration, prev):
    n = []
    mt = misses.get("matured_today") or []
    if mt:
        ex = np.mean([m["excess"] for m in mt])
        n.append("今天有 %d 份看空名单满 20 个交易日：平均比同池%s %.1f%%。"
                 % (len(mt), "少涨" if ex < 0 else "多涨", abs(ex) * 100))
    else:
        n.append("今天没有看空名单满 20 个交易日。")
    if misses.get("picks_scored"):
        n.append("到目前为止，看空名单里已满 20 天的 %d 只次（同一只连着几天上榜会重复计）中，"
                 "%.0f%% 之后确实比同池弱；大失误（多涨 5%% 以上）合并后是 %d 个独立事件。"
                 % (misses["picks_scored"], misses["hit_rate"] * 100, misses.get("miss_episodes", 0)))
    big = misses.get("miss_episodes", 0)
    if big:
        parts = ["%s %d 个" % (TAG_CN[t], v["count"]) for t, v in misses["pattern"].items() if v["count"]]
        n.append("这些大失误可能的原因：" + ("；".join(parts) if parts else "没找到共同特征") +
                 ("。独立事件还不到 %d 个，只记录，不当规律。" % PATTERN_MIN if big < PATTERN_MIN else "。"))
    tr = (calibration or {}).get("trust") or {}
    if tr.get("p_right") is not None:
        old = (prev or {}).get("trust_p_right")
        if old is None or abs(old - tr["p_right"]) < 0.005:
            n.append("把握：%.0f%%%s，和上次一样——还没有新的实盘段满期。"
                     % (tr["p_right"] * 100, "（%s）" % tr["label"] if tr.get("label") else ""))
        else:
            n.append("把握：从 %.0f%% 变成 %.0f%%%s。"
                     % (old * 100, tr["p_right"] * 100, "（%s）" % tr["label"] if tr.get("label") else ""))
        n.append("自动规则：实盘已对账 %d / %d 段，还%s触发暂停或换将的条件。"
                 % (tr.get("prospective_segments") or 0, MIN_SEGMENTS,
                    "不到" if (tr.get("prospective_segments") or 0) < MIN_SEGMENTS else "要看显著性才"))
    if components.get("segments", 0) < MIN_SEGMENTS:
        n.append("成分拆解：9 个成分的实盘记录已有 %d 段（需要 %d 段才分析谁在拖后腿）。"
                 % (components.get("segments", 0), MIN_SEGMENTS))
    if health.get("asic_stale"):
        n.append("⚠️ 空头数据晚了 %d 个交易日（正常 %d 天）：看空名单今天暂停参考。"
                 % (health["asic_lag_trading_days"], ASIC_NORMAL_LAG))
    elif health.get("asic_lag_trading_days") is not None:
        n.append("数据：空头数据晚了 %d 个交易日（正常 %d 天），正常。" % (health["asic_lag_trading_days"], ASIC_NORMAL_LAG))
    if health.get("price_stale"):
        n.append("⚠️ 价格数据已经 %d 天没更新。" % health["price_age_calendar_days"])
    return n


def reflect(px, spct, report, sector_of, con=None, now=None):
    from . import archive as AR
    from . import forward as FW
    now = now or dt.datetime.now(dt.timezone.utc)
    own = con is None
    con = con or AR._connect()
    try:
        record_run(report, con, now)
        health = data_health(px, report, now)
        snaps = con.execute("SELECT asof, short_picks, recorded_at FROM market_snapshots ORDER BY asof").fetchall()
        records = [{"asof": a, "picks": [c + ".AX" for c in (p or "").split(",") if c], "recorded_at": r}
                   for a, p, r in snaps if p]
        misses = review_misses(px, spct, sector_of, records, 20)
        misses5 = review_misses(px, spct, sector_of, records, 5)
        components = component_review(px, con)
        cal = report.get("calibration") or {}
        prev_row = con.execute("SELECT json FROM reflection_log WHERE asof < ? ORDER BY asof DESC LIMIT 1",
                               (report.get("as_of") or "",)).fetchone()
        prev = json.loads(prev_row[0]) if prev_row else None
        notes = _notes(health, misses, misses5, components, cal, prev)
        proposals = upsert_proposals(con, make_proposals(misses, components, cal, health), now)
        out = {"registered": REGISTERED, "as_of": report.get("as_of"), "notes": notes,
               "health": health, "misses_20d": misses, "misses_5d": {k: misses5[k] for k in
                                                                    ("picks_scored", "big_misses", "hit_rate")},
               "components": components, "proposals": proposals,
               "trust_p_right": (cal.get("trust") or {}).get("p_right"),
               "code": code_identity()}
        con.execute("INSERT OR REPLACE INTO reflection_log VALUES (?,?,?)",
                    (report.get("as_of"), now.isoformat(timespec="seconds"),
                     json.dumps(out, ensure_ascii=False, default=str)))
        con.commit()
        out["recent"] = [{"as_of": a, "notes": json.loads(j).get("notes", [])[:3]}
                         for a, j in con.execute("SELECT asof, json FROM reflection_log ORDER BY asof DESC "
                                                 "LIMIT 7").fetchall()]
        return out
    finally:
        if own:
            con.close()


def to_markdown(reflection, calibration=None, url=None):
    """给编码助手读的复盘摘要（站点上的 self-review.md）：系统自己发现了什么、提案怎么检验。"""
    x = reflection or {}
    lines = ["# 澳股雷达 · 系统自我复盘（%s）" % x.get("as_of", "—"), "",
             "> 自动生成。给接手的编码助手（Claude / ChatGPT）：先读这里，再读仓库里的 `交接文档.md`。",
             "> 改进提案只是**待检验的假设**：按提案里的检验方法做预先登记的检验，通过了再改代码；",
             "> 不许为了让结果好看去改 `calibrate.py` / `journal.py` 里写定的规则和阈值。", "",
             "## 今天的复盘"]
    lines += ["- " + n for n in x.get("notes", [])]
    cal = calibration or {}
    tr, st = cal.get("trust") or {}, cal.get("state") or {}
    lines += ["", "## 自动校准状态",
              "- 把握：%s（P(看空分真有效) = %s，实盘 %s 段）" % (tr.get("label"), tr.get("p_right"),
                                                       tr.get("prospective_segments")),
              "- 状态：%s" % json.dumps(st, ensure_ascii=False)]
    eps = (x.get("misses_20d") or {}).get("recent_episodes") or []
    if eps:
        lines += ["", "## 最近的大失误（独立事件）", "", "| 股票 | 上榜日 | 连续天数 | 比同池多涨 | 可能原因 |",
                  "|---|---|---|---|---|"]
        lines += ["| %s | %s | %s | %+.1f%% | %s |" % (e["code"], e["first"], e["days"], e["max_excess"] * 100,
                                                    "、".join(TAG_CN.get(t, t) for t in e["tags"]) or "—")
                  for e in eps]
    props = x.get("proposals") or []
    lines += ["", "## 未处理的改进提案（%d 条）" % len(props)]
    if not props:
        lines.append("- 无。生成规则：同一失误原因在 ≥%d 个独立事件里占 ≥%d%%；或某成分实盘显著反向；"
                     "或对手暂时领先；或把握跌破一半；或数据停更。" % (PATTERN_MIN, PATTERN_SHARE * 100))
    for p in props:
        lines += ["", "### %s" % p["title"], "- 假设：%s" % p["hypothesis"], "- 证据：%s" % p["evidence"],
                  "- 怎么检验：%s" % p["test"], "- 最早何时：%s" % p["earliest"],
                  "- 首次提出：%s；最近更新：%s" % (p.get("created_at"), p.get("updated_at"))]
    h = x.get("health") or {}
    code = x.get("code") or {}
    lines += ["", "## 数据与代码",
              "- ASIC 空头数据比价格晚 %s 个交易日（正常 %d；超过 %d 判过期）" % (h.get("asic_lag_trading_days"),
                                                                    ASIC_NORMAL_LAG, ASIC_STALE_LAG),
              "- 生成这份复盘的代码：%s（%s%s）" % (code.get("commit"), code.get("where"),
                                           "，含未提交改动" if code.get("dirty") else ""),
              "- 规则登记日：%s" % REGISTERED]
    return "\n".join(lines) + "\n"
