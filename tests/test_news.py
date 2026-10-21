# -*- coding: utf-8 -*-
"""AFR 要闻（默认首页）的回归测试 —— 不联网。

    python -m tests.test_news
"""
from __future__ import annotations

import copy
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")

from asxquant import news as NW                      # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("[PASS]" if cond else "[FAIL]", name,
                         ("  -- " + detail) if detail else ""))


AFR_URL = "https://www.afr.com/companies/financial-services/nab-to-cut-2500-jobs-20261020-p616yh"


def sample(date="2026-10-20"):
    return {
        "schema": NW.SCHEMA, "date": date,
        "generated_at": date + "T20:52:00+11:00", "generated_by": "Claude (test)",
        "market": {"text_zh": "ASX 200 收 8816.9 点，跌 0.33%。", "index_close": 8816.9, "index_pct": -0.33},
        "items": [{
            "title_zh": "NAB 裁员 2500 人",
            "afr_headline": "NAB to cut 2500 jobs",
            "afr_url": AFR_URL.replace("20261020", date.replace("-", "")),
            "what_zh": "国民银行宣布到明年 6 月裁掉约 2500 个岗位，约占员工 6%，重组费约 3 亿澳元。",
            "impact": [{"name": "国民银行", "code": "NAB", "direction": "偏空", "move_pct": -9.99},
                       {"name": "银行板块", "code": None, "direction": "偏空"}],
            "why_zh": "银行自己说经济放缓、房价下跌，短线偏空。",
            "sources": [{"name": "AFR", "url": "https://www.afr.com/x"}],
        }],
    }


