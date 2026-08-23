# -*- coding: utf-8 -*-
"""ASX announcement collector: substantial holder (603/604/605) and director (3Y) filings.

READ THIS BEFORE TRUSTING ANYTHING THIS MODULE PRODUCES
-------------------------------------------------------
The ASX research API returns **only the 5 most recent announcements per company**.
Verified against `count=200`, `pageSize=200`, and `startDate` — all four variants
return 5. There is no pagination and no historical window.

Consequence: substantial-holder history **cannot be backfilled**. The sector-flow
spec makes the F1 lead-lag test (`滞后相关峰值必须在 lag < 0`) the go/no-go gate for
this whole data source, and that test is impossible to run without history. So this
module only *accumulates* — it polls daily and appends to a local archive. Until the
archive is long enough to run the gate, these filings are **displayed, never scored**.
`scored=False` is returned with every payload to make that explicit downstream.

Everything is stored with the announcement's own timestamp, so when there eventually
is enough history the lead-lag test can be run without lookahead.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import os
import re
import sqlite3
import time

import requests

from .config import all_stock_tickers, asx_code

_HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")}
_API = "https://asx.api.markitdigital.com/asx-research/1.0/companies/%s/announcements?count=20&market=ASX"
_FILE = "https://asx.api.markitdigital.com/asx-research/1.0/file/%s?access_token=83ff96335c2d45a094df02a206a39ff4"

# Passive / nominee holders carry no information: an index fund crossing 5% is
# mechanical. Spec 2 section 2.1.
PASSIVE = ["vanguard", "blackrock", "ishares", "state street", "dimensional",
           "northern trust", "hsbc custody", "j p morgan nominees", "jpmorgan nominees",
           "citicorp nominees", "bnp paribas", "nominees", "custody"]

# relevant interest that is NOT economic ownership. Spec 2 section 2.2 — securities
# lending triggers 604s, which is exactly what makes F1 and F2 non-independent.
NON_ECONOMIC = ["securities lending", "stock loan", "borrowed", "lending",
                "swap", "equity derivative", "voting agreement", "prime broker"]

FORM_PATTERNS = [
    ("603", r"becoming a substantial holder|form 603|initial substantial"),
    ("605", r"ceasing to be a substantial|form 605"),
    ("604", r"change (?:in|of) (?:the )?interests? of substantial|form 604|change in substantial"),
    ("3Y",  r"appendix 3y|change of director'?s? interest|director'?s? interest notice"),
]


def _db_path():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(here, "cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "announcements.db")


def _connect():
    con = sqlite3.connect(_db_path(), timeout=30)
    con.execute("""CREATE TABLE IF NOT EXISTS filings (
        code TEXT, doc_key TEXT, lodged_ts TEXT, form_type TEXT,
        headline TEXT, ann_type TEXT, price_sensitive INTEGER,
        holder_name TEXT, nature TEXT, pct_now REAL, pct_prev REAL,
        direction TEXT, parsed INTEGER DEFAULT 0, first_seen TEXT,
        PRIMARY KEY (code, doc_key))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_filings_ts ON filings(lodged_ts)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_filings_form ON filings(form_type)")
    con.commit()
    return con


def classify(headline):
    h = (headline or "").lower()
    for form, pat in FORM_PATTERNS:
        if re.search(pat, h):
            return form
    return None


def _fetch_one(code):
    try:
        r = requests.get(_API % code, headers=_HDR, timeout=20)
        if r.status_code != 200:
            return code, []
        return code, ((r.json().get("data") or {}).get("items") or [])
    except Exception:
        return code, []


def polled_today():
    """True if the archive already has a poll recorded for today (keeps refresh fast)."""
    if not os.path.exists(_db_path()):
        return False
    try:
        con = _connect()
        row = con.execute("SELECT MAX(first_seen) FROM filings").fetchone()
        con.close()
        if not row or not row[0]:
            return False
        return row[0][:10] == dt.date.today().isoformat()
    except Exception:
        return False


def poll(log=print, max_workers=8):
    """Poll every constituent for new filings; append to the archive. Idempotent."""
    tickers = all_stock_tickers()
    codes = [asx_code(t) for t in tickers]
    log("公告采集: 轮询 %d 家公司 (每家最近20条) ..." % len(codes))

    con = _connect()
    now = dt.datetime.now().isoformat(timespec="seconds")
    new = 0
    seen = 0
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for code, items in ex.map(_fetch_one, codes):
            for x in items:
                seen += 1
                form = classify(x.get("headline"))
                if not form:
                    continue
                cur = con.execute("SELECT 1 FROM filings WHERE code=? AND doc_key=?",
                                  (code, x.get("documentKey"))).fetchone()
                if cur:
                    continue
                con.execute(
                    "INSERT OR IGNORE INTO filings (code,doc_key,lodged_ts,form_type,headline,"
                    "ann_type,price_sensitive,first_seen) VALUES (?,?,?,?,?,?,?,?)",
                    (code, x.get("documentKey"), x.get("date"), form, x.get("headline"),
                     x.get("announcementType"), 1 if x.get("isPriceSensitive") else 0, now))
                new += 1
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
    con.close()
    log("公告采集: 扫描 %d 条, 新增 %d 条持股/董事公告, 存档累计 %d 条" % (seen, new, total))
    return {"scanned": seen, "new": new, "total": total}


