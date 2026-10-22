# -*- coding: utf-8 -*-
"""AFR 要闻的两道检验（2026-10-21 用户选的方向 1 和 2）：

1. 数字核对（verify_brief）：简报并进 main 之前，在 GitHub 上打开每条的来源网页，逐个找「发生了什么」里的
   数字有没有出处；Claude 自己算的数字必须写在 calcs 里（算式），由程序重算。
   - 页面上标出找不到出处的数字；
   - 「错得离谱」直接退回（check_calcs / check_market，离线也能查，`news check` 就会报）：
     算式算出来和写的对不上、大盘收盘和涨跌和收件箱对不上。
2. 判断成绩单（scorecard）：分析版里每个带股票代码、方向不是「中性」的判断，按之后 1 天和 5 天的
   实际涨跌（扣掉 ASX 200）对账，算命中率和「比瞎猜好」的把握（单边二项检验）。

只用标准库，云端 Claude 的沙箱里也能跑离线那部分。
"""
from __future__ import annotations

import datetime as dt
import math
import re

# ---------------------------------------------------------------- 数字

_ZH_NUM = re.compile(
    r"(约|近|超过|逾|将近|大约|接近)?\s*(?<![A-Za-z\d./])(\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:\s*[–\-~至到]\s*(\d+(?:,\d{3})*(?:\.\d+)?))?\s*(%|个百分点|亿|万|千)?")
# 跟在数字后面就说明它是日期、时长或序号，不是要核对的事实
_ZH_SKIP_AFTER = ("年", "月", "日", "周", "天", "季度", "个月", "个交易日", "点钟", "时", "号", "岁", "次")
_ZH_MULT = {"亿": 1e8, "万": 1e4, "千": 1e3}

_EN_NUM = re.compile(
    r"(?<![\w.])(?:(?:A|US|NZ|HK|C)?\$\s?)?(\d+(?:,\d{3})*(?:\.\d+)?)\s?"
    r"(per cent|percent|pc\b|%|billion|bn\b|million|mn\b|m\b|b\b|trillion|tn\b|thousand|k\b|kt\b|koz\b|moz\b|mt\b)?",
    re.I)
_EN_MULT = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6, "trillion": 1e12, "tn": 1e12,
            "thousand": 1e3, "k": 1e3, "kt": 1e3, "koz": 1e3, "moz": 1e6, "mt": 1e6}
_EN_PCT = {"per cent", "percent", "pc", "%"}


def _f(s):
    return float(s.replace(",", ""))


def _decimals(s):
    return len(s.split(".")[1]) if "." in s else 0


def zh_numbers(text):
    """中文文字里要核对的数字 → [{raw, kind: pct|num, value, tol}]。日期、年份、时长、序号不算。"""
    out = []
    for m in _ZH_NUM.finditer(text or ""):
        approx, a, b, unit = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        after = text[m.end():m.end() + 4].lstrip()
        if not unit and after.startswith(_ZH_SKIP_AFTER):
            continue
        if not unit and not b and len(a) == 4 and a[:2] in ("19", "20") and "." not in a:
            continue                                        # 年份（2026、2008）
        if not unit and not b and _f(a) < 10 and "." not in a and not after.startswith(("澳元", "美元", "倍")):
            continue                                        # 「3 个」「两三」这类小整数核对意义不大，容易撞
        for s in (a, b) if b else (a,):
            v = _f(s)
            if unit in ("%", "个百分点"):
                tol = 0.5 if approx else max(0.051, 0.5 * 10 ** -_decimals(s) + 0.001)
                out.append({"raw": m.group(0).strip(), "kind": "pct", "value": v, "tol": tol})
            else:
                mult = _ZH_MULT.get(unit, 1.0)
                v = round(v * mult, 6)
                tol = max(abs(v) * (0.05 if approx else 0.006), 0.5 * 10 ** -_decimals(s) * mult)
                out.append({"raw": m.group(0).strip(), "kind": "num", "value": v, "tol": tol})
    return out


