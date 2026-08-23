# -*- coding: utf-8 -*-
"""Build the static site that GitHub Pages serves to the phone.

Output is the same PWA the local server serves, plus a `report.json` next to it.
`app.js` tries `api/report` first and falls back to `report.json`, so one codebase
covers both the local server and the static host with no build-time branching.
"""
from __future__ import annotations

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

    for name in ASSETS:
        src = os.path.join(WEB, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out_dir, name))

    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, separators=(",", ":"))

    # Pages would otherwise run the output through Jekyll and drop files it dislikes
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    size = sum(os.path.getsize(os.path.join(out_dir, f))
               for f in os.listdir(out_dir)
               if os.path.isfile(os.path.join(out_dir, f)))
    log("静态站点: %s (%d 个文件, %.1f MB)" % (out_dir, len(os.listdir(out_dir)), size / 1e6))
    return out_dir, size
