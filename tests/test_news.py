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
        r = NW.build_site(out, briefs_dir=src, log=logs.append, fetch=False)
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


def main():
    print("=" * 68)
    print("AFR 要闻回归测试")
    print("=" * 68)
    for fn in (test_validate, test_headlines, test_moves, test_build_site, test_page_wiring):
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
