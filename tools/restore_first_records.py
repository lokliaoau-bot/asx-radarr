# -*- coding: utf-8 -*-
"""从 git 历史里找回每日名单的**首次**记录时刻（一次性修复，可重复运行，幂等）。

    python tools/restore_first_records.py            # 只报告，不改
    python tools/restore_first_records.py --apply    # 修复 cache/announcements.db 并写审计记录

为什么需要：2026-10-15 之前 market_snapshots 用 INSERT OR REPLACE。次日开盘后行情还没更新的
重跑，会把前一天准时记下的名单**原样**再写一遍，记录时刻变成开盘之后，前瞻成绩单就把它误判
成「迟到」。存档每天都提交进 git，提交历史事后改不了，所以首次记录可以从历史里找回来。

规则（保守）：
  · 只恢复**记录时刻**，而且只在首次记录的看空、看多名单与现在**完全相同**时才恢复——
    名单相同说明后来那次只是重写，不是改了预测。
  · 名单不同的，一律不动，只在审计里列出（那种情况下现在的记录确实是开盘后的新名单）。
  · 每一处改动写进 tools/archive_repairs/ 的审计 JSON（提交哈希、提交时间、前后值）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "cache", "announcements.db")
REL = "cache/announcements.db"


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def first_records():
    """{asof: {recorded_at, short_picks, long_picks, commit, commit_time}}，取最早出现的那一版。"""
    commits = _git("log", "--reverse", "--format=%H %cI", "--", REL).decode().split("\n")
    first = {}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "v.db")
        for line in filter(None, commits):
            sha, when = line.split(" ", 1)
            try:
                blob = _git("show", "%s:%s" % (sha, REL))
            except subprocess.CalledProcessError:
                continue
            with open(path, "wb") as f:
                f.write(blob)
            try:
                con = sqlite3.connect(path)
                rows = con.execute("SELECT asof, recorded_at, short_picks, long_picks "
                                   "FROM market_snapshots").fetchall()
                con.close()
            except sqlite3.Error:
                continue
            for asof, rec, sp, lp in rows:
                if asof not in first:
                    first[asof] = {"recorded_at": rec, "short_picks": sp, "long_picks": lp,
                                   "commit": sha, "commit_time": when}
    return first


def _instant(naive_or_aware, commit_time):
    """把记录时刻变成带时区的时刻。没有时区的，按**那次提交自己的时区**解读：
    云端提交是 Z（UTC），本地提交带 +10:00/+11:00（悉尼）——记录和提交是同一次运行。"""
    try:
        t = dt.datetime.fromisoformat(str(naive_or_aware))
        c = dt.datetime.fromisoformat(str(commit_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=c.tzinfo)


def _both_readings(naive_or_aware):
    from zoneinfo import ZoneInfo
    try:
        t = dt.datetime.fromisoformat(str(naive_or_aware))
    except ValueError:
        return []
    if t.tzinfo:
        return [t]
    return [t.replace(tzinfo=dt.timezone.utc), t.replace(tzinfo=ZoneInfo("Australia/Sydney"))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    first = first_records()
    con = sqlite3.connect(DB)
    cur = {r[0]: r for r in con.execute(
        "SELECT asof, recorded_at, short_picks, long_picks FROM market_snapshots")}
    restored, differs, ambiguous = [], [], []
    for asof, f in sorted(first.items()):
        if asof not in cur:
            continue
        _, rec, sp, lp = cur[asof]
        if rec == f["recorded_at"]:
            continue
        first_t = _instant(f["recorded_at"], f["commit_time"])
        item = {"asof": asof, "current_recorded_at": rec, "first_recorded_at": f["recorded_at"],
                "first_recorded_utc": first_t.isoformat() if first_t else None,
                "first_seen_commit": f["commit"], "first_seen_commit_time": f["commit_time"],
                "short_picks": sp, "long_picks": lp}
        if sp != f["short_picks"] or lp != f["long_picks"]:
            item.update(first_short_picks=f["short_picks"], first_long_picks=f["long_picks"])
            differs.append(item)
            continue
        # 现在这条没有时区：云端写的是 UTC、本地写的是悉尼时间。两种读法下首次记录都更早，才恢复。
        cands = _both_readings(rec)
        if first_t is not None and cands and all(first_t < c for c in cands):
            item["restored_to"] = first_t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
            restored.append(item)
        else:
            ambiguous.append(item)
    if a.apply and restored:
        for it in restored:
            con.execute("UPDATE market_snapshots SET recorded_at=? WHERE asof=? AND recorded_at=?",
                        (it["restored_to"], it["asof"], it["current_recorded_at"]))
        con.commit()
    con.close()
    report = {"run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "applied": bool(a.apply), "restored_recorded_at": restored,
              "not_restored_picks_changed": differs,
              "not_restored_timezone_ambiguous": ambiguous,
              "rule": ("只在首次记录的名单与现在完全相同、且按 UTC 和悉尼两种读法首次记录都更早时，"
                       "才把记录时刻恢复为首次记录（写成带时区的 UTC）；其余不动")}
    if a.apply and (restored or differs):
        d = os.path.join(ROOT, "tools", "archive_repairs")
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, "first-records-%s.json" % dt.date.today().isoformat())
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print("审计记录:", out)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
