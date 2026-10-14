# -*- coding: utf-8 -*-
"""看空雷达（ChatGPT 版 AI Short Radar 并入）的回归测试 —— 不联网，纯合成数据。

    python -m tests.test_shortradar

覆盖并入时定下的几条规矩：真实行情必须走它原本的导入检查（不许绕过）、停牌零成交
和漏日必须挡住排名、评分必须与它冻结的公式逐位一致、前端枚举必须由后端生成、
页面顶部的实测结论文件必须存在且写着「无预测力」。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from asxquant import shortradar as SR            # noqa: E402
from short_radar import engine                    # noqa: E402
from short_radar.calendars import session         # noqa: E402
from short_radar.contracts import SECTORS         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, FAIL = [], []
NOW = dt.datetime(2026, 9, 30, 10, 0, tzinfo=dt.timezone.utc)      # 悉尼 20:00，当天已收盘
FETCHED = NOW - dt.timedelta(hours=1)


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


def asx_days(start=dt.date(2025, 1, 2), end=dt.date(2026, 9, 30)):
    d, out = start, []
    while d <= end:
        if session("AU", d):
            out.append(pd.Timestamp(d))
        d += dt.timedelta(days=1)
    return pd.DatetimeIndex(out)


def frames(tickers, idx, seed=1):
    rng = np.random.default_rng(seed)
    out = {k: pd.DataFrame(index=idx) for k in ("close", "open", "high", "low", "volume")}
    for i, t in enumerate(tickers):
        c = 20 * np.cumprod(1 + rng.normal(-0.0005 * i, 0.015, len(idx)))
        o = c * (1 + rng.normal(0, 0.003, len(idx)))
        out["close"][t], out["open"][t] = c, o
        out["high"][t] = np.maximum(c, o) * 1.01
        out["low"][t] = np.minimum(c, o) * 0.99
        out["volume"][t] = rng.integers(500_000, 2_000_000, len(idx)).astype(float)
    return out


def run(px, extra):
    return SR.build(px, extra, now=NOW, log=lambda m: None,
                    px_fetched_at=FETCHED, extra_fetched_at=FETCHED)


# ----------------------------------------------------------------------
def test_universe_and_contract():
    print("\n[1] 股票池、行业与前端契约")
    uni = SR.universe()
    check("每只股票的行业都在它的行业白名单里", all(s in SECTORS for _, s in uni.values()))
    check("对照基金 STW 在池里且标为基准", uni.get(SR.BENCH, (None, None))[1] == "基准")
    check("自选 GYG/360 在池里", "GYG.AX" in uni and "360.AX" in uni)
    check("关注行业：保险/餐饮/医药/REIT 都有股票",
          {"保险", "餐饮", "医药", "REIT"} <= {s for _, s in uni.values()})
    with open(os.path.join(ROOT, "web", "short", "plain.js"), encoding="utf-8") as f:
        check("web/short/plain.js 的枚举与后端契约一致（改了 contracts.py 要重新生成）",
              SR.contract_block() in f.read())


def test_end_to_end_scores():
    print("\n[2] 端到端：走它的导入检查，分数与冻结公式逐位一致")
    idx = asx_days()
    px = frames(["CSL.AX", "BHP.AX", "GMG.AX"], idx, seed=2)
    extra = frames([SR.BENCH], idx, seed=9)
    snap = run(px, extra)
    imp, r10 = snap["import"], snap["radar"]["10"]
    check("合成数据没有被拒的行", imp["rejected"] == 0, str(imp["issue_examples"][:3]))
    check("期望交易日 = 合成数据最后一天", r10["calendars"]["AU"]["expected_session"] == "2026-09-30")
    rows = {r["symbol_id"]: r for r in r10["rows"]}
    check("三只股票都可排名", all(rows[s]["technical_eligible"] for s in ("AU.CSL", "AU.BHP", "AU.GMG")),
          str({s: r["reasons"] for s, r in rows.items() if not r["technical_eligible"]}))
    check("基准不出现在名单里", "AU.STW" not in rows)
    worst = 0.0
    for h in SR.HORIZONS:
        w0, w1 = engine.WEIGHTS[h][:2]
        for r in snap["radar"][str(h)]["rows"]:
            f = r["factors"]
            expect = round(w0 * f["trend"] + w1 * f["distribution"], 2)
            worst = max(worst, abs(expect - r["known_contribution"]))
    check("三个持有期的分数 = 权重 × 因子（与 engine.WEIGHTS 一致）", worst < 1e-9, "最大偏差 %g" % worst)
    r = rows["AU.CSL"]
    check("没接入的三项保持空，不冒充 0",
          all(r["factors"][k] is None for k in ("fundamentals", "valuation", "catalyst")))
    check("完整研究分和执行状态仍是未知", r["research_score"] is None and r["execution_status"] == "unknown")
    check("来源与拆股版本照实声明",
          r["source"] == SR.SOURCE and r["adjustment_id"].startswith("yahoo-split-adj-")
          and r["verification"] == "single_source_or_user_declared")


def test_suspension_and_gaps():
    print("\n[3] 停牌零成交、漏日、休市日行情：必须挡下排名（CTD 停牌一年的教训）")
    idx = asx_days()
    px = frames(["CSL.AX", "CTD.AX", "TWE.AX"], idx, seed=4)
    px["volume"].loc[idx[-40:-20], "CTD.AX"] = 0.0          # 停牌：Yahoo 照给收盘价、成交量为 0
    px["close"].loc[idx[-60], "TWE.AX"] = np.nan             # 某天没有记录
    extra = frames([SR.BENCH], idx, seed=9)
    holiday = pd.Timestamp("2026-01-26")                    # 澳洲国庆日，ASX 休市
    for k in extra:
        extra[k].loc[holiday] = extra[k].iloc[-1]
        extra[k] = extra[k].sort_index()
    snap = run(px, extra)
    rows = {r["symbol_id"]: r for r in snap["radar"]["10"]["rows"]}
    check("停牌股不排名", not rows["AU.CTD"]["technical_eligible"] and rows["AU.CTD"]["known_contribution"] is None,
          rows["AU.CTD"]["state"])
    check("停牌原因写明是历史里有不能用的日子",
          "invalid_session_inside_history" in rows["AU.CTD"]["reasons"])
    check("漏一天也不排名，且列出漏掉的日期",
          not rows["AU.TWE"]["technical_eligible"]
          and str(idx[-60].date()) in (rows["AU.TWE"]["missing_history_sessions"] or []))
    check("正常股票不受牵连", rows["AU.CSL"]["technical_eligible"])
    check("休市日冒出来的行情被拒绝并计数",
          snap["import"]["rejected"] == 1 and any("trading day" in x for x in snap["import"]["issue_examples"]),
          str(snap["import"]["issue_examples"]))
    check("零成交记录被计入摘要", snap["import"]["states"].get("no_trades", 0) == 20)


def test_stale_and_missing_benchmark():
    print("\n[4] 行情没更新 / 缺市场对照")
    idx = asx_days()
    px = frames(["CSL.AX"], idx, seed=5)
    px["close"].loc[idx[-1], "CSL.AX"] = np.nan              # 今天还没拿到
    snap = run(px, frames([SR.BENCH], idx, seed=9))
    row = snap["radar"]["10"]["rows"][0]
    check("今天缺价 -> 判为行情没更新、不排名", row["state"] == "stale" and not row["technical_eligible"])
    snap = SR.build(frames(["CSL.AX"], idx, seed=5), None, now=NOW, log=lambda m: None,
                    px_fetched_at=FETCHED)
    row = snap["radar"]["10"]["rows"][0]
    check("没有 STW 对照 -> 不排名并写明原因",
          not row["technical_eligible"] and "benchmark_missing_or_invalid" in row["reasons"])
    early = SR.build(frames(["CSL.AX"], idx, seed=5), frames([SR.BENCH], idx, seed=9),
                     now=NOW, log=lambda m: None,
                     px_fetched_at=dt.datetime(2026, 9, 30, 4, 0, tzinfo=dt.timezone.utc),
                     extra_fetched_at=FETCHED)
    row = early["radar"]["10"]["rows"][0]
    check("收盘前抓的价格 -> 当天还没收完盘，不排名", row["state"] == "provisional",
          row["state"])


def test_evidence_file():
    print("\n[5] 页面顶部的实测结论")
    with open(os.path.join(ROOT, "web", "short", "evidence.json"), encoding="utf-8") as f:
        e = json.load(f)
    check("三个持有期都有结果", set(e["horizons"]) == {"5", "10", "20"})
    check("结论是「无预测力」，且与数字相符",
          e["verdict"] == "none" and all(abs(r["nw_t"]) < 2 for r in e["horizons"].values()))
    check("评估窗口截止日固定（保留期没被偷看）", e["window"][1] == "2026-02-27")
    check("逐点核对过生产公式", e["pointwise_crosscheck"] >= 300)


def main():
    print("=" * 68)
    print("看空雷达（AI Short Radar 并入）回归测试")
    print("=" * 68)
    for fn in (test_universe_and_contract, test_end_to_end_scores, test_suspension_and_gaps,
               test_stale_and_missing_benchmark, test_evidence_file):
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