_EN_RANGE = re.compile(
    r"(?<![\w.])(?:(?:A|US|NZ|HK|C)?\$\s?)?(\d+(?:,\d{3})*(?:\.\d+)?)\s?(?:–|-|to)\s?(?:(?:A|US|NZ|HK|C)?\$\s?)?"
    r"(\d+(?:,\d{3})*(?:\.\d+)?)\s?(billion|bn|million|mn|m|b)\b", re.I)


def en_numbers(text):
    """英文网页里的数字 → [(kind, value)]。带 billion/million 的同时给原数（防单位写法不同）；
    「$245–265 million」这种区间，单位对两头都算。"""
    out = []
    for m in _EN_RANGE.finditer(text or ""):
        mult = _EN_MULT[m.group(3).lower()]
        out += [("num", _f(m.group(1)) * mult), ("num", _f(m.group(2)) * mult)]
    for m in _EN_NUM.finditer(text or ""):
        v, unit = _f(m.group(1)), (m.group(2) or "").lower().strip()
        if unit in _EN_PCT:
            out.append(("pct", v))
            continue
        out.append(("num", v))
        if unit in _EN_MULT:
            out.append(("num", v * _EN_MULT[unit]))
    return out


def _match(n, cands):
    return any(k == n["kind"] and abs(v - n["value"]) <= n["tol"] for k, v in cands)


# ---------------------------------------------------------------- 算式（calcs）

_EXPR = re.compile(r"^[\d\s.+\-*/()]+$")


def eval_expr(expr):
    """只认数字和 + - * / ( )；别的字符一律不算（不执行任意代码）。"""
    if not isinstance(expr, str) or not _EXPR.match(expr) or len(expr) > 200:
        raise ValueError("算式只能有数字和 + - * / ( )")
    if "**" in expr or re.search(r"\d{16,}", expr):
        raise ValueError("算式里不许用乘方，数字也不能太长")      # 9**9**9 这种会把校验卡死
    return float(eval(compile(expr, "<calc>", "eval"), {"__builtins__": {}}, {}))


def check_calcs(item):
    """calcs: [{"value": "36%", "expr": "(1-255/397.5)*100"}]。算出来和写的对不上就是错（退回）。"""
    P = []
    calcs = item.get("calcs")
    if calcs is None:
        return P
    if not isinstance(calcs, list) or len(calcs) > 10:
        return ["calcs 要是列表（最多 10 个）"]
    for c in calcs:
        if not (isinstance(c, dict) and isinstance(c.get("value"), str) and isinstance(c.get("expr"), str)):
            P.append("calcs 每项要有 value（写在正文里的样子）和 expr（算式）")
            continue
        m = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", c["value"])
        if not m:
            P.append("calcs 的 value 里没有数字：%s" % c["value"])
            continue
        try:
            got = eval_expr(c["expr"])
        except Exception as e:
            P.append("calcs 算式算不了（%s）：%s" % (e, c["expr"]))
            continue
        # 算式按正文写的单位算（写「36%」就算到 36，写「2.55 亿」就算到 2.55）；容差 = 最后一位的一半
        want = _f(m.group(0))
        tol = max(0.5 * 10 ** -_decimals(m.group(0)), abs(want) * 0.006)
        if re.search(r"约|近|大约|接近", c["value"]):
            tol = max(tol, abs(want) * 0.05)
        if abs(abs(got) - abs(want)) > tol + 1e-9:
            P.append("算式算出来是 %.4g，正文写的是 %s：%s" % (got, c["value"], c["expr"]))
    return P


def check_market(b, inbox):
    """大盘收盘点数和涨跌必须和收件箱一致（收件箱是 GitHub 按收盘价抓的）。"""
    m, ix = b.get("market") or {}, (inbox or {}).get("index") or {}
    if not ix or b.get("date") != (inbox or {}).get("date"):
        return []
    P = []
    if isinstance(m.get("index_close"), (int, float)) and ix.get("close") is not None \
            and abs(m["index_close"] - ix["close"]) > 0.6:
        P.append("大盘收盘写的是 %s，行情是 %.1f" % (m["index_close"], ix["close"]))
    if isinstance(m.get("index_pct"), (int, float)) and ix.get("pct") is not None \
            and abs(m["index_pct"] - ix["pct"]) > 0.02:
        P.append("大盘涨跌写的是 %s%%，行情是 %+.2f%%" % (m["index_pct"], ix["pct"]))
    return P


