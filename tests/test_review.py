# -*- coding: utf-8 -*-
"""2026-10-14 审查轮的回归测试 —— 不联网、纯合成数据。

    python -m tests.test_review

每一条对应审查里查实并修掉的一个缺陷。
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from asxquant import datafeed as D               # noqa: E402
from asxquant import validate as V               # noqa: E402
from asxquant.config import all_stock_tickers    # noqa: E402
from tests.test_step1 import _synthetic_px       # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


def _suspend(px, t, start, end):
    """Yahoo 对停牌股的真实形态：收盘价照给（停在最后一价），成交量记 0。"""
    idx = px["close"].index
    seg = (idx >= idx[start]) & (idx <= idx[end])
    last = px["close"][t].iloc[start - 1]
    for k in ("close", "open", "high", "low", "adjclose"):
        px[k] = px[k].copy()
        px[k].loc[seg, t] = last
    px["volume"] = px["volume"].copy()
    px["volume"].loc[seg, t] = 0.0
    return px


def test_halt_detection():
    print("\n[R1] 停牌识别：成交量为 0 的日子不算成交（CTD 停牌近一年未被发现）")
    px = _synthetic_px(n=300, seed=5)
    tks = [t for t in all_stock_tickers() if t in px["close"].columns][:3]
    a, b, c = tks
    px = _suspend(px, a, 280, 299)                    # 最后 20 天停牌，价格还在
    px["volume"] = px["volume"].copy()
    px["volume"].loc[px["volume"].index[-1], b] = np.nan   # 只是成交量数据缺一天
    st = D.trading_status(px, tks)
    check("停牌股被判为停止成交", st[a]["stale_days"] == 20, str(st[a]))
    check("停牌股的「最后成交日」是停牌前一天",
          st[a]["last_trade"] == str(px["close"].index[279].date()))
    check("成交量缺失（NaN）不当成停牌", st[b]["stale_days"] == 0, str(st[b]))
    check("正常股票不受影响", st[c]["stale_days"] == 0)
    check("halted_tickers 只挑出停牌股", D.halted_tickers(px, tks) == {a})


def test_validation_halts():
    print("\n[R2] 回测：停牌日不能评分进场、持仓遇停牌按停牌处理（此前 halt_exits 恒为 0）")
    px = _synthetic_px(n=700, seed=6)
    tks = [t for t in all_stock_tickers() if t in px["close"].columns][:30]
    sp = pd.DataFrame(np.abs(np.random.default_rng(1).normal(3, 1.2, (len(px["close"]), len(tks)))),
                      index=px["close"].index, columns=tks)
    base = V.run_validation(px, sp, tks, {t: "materials" for t in tks}, horizon=20, n_side=10)
    he = base["halt_exits"]
    check("没有停牌时 halt_exits 为 0（对照组）", he["long"]["events"] + he["short"]["events"] == 0,
          str(he))
    for t in tks[:6]:                                  # 让六只股票各停牌 60 天，必然有持仓碰上
        k = tks.index(t)
        px = _suspend(px, t, 300 + 40 * k, 360 + 40 * k)
    out = V.run_validation(px, sp, tks, {t: "materials" for t in tks}, horizon=20, n_side=10)
    he = out["halt_exits"]
    check("持仓碰上停牌时 halt_exits 被记录", he["long"]["events"] + he["short"]["events"] > 0,
          str(he))


def main():
    print("=" * 68)
    print("澳股雷达 2026-10-14 审查轮回归测试")
    print("=" * 68)
    for fn in (test_halt_detection, test_validation_halts):
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
