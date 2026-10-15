# AGENTS.md — 给在这个仓库里改代码的 AI 编程助手（ChatGPT / Codex / Claude Code）

这是用户「澳股雷达」的代码，每天自动更新并部署到用户的手机：
https://lokliaoau-bot.github.io/asx-radarr/ （主页） · /short/ （看空雷达，ChatGPT 最初写的那个 App）

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
for t in test_step1 test_shortradar test_review test_round3 test_selfreview test_calibrate test_journal test_shortradar_review test_stock_lookup; do python -m tests.$t || exit 1; done
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
- 单股入口：`python run.py --stock BHP` 或首页输入代码。本地按需抓取；手机用每日缓存；不改变主股票池或评分。报价与完整日线分开，数据过期暂停判断，借券/费用未知保持 null。