# ---------------------------------------------------------------- 来源网页

def page_text(html_text):
    """网页 → 正文文字（有 <article> 就只要 article，少撞导航栏里的无关数字）。"""
    import html as H
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html_text or "")
    a, b = t.find("<article"), t.rfind("</article>")
    if 0 <= a < b:
        t = t[a:b]
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", t)))


def verify_brief(b, get, inbox=None, lead=None, log=lambda m: None):
    """给每条加 verify：{checked, found, derived, market, missing, sources_ok, sources_failed}。
    找出处的地方：来源网页（AFR 用公开开头）、AFR 原标题、收件箱的行情（这一条涉及的股票和大盘）、calcs。
    网页打不开的来源记在 sources_failed，不算错。返回 (简报, 退回原因列表)。"""
    cache = {}

    def fetch(url):
        if url not in cache:
            try:
                if "afr.com/" in url and lead:
                    cache[url] = " ".join(lead(url)["paragraphs"])
                else:
                    cache[url] = page_text(get(url))
            except Exception as e:
                log("来源打不开 %s: %s" % (url, e))
                cache[url] = None
        return cache[url]

    moves = (inbox or {}).get("moves") or {}
    ix = (inbox or {}).get("index") or {}
    head_codes = {h["url"]: h.get("codes") or [] for h in (inbox or {}).get("headlines") or []}
    hard = []
    for it in b.get("items") or []:
        hard += ["「%s」：%s" % (it.get("title_zh"), p) for p in check_calcs(it)]
        nums = zh_numbers(it.get("what_zh") or "")
        derived = [n for c in it.get("calcs") or [] for n in zh_numbers(c.get("value", ""))]
        codes = {t.get("code") for t in it.get("impact") or [] if t.get("code")} | set(head_codes.get(it.get("afr_url"), []))
        mkt = []
        for c in codes:
            mv = moves.get(c)
            if mv:
                mkt += [("pct", abs(mv["pct"])), ("num", mv["close"])]
        if ix:
            mkt += [("pct", abs(ix.get("pct") or 0)), ("num", ix.get("close") or 0)]
        src_c, ok, failed = en_numbers(it.get("afr_headline") or ""), [], []
        for s in it.get("sources") or []:
            txt = fetch(s.get("url", ""))
            if txt is None:
                failed.append(s.get("name"))
            else:
                ok.append(s.get("name"))
                src_c += en_numbers(txt) + [(k, n["value"]) for n in zh_numbers(txt) for k in (n["kind"],)]
        found, der, mk, missing = [], [], [], []
        for n in nums:
            if _match(n, [(d["kind"], d["value"]) for d in derived]):
                der.append(n["raw"])
            elif _match(n, src_c):
                found.append(n["raw"])
            elif _match(n, mkt):
                mk.append(n["raw"])
            else:
                missing.append(n["raw"])
        it["verify"] = {"checked": len(nums), "found": found, "derived": der, "market": mk,
                        "missing": list(dict.fromkeys(missing)), "sources_ok": ok, "sources_failed": failed}
    hard += check_market(b, inbox)
    b["verify"] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "checked": sum(it["verify"]["checked"] for it in b.get("items") or []),
                   "missing": sum(len(it["verify"]["missing"]) for it in b.get("items") or [])}
    return b, hard


# ---------------------------------------------------------------- 判断成绩单

SIGN = {"空": -1, "偏空": -1, "偏多": 1, "多": 1}
HORIZONS = (1, 5)
MIN_N = 20                       # 少于这么多次不下结论


def _sydney(iso):
    from zoneinfo import ZoneInfo
    try:
        t = dt.datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=ZoneInfo("Australia/Sydney"))
        return t.astimezone(ZoneInfo("Australia/Sydney"))
    except Exception:
        return None


