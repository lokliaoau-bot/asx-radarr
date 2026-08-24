# -*- coding: utf-8 -*-
"""Union-merge two versions of cache/announcements.db. Used as a git merge driver.

The archive is the one thing in this project that cannot be rebuilt after the fact
(ASX returns only the last few announcements per company, and each day's model
snapshot is computed from that day's data). Both sides of a conflict therefore hold
rows worth keeping: the GitHub Actions run writes snapshots from the cloud, a local
`run.py` writes them from here, and whoever pushes second would otherwise clobber
the other's rows by taking one side wholesale.

So: keep every row from both sides. EXCEPT drops rows that are byte-identical,
INSERT OR IGNORE absorbs the ones that collide on a unique key (filings is unique on
code+doc_key, and a re-parsed filing may differ only in fields filled in later).

Wire it up once per clone:

    git config merge.archivedb.name "union-merge the announcements archive"
    git config merge.archivedb.driver "python tools/merge_archive.py %O %A %B"

`.gitattributes` already points cache/announcements.db at this driver.
Called directly as `python tools/merge_archive.py BASE OURS THEIRS`; OURS is
rewritten in place, which is what git expects from a merge driver.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys


def _rows(path):
    con = sqlite3.connect(path)
    try:
        return sum(con.execute("SELECT count(*) FROM [%s]" % t).fetchone()[0]
                   for (t,) in con.execute(
                       "SELECT name FROM sqlite_master WHERE type='table'"))
    except sqlite3.Error:
        return -1
    finally:
        con.close()


def merge(ours, theirs):
    """Fold every row of `theirs` into `ours`. Returns rows added."""
    con = sqlite3.connect(ours)
    con.execute("ATTACH DATABASE ? AS other", (theirs,))
    added = 0
    tables = [t for (t,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        cols = ",".join("[%s]" % r[1] for r in con.execute("PRAGMA table_info([%s])" % t))
        if not cols:
            continue
        before = con.execute("SELECT count(*) FROM main.[%s]" % t).fetchone()[0]
        con.execute(
            "INSERT OR IGNORE INTO main.[{0}] ({1}) "
            "SELECT {1} FROM other.[{0}] EXCEPT SELECT {1} FROM main.[{0}]".format(t, cols))
        added += con.execute("SELECT count(*) FROM main.[%s]" % t).fetchone()[0] - before
    con.commit()
    con.close()
    return added


def main(argv):
    if len(argv) < 4:
        print("用法: merge_archive.py BASE OURS THEIRS", file=sys.stderr)
        return 2
    _base, ours, theirs = argv[1], argv[2], argv[3]
    n_ours, n_theirs = _rows(ours), _rows(theirs)
    if n_ours < 0 or n_theirs < 0:
        print("存档合并失败：有一侧不是可读的 SQLite 文件", file=sys.stderr)
        return 1
    # Start from whichever side has more rows so the merge does less work; the
    # result is the same union either way.
    if n_theirs > n_ours:
        shutil.copyfile(theirs, ours + ".tmp")
        merge(ours + ".tmp", ours)
        shutil.move(ours + ".tmp", ours)
    else:
        merge(ours, theirs)
    print("存档已并集合并：本地 %d 行 + 远端 %d 行 -> %d 行" % (n_ours, n_theirs, _rows(ours)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
