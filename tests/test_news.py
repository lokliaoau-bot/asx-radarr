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
                     codes=["NAB", "NXT", "BHP"], log=lambda m: None)
    urls = [h["url"] for h in box["headlines"]]
    check("抓到当天的 AFR 标题", any("nab-to-cut" in u for u in urls) and any("gold-miners" in u for u in urls), str(urls))
    check("太旧的文章（三天以前）不进收件箱", not any("old-story" in u for u in urls))
    check("收件箱不存 AFR 摘要正文", all(set(h) == {"title", "url", "section", "date", "published"} for h in box["headlines"]))
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
               test_workflows, test_page_wiring):
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
