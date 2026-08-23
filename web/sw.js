/* 澳股雷达 Service Worker —— 让它能装到手机主屏、离线也能打开上次的结果 */
var CACHE = "asx-radar-v1";
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

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  var isData = url.pathname.indexOf("report.json") >= 0 || url.pathname.indexOf("/api/") >= 0;

  if (isData) {
    // 数据：优先网络，拿不到就用上次缓存（离线时仍能看到上次结果）
    e.respondWith(
      fetch(req).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); });
        return res;
      }).catch(function () { return caches.match(req); })
    );
  } else {
    // 外壳：优先缓存，后台更新
    e.respondWith(
      caches.match(req).then(function (hit) {
        var net = fetch(req).then(function (res) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
          return res;
        }).catch(function () { return hit; });
        return hit || net;
      })
    );
  }
});
