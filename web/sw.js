/* 澳股雷达 Service Worker —— 让它能装到手机主屏、离线也能打开上次的结果 */

/* 缓存名改动会让 activate 清掉所有旧缓存。
   v2（2026-08-24）：v1 用的是「外壳优先读缓存」，每次部署后第一次打开都会拿到
   上一版的 app.js 去渲染这一版的 report.json。平时只是样式旧一轮，但只要报告里
   增删过字段，旧脚本就会直接抛异常、整页空白。所以外壳也改成网络优先。 */
var CACHE = "asx-radar-v3";     // 改 app.js / 增删 report.json 字段时必须 bump
var SHELL = ["./", "./index.html", "./app.js", "./style.css", "./manifest.webmanifest"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return c.addAll(SHELL).catch(function () { /* 单个失败不阻塞安装 */ });
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

/* 脚本与数据必须是同一代产物，所以两者用同一套策略：
   优先网络、失败回落缓存。离线时仍然能看到上次的完整结果。 */
self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;

  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); });
      }
      return res;
    }).catch(function () {
      return caches.match(req).then(function (hit) {
        return hit || caches.match("./index.html");
      });
    })
  );
});
