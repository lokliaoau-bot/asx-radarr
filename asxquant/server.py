# -*- coding: utf-8 -*-
"""Local dashboard server. Stdlib only -- no web framework, no CDN, works offline."""
from __future__ import annotations

import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import datafeed as D
from . import engine as E
from . import export as EX

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
REPORT_PATH = os.path.join(ROOT, "cache", "report.json")
# 看空雷达页：静态文件在 web/short/，每天的结果写在 cache/short/data/（不进仓库）
SHORT_DATA_PARENT = os.path.join(ROOT, "cache", "short")
SHORT_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
               ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}

_state = {
    "running": False,
    "pct": 0,
    "msg": "待命",
    "error": None,
    "log": [],
}
_lock = threading.Lock()


def _progress(msg, pct):
    with _lock:
        _state["msg"] = msg
        _state["pct"] = pct
        _state["log"].append(msg)
        _state["log"] = _state["log"][-40:]


def _refresh(force):
    try:
        with _lock:
            _state.update(running=True, pct=0, msg="启动 ...", error=None, log=[])
        rep = E.run(force=force, log=lambda m: _progress(m, _state["pct"]), progress=_progress)
        E.save(rep, REPORT_PATH)
        try:
            path, size = EX.export(rep)
            _progress("已同步导出云盘版: %s (%.1f MB)" % (os.path.basename(path), size / 1e6), 100)
        except Exception as e:
            _progress("云盘版导出失败: %s" % e, 100)
        try:
            from . import shortradar as SR
            SR.write_site(SHORT_DATA_PARENT, SR.build_from_cache(log=lambda m: _progress(m, 100)))
        except Exception as e:
            _progress("看空雷达页更新失败(不影响主页): %s" % e, 100)
        with _lock:
            _state.update(running=False, pct=100, msg="完成")
    except Exception:
        tb = traceback.format_exc()
        with _lock:
            _state.update(running=False, pct=0, msg="失败", error=tb.splitlines()[-1])
        print(tb)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Service-worker scripts are refused by some browsers when served no-store,
        # so static shell assets get a short max-age instead. Data stays uncached.
        self.send_header("Cache-Control", cache or "no-store")
        self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _file(self, name, ctype, cache="max-age=60"):
        p = os.path.join(WEB, name)
        if not os.path.exists(p):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        with open(p, "rb") as f:
            self._send(200, f.read(), ctype, cache=cache)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._file("app.js", "application/javascript; charset=utf-8")
        if path == "/stock.js":
            return self._file("stock.js", "application/javascript; charset=utf-8")
        if path == "/api/stock":
            from . import stock_lookup as SL
            params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if set(params) != {"ticker"} or len(params["ticker"]) != 1:
                return self._send(400, json.dumps({"error": "请只提供一个 ticker 参数。"}, ensure_ascii=False))
            try:
                result = SL.lookup(params["ticker"][0])
                return self._send(200, json.dumps(result, ensure_ascii=False, allow_nan=False))
            except BlockingIOError as e:
                return self._send(429, json.dumps({"error": str(e)}, ensure_ascii=False))
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
            except Exception as e:
                return self._send(502, json.dumps({"error": str(e)}, ensure_ascii=False))
        if path == "/style.css":
            return self._file("style.css", "text/css; charset=utf-8")
        if path == "/sw.js":
            return self._file("sw.js", "application/javascript; charset=utf-8")
        if path == "/manifest.webmanifest":
            return self._file("manifest.webmanifest", "application/manifest+json; charset=utf-8")
        if path in ("/icon-192.png", "/icon-512.png"):
            return self._file(path.lstrip("/"), "image/png")
        if path == "/report.json":
            if not os.path.exists(REPORT_PATH):
                return self._send(404, json.dumps({"error": "no report"}))
            with open(REPORT_PATH, "rb") as f:
                return self._send(200, f.read())
        if path == "/api/status":
            with _lock:
                return self._send(200, json.dumps(_state, ensure_ascii=False))
        if path == "/api/report":
            if not os.path.exists(REPORT_PATH):
                return self._send(404, json.dumps({"error": "尚无报告，请点击更新"}, ensure_ascii=False))
            with open(REPORT_PATH, "rb") as f:
                return self._send(200, f.read())
        if path == "/api/cache":
            info = {n: D.cache_age(n) for n in ("prices.pkl", "shorts.pkl", "models.pkl")}
            return self._send(200, json.dumps(info, ensure_ascii=False))
        if path == "/short":
            self.send_response(301)
            self.send_header("Location", "/short/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/short/"):
            return self._short(path[len("/short/"):] or "index.html")
        return self._send(404, json.dumps({"error": "not found"}))

    def _short(self, rel):
        from .publish import SHORT_ASSETS
        from . import shortradar as SR
        if rel.startswith("data/"):
            name = rel[len("data/"):]
            if name not in ("radar-5.json", "radar-10.json", "radar-20.json", "import.json", "forward.json"):
                return self._send(404, json.dumps({"error": "not found"}))
            p = os.path.join(SHORT_DATA_PARENT, "data", name)
            if not os.path.exists(p):
                return self._send(404, json.dumps({"error": "尚未生成，请在首页点「更新全部指标」"},
                                                  ensure_ascii=False))
            with open(p, "rb") as f:
                return self._send(200, f.read())
        if rel not in SHORT_ASSETS:
            return self._send(404, json.dumps({"error": "not found"}))
        with open(os.path.join(WEB, "short", rel), encoding="utf-8") as f:
            body = f.read()
        if rel == "plain.js":   # 与静态站点同一套：枚举当场由后端契约生成
            import re
            body = re.sub(re.escape(SR.START) + r".*?" + re.escape(SR.END),
                          lambda _m: SR.contract_block(), body, flags=re.S)
        ext = os.path.splitext(rel)[1]
        return self._send(200, body, SHORT_TYPES[ext], cache="max-age=60")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/refresh":
            force = "force=1" in self.path
            with _lock:
                if _state["running"]:
                    return self._send(409, json.dumps({"error": "已在运行"}, ensure_ascii=False))
            threading.Thread(target=_refresh, args=(force,), daemon=True).start()
            return self._send(202, json.dumps({"started": True, "force": force}))
        if path == "/api/export":
            if not os.path.exists(REPORT_PATH):
                return self._send(400, json.dumps({"ok": False, "error": "尚无报告，请先更新"},
                                                  ensure_ascii=False))
            try:
                with open(REPORT_PATH, encoding="utf-8") as f:
                    rep = json.load(f)
                p, size = EX.export(rep)
                return self._send(200, json.dumps({"ok": True, "path": p,
                                                   "bytes": size}, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"ok": False, "error": str(e)},
                                                  ensure_ascii=False))
        return self._send(404, json.dumps({"error": "not found"}))


def _lan_ip():
    """Best-effort LAN address so the phone on the same WiFi can be told where to go."""
    import socket
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("8.8.8.8", 80))
        ip = sk.getsockname()[0]
        sk.close()
        return ip
    except Exception:
        return None


def serve(port=8849, open_browser=True, host="0.0.0.0"):
    # Bind on all interfaces: the phone reaches this over the LAN, not just localhost.
    srv = ThreadingHTTPServer((host, port), Handler)
    url = "http://127.0.0.1:%d/" % port
    lan = _lan_ip()
    print("=" * 62)
    print("  澳股市场雷达  ASX Market Radar")
    print("  本机: %s" % url)
    if lan:
        print("  手机(同一WiFi): http://%s:%d/" % (lan, port))
        print("       -> 手机浏览器打开后选「添加到主屏幕」即可当App用")
    print("  按 Ctrl+C 停止")
    print("=" * 62)
    if open_browser:
        threading.Timer(1.0, lambda: __import__("webbrowser").open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        srv.shutdown()
