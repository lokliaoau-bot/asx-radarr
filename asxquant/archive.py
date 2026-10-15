
# -*- coding: utf-8 -*-
"""Point-in-time archive — the part of the system that gets better by running.

Everything else in this project recomputes from scratch each run: Yahoo hands back
the full price history, ASIC hands back whole years. Two things cannot be recovered
after the fact, and those are the two things stored here:

  1. **Announcements.** The ASX API returns only the last 5 filings per company, so a
     filing not captured within a few days of publication is gone forever.
  2. **What the model actually said on a given day.** Signals are recomputed from
     today's data, so yesterday's reading is not reproducible once inputs are revised.

Both accumulate into one SQLite file that is committed to the repo, not left in a
build cache. GitHub Actions caches evict after ~7 days of no access, which would
silently reset the archive and defeat the whole point of running daily.

Why the snapshots matter: the sector-flow spec makes "does F1 lead price" the go/no-go
gate for the substantial-holder data, and that test needs a point-in-time series that
does not exist yet. Every day this runs adds one row toward being able to run it.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3

from .announcements import _db_path


def _connect():
    con = sqlite3.connect(_db_path(), timeout=30)
    con.execute("""CREATE TABLE IF NOT EXISTS sector_snapshots (
        asof TEXT, sector TEXT, name TEXT,
        flow_score REAL, heat_score REAL, extension REAL, short_score REAL,
        short_pct REAL, short_chg_20d REAL, stage TEXT, quadrant TEXT,
        ret_20d REAL, net_flow_20d_m REAL, sector_index REAL,
        recorded_at TEXT,
        PRIMARY KEY (asof, sector))""")
    con.execute("""CREATE TABLE IF NOT EXISTS stock_snapshots (
        asof TEXT, code TEXT, sector TEXT,
        px REAL, in_score REAL, out_score REAL, long_score REAL, short_score REAL,
        s4_z REAL, cmf20 REAL, short_pct REAL, short_chg_20d REAL,
        ret_20d REAL, net_flow_20d_m REAL, side TEXT,
        recorded_at TEXT,
        PRIMARY KEY (asof, code))""")
    con.execute("""CREATE TABLE IF NOT EXISTS market_snapshots (
        asof TEXT PRIMARY KEY, benchmark REAL, rv20 REAL,
        p_up REAL, p_vol_up REAL, direction_conf REAL,
        long_sector TEXT, long_picks TEXT,
        short_sector TEXT, short_picks TEXT,
        recorded_at TEXT)""")
    # 2026-10-15 起：全部股票每天的多空评分（不只是名单上的三只）。名单只有三只、
    # 每天大多重复，攒一年也只有十几段独立样本；整个横截面才能做前瞻 IC 检验。
    con.execute("""CREATE TABLE IF NOT EXISTS score_snapshots (
        asof TEXT, code TEXT, long_score REAL, short_score REAL,
        liquid_long INTEGER, liquid_short INTEGER, recorded_at TEXT,
        PRIMARY KEY (asof, code))""")
    # 看空雷达页（ChatGPT 版 technical-v1）每天的排名，10 日口径，只存可排名的股票。
    con.execute("""CREATE TABLE IF NOT EXISTS shortradar_snapshots (
        asof TEXT, code TEXT, score REAL, rank INTEGER, eligible_n INTEGER,
        recorded_at TEXT, PRIMARY KEY (asof, code))""")
    # 自动校准（calibrate.py）：对手模型每天的评分，和每次状态改变的记录。
    con.execute("""CREATE TABLE IF NOT EXISTS challenger_snapshots (
        asof TEXT, model TEXT, code TEXT, score REAL, recorded_at TEXT,
        PRIMARY KEY (asof, model, code))""")
    con.execute("""CREATE TABLE IF NOT EXISTS model_decisions (
        decided_at TEXT PRIMARY KEY, actions TEXT, state_json TEXT, evidence_json TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_snap_sec ON sector_snapshots(sector, asof)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_snap_stk ON stock_snapshots(code, asof)")
    con.commit()
    return con


def _recorded_now():
    """记录时刻一律带时区写 UTC。旧行没有时区（云端是 UTC、本地是悉尼时间），
    forward.py 读旧行时按 UTC 解读——偏保守。新行不再有这个歧义。"""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def record_scores(asof, stock_rows, con=None):
    """全部股票当天的多空评分。同一 asof 重跑**不覆盖**——前瞻记录以第一次为准。"""
    own = con is None
    con = con or _connect()
    now = _recorded_now()
    n = 0
    for r in stock_rows or []:
        if r.get("short_score") is None and r.get("long_score") is None:
            continue
        con.execute("INSERT OR IGNORE INTO score_snapshots VALUES (?,?,?,?,?,?,?)",
                    (asof, r["ticker"].replace(".AX", ""), r.get("long_score"),
                     # 换将后 short_score 是对手的分；存档里永远记看空分自己的，才能继续给它对账
                     r.get("short_score_original", r.get("short_score")),
                     int(bool(r.get("liquid_long"))), int(bool(r.get("liquid_short"))), now))
        n += 1
    con.commit()
    if own:
        con.close()
    return n


def challenger_scores(stock_rows):
    """两个可换上场的对手，当天的分数（越高越看空）。公式 2026-10-15 写定。"""
    out = {"neg_mom20": {}, "short_build": {}}
    for r in stock_rows or []:
        t = r.get("ticker")
        if r.get("ret_20d") is not None:
            out["neg_mom20"][t] = -float(r["ret_20d"])
        if r.get("short_chg_20d") is not None:
            out["short_build"][t] = float(r["short_chg_20d"])
    return out


def record_challengers(asof, scores, con=None):
    own = con is None
    con = con or _connect()
    now = _recorded_now()
    n = 0
    for model, sc in scores.items():
        for t, v in sc.items():
            if v is None or not v == v:
                continue
            con.execute("INSERT OR IGNORE INTO challenger_snapshots VALUES (?,?,?,?,?)",
                        (asof, model, t.replace(".AX", ""), float(v), now))
            n += 1
    con.commit()
    if own:
        con.close()
    return n


def record_shortradar(radar10):
    """看空雷达 10 日口径的排名（只存可排名的）。asof 用它自己的期望交易日，不用运行日期。"""
    asof = (radar10.get("calendars", {}).get("AU") or {}).get("expected_session")
    rows = [r for r in radar10.get("rows", []) if r.get("technical_eligible")
            and r.get("known_contribution") is not None and r.get("session_date") == asof]
    if not asof or not rows:
        return {"ok": False, "reason": "no eligible rows for the expected session"}
    rows.sort(key=lambda r: -r["known_contribution"])
    con = _connect()
    now = _recorded_now()
    for i, r in enumerate(rows, 1):
        con.execute("INSERT OR IGNORE INTO shortradar_snapshots VALUES (?,?,?,?,?,?)",
                    (asof, r["symbol_id"].replace("AU.", ""), r["known_contribution"], i, len(rows), now))
    con.commit()
    con.close()
    return {"ok": True, "asof": asof, "rows": len(rows)}


def record(report, log=print, stock_rows=None):
    """Store one point-in-time row per sector / highlighted stock / market. Idempotent.

    Keyed on the data's own as-of date, so a re-run cannot invent a second observation.
    Sector/stock rows are overwritten on re-run; the market row (the daily pick lists that
    forward.py scores) and the score tables keep the FIRST record, so a late re-run cannot
    move an on-time prediction past the next open.
    """
    asof = report.get("as_of")
    if not asof:
        return {"ok": False, "reason": "no as_of"}
    now = _recorded_now()
    con = _connect()

    n_sec = 0
    for s in report.get("sectors") or []:
        con.execute(
            "INSERT OR REPLACE INTO sector_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (asof, s["key"], s["name"],
             (s.get("flow") or {}).get("score"), (s.get("heat") or {}).get("score"),
             (s.get("extension") or {}).get("score"), (s.get("short") or {}).get("score"),
             (s.get("raw") or {}).get("short_pct"), (s.get("raw") or {}).get("short_chg_20d"),
             (s.get("stage") or {}).get("label"), (s.get("rotation") or {}).get("quadrant_cn"),
             (s.get("perf") or {}).get("ret_20d"), s.get("signed_flow_20d_m"),
             # 这一列叫 sector_index，v1 却往里写了 ret_1d。存档是这个系统里唯一
             # 无法事后重建的东西，写错列等于把一段历史永久废掉。
             (s.get("index_level") if s.get("index_level") is not None
              else (s.get("perf") or {}).get("index_level")), now))
        n_sec += 1

    n_stk = 0
    mf = report.get("money_flow") or {}
    for side, blocks in (("in", mf.get("inflow") or []), ("out", mf.get("outflow") or [])):
        for blk in blocks:
            for k in blk.get("stocks") or []:
                con.execute(
                    "INSERT OR REPLACE INTO stock_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (asof, k["code"], blk["sector"], k.get("px"),
                     k.get("score") if side == "in" else None,
                     k.get("score") if side == "out" else None,
                     None, None,
                     k.get("s4_z"), k.get("cmf20"), k.get("short_pct"), k.get("short_chg_20d"),
                     k.get("ret_20d"), k.get("net_flow_20d_m"), side, now))
                n_stk += 1

    rec = report.get("recommendation") or {}
    L, S = rec.get("long") or {}, rec.get("short") or {}
    fc = {f["key"]: f for f in (report.get("forecasts") or [])}
    d = report.get("direction") or {}
    # 前瞻记录以**第一次**为准（INSERT OR IGNORE）。以前用 REPLACE：次日开盘后行情还没更新、
    # as_of 仍是前一天的重跑，会把前一天准时记下的名单原样覆盖一遍，记录时刻变成开盘之后，
    # 前瞻成绩单就把它误判成「迟到」。2026-08-28、09-25 两天就是这样被冤枉的（git 历史可证）。
    # 板块/个股的资金快照仍按 REPLACE（不参与前瞻打分）。
    con.execute("INSERT OR IGNORE INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (asof, (report.get("benchmark") or {}).get("last"),
                 (report.get("benchmark") or {}).get("rv20"),
                 d.get("p_up"), (fc.get("vol_up_20d") or {}).get("p_final"),
                 d.get("confidence"),
                 L.get("sector_name"), ",".join(p["code"] for p in (L.get("picks") or [])),
                 S.get("sector_name"), ",".join(p["code"] for p in (S.get("picks") or [])),
                 now))
    con.commit()
    n_scores = record_scores(asof, stock_rows, con) if stock_rows else 0
    if stock_rows:
        record_challengers(asof, challenger_scores(stock_rows), con)

    days = con.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    first = con.execute("SELECT MIN(asof) FROM market_snapshots").fetchone()[0]
    con.close()
    log("每日存档: 已记录 %d 个交易日的快照 (自 %s)" % (days, first))
    return {"ok": True, "sectors": n_sec, "stocks": n_stk, "scores": n_scores,
            "days": days, "since": first}


def stats():
    """Archive growth, shown in the UI so accumulation is visible rather than implied."""
    if not os.path.exists(_db_path()):
        return {"days": 0, "since": None, "sector_rows": 0, "stock_rows": 0, "filings": 0}
    try:
        con = _connect()
        q = lambda s: con.execute(s).fetchone()[0]          # noqa: E731
        out = {
            "days": q("SELECT COUNT(*) FROM market_snapshots"),
            "since": q("SELECT MIN(asof) FROM market_snapshots"),
            "latest": q("SELECT MAX(asof) FROM market_snapshots"),
            "sector_rows": q("SELECT COUNT(*) FROM sector_snapshots"),
            "stock_rows": q("SELECT COUNT(*) FROM stock_snapshots"),
            "filings": q("SELECT COUNT(*) FROM filings"),
        }
        con.close()
        # The spec's F1 lead-lag test needs roughly a quarter of weekly observations.
        out["ready_for_validation"] = out["days"] >= 60
        out["progress_pct"] = min(100, round(out["days"] / 60.0 * 100))
        return out
    except Exception:
        return {"days": 0, "since": None, "sector_rows": 0, "stock_rows": 0, "filings": 0}