# ----------------------------------------------------------------------
_PCT = re.compile(r"(\d{1,2}(?:\.\d{1,4})?)\s*%")


def _parse_pdf_text(txt):
    """Best-effort extraction. The spec warns 604 templates differ between law firms;
    anything not confidently parsed is left as None rather than guessed at."""
    low = " ".join((txt or "").split()).lower()
    holder = None
    m = re.search(r"(?:name of substantial holder|acn/arsn|company name/scheme)\s*[:\-]?\s*(.{3,70}?)(?:acn|arsn|\d{2,}|there was)", low)
    if m:
        holder = m.group(1).strip(" .:-")
    nature = None
    m2 = re.search(r"nature of (?:relevant interest|change)\s*[:\-]?\s*(.{3,180})", low)
    if m2:
        nature = m2.group(1).strip()
    pcts = [float(p) for p in _PCT.findall(low)[:6]]
    pct_now = pcts[0] if pcts else None
    pct_prev = pcts[1] if len(pcts) > 1 else None
    return holder, nature, pct_now, pct_prev


def parse_pending(limit=60, log=print):
    """Download and parse PDFs for filings not yet parsed. Requires pypdf; if it is
    not installed the archive still collects headlines, which is what the panel shows."""
    try:
        from pypdf import PdfReader
    except Exception:
        log("公告解析: 未安装 pypdf, 跳过 PDF 解析 (标题仍已入库)")
        return {"parsed": 0, "skipped": True}
    import io

    con = _connect()
    rows = con.execute("SELECT code,doc_key FROM filings WHERE parsed=0 LIMIT ?",
                       (limit,)).fetchall()
    done = 0
    for code, key in rows:
        try:
            r = requests.get(_FILE % key, headers=_HDR, timeout=25)
            if r.status_code != 200 or not r.content[:4] == b"%PDF":
                con.execute("UPDATE filings SET parsed=-1 WHERE code=? AND doc_key=?", (code, key))
                continue
            txt = ""
            reader = PdfReader(io.BytesIO(r.content))
            for pg in reader.pages[:4]:
                txt += pg.extract_text() or ""
            holder, nature, pn, pp = _parse_pdf_text(txt)
            con.execute("UPDATE filings SET holder_name=?,nature=?,pct_now=?,pct_prev=?,parsed=1 "
                        "WHERE code=? AND doc_key=?", (holder, nature, pn, pp, code, key))
            done += 1
            time.sleep(0.15)
        except Exception:
            con.execute("UPDATE filings SET parsed=-1 WHERE code=? AND doc_key=?", (code, key))
    con.commit()
    con.close()
    log("公告解析: 解析 %d 份 PDF" % done)
    return {"parsed": done, "skipped": False}


def is_passive(name):
    n = (name or "").lower()
    return any(k in n for k in PASSIVE)


def is_non_economic(nature):
    n = (nature or "").lower()
    return any(k in n for k in NON_ECONOMIC)


def summary(ticker_to_sector_map, sector_names, days=90):
    """What the archive currently holds, per sector. Display only — never scored.

    Returns `scored: False` and the archive age so the UI can be honest that this
    is still accumulating rather than quietly implying it feeds the ranking.
    """
    if not os.path.exists(_db_path()):
        return {"available": False, "scored": False, "days_collected": 0,
                "total": 0, "sectors": [], "recent": []}
    con = _connect()
    cutoff = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
    rows = con.execute(
        "SELECT code,form_type,lodged_ts,headline,holder_name,nature,pct_now "
        "FROM filings WHERE lodged_ts >= ? ORDER BY lodged_ts DESC", (cutoff,)).fetchall()
    first = con.execute("SELECT MIN(first_seen), COUNT(*) FROM filings").fetchone()
    con.close()

    days_collected = 0
    if first and first[0]:
        try:
            days_collected = (dt.datetime.now() - dt.datetime.fromisoformat(first[0])).days
        except Exception:
            days_collected = 0

    code2sec = {asx_code(t): s for t, s in ticker_to_sector_map.items()}
    agg = {}
    recent = []
    for code, form, ts, headline, holder, nature, pct in rows:
        if is_passive(holder) or is_non_economic(nature):
            continue
        sec = code2sec.get(code)
        if not sec:
            continue
        a = agg.setdefault(sec, {"sector": sec, "name": sector_names.get(sec, sec),
                                 "n_603": 0, "n_604": 0, "n_605": 0, "n_3y": 0,
                                 "codes": set()})
        key = {"603": "n_603", "604": "n_604", "605": "n_605", "3Y": "n_3y"}.get(form)
        if key:
            a[key] += 1
            a["codes"].add(code)
        if len(recent) < 25:
            recent.append({"code": code, "form": form, "ts": (ts or "")[:10],
                           "headline": headline, "holder": holder,
                           "sector_name": sector_names.get(sec, sec)})
    out = []
    for a in agg.values():
        a["n_stocks"] = len(a.pop("codes"))
        a["net_count"] = a["n_603"] + a["n_604"] - a["n_605"]
        out.append(a)
    out.sort(key=lambda x: -x["net_count"])
    return {"available": bool(rows), "scored": False,
            "days_collected": days_collected,
            "total": int(first[1]) if first else 0,
            "window_days": days, "sectors": out, "recent": recent}
