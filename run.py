# -*- coding: utf-8 -*-
"""澳股市场雷达 — 启动入口。

    python run.py              启动仪表板 (http://127.0.0.1:8849)
    python run.py --cli        只在终端打印报告
    python run.py --refresh    强制重下全部数据后再启动
    python run.py --export     只生成云盘用的单文件 HTML 后退出
    python run.py --publish    生成 site/ 静态站点(手机/云端用)后退出
    python run.py --port 9000  指定端口
"""
import argparse
import sys

import asxquant  # noqa: F401  -- must import first, it caps BLAS threads


def main():
    ap = argparse.ArgumentParser(description="澳股市场雷达 ASX Market Radar")
    ap.add_argument("--port", type=int, default=8849)
    ap.add_argument("--cli", action="store_true", help="只打印终端报告")
    ap.add_argument("--refresh", action="store_true", help="强制重下全部数据")
    ap.add_argument("--export", action="store_true", help="生成云盘单文件HTML后退出")
    ap.add_argument("--publish", action="store_true", help="生成 site/ 静态站点(云端/手机用)后退出")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if a.cli or a.refresh or a.export or a.publish:
        from asxquant import engine as E
        from asxquant import export as EX
        from asxquant.server import REPORT_PATH
        rep = E.run(force=a.refresh)
        E.save(rep, REPORT_PATH)
        path, size = EX.export(rep)
        print("\n云盘单文件已生成: %s  (%.1f MB)" % (path, size / 1e6))
        if a.publish:
            from asxquant import publish as PB
            d, sz = PB.build(rep)
            print("手机/云端站点已生成: %s  (%.1f MB)" % (d, sz / 1e6))
        if a.cli:
            _print(rep)
        if a.export or a.cli or a.publish:
            return

    from asxquant.server import serve
    serve(port=a.port, open_browser=not a.no_browser)


def _print(rep):
    b, d = rep["benchmark"], rep["direction"]
    print("\n" + "=" * 80)
    print("澳股市场雷达 | 行情截至 %s | ASIC空头截至 %s | ASX200 %.0f (%+.2f%% 当日, %+.2f%% 20日)" %
          (rep["as_of"], rep["short_as_of"], b["last"], b["chg_1d"] * 100, b["chg_20d"] * 100))
    print("=" * 80)

    rec = rep.get("recommendation") or {}
    v = rep.get("validation") or {}
    if rec:
        L, S = rec["long"], rec["short"]
        print("\n【结论 · 做多】%s  (%s)   证据强度: 弱 (做多分 IC t=%s)" %
              (L["sector_name"], L["stage"], (v.get("ic_long") or {}).get("t_stat")))
        for p in L["picks"]:
            print("   %-6s A$%-8.2f 做多分%+.2f  20日%+7.2f%%  空头%5.2f%%(Δ%+.2f)  止损≈A$%.2f" % (
                p["code"], p["px"] or 0, p["score"], (p["ret_20d"] or 0) * 100,
                p["short_pct"] or 0, p["short_chg_20d"] or 0,
                (p.get("stop_hint") or {}).get("stop_px") or 0))
            for x in p["reasons"][:3]:
                print("        · %s" % x)
        print("\n【结论 · 做空/低配】%s  (%s)   证据强度: 强 (做空分 IC t=%s)" %
              (S["sector_name"], S["stage"], (v.get("ic_short") or {}).get("t_stat")))
        for p in S["picks"]:
            print("   %-6s A$%-8.2f 做空分%+.2f  20日%+7.2f%%  空头%5.2f%%(Δ%+.2f)  回补%4.1f天  止损≈A$%.2f" % (
                p["code"], p["px"] or 0, p["score"], (p["ret_20d"] or 0) * 100,
                p["short_pct"] or 0, p["short_chg_20d"] or 0, p["days_to_cover"] or 0,
                (p.get("stop_hint") or {}).get("stop_px") or 0))
            for x in p["reasons"][:3]:
                print("        · %s" % x)

    print("\n【板块全景】")
    print("  %-12s %7s %7s %7s %7s %-22s %7s %8s" %
          ("板块", "资金", "热度", "延展", "做空", "资金阶段", "空头%", "20日"))
    for s in rep["sectors"]:
        print("  %-10s %+7.2f %+7.2f %+7.2f %+7.2f %-22s %6.2f%% %+7.2f%%" % (
            s["name"], s["flow"]["score"], s["heat"]["score"], s["extension"]["score"],
            s["short"]["score"], s["stage"]["label"], s["raw"]["short_pct"] or 0,
            (s["perf"]["ret_20d"] or 0) * 100))

    print("\n【大盘概率】")
    for r in rep["forecasts"]:
        m = r["metrics"] or {}
        print("  %-20s %6.1f%% (基准%5.1f%%, λ=%.2f)  AUC=%.3f BSS=%+.4f  %s" % (
            r["short"], r["p_final"] * 100, r["base_rate"] * 100, r["shrink_lambda"],
            m.get("auc") or 0, m.get("brier_skill_score") or 0, r["skill_cn"]))

    if v:
        legs = v.get("legs") or {}
        print("\n【选股评分验证】%s 至 %s (%.1f年)" % (v["start"], v["end"], v["years"]))
        for k in ("long", "short_basket", "market", "long_short_net"):
            s = legs.get(k)
            if s:
                print("  %-22s 年化%+7.2f%%  夏普%5.2f  最大回撤%6.1f%%" %
                      (s["label"], s["cagr"] * 100, s["sharpe"] or 0, s["max_dd"] * 100))
    print("\n提示: 做空分统计显著但衡量的是「跑输」而非「下跌」; 做多分未通过显著性检验。\n")


if __name__ == "__main__":
    main()