def base_day(generated_at, brief_date, days):
    """对账起点：写判断时已知的最新收盘。盘中（10:00–16:10）写的从当天收盘算起——不让它占
    「已经看到早盘涨跌」的便宜；收盘后、周末、开盘前写的从之前最近一个收盘算起。
    days 是交易日列表（字符串，升序）。"""
    t = _sydney(generated_at or "")
    if t is None:
        return brief_date if brief_date in days else None
    d = t.date().isoformat()
    if d in days and t.hour >= 10:          # 盘中写的用当天收盘（之后才出来）；收盘后写的用当天收盘（已知）
        return d
    prev = [x for x in days if x < d]       # 开盘前、周末、假日写的：之前最近一个收盘
    return prev[-1] if prev else None


def binom_p(hits, n):
    """单边：瞎猜（命中率 50%）至少猜中这么多次的概率。"""
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(hits, n + 1)) / 2 ** n


def scorecard(briefs, prices, now=None):
    """prices: {code: [(date, close, volume)]}，必须含 XJO。返回给页面和 Claude 看的成绩单。"""
    now = now or dt.datetime.now(dt.timezone.utc)
    idx = {d: c for d, c, _ in prices.get("XJO") or []}
    days = sorted(idx)
    calls, pending, neutral = [], 0, 0
    for b in briefs:
        if (b.get("stage") or "analysis") != "analysis":
            continue
        base = base_day(b.get("generated_at"), b.get("date"), days)
        for it in b.get("items") or []:
            for t in it.get("impact") or []:
                if not t.get("code"):
                    continue
                if t.get("direction") == "中性":
                    neutral += 1
                    continue
                sign = SIGN.get(t.get("direction"))
                px = {d: c for d, c, _ in prices.get(t["code"]) or []}
                row = {"date": b["date"], "code": t["code"], "name": t.get("name"), "direction": t["direction"],
                       "title_zh": it.get("title_zh")}
                if sign is None or base is None or base not in px:
                    continue
                i0, done = days.index(base), False
                for h in HORIZONS:
                    if i0 + h < len(days) and days[i0 + h] in px:
                        d1 = days[i0 + h]
                        ex = (px[d1] / px[base] - idx[d1] / idx[base]) * 100
                        row["ex%d" % h] = round(ex, 2)
                        row["hit%d" % h] = ex * sign > 0
                        done = True
                if done:
                    calls.append(row)
                else:
                    pending += 1
    out = {"updated": now.isoformat(timespec="seconds"), "pending": pending, "neutral": neutral, "horizons": {}}
    for h in HORIZONS:
        rows = [r for r in calls if "hit%d" % h in r]
        n, hits = len(rows), sum(r["hit%d" % h] for r in rows)
        signed = sorted(r["ex%d" % h] * SIGN[r["direction"]] for r in rows)
        strong = [r for r in rows if r["direction"] in ("空", "多")]
        out["horizons"][str(h)] = {
            "n": n, "hits": hits, "rate": round(hits / n, 3) if n else None,
            "p": round(binom_p(hits, n), 4) if n else None,
            "median_signed_excess": round(signed[n // 2] if n % 2 else (signed[n // 2 - 1] + signed[n // 2]) / 2, 2) if n else None,
            "strong_n": len(strong), "strong_hits": sum(r["hit%d" % h] for r in strong)}
    h5 = out["horizons"]["5"]
    h1 = out["horizons"]["1"]
    ref = h5 if h5["n"] >= MIN_N else h1
    if ref["n"] < MIN_N:
        out["verdict_zh"] = "样本还太少（已到期 %d 次），看不出准不准" % ref["n"]
    elif ref["p"] < 0.05:
        out["verdict_zh"] = "明显比瞎猜准（%d 次里对了 %.0f%%）" % (ref["n"], ref["rate"] * 100)
    elif ref["rate"] > 0.5:
        out["verdict_zh"] = "略好于瞎猜，但还不能排除是运气（%d 次里对了 %.0f%%）" % (ref["n"], ref["rate"] * 100)
    else:
        out["verdict_zh"] = "目前不比瞎猜好（%d 次里对了 %.0f%%）" % (ref["n"], ref["rate"] * 100)
    out["recent"] = sorted(calls, key=lambda r: (r["date"], r["code"]), reverse=True)[:12]
    out["misses"] = [r for r in out["recent"] if r.get("hit5") is False or (r.get("hit5") is None and r.get("hit1") is False)][:6]
    return out
