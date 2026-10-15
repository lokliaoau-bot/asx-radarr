# -*- coding: utf-8 -*-
"""Build the static site that GitHub Pages serves to the phone.

Output is the same PWA the local server serves, plus a `report.json` next to it.
`app.js` tries `api/report` first and falls back to `report.json`, so one codebase
covers both the local server and the static host with no build-time branching.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
SITE = os.path.join(ROOT, "site")

ASSETS = ["index.html", "app.js", "style.css", "sw.js",
          "manifest.webmanifest", "icon-192.png", "icon-512.png"]


def build(report, out_dir=None, log=print):
    out_dir = out_dir or SITE
    os.makedirs(out_dir, exist_ok=True)

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    for name in ASSETS:
        src = os.path.join(WEB, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(out_dir, name)
        if name == "app.js":
            # Stamp the build time into the shipped script. The header then shows which
            # BUILD of the interface is on screen, not just how fresh the DATA is --
            # a phone with a stale cached app.js would otherwise show a brand-new
            # "生成于" timestamp on top of last week's layout.
            txt = io.open(src, encoding="utf-8").read().replace("@@BUILD@@", stamp)
            with io.open(dst, "w", encoding="utf-8") as f:
                f.write(txt)
        else:
            shutil.copy2(src, dst)

    from .fileio import atomic_bytes
    atomic_bytes(os.path.join(out_dir, "report.json"),
                 json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    # 给编码助手读的系统自我复盘（journal.to_markdown）；复盘失败不影响主站
    try:
        from . import journal as JR
        if report.get("reflection"):
            atomic_bytes(os.path.join(out_dir, "self-review.md"),
                         JR.to_markdown(report["reflection"], report.get("calibration")).encode("utf-8"))
    except Exception as e:
        log("自我复盘摘要生成失败(不影响主站): %s" % e)

    # Pages would otherwise run the output through Jekyll and drop files it dislikes
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    # 看空雷达（ChatGPT 版并入）是附加页：它失败不能拖垮主站，页面自己会显示「今天的结果还没生成」。
    try:
        build_short(os.path.join(out_dir, "short"), log=log)
    except Exception as e:
        log("看空雷达页生成失败(不影响主站): %s" % e)

    size = sum(os.path.getsize(os.path.join(dp, f))
               for dp, _, fs in os.walk(out_dir) for f in fs)
    log("静态站点: %s (%.1f MB)" % (out_dir, size / 1e6))
    return out_dir, size


SHORT_WEB = os.path.join(WEB, "short")
SHORT_ASSETS = ["index.html", "workbench.js", "workbench.css", "plain.js", "evidence.json"]


def copy_short_assets(out_dir):
    """复制看空雷达的静态文件；plain.js 里的枚举当场由后端契约重新生成，两边不可能不同步。"""
    from . import shortradar as SR
    os.makedirs(out_dir, exist_ok=True)
    for name in SHORT_ASSETS:
        shutil.copy2(os.path.join(SHORT_WEB, name), os.path.join(out_dir, name))
    SR.sync_contract(os.path.join(out_dir, "plain.js"))


def build_short(out_dir, snap=None, log=print):
    from . import shortradar as SR
    copy_short_assets(out_dir)
    production = snap is None
    snap = snap or SR.build_from_cache(log=log)
    SR.write_site(out_dir, snap)
    if production:
        # 前瞻记录：今天的排名写进存档（随存档提交进仓库，事后改不了），再给过去的记录打分。
        # 只在生产路径做——测试传进来的合成 snap 绝不能写进真实存档。
        try:
            SR.record_and_score(out_dir, snap, log=log)
        except Exception as e:
            log("看空雷达前瞻记录失败(不影响页面): %s" % e)
    return out_dir