def test_validate():
    print("\n[N1] 校验：合格的简报通过；新闻不是 AFR 的、方向不在五档里、缺内容的都挡下")
    check("合格的简报通过", NW.validate(sample()) == [], str(NW.validate(sample())))

    def bad(mut):
        b = sample()
        mut(b)
        return NW.validate(b)
    check("新闻链接不是 afr.com → 挡下", bad(lambda b: b["items"][0].update(afr_url="https://www.reuters.com/x")))
    check("方向不在 空/偏空/中性/偏多/多 → 挡下",
          bad(lambda b: b["items"][0]["impact"][0].update(direction="看跌")))
    check("没写「发生了什么」→ 挡下", bad(lambda b: b["items"][0].update(what_zh="")))
    check("没写分析 → 挡下", bad(lambda b: b["items"][0].update(why_zh="")))
    check("股票代码格式不对 → 挡下", bad(lambda b: b["items"][0]["impact"][0].update(code="nab.ax")))
    check("来源不是 https 链接 → 挡下",
          bad(lambda b: b["items"][0]["sources"].append({"name": "x", "url": "javascript:alert(1)"})))
    check("文字里夹 HTML → 挡下", bad(lambda b: b["items"][0].update(title_zh="<img src=x onerror=1>")))
    check("用了旧闻（AFR 发稿日期早于简报 7 天以上）→ 挡下",
          bad(lambda b: b["items"][0].update(afr_url=AFR_URL.replace("20261020", "20261001"))))
    check("同一篇写两次 → 挡下", bad(lambda b: b["items"].append(copy.deepcopy(b["items"][0]))))
    check("超过 10 条 → 挡下", bad(lambda b: b.update(items=b["items"] * 11)))
    check("今天的简报真的通过", NW.validate(json.load(io.open("news/briefs/2026-10-20.json", encoding="utf-8"))) == [])


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>AFR</title>
<item><category>Companies / Financial services</category><title>NAB to cut 2500 jobs</title>
<link>https://www.afr.com/companies/financial-services/nab-to-cut-2500-jobs-20261020-p616yh?x=1</link>
<description>Long standfirst that must not be kept.</description><pubDate>Tue, 20 Oct 2026 08:13:45 GMT</pubDate></item>
<item><title>Haines attack</title><link>https://www.afr.com/politics/federal/haines-20261020-p616xt</link></item>
<item><title>Not AFR</title><link>https://example.com/x-20261020-p616xx</link></item>
</channel></rss>"""

HOME = """<a href="/companies/mining/gold-miners-warn-of-higher-costs-20261020-p6171d"><img></a>
<a class="x" href="/companies/mining/gold-miners-warn-of-higher-costs-20261020-p6171d"><h3>Gold miners warn of higher costs as diesel bites</h3></a>
<a href="/companies/financial-services/nab-to-cut-2500-jobs-20261020-p616yh">NAB to cut 2500 jobs in huge restructuring</a>
<a href="/life-and-luxury/arts/everest-film-20261020-p616qn">Why this Everest film is like nothing else</a>
<a href="/about-us">About us and other things here</a>"""


def test_headlines():
    print("\n[N2] 抓 AFR 标题：RSS 在前、首页补齐；只留市场相关栏目；不保存摘要正文")
    pages = {NW.AFR_RSS: RSS, NW.AFR + "/": HOME}
    h = NW.headlines(get=lambda u: pages[u])
    urls = [x["url"] for x in h]
    check("RSS 里的 NAB 在、链接去掉了参数", urls[0].endswith("nab-to-cut-2500-jobs-20261020-p616yh"), str(urls))
    check("首页补到 RSS 没有的金矿新闻", any("gold-miners" in u for u in urls))
    check("同一篇不重复", len(urls) == len(set(urls)) == 2, str(urls))
    check("政治、生活栏目和外站链接都不选", not any(("politics" in u or "life-and" in u or "example" in u) for u in urls))
    check("不保存摘要正文（版权）", all("description" not in x for x in h))
    check("带上发稿日期和栏目", h[0]["date"] == "2026-10-20" and h[0]["section"] == "companies")
    check("RSS 取不到时首页照样能用",
          len(NW.headlines(get=lambda u: pages[u] if u != NW.AFR_RSS else (_ for _ in ()).throw(OSError("x")))) == 2)


def test_moves():
    print("\n[N3] 涨跌按收盘价重算，覆盖手写数字；停牌单独标；取不到就标「未核对」")
    b = sample()
    b["items"][0]["impact"].append({"name": "NextDC", "code": "NXT", "direction": "偏空"})
    b["items"][0]["impact"].append({"name": "没行情", "code": "ZZZ", "direction": "中性", "move_pct": 1.0})
    prices = {"NAB": [("2026-10-16", 39.32, 1e6), ("2026-10-19", 39.90, 1e6), ("2026-10-20", 39.20, 1e6)],
              "NXT": [("2026-10-19", 9.18, 0.0), ("2026-10-20", 9.18, 0.0)]}
    NW.fill_moves([b], prices=prices, log=lambda m: None)
    nab, sector, nxt, zzz = b["items"][0]["impact"]
    check("NAB：手写的 -9.99 被收盘价算出的 -1.75 覆盖", nab["move_pct"] == -1.75 and nab["move_checked"], str(nab))
    check("板块没有代码，不动", "move_checked" not in sector)
    check("当天成交量为 0 → 停牌", nxt["halted"] is True and nxt["move_checked"])
    check("取不到行情 → 保留原数字、标未核对", zzz["move_pct"] == 1.0 and zzz["move_checked"] is False)


def test_build_site():
    print("\n[N4] 发布：只发通过校验的简报，最新一期放 brief.json，往期留档；不联网也能建")
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "briefs")
        os.makedirs(src)
        for d in ("2026-10-16", "2026-10-19"):
            with io.open(os.path.join(src, d + ".json"), "w", encoding="utf-8") as f:
                json.dump(sample(d), f, ensure_ascii=False)
        broken = sample("2026-10-20")
        broken["items"][0]["impact"][0]["direction"] = "看跌"
        with io.open(os.path.join(src, "2026-10-20.json"), "w", encoding="utf-8") as f:
            json.dump(broken, f, ensure_ascii=False)
        with io.open(os.path.join(src, "2026-10-21.json"), "w", encoding="utf-8") as f:
            json.dump(sample("2026-10-20"), f, ensure_ascii=False)        # 文件名和日期对不上
        logs = []
        out = os.path.join(tmp, "site")
        r = NW.build_site(out, briefs_dir=src, log=logs.append, fetch=False, inbox_dir=os.path.join(tmp, "no-inbox"))
        nd = os.path.join(out, "news")
        latest = json.load(io.open(os.path.join(nd, "brief.json"), encoding="utf-8"))
        idx = json.load(io.open(os.path.join(nd, "index.json"), encoding="utf-8"))["briefs"]
        check("没过校验的两份被跳过，并记了原因", r["briefs"] == 2 and sum("跳过" in x for x in logs) == 2, str(logs))
        check("最新一期 = 最后一份合格的", latest["date"] == "2026-10-19")
        check("往期目录新的在前", [x["date"] for x in idx] == ["2026-10-19", "2026-10-16"])
        check("每期单独一个文件", sorted(os.listdir(os.path.join(nd, "briefs"))) == ["2026-10-16.json", "2026-10-19.json"])
        check("不联网时标题列表为空但文件在（页面不报错）",
              json.load(io.open(os.path.join(nd, "headlines.json"), encoding="utf-8"))["items"] == [])
        check("不联网时不改手写涨跌", latest["items"][0]["impact"][0]["move_pct"] == -9.99)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def headlines_only(date="2026-10-20"):
    b = sample(date)
    b.update(stage="headlines", model="claude-opus-5-5", effort="medium")
    for it in b["items"]:
        it.pop("impact")
        it.pop("why_zh")
    return b


def test_stages():
    print("\n[N6] 两个阶段：每日自动抓标题（不写分析）→ 点按钮补上分析；标题版不能盖掉分析版")
    check("标题版不写影响和分析也能通过", NW.validate(headlines_only()) == [], str(NW.validate(headlines_only())))
    half = headlines_only()
    half["items"][0]["why_zh"] = "只写了一半的分析"
    check("标题版里分析只写一半 → 挡下（要么不写，要么写全）", NW.validate(half))
    a = sample()
    a["stage"] = "analysis"
    del a["items"][0]["impact"]
    check("分析版缺影响 → 挡下", NW.validate(a))
    check("没写 stage 的老简报当作分析版（今天这期照常显示）", NW.stage_of(sample()) == "analysis")
    check("stage 写错 → 挡下", NW.validate(dict(sample(), stage="draft")))
    check("effort 写错 → 挡下", NW.validate(dict(sample(), effort="max")))

    H = json.dumps(headlines_only(), ensure_ascii=False)
    A = json.dumps(dict(sample(), stage="analysis", effort="high"), ensure_ascii=False)
    name = "news/briefs/2026-10-20.json"
    check("新的一天，标题版直接收", NW.accept_brief(name, H, None)[0])
    check("标题版 → 分析版：收（点了深度分析）", NW.accept_brief(name, A, H)[0])
    check("分析版 → 再一次分析版：收（又点了一次）", NW.accept_brief(name, A, A)[0])
    ok, why = NW.accept_brief(name, H, A)
    check("分析版 → 标题版：不收，而且不算错误", not ok and why.startswith("main 上这一天已经有分析版"), why)
    check("文件名和日期对不上：不收", not NW.accept_brief("news/briefs/2026-10-21.json", H, None)[0])
    check("没过校验：不收", not NW.accept_brief(name, H.replace("afr.com", "example.com"), None)[0])
    hb = headlines_only()
    check("标题版没有影响对象，重算涨跌不出错", NW.fill_moves([hb], prices={}, log=lambda m: None) == 0)


class FakeGit:
    """假的 git：记录调用；push 成不成功、第几次 fetch 后 main 上出现收件箱，都由测试指定。"""

    def __init__(self, push_ok=True, appear_after=None, inbox=None):
        self.calls, self.fetches = [], 0
        self.push_ok, self.appear_after, self.inbox = push_ok, appear_after, inbox

    def __call__(self, *args):
        import types
        self.calls.append(args)
        ok = lambda out="": types.SimpleNamespace(returncode=0, stdout=out, stderr="")
        bad = lambda err="": types.SimpleNamespace(returncode=1, stdout="", stderr=err)
        if args[0] == "push":
            return ok() if self.push_ok else bad(
                "remote: Claude doesn't have GitHub access to lokliaoau-bot/asx-radarr\nfatal: 403")
        if "commit-tree" in args:
            return ok("deadbeef1234\n")
        if args[0] == "fetch":
            self.fetches += 1
            return ok()
        if args[0] == "show":
            if self.appear_after is not None and self.fetches >= self.appear_after:
                return ok(json.dumps(self.inbox))
            return bad("does not exist")
        return ok()


def test_inbox():
    print("\n[N7] 收件箱：GitHub 抓 AFR 标题和行情，云端 Claude 只从这里取；编出来的 AFR 链接不收")
    import datetime as dt
    pages = {NW.AFR_RSS: RSS, NW.AFR + "/": HOME.replace(
        "</a>\n<a href=\"/life", "</a>\n<a href=\"/companies/old-story-20261001-p600aa\">An old story from early October here</a>\n<a href=\"/life", 1)}
    prices = {"NAB": [("2026-10-19", 39.90, 1e6), ("2026-10-20", 39.20, 1e6)],
              "NXT": [("2026-10-19", 9.18, 0.0), ("2026-10-20", 9.18, 0.0)],
              "BHP": [("2026-10-19", 60.46, 1e6), ("2026-10-20", 59.50, 1e6)],
              "XJO": [("2026-10-19", 8845.7, 0.0), ("2026-10-20", 8816.9, 0.0)]}
    box = NW.collect(dt.date(2026, 10, 20), get=lambda u: pages[u], prices=prices,
                     codes=["NAB", "NXT", "BHP"], log=lambda m: None, sections=(), sleep=lambda s: None)
    urls = [h["url"] for h in box["headlines"]]
    check("抓到当天的 AFR 标题", any("nab-to-cut" in u for u in urls) and any("gold-miners" in u for u in urls), str(urls))
    check("太旧的文章（三天以前）不进收件箱", not any("old-story" in u for u in urls))
    check("收件箱不存 AFR 摘要正文", all(set(h) <= {"title", "url", "section", "date", "published", "codes", "hints"}
                                    for h in box["headlines"]))
    check("指数收盘和涨跌（指数没有成交量，不算停牌）",
          box["index"] == {"code": "XJO", "pct": -0.33, "close": 8816.9}, str(box["index"]))
    check("个股涨跌按收盘价算", box["moves"]["NAB"]["pct"] == -1.75 and box["moves"]["NAB"]["close"] == 39.2)
    check("涨跌最大的排前面，停牌的不算", box["top_movers"] == ["NAB", "BHP"], str(box["top_movers"]))

    tmp = tempfile.mkdtemp()
    try:
        with io.open(os.path.join(tmp, "2026-10-20.json"), "w", encoding="utf-8") as f:
            json.dump(box, f, ensure_ascii=False)
        b = sample()
        b["items"][0]["afr_url"] = "https://www.afr.com/companies/financial-services/nab-to-cut-2500-jobs-20261020-p616yh"
        check("链接在收件箱里 → 通过", NW.check_urls(b, tmp) == [], str(NW.check_urls(b, tmp)))
        b2 = sample()
        b2["items"][0]["afr_url"] = "https://www.afr.com/companies/made-up-story-20261020-p699zz"
        check("编出来的 AFR 链接 → 挡下", NW.check_urls(b2, tmp))
        ok, why = NW.accept_brief("news/briefs/2026-10-20.json", json.dumps(b2, ensure_ascii=False), None, tmp)
        check("收件流程同样挡下编出来的链接", not ok and "收件箱" in why, why)
        check("当天没有收件箱的老简报照常通过（兼容手写的）", NW.check_urls(sample("2026-10-19"), tmp) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    day = dt.date(2026, 10, 20)
    g = FakeGit(push_ok=False)
    try:
        NW.request_inbox(day, git=g, sleep=lambda s: None, log=lambda m: None)
        code = None
    except NW.RequestError as e:
        code, msg = e.code, str(e)
    check("没装 Claude GitHub App（推不上）→ 立刻报错，退出码 3，并说明怎么修",
          code == 3 and "github.com/apps/claude" in msg)
    fresh = dict(box, collected_at="2099-01-01T00:00:00+00:00")
    g = FakeGit(appear_after=2, inbox=fresh)
    path = NW.request_inbox(day, git=g, sleep=lambda s: None, log=lambda m: None)
    pushed = [c for c in g.calls if c[0] == "push"]
    check("先推一个 claude/news-request-* 分支请 GitHub 去抓",
          len(pushed) == 1 and pushed[0][2].startswith("deadbeef1234:refs/heads/claude/news-request-2026-10-20-"), str(pushed))
    made = [c for c in g.calls if "commit-tree" in c]
    msg = made[0][made[0].index("-m") + 1] if made else ""
    check("推的是新做的空提交，不是 main 最新那个（存档提交带跳过 CI 标记，GitHub 会不触发）",
          len(made) == 1 and "skip" not in msg.lower() and "HEAD^{tree}" in made[0], str(made))
    check("等到 GitHub 提交了新的收件箱就拉下来", path == "news/inbox/2026-10-20.json" and g.fetches == 2
          and ("merge", "--ff-only", "origin/main") in g.calls)
    t = [0]

    def clock():
        t[0] += 30
        return t[0]
    g = FakeGit(appear_after=None)
    try:
        NW.request_inbox(day, git=g, sleep=lambda s: None, clock=clock, wait=300, log=lambda m: None)
        code = None
    except NW.RequestError as e:
        code = e.code
    check("等超时、当天一份都没有 → 退出码 4，不硬写", code == 4)
    stale = dict(box, collected_at="2000-01-01T00:00:00+00:00")
    t[0] = 0
    g = FakeGit(appear_after=0, inbox=stale)
    check("等超时但当天早些时候定时抓过 → 用那一份",
          NW.request_inbox(day, git=g, sleep=lambda s: None, clock=clock, wait=300, log=lambda m: None)
          == "news/inbox/2026-10-20.json")


ASX_CSV = '''ASX listed companies as at Wed Oct 21 20:03:20 AEDT 2026

Company name,ASX code,GICS industry group
"NEXTDC LIMITED","NXT","Software & Services"
"NORTHERN STAR RESOURCES LIMITED","NST","Materials"
"STAR ENTERTAINMENT GROUP LIMITED (THE)","SGR","Consumer Services"
"NEW ZEALAND KING SALMON INVESTMENTS LIMITED","NZK","Food, Beverage & Tobacco"
"COOPER METALS LIMITED","CPM","Materials"
"EQT HOLDINGS LIMITED","EQT","Financial Services"
"ASX LIMITED","ASX","Financial Services"
"BLACKSTONE MINERALS LIMITED","BSX","Materials"
"PEET LIMITED","PPC","Real Estate Management & Development"
"INGENIA COMMUNITIES GROUP","INA","Equity Real Estate Investment Trusts (REITs)"
"NATIONAL AUSTRALIA BANK LIMITED","NAB","Banks"
"VANGUARD AUSTRALIAN SHARES INDEX ETF","VAS","Not Applic"
'''

ST_PAGE = '''<a href="/street-talk/eqt-set-to-sweep-cleanaway-off-the-asx-20261021-p617aa">EQT set to sweep Cleanaway off the ASX with $9.4b bid</a>
<a href="/street-talk/old-deal-from-last-week-20261012-p615aa">An old Street Talk deal from last week here</a>'''

ARTICLE = '''<html><article><p>Sarah ThompsonSenior reporterOct 21, 2026 – 10.37am, first published at 9.02amIngenia Communities
has agreed a $1.2 billion deal with Warburg Pincus, advised by Macquarie Capital, while Peet shareholders wait.</p>
<p>The Sydney-based group said the 49.9 per cent stake would be sold on Wednesday, according to Milan Cooper.</p>
<p>Ingenia CommunitiesPeet LimitedMore from Latest In Financial services Fetching latest articles</p></article></html>'''


def test_more_news():
    print("\n[N9] 抓更多新闻：栏目页（含 Street Talk）、公司代码、文章线索、同日合并")
    import datetime as dt
    idx = NW.parse_asx_list(ASX_CSV)
    bare = {"NAB", "EQT"}
    d = lambda t, **k: NW.detect_codes(t, idx, bare=bare, **k)
    check("NextDC → NXT", d("NextDC slashes earnings forecast") == ["NXT"])
    check("「Northern Star’s」认成 NST，不会拆出 Star（SGR）", d("Northern Star’s costs jump") == ["NST"])
    check("「New Zealand」是地名，不当成 New Zealand King Salmon", d("Genesis snaps up New Zealand’s Harbour") == [])
    check("「EQT」「ASX」「Blackstone」在新闻里多半指海外机构/大盘，不认",
          d("EQT set to sweep Cleanaway off the ASX") == [] and d("Blackstone lobs bid") == [])
    check("「NAB」这种大写代码只认股票池里的", d("NAB’s job cuts") == ["NAB"]
          and NW.detect_codes("NAB’s job cuts", idx, bare=set()) == [])
    check("正文里的人名不撞公司首词（Milan Cooper ≠ Cooper Metals），单词全名照认（Peet）",
          d("founded by Milan Cooper, while Peet waits", first_word=False) == ["PPC"])
    check("ETF 等「Not Applic」不进清单", ("vanguard",) not in idx)

    lead = NW.article_lead("x", get=lambda u: ARTICLE)["paragraphs"]
    check("文章开头去掉标签云和「最新文章」栏", len(lead) == 2 and not any("Fetching" in p for p in lead), str(lead))
    h = NW.lead_hints(lead, idx, bare)
    check("线索：数字", h["amounts"][:2] == ["$1.2 billion", "49.9 per cent"], str(h["amounts"]))
    check("线索：名字（去掉署名、星期、Sydney-based 这类修饰）",
          "Warburg Pincus" in h["names"] and "Macquarie Capital" in h["names"]
          and not any(("Thompson" in n or n == "Wednesday" or "based" in n) for n in h["names"]), str(h["names"]))
    check("线索：正文里认出的公司", h["codes"] == ["INA", "PPC"], str(h["codes"]))
    check("线索里没有整句（不转载原文）", all(len(n) <= 60 for n in h["names"]))

    pages = {NW.AFR_RSS: RSS, NW.AFR + "/": HOME, NW.AFR + "/street-talk": ST_PAGE, NW.ASX_LIST: ASX_CSV}
    slept = []

    def get(u):
        if u in pages:
            return pages[u]
        if "eqt-set-to-sweep" in u or "nab-to-cut" in u:
            return ARTICLE
        raise OSError("404")
    prices = {"PPC": [("2026-10-20", 1.55, 1e5), ("2026-10-21", 1.44, 1e5)],
              "INA": [("2026-10-20", 4.24, 1e5), ("2026-10-21", 4.65, 1e5)]}
    box = NW.collect(dt.date(2026, 10, 21), get=get, prices=prices, codes=["NAB"], log=lambda m: None,
                     sections=("/street-talk",), sleep=slept.append)
    st = [x for x in box["headlines"] if x["section"] == "street-talk"]
    check("Street Talk 栏目页的标题抓到了，上周的旧闻不要", len(st) == 1 and "eqt-set-to-sweep" in st[0]["url"], str(st))
    check("近两天的文章带线索；不存任何正文段落",
          "hints" in st[0] and all("paragraphs" not in x and "lead" not in x for x in box["headlines"]))
    check("正文里提到的股票池外公司（PPC、INA）也取了涨跌",
          box["moves"].get("PPC", {}).get("pct") == -7.1 and "INA" in box["moves"], str(box["moves"]))
    check("读文章之间有停顿（别把 AFR 刷太快）", slept and all(s == 0.3 for s in slept))

    old = {"date": "2026-10-21", "headlines": [{"url": "u1", "title": "早上的", "hints": {"amounts": ["$1"]},
                                               "codes": ["NXT"]}, {"url": "u2", "title": "只在早上出现"}]}
    new = {"date": "2026-10-21", "headlines": [{"url": "u1", "title": "晚上的", "codes": []},
                                               {"url": "u3", "title": "晚上新出现"}], "moves": {"X": 1}}
    m = NW.merge_inbox(old, new)
    urls = [x["url"] for x in m["headlines"]]
    check("同一天抓两次：标题取并集（首页轮换掉的早上链接还在）", urls == ["u1", "u3", "u2"], str(urls))
    check("同一篇新的没读到线索就沿用旧的；行情用新的",
          m["headlines"][0]["hints"] == {"amounts": ["$1"]} and m["headlines"][0]["codes"] == ["NXT"] and m["moves"] == {"X": 1})
    check("换了一天就不合并", NW.merge_inbox(old, dict(new, date="2026-10-22")) == dict(new, date="2026-10-22"))

    tmp = tempfile.mkdtemp()
    try:
        for n in ("2026-09-01.json", "2026-10-20.json", "notes.txt"):
            io.open(os.path.join(tmp, n), "w").close()
        gone = NW.prune_inbox(dt.date(2026, 10, 21), tmp)
        check("收件箱只留 30 天", gone == ["2026-09-01.json"] and sorted(os.listdir(tmp)) == ["2026-10-20.json", "notes.txt"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    buf = io.StringIO()
    NW.print_inbox(box, out=buf)
    txt = buf.getvalue()
    check("看收件箱时 Street Talk 单独一组排在前面", txt.index("## Street Talk") < txt.index("## 其他 AFR 标题"))
    js = io.open("web/news.js", encoding="utf-8").read()
    check("页面卡片标出 Street Talk 等专栏", '"street-talk": "Street Talk"' in js and "afrLabel(it.afr_url)" in js)


def test_round2():
    print("\n[N10] 第二轮：Street Talk 优先读、周一补周末、涨跌榜不被小公司挤占")
    import datetime as dt
    rss = RSS.replace("nab-to-cut-2500-jobs-20261020-p616yh", "nab-to-cut-2500-jobs-20261026-p616yh")
    st = ('<a href="/street-talk/weekend-deal-for-ingenia-20261024-p618aa">Weekend Street Talk deal for Ingenia here</a>'
          '<a href="/street-talk/older-deal-20261021-p618bb">Older Street Talk deal from Wednesday here</a>')
    pages = {NW.AFR_RSS: rss, NW.AFR + "/": "", NW.AFR + "/street-talk": st, NW.ASX_LIST: ASX_CSV}
    read = []

    def get(u):
        if u in pages:
            return pages[u]
        read.append(u)
        return ARTICLE
    old_limit = NW.LEAD_LIMIT
    try:
        NW.LEAD_LIMIT = 1
        box = NW.collect(dt.date(2026, 10, 26), get=get, prices={}, codes=["NAB"], log=lambda m: None,
                         sections=("/street-talk",), sleep=lambda s: None)
    finally:
        NW.LEAD_LIMIT = old_limit
    check("篇数有限时先读 Street Talk（RSS 里的公司新闻排在后面）",
          len(read) == 1 and "/street-talk/" in read[0], str(read))
    check("周一：周六的 Street Talk 也算「近两天」，会读线索",
          any("weekend-deal" in h["url"] and "hints" in h for h in box["headlines"]))
    read.clear()
    NW.collect(dt.date(2026, 10, 28), get=get, prices={}, codes=["NAB"], log=lambda m: None,
               sections=("/street-talk",), sleep=lambda s: None)
    check("平日：三天前的文章不读（只读今天和昨天）", not any("weekend-deal" in u or "older-deal" in u for u in read), str(read))

    prices = {"NAB": [("2026-10-20", 39.9, 1e6), ("2026-10-21", 39.2, 1e6)],
              "PPC": [("2026-10-20", 1.55, 1e5), ("2026-10-21", 1.10, 1e5)]}
    pages2 = {NW.AFR_RSS: RSS, NW.AFR + "/": "", NW.ASX_LIST: ASX_CSV}
    box = NW.collect(dt.date(2026, 10, 21), get=lambda u: pages2.get(u, ARTICLE), prices=prices, codes=["NAB"],
                     log=lambda m: None, sections=(), sleep=lambda s: None)
    check("正文提到的小公司照样有涨跌，但不进涨跌榜",
          "PPC" in box["moves"] and box["top_movers"] == ["NAB"], str(box["top_movers"]))
    col = io.open(".github/workflows/news-collect.yml", encoding="utf-8").read()
    check("抓取失败时请求分支也会被删掉（单独一步，always）", "if: always()" in col and "--delete" in col)


def test_number_check():
    print("\n[N11] 数字核对：中英文数字对得上；自己算的写算式；算错和大盘不对直接退回")
    from asxquant import newscheck as K
    t = ("NextDC 收 5.35 澳元，跌 41.7%，约是 2018 年初以来最低，市值蒸发约 30 亿澳元。明年 2 月前不会复工，"
         "推迟到 FY28（2027/28 财年）。指引从 3.85–4.10 亿砍到 2.45–2.65 亿，按中值少约 36%。裁掉约 2500 个岗位，"
         "卖出约 36.4 万盎司黄金，400.7 千吨，3 个原因。")
    raws = [n["raw"] for n in K.zh_numbers(t)]
    check("年份、月份、财年、2027/28、小整数都不当成要核对的数字",
          not any(r in ("2018", "2", "28", "27", "2027", "3") for r in raws), str(raws))
    check("区间和单位：3.85–4.10 亿 = 3.85 亿和 4.10 亿；36.4 万；400.7 千",
          [n["value"] for n in K.zh_numbers("3.85–4.10 亿")] == [3.85e8, 4.10e8]
          and K.zh_numbers("36.4 万")[0]["value"] == 364000 and K.zh_numbers("400.7 千吨")[0]["value"] == 400700)
    en = K.en_numbers("EBITDA $245–265 million; shares fell 41.7 per cent to $5.35; 400.7kt; 2,500 jobs; A$3 billion")
    check("英文：区间单位对两头都算、per cent、kt、逗号、billion",
          all(x in en for x in [("num", 245e6), ("num", 265e6), ("pct", 41.7), ("num", 5.35), ("num", 400700.0),
                                ("num", 2500.0), ("num", 3e9)]), str(en))
    zh = {n["raw"]: n for n in K.zh_numbers(t)}
    check("中文 2.45 亿 ↔ 英文 $245 million；约 30 亿 ↔ A$3 billion；41.7% ↔ 41.7 per cent",
          K._match(K.zh_numbers("2.45 亿")[0], en) and K._match(zh["约 30 亿"], en) and K._match(zh["41.7%"], en))
    check("四舍五入算对得上（42% ≈ 41.7 per cent），差得远的不算（45%）",
          K._match(K.zh_numbers("42%")[0], en) and not K._match(K.zh_numbers("45%")[0], en))

    ok = {"calcs": [{"value": "约 36%", "expr": "(1-255/397.5)*100"}, {"value": "2.55 亿", "expr": "(2.45+2.65)/2"}]}
    check("算式算得对 → 通过", K.check_calcs(ok) == [], str(K.check_calcs(ok)))
    check("算式算错 → 退回", K.check_calcs({"calcs": [{"value": "40%", "expr": "(1-255/397.5)*100"}]}))
    check("算式里只许数字和加减乘除（不执行任何代码）",
          K.check_calcs({"calcs": [{"value": "36%", "expr": "__import__('os').system('x')"}]}))
    b = sample()
    b["items"][0]["calcs"] = [{"value": "40%", "expr": "1+1"}]
    check("校验（news check）就会挡下算错的简报", any("算式" in p for p in NW.validate(b)))

    inbox = {"date": "2026-10-20", "index": {"code": "XJO", "pct": -0.33, "close": 8816.9}, "moves": {}, "headlines": []}
    check("大盘收盘和涨跌对得上 → 通过", K.check_market(sample(), inbox) == [])
    bad = sample()
    bad["market"].update(index_close=8900.0, index_pct=0.5)
    check("大盘收盘或涨跌和行情对不上 → 退回", len(K.check_market(bad, inbox)) == 2)
    check("收件箱不是同一天的不比", K.check_market(bad, dict(inbox, date="2026-10-19")) == [])

    item = {"title_zh": "NAB 裁员", "afr_headline": "NAB to cut 2500 jobs",
            "afr_url": "https://www.afr.com/companies/x-20261020-p616yh",
            "what_zh": "国民银行裁掉约 2500 个岗位，约占员工 6%，重组费约 3 亿澳元，股价收跌 1.75%，按中值少约 36%，还有 999 个谜。",
            "calcs": [{"value": "约 36%", "expr": "(1-255/397.5)*100"}],
            "impact": [{"name": "国民银行", "code": "NAB", "direction": "偏空"}],
            "sources": [{"name": "AFR", "url": "https://www.afr.com/companies/x-20261020-p616yh"},
                        {"name": "ABC", "url": "https://abc.example/nab"},
                        {"name": "Reuters", "url": "https://reuters.example/nab"}]}
    br = {"date": "2026-10-20", "items": [item], "market": {"text_zh": "x", "index_close": 8816.9, "index_pct": -0.33}}
    pages = {"https://abc.example/nab": "<article><p>NAB will take a $300 million charge, about 6 per cent of staff.</p></article>"}

    def get(u):
        if u in pages:
            return pages[u]
        raise OSError("403")
    lead = lambda u: {"paragraphs": ["National Australia Bank will slash 2500 jobs."]}
    box = dict(inbox, moves={"NAB": {"pct": -1.75, "close": 39.2, "halted": False}})
    out, hard = K.verify_brief(br, get, box, lead=lead)
    v = out["items"][0]["verify"]
    check("来源网页里找到（2500 来自 AFR 公开开头，6% 和 3 亿来自 ABC）",
          set(v["found"]) == {"约 2500", "6%", "约 3 亿"}, str(v))
    check("股价涨跌对上行情", v["market"] == ["1.75%"], str(v["market"]))
    check("自己算的数字按算式核对", v["derived"] == ["约 36%"])
    check("哪都找不到的标成未核实", v["missing"] == ["999"], str(v["missing"]))
    check("打不开的来源单独记下（不算错）", v["sources_failed"] == ["Reuters"] and hard == [])
    check("整份简报记下核对时间和总数", out["verify"]["checked"] == 6 and out["verify"]["missing"] == 1)
    js = io.open("web/news.js", encoding="utf-8").read()
    check("页面卡片显示数字核对结果", "verifyLine(it.verify)" in js and "未核实" in js)
    intake = io.open(".github/workflows/news-intake.yml", encoding="utf-8").read()
    check("收简报时先核对数字再并入 main", intake.index("news verify") < intake.index("git add $FILES"))


def test_scorecard():
    print("\n[N12] 判断成绩单：按之后的实际涨跌（扣大盘）对账，样本少就说看不出")
    from asxquant import newscheck as K
    days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27",
            "2026-10-28"]
    check("收盘后写的：从当天收盘算起", K.base_day("2026-10-20T20:52:00+11:00", "2026-10-20", days) == "2026-10-20")
    check("盘中写的：从当天收盘算起（不占早盘已知涨跌的便宜）",
          K.base_day("2026-10-21T11:30:00+11:00", "2026-10-21", days) == "2026-10-21")
    check("开盘前写的：从前一天收盘算起", K.base_day("2026-10-21T08:00:00+11:00", "2026-10-21", days) == "2026-10-20")
    check("周末写的：从周五收盘算起", K.base_day("2026-10-24T15:00:00+11:00", "2026-10-24", days) == "2026-10-23")

    flat = [(d, 100.0, 1.0) for d in days]
    down = [(d, 10.0 - i * 0.5, 1e5) for i, d in enumerate(days)]
    up = [(d, 10.0 + i * 0.5, 1e5) for i, d in enumerate(days)]
    prices = {"XJO": flat, "AAA": down, "BBB": up, "CCC": down}

    def brief(date, gen, stage, impacts):
        return {"date": date, "generated_at": gen, "stage": stage,
                "items": [{"title_zh": "t", "impact": impacts}]}
    briefs = [
        brief("2026-10-20", "2026-10-20T20:00:00+11:00", "analysis",
              [{"name": "A", "code": "AAA", "direction": "偏空"}, {"name": "B", "code": "BBB", "direction": "偏空"},
               {"name": "C", "code": "CCC", "direction": "中性"}, {"name": "板块", "code": None, "direction": "偏空"}]),
        brief("2026-10-27", "2026-10-27T20:00:00+11:00", "analysis", [{"name": "A", "code": "AAA", "direction": "空"}]),
        brief("2026-10-28", "2026-10-28T20:00:00+11:00", "analysis", [{"name": "B", "code": "BBB", "direction": "多"}]),
        brief("2026-10-21", "2026-10-21T20:00:00+11:00", "headlines", [{"name": "A", "code": "AAA", "direction": "多"}]),
    ]
    card = K.scorecard(briefs, prices)
    h1, h5 = card["horizons"]["1"], card["horizons"]["5"]
    check("判空之后跑输大盘算对，判空之后跑赢算错", h1["n"] == 3 and h1["hits"] == 2, str(h1))
    check("5 天后对账：只有到期的才算", h5["n"] == 2 and h5["hits"] == 1, str(h5))
    check("「中性」单独计数、板块（没代码）和标题版不算", card["neutral"] == 1 and len(card["recent"]) == 3)
    check("还没到期的记为待到期", card["pending"] == 1, str(card["pending"]))
    check("样本少于 20 次：明说看不出准不准", "看不出" in card["verdict_zh"])
    check("最近判错的例子列出来（给 Claude 下次参考）", any(r["code"] == "BBB" for r in card["misses"]))
    check("瞎猜的概率：20 次对 14 次约 0.058，20 次对 10 次约 0.59",
          abs(K.binom_p(14, 20) - 0.0577) < 0.001 and abs(K.binom_p(10, 20) - 0.588) < 0.001)
    many = [brief(d, d + "T20:00:00+11:00", "analysis", [{"name": "A", "code": "AAA", "direction": "偏空"}])
            for d in days[:-1] for _ in range(4)]
    check("样本够多而且明显比瞎猜准时这么说", "明显比瞎猜准" in K.scorecard(many, prices)["verdict_zh"])

    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "news", "briefs")
        os.makedirs(src)
        with io.open(os.path.join(src, "2026-10-20.json"), "w", encoding="utf-8") as f:
            json.dump(sample(), f, ensure_ascii=False)
        with io.open(os.path.join(tmp, "news", "scorecard.json"), "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False)
        NW.build_site(os.path.join(tmp, "site"), briefs_dir=src, log=lambda m: None, fetch=False,
                      inbox_dir=os.path.join(tmp, "none"))
        check("发布时把成绩单放进站点", os.path.exists(os.path.join(tmp, "site", "news", "scorecard.json")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    js = io.open("web/news.js", encoding="utf-8").read()
    col = io.open(".github/workflows/news-collect.yml", encoding="utf-8").read()
    check("页面底部有成绩单，每次抓收件箱时更新", "scoreBlock()" in js and "news/scorecard.json" in js
          and "news score" in col and "news/scorecard.json" in col)


def test_workflows():
    print("\n[N8] GitHub 上的三道保险：抓标题、收简报、看门狗")
    col = io.open(".github/workflows/news-collect.yml", encoding="utf-8").read()
    intake = io.open(".github/workflows/news-intake.yml", encoding="utf-8").read()
    dog = io.open(".github/workflows/news-watchdog.yml", encoding="utf-8").read()
    check("推 claude/news-request-* 分支就会触发抓取", "claude/news-request-**" in col and "news collect" in col)
    check("抓取也有定时兜底和手动触发", "schedule:" in col and "workflow_dispatch" in col)
    check("收简报的流程不理请求分支", "'!claude/news-request-**'" in intake)
    check("收简报用 accept（校验 + 链接核对 + 不让标题版盖掉分析版）", "news accept" in intake)
    check("看门狗：交易日晚上没有简报就开 issue 提醒", "news/briefs/$DAY.json" in dog and "gh issue create" in dog)
    for name in ("afr-headlines", "afr-analysis"):
        md = io.open(".claude/agents/%s.md" % name, encoding="utf-8").read()
        check("子代理 %s 先请 GitHub 抓标题、只从收件箱选" % name,
              "news request" in md and "news inbox" in md and "原样照抄" in md)


def test_page_wiring():
    print("\n[N5] 页面：默认 AFR 要闻，原来的分析一行没删、收在「多角度分析」里")
    html = io.open("web/index.html", encoding="utf-8").read()
    css = io.open("web/style.css", encoding="utf-8").read()
    js = io.open("web/news.js", encoding="utf-8").read()
    sw = io.open("web/sw.js", encoding="utf-8").read()
    from asxquant import publish as PB
    check("默认是要闻模式", 'class="mode-brief"' in html)
    check("有「多角度分析」按钮", 'id="btnMulti">多角度分析<' in html)
    check("原来的整页（#app、更新按钮、看空雷达入口）都还在",
          all(x in html for x in ('id="app"', 'id="btnRefresh"', 'id="lnkShort"', 'src="app.js"')))
    check("要闻模式把原来的整页藏起来，多角度模式把要闻藏起来",
          ".mode-brief #app" in css and ".mode-multi #brief" in css)
    check("news.js 先于 app.js 加载（app.js 出错也不影响要闻）", html.index("news.js") < html.index("app.js"))
    check("news.js 会发布、也进离线缓存", "news.js" in PB.ASSETS and "./news.js" in sw)
    check("页面上所有新闻文字都经过转义", js.count("esc(it.") >= 4 and "safeUrl(" in js)
    check("五档方向都有颜色", all(d in js for d in NW.DIRECTIONS))
    check("有「深度分析」按钮，指向 claude.ai 上的云端任务页",
          "Claude 深度分析 · Opus 5.5（高）" in js and 'DEEP_URL = "https://claude.ai/code/routines/' in js)
    check("标题版（还没分析）的卡片不画影响框", "it.impact && it.impact.length" in js)


def main():
    print("=" * 68)
    print("AFR 要闻回归测试")
    print("=" * 68)
    for fn in (test_validate, test_headlines, test_moves, test_build_site, test_stages, test_inbox,
               test_more_news, test_round2, test_number_check, test_scorecard, test_workflows, test_page_wiring):
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
