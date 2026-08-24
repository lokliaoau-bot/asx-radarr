# -*- coding: utf-8 -*-
"""Export the dashboard as ONE self-contained HTML file.

The point is portability: drop the file in OneDrive / Google Drive / Dropbox / 百度网盘,
open it on any other machine, and it renders completely offline -- no Python, no server,
no internet, no fonts or scripts fetched from anywhere. The same `app.js` powers both
modes; it renders from `window.__REPORT__` when that global exists and falls back to
the local API when it does not.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
EXPORT_DIR = os.path.join(ROOT, "export")
FILENAME = "ASX市场雷达.html"


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def _safe_json(obj):
    """JSON that is safe to embed inside a <script> block."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    # A literal </script> inside any string would close the tag early.
    return s.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def export(report, out_dir=None, filename=None):
    out_dir = out_dir or EXPORT_DIR
    filename = filename or FILENAME
    os.makedirs(out_dir, exist_ok=True)

    css = _read(os.path.join(WEB, "style.css"))
    js = _read(os.path.join(WEB, "app.js")).replace(
        "@@BUILD@@", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    stamp = report.get("as_of", "")
    gen = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>澳股市场雷达 %s · ASX Market Radar</title>
<style>
%s
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="hrow">
      <div class="brand">
        <h1>澳股市场雷达</h1>
        <div class="sub" id="asof">载入中 ...</div>
      </div>
      <div class="quote" id="quote"></div>
      <span id="live" style="display:none"></span>
    </div>
    <div class="prog" id="prog"><div class="bar"><i id="progBar"></i></div><div class="msg" id="progMsg"></div></div>
  </header>
  <div id="app"></div>
  <div style="margin-top:34px;padding-top:16px;border-top:1px solid var(--line);
              font-size:11px;color:var(--mut);text-align:center">
    离线快照 · 数据截至 %s · 导出于 %s · 本文件不含任何外部依赖，可离线打开
  </div>
</div>
<script>window.__REPORT__ = %s;</script>
<script>
%s
</script>
<script>
/* 看门狗：app.js 若有语法错误，整个脚本一行都不会执行——app.js 内部的 try/catch
   救不了自己，页面就永远停在"正在载入 ..."，看起来像卡住或没数据。这段独立的内联
   脚本不受 app.js 解析失败影响，超时后把真实原因显示出来。 */
window.addEventListener("error", function (e) { window.__BOOT_ERR__ = e && e.message; });
setTimeout(function () {
  var app = document.getElementById("app");
  if (!app) return;
  var idle = app.innerHTML.length < 200 || app.innerHTML.indexOf("正在载入") >= 0;
  if (!idle) return;                                          // 已经渲染出来了
  var why = (typeof render === "undefined")
    ? "app.js 没能加载或解析（脚本报错，整份脚本都没执行）"
    : "脚本已加载，但数据一直没回来";
  app.innerHTML = '<div class="empty">页面没能载入。<br><br><b>' + why + '</b>' +
    '<br><br>先<b>强制刷新一次</b>（手机：关掉 App 再打开；电脑：Ctrl+F5）。' +
    (window.__BOOT_ERR__
      ? '<br><br><span style="font-size:12px;color:#5d6478">技术细节：' +
        String(window.__BOOT_ERR__).replace(/[<>&]/g, "") + '</span>'
      : "") + '</div>';
}, 12000);
</script>
</body>
</html>
""" % (stamp, css, stamp, gen, _safe_json(report), js)

    path = os.path.join(out_dir, filename)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path, len(html.encode("utf-8"))
