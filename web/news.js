/* AFR 每日要闻（默认首页）：标题 + 发生了什么 + 对股价或板块的影响（空 / 偏空 / 中性 / 偏多 / 多）。
   原来的整套分析一行没删，收在「多角度分析」按钮后面（#app，照常由 app.js 渲染）。
   数据：news/brief.json（最新一期）、news/briefs/<日期>.json（往期）、news/index.json、
   news/headlines.json（发布时抓的 AFR 标题，解读还没生成时兜底）。格式见 asxquant/news.py。
   整个文件包在一个函数里，不和 app.js 的全局变量（el、esc 等）互相干扰。 */
(function () {
  "use strict";
  var DIR_CLASS = { "空": "d-s2", "偏空": "d-s1", "中性": "d-0", "偏多": "d-l1", "多": "d-l2" };
  var WEEK = "日一二三四五六";
  var latest = null, heads = [], index = [];

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function safeUrl(u) { return /^https:\/\//.test(u || "") ? esc(u) : "#"; }
  function getJSON(p) {
    return fetch(p + "?t=" + Date.now()).then(function (r) {
      if (!r.ok) throw new Error(p + " " + r.status);
      return r.json();
    });
  }
  function cnDate(d) {
    var x = new Date(d + "T00:00:00");
    return (x.getMonth() + 1) + " 月 " + x.getDate() + " 日 周" + WEEK[x.getDay()];
  }
  function link(url, text, cls) {
    return '<a' + (cls ? ' class="' + cls + '"' : "") + ' href="' + safeUrl(url) +
      '" target="_blank" rel="noopener">' + text + "</a>";
  }

  /* ---------- 两种模式：AFR 要闻（默认） / 多角度分析（原来的整页） ---------- */
  function setMode(multi) {
    var b = document.body;
    b.classList.toggle("mode-multi", multi);
    b.classList.toggle("mode-brief", !multi);
    $("btnMulti").textContent = multi ? "← 返回 AFR 要闻" : "多角度分析";
    try {
      if (multi) history.replaceState(null, "", "#multi");
      else history.replaceState(null, "", location.pathname + location.search);
    } catch (e) { /* 单文件快照等环境不支持就算了 */ }
    window.scrollTo(0, 0);
  }

  /* ---------- 一条新闻 ---------- */
  function move(t) {
    if (t.halted) return '<span class="mv">停牌</span>';
    if (typeof t.move_pct !== "number") return "";
    var v = t.move_pct;
    return '<span class="mv ' + (v > 0 ? "up" : v < 0 ? "dn" : "") + '"' +
      (t.move_checked === false ? ' title="发布时没能用收盘价核对，是简报里写的数字"' : "") + ">" +
      (t.move_checked === false ? "约 " : "") + (v > 0 ? "+" : "") + v.toFixed(2) + "%</span>";
  }

  function card(it) {
    var imp = it.impact.map(function (t) {
      return '<div class="nimp"><span class="dir ' + (DIR_CLASS[t.direction] || "d-0") + '">' +
        esc(t.direction) + '</span><span class="nt">' + esc(t.name) +
        (t.code ? " <b>" + esc(t.code) + "</b>" : "") + "</span>" + move(t) + "</div>";
    }).join("");
    var src = it.sources.map(function (s) { return link(s.url, esc(s.name)); }).join(" · ");
    return '<article class="ncard">' +
      "<h3>" + esc(it.title_zh) + "</h3>" +
      link(it.afr_url, "AFR：" + esc(it.afr_headline) + " ↗", "norig") +
      '<p class="nwhat">' + esc(it.what_zh) + "</p>" +
      '<div class="nbox"><div class="nlbl">影响</div>' + imp +
      '<p class="nwhy">' + esc(it.why_zh) + "</p></div>" +
      '<div class="nsrc">来源：' + src + "</div></article>";
  }

  /* ---------- 一期简报 ---------- */
  function render(b) {
    var isLatest = latest && b.date === latest.date;
    var newer = heads.filter(function (h) { return h.date && h.date > b.date; }).length;
    var shown = {};
    b.items.forEach(function (it) { shown[it.afr_url] = 1; });
    var others = heads.filter(function (h) { return !shown[h.url]; });

    var html = '<div class="nhead"><h2 class="ntitle">AFR 要闻 · Claude 解读</h2>' +
      '<div class="nmeta">' + cnDate(b.date) + " · " + b.items.length + " 条" +
      (isLatest ? "" : ' · <a href="#" id="nBack">回到最新一期</a>') + "</div>" +
      (b.market ? '<div class="nmkt">' + esc(b.market.text_zh) + "</div>" : "") + "</div>";
    if (isLatest && newer) {
      html += '<div class="nstale">这是 ' + cnDate(b.date) + ' 的解读。AFR 之后又有 ' + newer +
        ' 条新闻，Claude 还没解读——标题先列在最下面。</div>';
    }
    html += b.items.map(card).join("");
    if (isLatest && others.length) html += headList(others, "AFR 其他标题（" + others.length + " 条，未解读）", false);
    html += foot();
    $("brief").innerHTML = html;
    var back = $("nBack");
    if (back) back.onclick = function (e) { e.preventDefault(); render(latest); window.scrollTo(0, 0); };
    wireHistory();
  }

  function headList(list, title, open) {
    return '<details class="nmore"' + (open ? " open" : "") + "><summary>" + esc(title) + "</summary><ul>" +
      list.map(function (h) {
        return "<li>" + link(h.url, esc(h.title)) + (h.date ? ' <span class="nd">' + esc(h.date.slice(5)) + "</span>" : "") + "</li>";
      }).join("") + "</ul></details>";
  }

  function foot() {
    var past = index.slice(0, 14).map(function (x) {
      return '<button class="npast" data-d="' + esc(x.date) + '">' + esc(x.date.slice(5)) + "</button>";
    }).join("");
    return '<div class="nfoot">「影响」是 Claude 根据新闻做的判断，没有经过回测验证，可能出错；' +
      "涨跌幅是发布时按收盘价重算的。以上是分析，不是投资建议。" +
      (index.length > 1 ? '<div class="npasts">往期：' + past + "</div>" : "") + "</div>";
  }

  function wireHistory() {
    Array.prototype.forEach.call(document.querySelectorAll(".npast"), function (btn) {
      btn.onclick = function () {
        var d = btn.getAttribute("data-d");
        if (latest && d === latest.date) { render(latest); window.scrollTo(0, 0); return; }
        getJSON("news/briefs/" + d + ".json").then(function (b) { render(b); window.scrollTo(0, 0); })
          .catch(function () { alert("这一期取不到了"); });
      };
    });
  }

  /* ---------- 启动 ---------- */
  $("btnMulti").onclick = function () { setMode(!document.body.classList.contains("mode-multi")); };
  setMode(location.hash === "#multi");

  Promise.all([
    getJSON("news/brief.json").catch(function () { return null; }),
    getJSON("news/headlines.json").then(function (h) { return h.items || []; }).catch(function () { return []; }),
    getJSON("news/index.json").then(function (x) { return x.briefs || []; }).catch(function () { return []; })
  ]).then(function (r) {
    latest = r[0]; heads = r[1]; index = r[2];
    if (latest && latest.items && latest.items.length) return render(latest);
    if (heads.length) {
      $("brief").innerHTML = '<div class="nhead"><h2 class="ntitle">AFR 要闻</h2>' +
        '<div class="nmeta">Claude 的解读还没生成，先看 AFR 最新标题</div></div>' +
        headList(heads, "AFR 最新标题（" + heads.length + " 条）", true) + foot();
      return;
    }
    // 本地服务器或还没发布过新闻：没东西可看，直接打开原来的分析页
    $("brief").innerHTML = '<div class="empty">还没有 AFR 要闻。</div>';
    setMode(true);
  });
})();
