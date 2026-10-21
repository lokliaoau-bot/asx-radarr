# AGENTS.md — 给在这个仓库里改代码的 AI 编程助手（ChatGPT / Codex / Claude Code）

这是用户「澳股雷达」的代码，每天自动更新并部署到用户的手机：
https://lokliaoau-bot.github.io/asx-radarr/ （主页） · /short/ （看空雷达，ChatGPT 最初写的那个 App）

**主页默认显示「AFR 要闻 · Claude 解读」**（2026-10-20 用户要求：原来的界面太复杂）。原来的整套分析
一行没删，收在右上角「多角度分析」按钮里（`web/news.js` 切换，`#app` 照常由 `web/app.js` 渲染）。
不要把复杂内容加回默认页面；要加就加在「多角度分析」里。

## 开工前必读（按顺序）
1. https://lokliaoau-bot.github.io/asx-radarr/self-review.md —— 系统每天自动写的自我复盘和**未处理的改进提案**
2. `交接文档.md` 的五点五～五点十节（最新进展、所有冻结规则的来历）
3. 本文件

## 交付方式（硬性）
- **不要直接推送到 `main`。** `main` 一有新提交就会自动部署到用户手机。
  一律新建分支、开 Pull Request；PR 会自动跑全部测试（`.github/workflows/tests.yml`），全绿后由用户合并。
- 一个假设一个 PR，越小越好。不要夹带锁文件、证据 JSON、运行日志、大段文档。
- 提交信息里**不要原样写出 GitHub 的「跳过 CI」方括号标记**（写了部署和测试都不会触发）。
- 公开仓库：不放任何账号、密码、持仓或个人信息。

## 本地验证（全部离线、合成数据，约 3–5 分钟）
```bash
pip install -r requirements.txt
for t in test_step1 test_shortradar test_review test_round3 test_selfreview test_calibrate test_journal test_shortradar_review test_news; do python -m tests.$t || exit 1; done
```
Windows：所有文本读写写明 `encoding="utf-8"`；改 JS 文件不要用「heredoc 套 Python 字符串」，转义会写坏。

## 冻结的东西（要改必须先问用户，并且只能对**以后**的数据生效）
- 判决统计量：`validate._overlap_stats` 的 `t_stat` 是固定第 0 起点的不重叠 t。**不要改回 Newey-West**
  （`tools/experiments/fpr_check.py`：慢变评分下 NW 误报 7–13%）。
- `asxquant/calibrate.py` 顶部的登记常数（REGISTERED / HORIZON / MIN_SEGMENTS / ALPHA / BACKTEST_HAIRCUT /
  CHALLENGERS / SWITCHABLE）和 `asxquant/journal.py` 顶部的阈值。
- `tools/eval_short_radar.py` 的 `EVAL_END = 2026-02-27`（之后是保留期）。
- technical-v1（看空雷达）的公式和权重；`picks.py` 的权重（改了必须同步 `validate.py`）。
- 存档 `cache/announcements.db`：不删行、不重写；前瞻相关的表一律**以第一次记录为准**（INSERT OR IGNORE）。
  新表要经得起 `tools/merge_archive.py` 合并。

## 规矩
- 主页是给**不懂金融**的用户看的：结论放最前面、用大白话；结论跟着检验结果自动生成，不要写死。
- 任何上主页的数字都要附检验（t、p、独立段数）；没检验过的不要上。
- 不会因为最近几次成绩去改评分或权重——信号很弱（`tools/experiments/power_check.py`：真实有效时
  60 段也只有约 30% 机会测出）。要改，按 self-review.md 里提案写的检验方法做预先登记的检验。
- 改 `web/app.js` 或 report.json 字段时，`web/sw.js` 的 `CACHE` 版本号加一。
- 看空雷达的 ASX 日历只核查到 2027 年；提前收市日的收盘时段在 `asxquant/calendar_overrides/`
  （来源：ASX 官网交易日历页）。**2027 年底前**要按官网补 2028 年（`short_radar/asx_calendar.py` 的 HOLIDAYS
  + 当年提前收市日的补充说明）。系统会提前 120 天在 self-review.md 里报警。
- 不登录券商、不下单、不付费、不新建网站。

## AFR 要闻（`news/`）
- 简报写法和步骤全在 `news/README.md`；格式和校验在 `asxquant/news.py`（`validate()`）。
- 新闻只从 AFR 选；AFR 正文被订阅墙挡住时去其他权威来源补全，每条列来源。影响方向只有
  空 / 偏空 / 中性 / 偏多 / 多 五档。涨跌幅发布时按收盘价重算（`fill_moves`），不信手写数字。
- 两个阶段（用户 2026-10-20 指定模型和强度）：每个交易日悉尼 18:00 云端任务「AFR 每日抓标题」调用子代理
  `.claude/agents/afr-headlines.md`（Opus 5.5，effort medium）只写标题和内容（`stage: headlines`）；用户点页面上的
  「深度分析」按钮 → 云端任务「AFR 深度分析」调用 `.claude/agents/afr-analysis.md`（Opus 5.5，effort high）补上
  影响和分析（`stage: analysis`）。云端任务本身设不了思考强度，所以强度写在子代理定义里——**不要改这两个文件的
  `model` / `effort`**，除非用户要求。同一天已有分析版时，标题版不会覆盖它（`news.accept_brief`）。
- **云端 Claude 的沙箱连不上 afr.com 和行情网站**（2026-10-21 实测；AFR 还屏蔽 Anthropic 的抓取）。联网抓取全部在
  GitHub Actions 做：`.github/workflows/news-collect.yml` 把 AFR 标题和收盘行情写进 `news/inbox/<日期>.json`
  （只有标题、链接和行情数字，不存 AFR 正文——仓库是公开的）。云端 Claude 用 `python -m asxquant.news request`
  请它现抓，再只从收件箱选新闻；有收件箱时，简报里的 AFR 链接必须在收件箱里（`news.check_urls`）。
  云端推送需要 Claude GitHub App 装在本仓库。`news-watchdog.yml` 每个交易日晚上检查，没有简报就开 issue。
- 判断成绩单（`news/scorecard.json`）和数字核对（简报的 `verify` 字段）都在 `asxquant/newscheck.py`。
  自己算出来的数字必须写 `calcs` 算式；改对账口径（起点、1/5 天、扣大盘、20 次门槛）要先问用户。
- **唯一允许不走 Pull Request 进 main 的改动**：推到 `claude/` 开头分支、且只新增或修改
  `news/briefs/YYYY-MM-DD.json` 的提交，由 `.github/workflows/news-intake.yml` 用 main 上的校验程序检查后自动并入；
  以及 `news-collect.yml` 自己提交的 `news/inbox/` 收件箱和 `news/scorecard.json`。
  别的改动混在同一分支里，它就不合并。改 `news-intake.yml` 或 `validate()` 规则要先问用户。
