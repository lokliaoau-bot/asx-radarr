/* 澳股雷达 Service Worker —— 让它能装到手机主屏、离线也能打开上次的结果 */

/* 缓存名改动会让 activate 清掉所有旧缓存。
   v2（2026-08-24）：v1 用的是「外壳优先读缓存」，每次部署后第一次打开都会拿到
   上一版的 app.js 去渲染这一版的 report.json。平时只是样式旧一轮，但只要报告里
   增删过字段，旧脚本就会直接抛异常、整页空白。所以外壳也改成网络优先。
   v7（2026-10-15，采纳 ChatGPT 审查意见并复核）：
   · 只管本站范围内的请求，api/ 不缓存（离线时给出过期的「运行状态」比报错更误导）；
   · 只清理自己名下（asx-radar-*）的旧缓存，不碰同源上别的缓存；
   · 离线时打开看空雷达页，回落到看空雷达页本身，而不是错跳到主页；
   · 缺的数据文件离线时明确返回 503，而不是把主页 HTML 当 JSON 塞给页面。 */
var CACHE = "asx-radar-v7";     // 改 app.js / 增删 report.json 字段时必须 bump（v7：严格检验 + 前瞻成绩单）
var SHELL = ["./", "./index.html", "./app.js", "./style.css", "./manifest.webmanifest"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return c.addAll(SHELL).catch(function () { /* 单个失败不阻塞安装 */ });
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k.indexOf("asx-radar-") === 0 && k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

/* 脚本与数据必须是同一代产物，所以两者用同一套策略：
   优先网络、失败回落缓存。离线时仍然能看到上次的完整结果。 */
self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  var scope = new URL(self.registration.scope);
  if (url.origin !== scope.origin || url.pathname.indexOf(scope.pathname) !== 0) return;
  var rel = url.pathname.slice(scope.pathname.length);
  if (rel.indexOf("api/") === 0) return;

  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok) {
        var copy = res.clone();
        e.waitUntil(caches.open(CACHE).then(function (c) { return c.put(req, copy); })
          .catch(function () {}));
      }
      return res;
    }).catch(function () {
      return caches.match(req).then(function (hit) {
        if (hit) return hit;
        if (req.mode === "navigate") {
          var page = rel.indexOf("short/") === 0 ? "short/" : "./";
          return caches.match(new URL(page, scope).href).then(function (p) {
            return p || caches.match(new URL(page === "./" ? "index.html" : "short/index.html", scope).href);
          }).then(function (p) {
            return p || new Response("离线，且这一页还没有缓存过", {status: 503,
              headers: {"Content-Type": "text/plain; charset=utf-8"}});
          });
        }
        var json = /\.json$/.test(url.pathname);
        return new Response(json ? JSON.stringify({error: "offline_snapshot_missing"}) : "offline", {
          status: 503,
          headers: {"Content-Type": json ? "application/json; charset=utf-8" : "text/plain; charset=utf-8"}
        });
      });
    })
  );
});
