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
    con.execute("CREATE INDEX IF NOT EXISTS ix_snap_sec ON sector_snapshots(sector, asof)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_snap_stk ON stock_snapshots(code, asof)")
    con.commit()
    return con


def record(report, log=print):
    """Store one point-in-time row per sector / highlighted stock / market. Idempotent.

    Keyed on the data's own as-of date, so re-running the same day overwrites rather
    than duplicating, and a run on a stale cache cannot invent a second observation.
    """
    asof = report.get("as_of")
    if not asof:
        return {"ok": False, "reason": "no as_of"}
    now = dt.datetime.now().isoformat(timespec="seconds")
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
             (s.get("perf") or {}).get("ret_1d"), now))
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
    con.execute("INSERT OR REPLACE INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (asof, (report.get("benchmark") or {}).get("last"),
                 (report.get("benchmark") or {}).get("rv20"),
                 d.get("p_up"), (fc.get("vol_up_20d") or {}).get("p_final"),
                 d.get("confidence"),
                 L.get("sector_name"), ",".join(p["code"] for p in (L.get("picks") or [])),
                 S.get("sector_name"), ",".join(p["code"] for p in (S.get("picks") or [])),
                 now))
    con.commit()

    days = con.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    first = con.execute("SELECT MIN(asof) FROM market_snapshots").fetchone()[0]
    con.close()
    log("每日存档: 已记录 %d 个交易日的快照 (自 %s)" % (days, first))
    return {"ok": True, "sectors": n_sec, "stocks": n_stk, "days": days, "since": first}


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
