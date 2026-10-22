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
  python -m asxquant.news confirm <日期>                  # 云端 Claude 用：推送后确认简报真的并进了 main
  python -m asxquant.news template headlines|analysis     # 云端 Claude 用：打印填好固定字段的简报骨架
  python -m asxquant.news due                             # 看门狗用：现在应该已有简报的那个交易日
  python -m asxquant.news verify <文件…>                  # news-intake.yml 用：打开来源网页逐个核对数字
  python -m asxquant.news score                           # news-collect.yml 用：判断成绩单 → news/scorecard.json
"""
from __future__ import annotations

import csv
import datetime as dt
import email.utils
import html
import io
import json
import os
import re
import sys
import urllib.error
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
# 备用标题来源（2026-10-22 加）：AFR 万一挡住 GitHub 的机器，Bing 新闻 RSS 仍能给出 AFR 的标题和真实链接
BING_RSS = "https://www.bing.com/news/search?q=site%3aafr.com&format=rss&count=50"
STATUS = "news/inbox/_status.json"          # 抓取 / 收件流程的最近一次结果，云端 Claude 靠它快速知道失败原因
SOURCE_STATS = "news/source_stats.json"     # 各来源网站「程序能不能打开」的累计记录（数字核对时更新）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# 和股市有关的栏目（链接路径第一段）。政治、国际、生活、文化不选。
MARKET_SECTIONS = {"companies", "markets", "street-talk", "chanticleer", "rear-window",
                   "policy", "technology", "property", "wealth"}
# 栏目页（2026-10-21 加，用户要「抓更多新闻、把 Street Talk 也抓到」）：RSS 只有最新 20 条、首页约 50 条，
# 每个栏目页还能各补约 20 条，Street Talk 页能拿到它当天和前两天的全部标题。
SECTION_PAGES = (
    "/street-talk", "/chanticleer", "/rear-window",
    "/companies", "/companies/financial-services", "/companies/mining", "/companies/energy",
    "/companies/infrastructure", "/companies/healthcare-and-fitness", "/companies/retail",
    "/companies/telecommunications", "/companies/transport", "/companies/professional-services",
    "/markets", "/markets/equity-markets", "/markets/commodities", "/markets/debt-markets",
    "/markets/currencies", "/policy/economy", "/policy/tax-and-super", "/technology", "/property/commercial",
)
SECTION_LABELS = {"street-talk": "Street Talk", "chanticleer": "Chanticleer", "rear-window": "Rear Window",
                  "companies": "公司", "markets": "市场", "policy": "政策", "technology": "科技",
                  "property": "地产", "wealth": "理财"}
ASX_LIST = "https://www.asx.com.au/asx/research/ASXListedCompanies.csv"
LEAD_LIMIT = 180    # 收件时最多读多少篇文章的公开开头（只提取线索，不存原文）；10-22 读满了 100 篇，调高
LEAD_WORKERS = 4    # 同时读几篇（每篇之间仍有停顿，对 AFR 很温和）
LEAD_BUDGET = 240   # 读文章最多花几秒：AFR 很慢时也不会拖垮整个收件（请求方只等 15 分钟）
LEAD_PRIORITY = {s: i for i, s in enumerate(("street-talk", "companies", "markets", "chanticleer", "rear-window",
                                              "policy", "technology", "property", "wealth"))}
INBOX_KEEP_DAYS = 30
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


def parse_bing(text):
    """Bing 新闻 RSS → [{title, url}]：链接是 Bing 的跳转，真实地址在 url= 参数里；只留 afr.com 的。"""
    import urllib.parse
    out = []
    for it in re.findall(r"<item>(.*?)</item>", text or "", flags=re.S):
        t = re.search(r"<title>(.*?)</title>", it, flags=re.S)
        l = re.search(r"<link>(.*?)</link>", it, flags=re.S)
        if not (t and l):
            continue
        link = html.unescape(l.group(1).strip())
        url = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url", [link])[0].split("?")[0]
        title = _clean(re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)))
        if url.startswith(AFR + "/") and len(title) >= 20 and _SLUG_DATE.search(url):
            out.append({"title": title, "url": url, "category": None, "published": None})
    return out


def _retrying(get, sleep, tries=3, log=lambda m: None):
    """网络偶尔抽风（超时、5xx）就隔几秒重试；404/403 这种明确拒绝不重试。"""
    def wrapped(url):
        for i in range(tries):
            try:
                return get(url)
            except urllib.error.HTTPError as e:
                if e.code < 500 or i == tries - 1:
                    raise
            except Exception:
                if i == tries - 1:
                    raise
            log("重试 %s（第 %d 次）" % (url, i + 2))
            sleep(2 * (i + 1))
    return wrapped


def headlines(market_only=True, get=_get, log=lambda m: None, sections=(), fallback=False):
    """RSS 在前（有时间和栏目），首页补齐（RSS 只有最新 20 条，早上的大新闻常常已经掉出去），
    再按 sections 逐个读栏目页（收件时用 SECTION_PAGES；发布时为了快，只读 RSS 和首页）。
    fallback=True 时再读 Bing 新闻的 AFR 结果：平时补漏，AFR 挡住 GitHub 时就靠它。"""
    items, seen = [], set()
    pages = [(AFR_RSS, parse_rss), (AFR + "/", parse_home)] + [(AFR + p, parse_home) for p in sections]
    if fallback:
        pages.append((BING_RSS, parse_bing))
    streak = 0
    for url, parse in pages:
        if streak >= 3 and url.startswith(AFR):
            continue                    # AFR 连续 3 页失败：多半整个挡住或宕了，别一页页等超时（Bing 照样试）
        try:
            got = parse(get(url))
            streak = 0
        except Exception as e:
            streak += url.startswith(AFR)
            log("%s 取不到: %s" % (url, e))
            if streak == 3:
                log("AFR 连续 3 页取不到，剩下的 AFR 栏目页跳过")
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
        # 第一段前面粘着署名和时间（「…SmithSenior reporterOct 21, 2026 – 10.37amWoodside…」），先去掉，
        # 否则会被下面的「标签云」规则误删——而第一段恰恰是最重要的
        p = _BYLINE.sub("", _clean(p), count=1).strip()
        # 导航栏会被拼成一长串没有空格的词，按空格密度筛掉；订阅推广语也去掉
        if (len(p) > 60 and p.count(" ") / len(p) > 0.08 and "Gift articles" not in p
                and "Latest In" not in p and "Fetching latest" not in p
                and len(re.findall(r"[a-z][A-Z][a-z]", p)) < 3):      # 标签云会拼成「CommunitiesPeet」
            paras.append(p)
    m = re.search(r'"datePublished":"([^"]+)"', text)
    return {"url": url, "published": m.group(1) if m else None, "paragraphs": paras[:n]}


# ---------------------------------------------------------------- 公司名 → ASX 代码、文章线索

# 公司名里常见、单独出现时多半不是指这家公司的词：只有它们打头的公司，不用第一个词单独匹配
_GENERIC = set("""
bank gold energy capital global national first western northern southern eastern central metals mining minerals
health healthcare property properties investment investments australian australia international pacific asia
china india japan american america sydney melbourne brisbane perth adelaide queensland victoria tasmania star
commonwealth treasury nine seven super fund funds market markets real future digital data media network networks
services solutions systems infrastructure airport airports life insurance credit finance financial money equity
battery uranium copper iron silver rare earth earths clean green blue black white golden royal united general
premier prime advance smart open next alpha peak summit vision pure true bright liberty unity fortune atlas titan
phoenix orion nova pilbara hunter wealth trade retail home house housing bond bonds dollar rate rates price
labor liberal federal reserve street talk chief lithium power water coal resources holdings partners ventures
technologies technology industries trust income growth value select crown platinum horizon frontier pioneer
legacy heritage diamond regional city state public private island coast north south east west great new
blackstone apollo carlyle bain goldman morgan citi jpmorgan microsoft google amazon apple nvidia tesla spacex
openai intel oracle samsung toyota boeing shell walmart costco
""".split())
# 大写三字母但不是在说那家公司的缩写（「ASX rallies」说的是大盘，不是 ASX 这家公司）
_ACRONYMS = {"ASX", "CEO", "CFO", "IPO", "AFR", "ESG", "GDP", "CPI", "USD", "AUD", "RBA", "ATO", "NSW", "GST",
             "AGM", "EPS", "NPV", "LNG", "API", "FY2", "FY1", "THE", "AND", "ACT", "ACC", "ACL", "IMF", "OPEC"}
# 和海外同名机构撞名、新闻里几乎总是指海外那家的代码（瑞典私募 EQT 收购 Cleanaway、TAL 第一生命、ADNOC 的 XRG）
_COLLIDE = {"EQT", "TAL", "XRG"}
# 两个词的地名/常用词组：不能当公司名前缀（「New Zealand」不是 New Zealand King Salmon）
_PHRASES = {("new", "zealand"), ("western", "australia"), ("south", "australia"), ("new", "south"),
            ("new", "york"), ("hong", "kong"), ("united", "states"), ("gold", "coast"), ("sunshine", "coast"),
            ("north", "america"), ("south", "africa"), ("middle", "east"), ("northern", "territory"),
            ("south", "east"), ("great", "southern"), ("new", "zealand's"), ("west", "australia")}
_SUFFIX = re.compile(r"\b(LIMITED|LTD|HOLDINGS?|GROUP|CORPORATION|CORP|INCORPORATED|INC|PLC|N\.?V\.?|AG|SE|NL|"
                     r"TRUST|FUND|REIT|STAPLED|SECURITIES|THE|CO)\b\.?|\(THE\)", re.I)


def parse_asx_list(text):
    """ASX 官网的上市公司清单（CSV）→ {别名词组（小写元组）: 代码}。一个别名对应多家公司的直接丢掉。
    别名三种：全名（去掉 Limited/Group 等）、前两个词、第一个词；只靠第一个词认的记在 __first__ 里——
    正文里人名常撞上这类别名（「Milan Cooper」≠ Cooper Metals），所以正文只用全名和多词别名。"""
    seen, index, first = {}, {}, set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or not _CODE.match(row[1].strip()) or row[2].strip() == "Not Applic":
            continue
        code = row[1].strip()
        name = re.sub(r"\s+", " ", _SUFFIX.sub(" ", row[0])).strip(" .,&-")
        words = [w.lower() for w in name.split()]
        if not words:
            continue
        aliases = {tuple(words)}
        if len(words) >= 2 and tuple(words[:2]) not in _PHRASES:
            aliases.add(tuple(words[:2]))
        if len(words) > 1 and len(words[0]) >= 4 and words[0] not in _GENERIC:
            aliases.add((words[0],))
            first.add((words[0],))
        for a in aliases:
            if len(a) == 1 and (a[0].upper() in _ACRONYMS or a[0].upper() in _COLLIDE or a[0] in _GENERIC):
                continue                # 「ASX」「Blackstone」这类单词单独出现多半不是在说这家公司
            if a in _PHRASES:
                continue
            seen.setdefault(a, set()).add(code)
    for a, codes in seen.items():
        if len(codes) == 1:
            index[a] = next(iter(codes))
    index["__codes__"] = {c for codes in seen.values() for c in codes}
    index["__first__"] = {a for a in first if a in index}
    return index


def detect_codes(text, index, first_word=True, bare=None):
    """在标题或正文里找 ASX 公司（专有名词要大写开头；长名字优先，「Northern Star」不会被拆成「Star」）。
    first_word=False：不用「只靠第一个词」的别名（正文里人名太多）。
    bare：大写三字母直接当代码认时，只认这个集合里的（股票池里的大公司）——「EQT」「TAL」「XRG」在新闻里
    常常是海外同名机构（瑞典私募 EQT、TAL 第一生命、阿布扎比的 XRG），不是同代码的 ASX 小公司。"""
    if not index:
        return []
    skip = set() if first_word else index.get("__first__", set())
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9&.\-]*", re.sub(r"[’']s\b", "", text or ""))
    low = [t.lower().strip(".") for t in toks]
    codes, all_codes, i = [], index.get("__codes__", set()), 0
    while i < len(toks):
        hit = None
        for n in (6, 5, 4, 3, 2, 1):
            key = tuple(low[i:i + n])
            if len(key) == n and key in index and key not in skip and toks[i][0].isupper():
                hit = (index[key], n)
                break
        if hit:
            codes.append(hit[0])
            i += hit[1]
            continue
        t = toks[i]
        if (len(t) == 3 and t.isupper() and t in all_codes and t not in _ACRONYMS and t not in _COLLIDE
                and (bare is None or t in bare)):
            codes.append(t)
        i += 1
    return list(dict.fromkeys(codes))


_BYLINE = re.compile(r"^.{0,160}?\b20\d\d\s*[–-]\s*\d{1,2}\.\d{2}\s*[ap]m(?:,\s*first published at\s*\d{1,2}\.\d{2}\s*[ap]m)?")
_AMOUNT = re.compile(r"(?:A|US|NZ|HK)?\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:billion|million|trillion|bn|m)\b)?"
                     r"|\b\d[\d,]*(?:\.\d+)?\s?(?:per cent|pc)\b"
                     r"|\b\d[\d,]*(?:\.\d+)?\s?(?:megawatts|gigawatts|MW|GW|ounces|tonnes|barrels|jobs)\b", re.I)
_NAME_RUN = re.compile(r"(?:[A-Z][A-Za-z&\-]+(?:\s+(?:of|and|&)\s+|\s+)?){1,5}")    # 不含句点，不会跨句
_NAME_STOP = {"The", "A", "An", "In", "On", "At", "But", "And", "It", "Its", "This", "That", "These", "Those", "He",
              "She", "They", "We", "I", "After", "Before", "As", "While", "When", "With", "From", "For", "Of", "To",
              "By", "Shares", "Mr", "Ms", "Dr", "Chief", "Executive", "If", "Updated", "Key", "Posts", "Share",
              "Now", "Just", "Big", "Private", "Mergers", "Five", "Four", "Three", "Two", "One", "Six", "Ten",
              "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January", "February",
              "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
              "CEO", "CFO", "ASX", "Capital", "Partners", "Group", "Limited", "More", "Latest", "So", "Yet", "Also",
              "Investing", "Shares", "Fixed", "Income", "Courts", "Industrial", "Relations", "Financial", "Services",
              "There", "Here", "Some", "Many", "Most", "Other", "Others", "Such"}
_NAME_PREFIX = re.compile(r"^(?:ASX-listed|[A-Z][a-z]+-(?:headquartered|based|listed|backed|owned|themed))\s*")


def lead_hints(paragraphs, index=None, bare=None):
    """从文章公开的开头里只取「线索」：金额百分比、出现的公司和人名、对应的 ASX 代码。
    不存句子（仓库是公开的，不转载 AFR 原文）。线索只用来搜别的来源核实，不能直接当事实写。"""
    text = " ".join(paragraphs or [])
    text = _BYLINE.sub("", text, count=1)
    amounts = list(dict.fromkeys(m.group(0).strip() for m in _AMOUNT.finditer(text)))[:6]
    names = []
    for m in _NAME_RUN.finditer(text):
        run = _NAME_PREFIX.sub("", m.group(0).strip().rstrip("."))
        words = run.split()
        while words and words[0] in _NAME_STOP:
            words = words[1:]
        while words and words[-1].lower() in ("and", "of", "&"):
            words = words[:-1]
        if len(words) == 1 and (words[0] in _NAME_STOP or "-" in words[0]):
            continue
        run = " ".join(words)
        if len(run) >= 3 and run not in names and not run.isdigit():
            names.append(run)
    return {"amounts": amounts, "names": names[:8],
            "codes": detect_codes(text, index, first_word=False, bare=bare)[:5]}


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
    # Claude 自己算的数字要写算式（calcs），算出来对不上就退回
    from .newscheck import check_calcs
    for i, it in enumerate(items, 1):
        if isinstance(it, dict):
            P += ["第 %d 条：%s" % (i, x) for x in check_calcs(it)]
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


def check_inbox(b, inbox_dir=INBOX):
    """有收件箱时的两项核对：AFR 链接是真实抓到的；大盘收盘和涨跌和行情一致。"""
    from .newscheck import check_market
    P = check_urls(b, inbox_dir)
    if isinstance(b, dict):
        try:
            P += check_market(b, load_inbox(b.get("date"), inbox_dir))
        except Exception:
            pass
    return P


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
        P = check_inbox(b, inbox_dir)
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


def collect(day=None, get=_get, prices=None, codes=None, log=print, sections=SECTION_PAGES, sleep=None):
    """GitHub Actions 里跑：AFR 标题（RSS + 首页 + 各栏目页，含 Street Talk）、每篇提到的 ASX 公司、
    近两天文章的线索，以及股票池、被提到的公司和 ASX 200 的收盘涨跌。
    只存标题、链接、栏目、时间、线索和行情数字，不存 AFR 的摘要或正文（仓库是公开的）。"""
    import time
    sleep = sleep or time.sleep
    day = day or sydney_today()
    lo = day - dt.timedelta(days=3)
    if get is _get:
        get = lambda u: _get(u, timeout=10)     # noqa: E731  收件时单次请求最多等 10 秒
    get = _retrying(get, sleep, log=log)
    heads = [h for h in headlines(get=get, log=log, sections=sections, fallback=True)
             if not h.get("date") or lo <= dt.date.fromisoformat(h["date"]) <= day]
    try:
        index = parse_asx_list(get(ASX_LIST))
    except Exception as e:
        log("ASX 公司清单取不到（不标公司代码）: %s" % e)
        index = {}
    if codes is None:
        from . import config as C
        codes = sorted({C.asx_code(t) for t in C.all_stock_tickers()})
    bare = set(codes)
    # 「近两天」：周一要把周末算进去（周五晚上、周六的 Street Talk 常常是周一开盘前最要紧的）
    recent = (day - dt.timedelta(days=3 if day.weekday() == 0 else 1)).isoformat()
    for h in heads:
        h["codes"] = detect_codes(h["title"], index, bare=bare)
    # 文章按栏目优先级读：Street Talk 排第一。原来按 RSS→首页→栏目页的顺序，忙的日子里 Street Talk 会被
    # 读的篇数上限挤掉。
    todo = sorted((h for h in heads if (h.get("date") or "") >= recent),
                  key=lambda h: LEAD_PRIORITY.get(h.get("section"), len(LEAD_PRIORITY)))
    from concurrent.futures import ThreadPoolExecutor

    deadline = time.time() + LEAD_BUDGET

    def read_one(h):
        if time.time() > deadline:                  # 时间预算用完：剩下的不读了（GitHub 一次最多跑 15 分钟）
            return h, None
        try:
            return h, lead_hints(article_lead(h["url"], get=get)["paragraphs"], index, bare)
        except Exception as e:                      # 还没上线的文章是 404，正常
            log("读不到 %s: %s" % (h["url"], e))
            return h, None
        finally:
            sleep(0.3)                              # 别把 AFR 刷太快
    todo = todo[:LEAD_LIMIT]
    with ThreadPoolExecutor(max_workers=LEAD_WORKERS) as pool:
        results = list(pool.map(read_one, todo))
    if todo and sum(r is None for _, r in results) > len(todo) * 0.8:
        log("读文章几乎全部失败（%d/%d），AFR 可能在挡 GitHub" % (sum(r is None for _, r in results), len(todo)))
    for h, hints in results:
        if hints is None:
            continue
        h["hints"] = {"amounts": hints["amounts"], "names": hints["names"]}
        h["codes"] = list(dict.fromkeys(h["codes"] + hints["codes"]))[:6]
    mentioned = {c for h in heads for c in h.get("codes", [])}
    codes = sorted(set(codes) | mentioned)
    probe = {"date": day.isoformat(), "items": [{"impact": [{"code": c} for c in list(codes) + ["XJO"]]}]}
    try:
        fill_moves([probe], prices=prices, log=log)
    except Exception as e:
        log("收件箱：取行情失败（只存标题）: %s" % e)
    got = {t["code"]: t for t in probe["items"][0]["impact"] if t.get("move_checked")}
    idx = got.pop("XJO", None)
    moves = {c: {"pct": t["move_pct"], "close": t["close"], "halted": t["halted"]} for c, t in sorted(got.items())}
    # 涨跌榜只看股票池（大中盘）：正文里提到的小公司一天涨跌三成很常见，放进来会把真正的大事挤出去
    movers = sorted((c for c in moves if c in bare and not moves[c]["halted"]),
                    key=lambda c: -abs(moves[c]["pct"]))[:15]
    return {
        "schema": INBOX_SCHEMA, "date": day.isoformat(), "collected_at": _now_iso(),
        "headlines": [{k: h[k] for k in ("title", "url", "section", "date", "published", "codes", "hints")
                       if h.get(k) is not None} for h in heads],
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


def read_status(text):
    try:
        return json.loads(text) if text else {}
    except Exception:
        return {}


def write_status(kind, ok, info="", day=None, path=None):
    """GitHub Actions 用：把这一步（collect / intake）的结果记进 news/inbox/_status.json。"""
    path = path or os.path.join(ROOT, STATUS)
    st = {}
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            st = read_status(f.read())
    st[kind] = {"at": _now_iso(), "ok": bool(ok), "info": (info or "")[-600:],
                "date": day or sydney_today().isoformat(), "run": os.environ.get("RUN_URL", "")}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return st


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
    # 不能直接推 HEAD：main 最新的提交常常是每日存档，提交信息里带「跳过 CI」标记，GitHub 会因此
    # 不触发任何 workflow（2026-10-21 实测，请求发出去了却一直没人抓）。所以另做一个空提交再推，
    # 只改分支、不动工作区和 HEAD。
    c = git("-c", "user.name=asx-radar-request", "-c", "user.email=actions@github.com", "commit-tree",
            "HEAD^{tree}", "-p", "HEAD", "-m", "请求抓取 AFR 标题和收盘行情 %s" % day.isoformat())
    ref = (c.stdout or "").strip() if c.returncode == 0 and (c.stdout or "").strip() else "HEAD"
    r = git("push", "origin", "%s:refs/heads/%s" % (ref, branch))
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
        # 抓取失败时 GitHub 会写 _status.json：马上知道原因，不用干等 15 分钟
        st = read_status(git("show", "origin/main:" + STATUS).stdout).get("collect") or {}
        if st.get("at", "") >= asked and st.get("ok") is False:
            if git("show", "origin/main:" + path).returncode == 0:
                log("这次抓取失败（%s），先用当天早些时候抓的收件箱。" % st.get("info", "")[-200:])
                break
            raise RequestError("GitHub 抓取失败：%s %s" % (st.get("info", "")[-400:], st.get("run", "")), 5)
    if git("show", "origin/main:" + path).returncode != 0:
        raise RequestError("等了 %d 分钟，GitHub 还没把今天的 AFR 标题提交回来（去 Actions 看「抓 AFR 标题和收盘行情」"
                           "有没有失败）。" % (wait // 60), 4)
    if git("merge", "--ff-only", "origin/main").returncode != 0:
        git("checkout", "origin/main", "--", path)
    if not fresh:
        log("没等到这次请求的结果，先用当天早些时候定时抓的收件箱。")
    return path


def confirm_brief(day, local_path, wait=600, poll=20, git=_git, sleep=None, clock=None, log=print):
    """推送之后确认简报真的并进了 main（收件流程会再校验一遍，可能退回）。
    返回 None = 上线了；否则返回原因（退回原因或超时）。"""
    import time
    sleep = sleep or time.sleep
    clock = clock or time.time
    with io.open(local_path, encoding="utf-8") as f:
        mine = json.load(f)
    path = "news/briefs/%s.json" % day
    started = _now_iso()
    deadline = clock() + wait
    while clock() < deadline:
        sleep(poll)
        git("fetch", "-q", "origin", "main")
        show = git("show", "origin/main:" + path)
        if show.returncode == 0:
            try:
                theirs = json.loads(show.stdout)
            except Exception:
                theirs = {}
            if (theirs.get("generated_at") == mine.get("generated_at")
                    and theirs.get("stage") == mine.get("stage")):
                return None
            # 标题版推上去时，main 上已经有更新的分析版（用户点了深度分析）：标题版本来就不该覆盖它，算成功
            if stage_of(mine) == "headlines" and stage_of(theirs) == "analysis":
                log("main 上这天已经是分析版，标题版按规则不覆盖。")
                return None
        st = read_status(git("show", "origin/main:" + STATUS).stdout).get("intake") or {}
        if st.get("at", "") >= started and st.get("ok") is False and st.get("date") == day:
            return "收件流程退回了：%s %s" % (st.get("info", ""), st.get("run", ""))
    return "等了 %d 分钟还没在 main 上看到这期简报（去 Actions 看「收 AFR 简报」）" % (wait // 60)


def due_day(now=None):
    """「到现在为止应该已经有简报的那个交易日」：悉尼 19:00 之前看上一个交易日，之后看今天。
    看门狗用它——GitHub 的定时任务常常晚几个小时，跑到第二天凌晨时不能拿「新的一天」去查（10-21 误报过）。"""
    from zoneinfo import ZoneInfo
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo("Australia/Sydney"))
    d = now.date() if now.hour >= 19 else now.date() - dt.timedelta(days=1)
    for _ in range(10):
        if is_trading_day(d):
            return d
        d -= dt.timedelta(days=1)
    return d


def is_trading_day(d):
    try:
        from short_radar.asx_calendar import session
        return session(d) is not None
    except Exception:                       # 日历只核查到 2027 年；之后退回「周一到周五」
        return d.weekday() < 5


def template(stage, day=None, inbox=None, now=None):
    """给云端 Claude 的简报骨架：固定字段和大盘数字都填好了，尖括号里的要换成真内容（不换的话校验会挡下）。"""
    from zoneinfo import ZoneInfo
    day = day or sydney_today()
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo("Australia/Sydney"))
    ix = (inbox or {}).get("index") or {}
    item = {"title_zh": "<中文标题，一句话说清是什么事，≤40 字>",
            "afr_headline": "<AFR 原标题，照抄收件箱>",
            "afr_url": "<AFR 链接，照抄收件箱>",
            "afr_section": "<栏目，例如 Companies / Mining>",
            "what_zh": "<发生了什么：事实 + 关键数字 + 当天股价反应，100–250 字>",
            "calcs": [{"value": "<正文里自己算出来的数字，例如 约 36%>", "expr": "<算式，例如 (1-255/397.5)*100>"}],
            "sources": [{"name": "AFR", "url": "<同 afr_url>"}, {"name": "<来源名>", "url": "<https 链接>"}]}
    if stage == "analysis":
        item["impact"] = [{"name": "<公司名>", "code": "<3 位 ASX 代码>", "direction": "<空/偏空/中性/偏多/多>",
                           "move_pct": None},
                          {"name": "<板块名>", "code": None, "direction": "<空/偏空/中性/偏多/多>"}]
        item["why_zh"] = "<为什么这样判断：怎么一步步影响到股价、主要风险，60–150 字>"
    return {"schema": SCHEMA, "date": day.isoformat(), "stage": stage, "model": "claude-opus-5-5",
            "effort": "high" if stage == "analysis" else "medium",
            "generated_at": now.replace(microsecond=0).isoformat(), "generated_by": "Claude (claude-opus-5-5)",
            "market": {"text_zh": "<一句话今日大盘：ASX 200 收 %s 点，%s，谁拖累谁拉动>" % (
                round(ix["close"], 1) if ix.get("close") else "?",
                ("涨 %.2f%%" % ix["pct"] if ix.get("pct", 0) >= 0 else "跌 %.2f%%" % -ix["pct"]) if ix else "?"),
                "index_close": round(ix["close"], 1) if ix.get("close") else None,
                "index_pct": ix.get("pct")},
            "items": [item]}


def update_source_stats(brief, path=None):
    """数字核对之后记下每个来源网站这次能不能打开，累计成「程序能打开的网站」名单。"""
    import urllib.parse
    path = path or os.path.join(ROOT, SOURCE_STATS)
    stats = {}
    if os.path.exists(path):
        try:
            with io.open(path, encoding="utf-8") as f:
                stats = json.load(f)
        except Exception:
            stats = {}
    for it in brief.get("items") or []:
        v = it.get("verify") or {}
        bad = set(v.get("sources_failed") or [])
        for s_ in it.get("sources") or []:
            host = urllib.parse.urlparse(s_.get("url") or "").netloc.lower().replace("www.", "")
            if not host:
                continue
            row = stats.setdefault(host, {"ok": 0, "fail": 0})
            row["fail" if s_.get("name") in bad else "ok"] += 1
            row["last"] = brief.get("date")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dict(sorted(stats.items())), f, ensure_ascii=False, indent=1)
        f.write("\n")
    return stats


def merge_inbox(old, new):
    """同一天抓多次时合并：标题取并集（AFR 首页会轮换，早上抓到的链接晚上可能就不在了——10-21 的简报
    因此有一条链接被校验挡下）；同一篇两次都有就用新的，新的没读到线索就沿用旧的；行情用新的。"""
    if not old or old.get("date") != new.get("date"):
        return new
    prev = {h["url"]: h for h in old.get("headlines") or []}
    heads = []
    for h in new.get("headlines") or []:
        o = prev.pop(h["url"], None)
        if o and "hints" in o and "hints" not in h:
            h = dict(h, hints=o["hints"], codes=list(dict.fromkeys((h.get("codes") or []) + (o.get("codes") or []))))
        heads.append(h)
    heads += list(prev.values())
    return dict(new, headlines=heads)


def prune_inbox(today, inbox_dir=INBOX, keep_days=INBOX_KEEP_DAYS):
    """收件箱只留最近 keep_days 天，仓库不会越长越大。"""
    if not os.path.isdir(inbox_dir):
        return []
    cut = (today - dt.timedelta(days=keep_days)).isoformat()
    gone = [n for n in os.listdir(inbox_dir) if _FILE.match(n) and n[:10] < cut]
    for n in gone:
        os.remove(os.path.join(inbox_dir, n))
    return gone


def print_inbox(inbox, out=None, everything=False, stats=None):
    """默认只列今天和前一个交易日的标题（周一含周末）——10-22 全列出来有 49KB，云端读起来又慢又贵；
    everything=True 列全部。stats：各来源网站程序能不能打开（数字核对用），帮 Claude 挑能核对的来源。"""
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
    moves = inbox.get("moves") or {}

    def code_txt(h):
        out_ = []
        for c in h.get("codes") or []:
            m = moves.get(c)
            out_.append(c + ("（停牌）" if m and m.get("halted") else (" %+.2f%%" % m["pct"]) if m else ""))
        return ("  [可能涉及 " + "，".join(out_) + "]") if out_ else ""

    heads = inbox.get("headlines") or []
    hidden = 0
    if not everything:
        day = dt.date.fromisoformat(inbox["date"])
        lo = (day - dt.timedelta(days=3 if day.weekday() == 0 else 1)).isoformat()
        keep = [h for h in heads if not h.get("date") or h["date"] >= lo]
        hidden, heads = len(heads) - len(keep), keep
    groups = [("Street Talk（交易内幕：收购、出售、融资、IPO）", [h for h in heads if h.get("section") == "street-talk"]),
              ("其他 AFR 标题", [h for h in heads if h.get("section") != "street-talk"])]
    print("\nAFR 标题（只能从这里选，链接原样照抄；「线索」只是搜别的来源用的关键词，不能直接当事实写）：", file=out)
    for title, group in groups:
        print("\n## %s：%d 条" % (title, len(group)), file=out)
        for h in group:
            print("- [%s] %s | %s%s | %s" % (h.get("date"), h.get("section"), h.get("title"), code_txt(h), h.get("url")),
                  file=out)
            hints = h.get("hints") or {}
            if hints.get("amounts") or hints.get("names"):
                print("    线索：%s%s" % ("数字 " + "、".join(hints["amounts"]) + "；" if hints.get("amounts") else "",
                                       "名字 " + "、".join(hints.get("names") or [])), file=out)
    if hidden:
        print("\n（还有 %d 条更早的标题没列出；要看全部用 inbox --all）" % hidden, file=out)
    print("\n其余股票的涨跌在 JSON 的 moves 里（股票池外的，发布时会自动补）。", file=out)
    if stats:
        rows = sorted(stats.items(), key=lambda kv: (-(kv[1]["ok"] / max(1, kv[1]["ok"] + kv[1]["fail"])),
                                                      -(kv[1]["ok"] + kv[1]["fail"])))
        good = ["%s %d/%d" % (d, v["ok"], v["ok"] + v["fail"]) for d, v in rows if v["ok"] >= v["fail"]][:15]
        bad = ["%s %d/%d" % (d, v["ok"], v["ok"] + v["fail"]) for d, v in rows if v["ok"] < v["fail"]][:15]
        print("\n来源网站（发布前要打开它核对数字；能打开的优先引用）：", file=out)
        print("  程序能打开：" + ("，".join(good) or "暂无记录"), file=out)
        print("  打不开（数字会显示没法核对）：" + ("，".join(bad) or "暂无记录"), file=out)


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
    score = os.path.join(os.path.dirname(briefs_dir), "scorecard.json")
    if os.path.exists(score):                   # 判断成绩单（news-collect.yml 每次抓取时更新）
        try:
            with io.open(score, encoding="utf-8") as f:
                _write(os.path.join(nd, "scorecard.json"), json.load(f))
        except Exception as e:
            log("成绩单读不了（页面不显示）: %s" % e)
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
                    P = check_inbox(b)
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
        for n in prune_inbox(day):
            print("删掉过期收件箱 %s" % n, file=sys.stderr)
        path = os.path.join(INBOX, day.isoformat() + ".json")
        try:
            inbox = merge_inbox(load_inbox(day), inbox)
        except Exception as e:
            print("! 旧收件箱读不了，直接覆盖: %s" % e, file=sys.stderr)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(inbox, f, ensure_ascii=False, indent=1)
            f.write("\n")
        st = sum(h.get("section") == "street-talk" for h in inbox["headlines"])
        print("%s：AFR 标题 %d 条（Street Talk %d 条，带线索 %d 条），行情 %d 只，ASX 200 %s"
              % (path, len(inbox["headlines"]), st, sum("hints" in h for h in inbox["headlines"]),
                 len(inbox["moves"]), inbox["index"]))
        return 0
    if cmd == "due":
        print(due_day().isoformat())
        return 0
    if cmd == "status" and len(a) >= 2:
        # GitHub Actions 用：python -m asxquant.news status collect|intake ok|fail [说明] [日期]
        write_status(a[0], a[1] == "ok", a[2] if len(a) > 2 else "", a[3] if len(a) > 3 else None)
        return 0
    if cmd == "confirm" and a:
        day = a[0]
        why = confirm_brief(day, a[1] if len(a) > 1 else "news/briefs/%s.json" % day,
                            log=lambda m: print(m, file=sys.stderr))
        if why:
            print("没上线：" + why)
            return 6
        print("已上线：main 上已经是这期简报，几分钟后手机上可见")
        return 0
    if cmd == "request":
        try:
            print(request_inbox(log=lambda m: print(m, file=sys.stderr)))
            return 0
        except RequestError as e:
            print("失败：%s" % e, file=sys.stderr)
            return e.code
    if cmd == "inbox":
        everything = "--all" in a
        a = [x for x in a if x != "--all"]
        day = dt.date.fromisoformat(a[0]) if a else sydney_today()
        inbox = load_inbox(day)
        if not inbox:
            print("没有 news/inbox/%s.json，先运行 python -m asxquant.news request" % day, file=sys.stderr)
            return 1
        stats = None
        if os.path.exists(os.path.join(ROOT, SOURCE_STATS)):
            with io.open(os.path.join(ROOT, SOURCE_STATS), encoding="utf-8") as f:
                stats = json.load(f)
        print_inbox(inbox, everything=everything, stats=stats)
        return 0
    if cmd == "template" and a and a[0] in STAGES:
        day = sydney_today()
        print(json.dumps(template(a[0], day, load_inbox(day)), ensure_ascii=False, indent=2))
        return 0
    if cmd == "verify" and a:
        # news-intake.yml 用：打开来源网页逐个核对数字，结果写回简报（verify 字段）。网页打不开不算错。
        from .newscheck import verify_brief
        bad = 0
        for path in a:
            with io.open(path, encoding="utf-8") as f:
                b = json.load(f)
            b, hard = verify_brief(b, lambda u: _get(u, timeout=10), load_inbox(b.get("date")),
                                   lead=lambda u: article_lead(u, get=lambda x: _get(x, timeout=10)),
                                   log=lambda m: print("!", m, file=sys.stderr))
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(b, f, ensure_ascii=False, indent=2)
                f.write("\n")
            try:
                update_source_stats(b)
            except Exception as e:
                print("! 来源统计没更新：%s" % e, file=sys.stderr)
            for it in b["items"]:
                v = it["verify"]
                print("%s：%d 个数字，来源找到 %d，行情对上 %d，自己算的 %d，没找到 %s（打不开的来源：%s）"
                      % (it["title_zh"], v["checked"], len(v["found"]), len(v["market"]), len(v["derived"]),
                         "、".join(v["missing"]) or "无", "、".join(v["sources_failed"]) or "无"))
            for h in hard:
                print("退回：" + h)
            bad += bool(hard)
        return 1 if bad else 0
    if cmd == "score":
        # news-collect.yml 用：给所有分析版的方向判断对账，写 news/scorecard.json
        from .newscheck import scorecard
        briefs = load_briefs(log=lambda m: print("!", m, file=sys.stderr))
        codes = sorted({t["code"] for b in briefs if stage_of(b) == "analysis" for it in b["items"]
                        for t in it.get("impact") or [] if t.get("code")})
        prices = {}
        if codes:
            start = min(dt.date.fromisoformat(b["date"]) for b in briefs) - dt.timedelta(days=10)
            prices = _yahoo(codes + ["XJO"], start, lambda m: print("!", m, file=sys.stderr))
        card = scorecard(briefs, prices)
        _write(os.path.join(os.path.dirname(BRIEFS), "scorecard.json"), card)
        h1, h5 = card["horizons"]["1"], card["horizons"]["5"]
        print("判断成绩：1 天后 %d 次对 %d；5 天后 %d 次对 %d；待到期 %d；%s"
              % (h1["n"], h1["hits"], h5["n"], h5["hits"], card["pending"], card["verdict_zh"]))
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
