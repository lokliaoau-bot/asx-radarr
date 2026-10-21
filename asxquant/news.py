# -*- coding: utf-8 -*-
"""AFR 每日要闻：抓 AFR 标题，校验并发布 Claude 写的解读简报。

用户要的是「简洁、准确的 AFR 新闻标题 + 发生了什么 + Claude 的分析」，所以：
  · 新闻只从 AFR 选（标题和链接必须是 afr.com 的）。AFR 正文被订阅墙挡住时，「发生了什么」
    可以去其他权威来源补全，但每条都要列出来源链接。
  · 简报由 Claude 写成 news/briefs/YYYY-MM-DD.json（格式见 validate()，写法见 news/README.md）。
    页面只认通过 validate() 的简报，没通过的直接跳过。
  · 股价涨跌不信手写的数字：发布时按收盘价用代码重算并覆盖（fill_moves），停牌单独标出。
  · 影响方向只有五档：空 / 偏空 / 中性 / 偏多 / 多。这是 Claude 的判断，没有回测过，页面上照实写明。
  · 两个阶段：stage=headlines（每日自动，只有标题 + 发生了什么）→ stage=analysis（用户点按钮后补上影响和分析）。
    同一天已经有分析版时，标题版不能把它盖掉（accept_brief）。

命令行（Claude 云端任务用。除 moves 外只用标准库，不用装依赖）：
  python -m asxquant.news today                           # 悉尼今天的日期，以及 ASX 开不开市
  python -m asxquant.news headlines                       # 今天 AFR 跟股市有关的标题（RSS + 首页）
  python -m asxquant.news lead <AFR 链接>                  # 文章开头几段（订阅墙前公开的部分，只供自己读，不上页面）
  python -m asxquant.news moves 2026-10-20 NAB CGF XJO    # 按收盘价算当天涨跌（XJO = ASX 200 指数；需要 yfinance）
  python -m asxquant.news check news/briefs/2026-10-20.json
  python -m asxquant.news accept <git 引用> <文件…>        # news-intake.yml 用：校验 + 不许标题版盖掉分析版
  python -m asxquant.news collect [日期]                  # GitHub Actions 用：抓 AFR 标题和收盘行情 → news/inbox/
  python -m asxquant.news request                         # 云端 Claude 用：请 GitHub 现抓一份，等它提交后拉下来
  python -m asxquant.news inbox [日期]                    # 云端 Claude 用：看收件箱里的 AFR 标题和涨跌
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import io
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEFS = os.path.join(ROOT, "news", "briefs")
# GitHub Actions 抓回来的 AFR 标题和收盘行情（news-collect.yml）。云端 Claude 的沙箱连不上 afr.com 和
# 行情网站（2026-10-21 每日任务因此失败），所以它只从这里取 AFR 标题和链接。
INBOX = os.path.join(ROOT, "news", "inbox")
INBOX_SCHEMA = "afr-inbox-v1"

SCHEMA = "afr-brief-v1"
DIRECTIONS = ("空", "偏空", "中性", "偏多", "多")
# 两个阶段（2026-10-20 用户要求）：每天收盘后 Opus 5.5（中）自动抓标题、写「发生了什么」= headlines；
# 用户在页面上点「深度分析」才由 Opus 5.5（高）补上影响和分析 = analysis。
STAGES = ("headlines", "analysis")
EFFORTS = ("low", "medium", "high")
AFR = "https://www.afr.com"
AFR_RSS = AFR + "/rss/feed.xml"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# 和股市有关的栏目（链接路径第一段）。政治、国际、生活、文化不选。
MARKET_SECTIONS = {"companies", "markets", "street-talk", "chanticleer", "rear-window",
                   "policy", "technology", "property", "wealth"}
KEEP_SITE = 30      # 站点里保留最近多少期
CHECK_MOVES = 10    # 发布时重算最近多少期的涨跌
MAX_HEADLINES = 40

_SLUG_DATE = re.compile(r"-(20\d{2})(\d{2})(\d{2})-p[0-9a-z]{5}$")
_HOME_A = re.compile(r'<a[^>]+href="((?:https://www\.afr\.com)?/[a-z0-9\-/]+-20\d{6}-p[0-9a-z]{5})"[^>]*>(.*?)</a>', re.S)
_CODE = re.compile(r"^[A-Z0-9]{3}$")
_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


# ---------------------------------------------------------------- 抓 AFR

def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def slug_date(url):
    """AFR 链接末尾带发稿日期（…-20261020-p616yh）→ '2026-10-20'；没有就 None。"""
    m = _SLUG_DATE.search((url or "").split("?")[0])
    return "%s-%s-%s" % m.groups() if m else None


def section(url):
    path = url.split("afr.com", 1)[1] if "afr.com" in url else url
    return path.strip("/").split("/", 1)[0]


def parse_rss(text):
    """AFR RSS（最新约 20 条）→ [{title, url, category, published}]。只留标题和链接，不留摘要。"""
    root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    out = []
    for it in root.iter("item"):
        url = (it.findtext("link") or "").strip().split("?")[0]
        title = _clean(it.findtext("title"))
        if not url.startswith(AFR + "/") or not title:
            continue
        try:
            pub = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "")
            pub = pub.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
        except Exception:
            pub = None
        out.append({"title": title, "url": url, "category": _clean(it.findtext("category")) or None,
                    "published": pub})
    return out


def parse_home(text):
    """AFR 首页 → [{title, url}]。同一链接常先出现一个只有图片的 <a>，取第一个有文字的。"""
    seen, out = set(), []
    for href, inner in _HOME_A.findall(text):
        url = href if href.startswith("http") else AFR + href
        title = _clean(inner)
        if len(title) < 20 or url in seen:
            continue
        seen.add(url)
        out.append({"title": title, "url": url, "category": None, "published": None})
    return out


def headlines(market_only=True, get=_get, log=lambda m: None):
    """RSS 在前（有时间和栏目），首页补齐（RSS 只有最新 20 条，早上的大新闻常常已经掉出去）。"""
    items, seen = [], set()
    for url, parse in ((AFR_RSS, parse_rss), (AFR + "/", parse_home)):
        try:
            got = parse(get(url))
        except Exception as e:
            log("AFR %s 取不到: %s" % (url, e))
            continue
        for h in got:
            if h["url"] in seen:
                continue
            seen.add(h["url"])
            h["section"] = section(h["url"])
            h["date"] = slug_date(h["url"])
            items.append(h)
    if market_only:
        items = [h for h in items if h["section"] in MARKET_SECTIONS]
    return items


def article_lead(url, n=4, get=_get):
    """文章开头几段（AFR 在订阅墙前公开的部分）。只给写简报的人读，不上页面。
    还没上线的文章（首页有时提前挂出第二天的链接）会是 404，调用方跳过即可。"""
    text = get(url)
    a, b = text.find("<article"), text.rfind("</article>")
    body = text[a:b] if 0 <= a < b else text
    paras = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", body, flags=re.S):
        p = _clean(p)
        # 导航栏会被拼成一长串没有空格的词，按空格密度筛掉；订阅推广语也去掉
        if len(p) > 60 and p.count(" ") / len(p) > 0.08 and "Gift articles" not in p:
            paras.append(p)
    m = re.search(r'"datePublished":"([^"]+)"', text)
    return {"url": url, "published": m.group(1) if m else None, "paragraphs": paras[:n]}


# ---------------------------------------------------------------- 校验

def _date(s):
    try:
        return dt.date.fromisoformat(s) if isinstance(s, str) and len(s) == 10 else None
    except ValueError:
        return None


def _str(v, lo, hi):
    return isinstance(v, str) and lo <= len(v.strip()) <= hi


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def validate(b):
    """返回问题列表；空列表 = 通过。规则写得严，是为了让页面上每一条都站得住。"""
    if not isinstance(b, dict):
        return ["整份简报不是 JSON 对象"]
    P = []
    if b.get("schema") != SCHEMA:
        P.append("schema 必须是 %s" % SCHEMA)
    stage = stage_of(b)
    if stage not in STAGES:
        P.append("stage 只能是 headlines（只抓标题和内容）或 analysis（含影响分析）")
    if b.get("model") is not None and not _str(b.get("model"), 1, 40):
        P.append("model 要是模型名")
    if b.get("effort") is not None and b.get("effort") not in EFFORTS:
        P.append("effort 只能是 " + "/".join(EFFORTS))
    d = _date(b.get("date"))
    if d is None:
        P.append("date 必须是 YYYY-MM-DD（悉尼日期）")
    for k in ("generated_at", "generated_by"):
        if not _str(b.get(k), 1, 80):
            P.append("缺 %s" % k)
    m = b.get("market")
    if m is not None:
        if not isinstance(m, dict) or not _str(m.get("text_zh"), 4, 200):
            P.append("market.text_zh（今日大盘一句话）要 4–200 字")
        else:
            for k in ("index_close", "index_pct"):
                if m.get(k) is not None and not _num(m.get(k)):
                    P.append("market.%s 必须是数字或 null" % k)
    items = b.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        P.append("items 要 1–10 条")
        items = []
    seen = set()
    for i, it in enumerate(items, 1):
        w = "第 %d 条：" % i
        if not isinstance(it, dict):
            P.append(w + "不是对象")
            continue
        if not _str(it.get("title_zh"), 2, 40):
            P.append(w + "title_zh（中文标题）要 2–40 字")
        if not _str(it.get("afr_headline"), 5, 200):
            P.append(w + "afr_headline（AFR 原标题）缺失")
        u = it.get("afr_url")
        if not (isinstance(u, str) and u.startswith(AFR + "/")):
            P.append(w + "afr_url 必须是 afr.com 的文章链接（新闻只从 AFR 选）")
        else:
            if u in seen:
                P.append(w + "和前面的条目是同一篇")
            seen.add(u)
            sd = _date(slug_date(u))
            if d and sd and not d - dt.timedelta(days=7) <= sd <= d:
                P.append(w + "AFR 发稿日期 %s 不在简报日期前 7 天内" % sd)
        if not _str(it.get("what_zh"), 20, 400):
            P.append(w + "what_zh（发生了什么）要 20–400 字")
        if stage == "headlines" and "impact" not in it and "why_zh" not in it:
            imp = []                           # 标题版：还没有分析，正常
        else:
            if not _str(it.get("why_zh"), 8, 240):
                P.append(w + "why_zh（为什么这样判断）要 8–240 字")
            imp = it.get("impact")
            if not isinstance(imp, list) or not 1 <= len(imp) <= 5:
                P.append(w + "impact 要 1–5 个对象（股票或板块）")
                imp = []
        for t in imp:
            if not isinstance(t, dict):
                P.append(w + "impact 里有非对象")
                continue
            if t.get("direction") not in DIRECTIONS:
                P.append(w + "方向只能是 " + "/".join(DIRECTIONS))
            if not _str(t.get("name"), 1, 30):
                P.append(w + "impact.name 缺失")
            c = t.get("code")
            if c is not None and not (isinstance(c, str) and _CODE.match(c)):
                P.append(w + "impact.code 要是 3 位 ASX 代码或 null（板块填 null）")
            if t.get("move_pct") is not None and not _num(t.get("move_pct")):
                P.append(w + "impact.move_pct 必须是数字或 null")
        src = it.get("sources")
        if not isinstance(src, list) or not 1 <= len(src) <= 6:
            P.append(w + "sources 要 1–6 个")
            src = []
        for s in src:
            if not (isinstance(s, dict) and _str(s.get("name"), 1, 40)
                    and isinstance(s.get("url"), str) and s["url"].startswith("https://")):
                P.append(w + "每个来源要有 name 和 https 链接")
    # 页面会转义，这里再挡一道：任何文字里都不许出现尖括号
    for s in _strings(b):
        if "<" in s or ">" in s:
            P.append("文字里不能有 < 或 >：" + s[:40])
            break
    return P


# ---------------------------------------------------------------- 涨跌重算

def _sym(code):
    return "^AXJO" if code == "XJO" else code + ".AX"


def stage_of(b):
    return b.get("stage", "analysis") if isinstance(b, dict) else None


def inbox_urls(day, inbox_dir=INBOX):
    """简报日期当天及前 3 天收件箱里的 AFR 链接。当天的收件箱不存在时返回 None（不检查，兼容手写的老简报）。"""
    day = _date(day) if isinstance(day, str) else day
    if day is None or not os.path.exists(os.path.join(inbox_dir, day.isoformat() + ".json")):
        return None
    urls = set()
    for k in range(4):
        path = os.path.join(inbox_dir, (day - dt.timedelta(days=k)).isoformat() + ".json")
        if os.path.exists(path):
            try:
                with io.open(path, encoding="utf-8") as f:
                    urls |= {h.get("url") for h in json.load(f).get("headlines") or []}
            except Exception:
                pass
    return urls


def check_urls(b, inbox_dir=INBOX):
    """有收件箱时，每条的 AFR 链接都必须是 GitHub 真实抓到的——挡住编出来的链接。"""
    urls = inbox_urls(b.get("date"), inbox_dir) if isinstance(b, dict) else None
    if urls is None:
        return []
    return ["第 %d 条：AFR 链接不在收件箱里（只能用 news/inbox 里真实抓到的 AFR 文章）：%s" % (i, it.get("afr_url"))
            for i, it in enumerate(b.get("items") or [], 1)
            if isinstance(it, dict) and it.get("afr_url") not in urls]


def accept_brief(name, new_text, old_text=None, inbox_dir=INBOX):
    """news-intake.yml 收简报时的把关：(能不能收, 原因)。
    规则：通过校验、文件名和日期一致；同一天 main 上已经是分析版时，不收标题版（不然每日自动抓标题会把
    用户点按钮换来的分析盖掉）。分析版可以覆盖任何版本（用户又点了一次深度分析）。"""
    try:
        b = json.loads(new_text)
    except Exception as e:
        return False, "不是合法 JSON: %s" % e
    P = validate(b)
    if isinstance(b, dict) and b.get("date") != os.path.basename(name)[:10]:
        P.append("文件名和 date 对不上")
    if not P:
        P = check_urls(b, inbox_dir)
    if P:
        return False, "没过校验: " + "；".join(P[:5])
    if old_text:
        try:
            old = json.loads(old_text)
        except Exception:
            old = None
        if stage_of(old) == "analysis" and stage_of(b) == "headlines":
            return False, "main 上这一天已经有分析版，标题版不覆盖（不是错误）"
    return True, stage_of(b)


def _yahoo(codes, start, log):
    try:
        import yfinance as yf
        df = yf.download([_sym(c) for c in codes], start=start.isoformat(), progress=False,
                         auto_adjust=False, threads=True)
    except Exception as e:
        log("涨跌重算：取行情失败 %s" % e)
        return {}
    out = {}
    for c in codes:
        try:
            cl, vo = df["Close"], df["Volume"]
            if hasattr(cl, "columns"):
                cl, vo = cl[_sym(c)], vo[_sym(c)]
        except KeyError:
            continue
        rows = []
        for ix in cl.index:
            x, v = cl[ix], vo[ix]
            if x == x:                      # 跳过 NaN
                rows.append((ix.date().isoformat(), float(x), float(v) if v == v else None))
        out[c] = rows
    return out


def fill_moves(briefs, prices=None, log=print):
    """按收盘价重算每个股票代码在简报当天的涨跌，覆盖简报里手写的数字。
    当天成交量为 0 记为停牌。取不到行情就保留原数字并标 move_checked=False（页面显示「约」）。
    prices: {code: [(date, close, volume), ...]}；测试直接给，不给就用 yfinance 取。"""
    codes = sorted({t["code"] for b in briefs for it in b["items"] for t in it.get("impact") or []
                    if t.get("code")})
    if not codes:
        return 0
    if prices is None:
        start = min(dt.date.fromisoformat(b["date"]) for b in briefs) - dt.timedelta(days=12)
        prices = _yahoo(codes, start, log)
    n = 0
    for b in briefs:
        d = b["date"]
        for it in b["items"]:
            for t in it.get("impact") or []:
                rows = sorted(prices.get(t.get("code")) or []) if t.get("code") else []
                today = [r for r in rows if r[0] == d]
                before = [r for r in rows if r[0] < d]
                if not today or not before:
                    if t.get("code"):
                        t["move_checked"] = False
                    continue
                c1, v1 = today[-1][1], today[-1][2]
                t["halted"] = v1 == 0 and t["code"] != "XJO"      # 指数没有成交量
                t["move_pct"] = 0.0 if t["halted"] else round((c1 / before[-1][1] - 1) * 100, 2)
                t["move_checked"] = True
                t["close"] = round(c1, 4)
                n += 1
    return n


# ---------------------------------------------------------------- 收件箱（GitHub Actions 抓，云端 Claude 读）

def sydney_today():
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("Australia/Sydney")).date()


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def collect(day=None, get=_get, prices=None, codes=None, log=print):
    """GitHub Actions 里跑：今天的 AFR 标题 + 股票池和 ASX 200 的收盘涨跌。
    只存标题、链接、栏目、时间和行情数字，不存 AFR 的摘要或正文（仓库是公开的）。"""
    day = day or sydney_today()
    lo = day - dt.timedelta(days=3)
    heads = [h for h in headlines(get=get, log=log)
             if not h.get("date") or lo <= dt.date.fromisoformat(h["date"]) <= day]
    if codes is None:
        from . import config as C
        codes = sorted({C.asx_code(t) for t in C.all_stock_tickers()})
    probe = {"date": day.isoformat(), "items": [{"impact": [{"code": c} for c in list(codes) + ["XJO"]]}]}
    try:
        fill_moves([probe], prices=prices, log=log)
    except Exception as e:
        log("收件箱：取行情失败（只存标题）: %s" % e)
    got = {t["code"]: t for t in probe["items"][0]["impact"] if t.get("move_checked")}
    idx = got.pop("XJO", None)
    moves = {c: {"pct": t["move_pct"], "close": t["close"], "halted": t["halted"]} for c, t in sorted(got.items())}
    movers = sorted((c for c in moves if not moves[c]["halted"]), key=lambda c: -abs(moves[c]["pct"]))[:15]
    return {
        "schema": INBOX_SCHEMA, "date": day.isoformat(), "collected_at": _now_iso(),
        "headlines": [{k: h.get(k) for k in ("title", "url", "section", "date", "published")} for h in heads],
        "index": {"code": "XJO", "pct": idx["move_pct"], "close": idx["close"]} if idx else None,
        "moves": moves, "top_movers": movers,
    }


def load_inbox(day, inbox_dir=INBOX):
    path = os.path.join(inbox_dir, (day.isoformat() if hasattr(day, "isoformat") else day) + ".json")
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _git(*args):
    import subprocess
    return subprocess.run(["git"] + list(args), capture_output=True, text=True, encoding="utf-8")


class RequestError(RuntimeError):
    def __init__(self, msg, code):
        RuntimeError.__init__(self, msg)
        self.code = code


def request_inbox(day=None, wait=900, poll=20, git=_git, sleep=None, clock=None, log=print):
    """云端 Claude 开工时调用：推一个 claude/news-request-* 分支 → GitHub 上的 news-collect.yml 几十秒内开跑，
    抓好后把 news/inbox/<日期>.json 提交到 main → 这里等到它出现，再把 main 快进拉下来。
    推不上（没装 Claude GitHub App）立刻报错 code=3；等超时但当天已有定时抓的收件箱就用它，否则 code=4。"""
    import time
    sleep = sleep or time.sleep
    clock = clock or time.time
    day = day or sydney_today()
    path = "news/inbox/%s.json" % day.isoformat()
    asked = _now_iso()
    branch = "claude/news-request-%s-%s" % (day.isoformat(), dt.datetime.now(dt.timezone.utc).strftime("%H%M%S"))
    r = git("push", "origin", "HEAD:refs/heads/" + branch)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        raise RequestError(
            "推不上 GitHub，没法请 GitHub 抓 AFR 标题：%s\n多半是 Claude GitHub App 没有装到 lokliaoau-bot/asx-radarr"
            "（https://github.com/apps/claude/installations/select_target）。" % " / ".join(tail), 3)
    log("已请 GitHub 抓今天的 AFR 标题和行情（分支 %s），等它提交 …" % branch)
    deadline = clock() + wait
    fresh = False
    while clock() < deadline:
        sleep(poll)
        git("fetch", "-q", "origin", "main")
        show = git("show", "origin/main:" + path)
        if show.returncode == 0:
            try:
                fresh = json.loads(show.stdout).get("collected_at", "") >= asked
            except Exception:
                fresh = False
            if fresh:
                break
    if git("show", "origin/main:" + path).returncode != 0:
        raise RequestError("等了 %d 分钟，GitHub 还没把今天的 AFR 标题提交回来（去 Actions 看「抓 AFR 标题和收盘行情」"
                           "有没有失败）。" % (wait // 60), 4)
    if git("merge", "--ff-only", "origin/main").returncode != 0:
        git("checkout", "origin/main", "--", path)
    if not fresh:
        log("没等到这次请求的结果，先用当天早些时候定时抓的收件箱。")
    return path


def print_inbox(inbox, out=None):
    out = out or sys.stdout
    ix = inbox.get("index") or {}
    print("日期 %s，抓取于 %s" % (inbox["date"], inbox.get("collected_at")), file=out)
    if ix:
        print("ASX 200（XJO）收 %s，%+.2f%%" % (ix.get("close"), ix.get("pct")), file=out)
    print("\n涨跌最大的 15 只（股票池内）：" + "，".join(
        "%s %+.2f%%" % (c, inbox["moves"][c]["pct"]) for c in inbox.get("top_movers") or []), file=out)
    halted = [c for c, v in (inbox.get("moves") or {}).items() if v.get("halted")]
    if halted:
        print("停牌：" + "，".join(halted), file=out)
    print("\nAFR 标题（只能从这里选，链接原样照抄）：", file=out)
    for h in inbox.get("headlines") or []:
        print("- [%s] %s | %s | %s" % (h.get("date"), h.get("section"), h.get("title"), h.get("url")), file=out)
    print("\n其余股票的涨跌在 JSON 的 moves 里（股票池外的，发布时会自动补）。", file=out)


# ---------------------------------------------------------------- 发布

def load_briefs(briefs_dir=BRIEFS, log=print):
    out = []
    if not os.path.isdir(briefs_dir):
        return out
    for name in sorted(os.listdir(briefs_dir)):
        if not _FILE.match(name):
            continue
        try:
            with io.open(os.path.join(briefs_dir, name), encoding="utf-8") as f:
                b = json.load(f)
        except Exception as e:
            log("简报 %s 读不了，跳过: %s" % (name, e))
            continue
        P = validate(b)
        if isinstance(b, dict) and b.get("date") != name[:10]:
            P.append("文件名和 date 对不上")
        if P:
            log("简报 %s 没过校验，跳过: %s" % (name, "；".join(P[:3])))
            continue
        out.append(b)
    return out


def _write(path, obj):
    from .fileio import atomic_bytes
    atomic_bytes(path, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def build_site(out_dir, briefs_dir=BRIEFS, log=print, fetch=True, prices=None, inbox_dir=INBOX):
    """写 <out_dir>/news/：brief.json（最新一期）、briefs/<日期>.json、index.json、headlines.json。
    fetch=False 时不联网（不重算涨跌、不抓标题），测试和本地预览用。"""
    nd = os.path.join(out_dir, "news")
    os.makedirs(os.path.join(nd, "briefs"), exist_ok=True)
    briefs = load_briefs(briefs_dir, log)[-KEEP_SITE:]
    if briefs and (fetch or prices is not None):
        try:
            fill_moves(briefs[-CHECK_MOVES:], prices=prices, log=log)
        except Exception as e:
            log("涨跌重算失败（沿用简报里的数字）: %s" % e)
    for b in briefs:
        _write(os.path.join(nd, "briefs", b["date"] + ".json"), b)
    _write(os.path.join(nd, "index.json"),
           {"briefs": [{"date": b["date"], "n": len(b["items"]), "stage": stage_of(b)} for b in reversed(briefs)]})
    if briefs:
        _write(os.path.join(nd, "brief.json"), briefs[-1])
    heads = []
    if fetch:
        try:
            heads = headlines(log=log)[:MAX_HEADLINES]
        except Exception as e:
            log("AFR 标题抓取失败（页面照常显示简报）: %s" % e)
    if not heads and os.path.isdir(inbox_dir):     # 现抓失败就用最近一份收件箱，页面至少有标题可看
        latest = sorted(n for n in os.listdir(inbox_dir) if _FILE.match(n))[-1:]
        if latest:
            heads = (load_inbox(latest[0][:10], inbox_dir) or {}).get("headlines", [])[:MAX_HEADLINES]
    _write(os.path.join(nd, "headlines.json"), {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "items": [{k: h.get(k) for k in ("title", "url", "section", "date")} for h in heads]})
    log("AFR 要闻: %d 期简报，最新 %s；AFR 标题 %d 条"
        % (len(briefs), briefs[-1]["date"] if briefs else "无", len(heads)))
    return {"briefs": len(briefs), "headlines": len(heads)}


# ---------------------------------------------------------------- 命令行

def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a.pop(0) if a else ""
    if cmd == "today":
        from zoneinfo import ZoneInfo
        d = dt.date.fromisoformat(a[0]) if a else dt.datetime.now(ZoneInfo("Australia/Sydney")).date()
        try:
            from short_radar.asx_calendar import session
            is_open = session(d) is not None
        except Exception:                       # 日历只核查到 2027 年；之后退回「周一到周五」
            is_open = d.weekday() < 5
        print("%s %s" % (d.isoformat(), "交易日" if is_open else "休市"))
        return 0
    if cmd == "headlines":
        for h in headlines(log=lambda m: print("!", m, file=sys.stderr)):
            print(json.dumps(h, ensure_ascii=False))
        return 0
    if cmd == "lead" and a:
        print(json.dumps(article_lead(a[0]), ensure_ascii=False, indent=1))
        return 0
    if cmd == "moves" and len(a) >= 2:
        fake = {"date": a[0], "items": [{"impact": [{"code": c.upper()} for c in a[1:]]}]}
        fill_moves([fake], log=lambda m: print("!", m, file=sys.stderr))
        for t in fake["items"][0]["impact"]:
            print("%s  %s" % (t["code"], "停牌" if t.get("halted") else
                              ("%+.2f%%" % t["move_pct"]) if t.get("move_checked") else "取不到当天收盘价"))
        return 0
    if cmd == "check" and a:
        bad = 0
        for path in a:
            try:
                with io.open(path, encoding="utf-8") as f:
                    b = json.load(f)
                P = validate(b)
                if isinstance(b, dict) and b.get("date") != os.path.basename(path)[:10]:
                    P.append("文件名和 date 对不上")
                if not P:
                    P = check_urls(b)
            except Exception as e:
                P = ["读不了: %s" % e]
            print("%s: %s" % (path, "通过" if not P else "不通过"))
            for p in P:
                print("  - " + p)
            bad += bool(P)
        return 1 if bad else 0
    if cmd == "collect":
        day = dt.date.fromisoformat(a[0]) if a else sydney_today()
        inbox = collect(day, log=lambda m: print("!", m, file=sys.stderr))
        if not inbox["headlines"]:
            print("一条 AFR 标题都没抓到，不写收件箱", file=sys.stderr)
            return 1
        os.makedirs(INBOX, exist_ok=True)
        path = os.path.join(INBOX, day.isoformat() + ".json")
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(inbox, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("%s：AFR 标题 %d 条，行情 %d 只，ASX 200 %s"
              % (path, len(inbox["headlines"]), len(inbox["moves"]), inbox["index"]))
        return 0
    if cmd == "request":
        try:
            print(request_inbox(log=lambda m: print(m, file=sys.stderr)))
            return 0
        except RequestError as e:
            print("失败：%s" % e, file=sys.stderr)
            return e.code
    if cmd == "inbox":
        day = dt.date.fromisoformat(a[0]) if a else sydney_today()
        inbox = load_inbox(day)
        if not inbox:
            print("没有 news/inbox/%s.json，先运行 python -m asxquant.news request" % day, file=sys.stderr)
            return 1
        print_inbox(inbox)
        return 0
    if cmd == "accept" and len(a) >= 2:
        # 标准输出只打印收下的文件路径（给 shell 用），说明都写到标准错误
        import subprocess
        ref, bad = a[0], 0
        for path in a[1:]:
            new = subprocess.run(["git", "show", "%s:%s" % (ref, path)], capture_output=True,
                                 text=True, encoding="utf-8").stdout
            old = io.open(path, encoding="utf-8").read() if os.path.exists(path) else None
            ok, why = accept_brief(path, new, old)
            print("%s: %s" % (path, ("收下（%s）" % why) if ok else why), file=sys.stderr)
            if ok:
                with io.open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
                print(path)
            elif not why.startswith("main 上这一天已经有分析版"):
                bad += 1
        return 1 if bad else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
