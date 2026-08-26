/* 澳股市场雷达 — 前端渲染。无外部依赖，图表为原生 SVG。
   两种模式：
     live     — 从本机 /api/report 读取，右上角按钮可一键更新
     snapshot — 数据已内嵌于 window.__REPORT__（云盘单文件版），只读 */
"use strict";

var SNAPSHOT = (typeof window.__REPORT__ !== "undefined" && window.__REPORT__);
var R = null;

function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
/* 由 publish.py / export.py 在构建时替换。本地直接跑 server.py 时保持原样，
   于是本地显示"本地开发版"，云端显示真正的构建时间——用来一眼看出
   手机上打开的到底是不是最新那版界面（数据新不代表界面新：装成 App 后
   Service Worker 可能还留着上一版的 app.js）。 */
var BUILD_STAMP = "@@BUILD@@";
function buildLabel() {
  return BUILD_STAMP.charAt(0) === "@" ? "本地开发版" : BUILD_STAMP;
}

function num(v, d) { return (v === null || v === undefined || isNaN(v)) ? "—" : Number(v).toFixed(d === undefined ? 2 : d); }
function pct(v, d) { return (v === null || v === undefined || isNaN(v)) ? "—" : (v * 100).toFixed(d === undefined ? 1 : d) + "%"; }
function spct(v, d) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  var s = (v * 100).toFixed(d === undefined ? 2 : d) + "%";
  return v > 0 ? "+" + s : s;
}
function sgn(v, d) {
  if (v === null || v === undefined || isNaN(v)) return "—";
  var s = Number(v).toFixed(d === undefined ? 1 : d);
  return v > 0 ? "+" + s : s;
}
function cls(v) { return v > 0 ? "up" : (v < 0 ? "dn" : "mut"); }

/* ---------------- 大白话：术语悬停解释 ---------------- */
function term(text, explain) {
  return '<span class="term" title="' + esc(explain) + '">' + esc(text) + '</span>';
}
var GLOSSARY = {
  "空头持仓": "机构申报的、赌这只股票会跌的仓位规模（占总股本的比例）。澳洲法律要求每天公开申报，所以是真实数据不是猜的。",
  "做空": "先借股票卖出、赌它跌了再低价买回还回去赚差价。跌了赚钱，涨了亏钱，风险理论上无上限。",
  "回补天数": "空头如果要全部平仓（买回股票）大约需要几天。天数越少，一旦股价上涨越容易被逼着抢购、引发暴涨（轧空）。",
  "轧空": "股价上涨迫使做空的人抢着买回股票止损，越买越推高，形成短时暴涨。空头越拥挤越容易发生。",
  "波动率": "价格上下摆动的剧烈程度。高=大涨大跌很颠簸，低=走得平稳。",
  "延展度": "价格已经涨了多远、是不是涨太多了。越高说明离近期低点越远、越可能需要回调。",
  "热度": "这个板块现在被炒作的火热程度：成交额是否暴增、价格是否加速、有多少股票超买。热≠资金在进，可能是在高位换手派发。",
  "蔡金资金流": "看每天收盘价靠近当天最高还是最低，来估算是买盘强还是卖盘强。正=买盘占优。",
  "象限": "板块相对大盘正处在轮动的哪个阶段：领先、改善、转弱、还是落后。",
  "基准率": "历史上这件事平均多久发生一次。",
  "立场分": "这个信号现在是偏多还是偏空，正=偏多。",
  "夏普": "衡量赚钱赚得稳不稳：每承担一分波动能换来多少收益。1以上算不错，越高越好。",
  "最大回撤": "历史上从最高点跌到最低点、最惨的一次亏了多少。",
  "年化": "把收益换算成一整年的水平，方便比较。",
  "IC": "这套挑股方法的打分和之后股票实际涨跌的吻合程度。数值离0越远、越稳定，说明方法越有效。",
  "t值": "统计上判断一个结果是真本事还是碰运气的指标。绝对值大于2，一般就认为不是运气。",
  "AUC": "预测准不准的评分：0.5=和抛硬币一样瞎猜，越接近1越准。",
  "朴素基线": "完全不用模型、只用一行最土的算术能拿到的准确度。比如「现在波动率低不低」本身就很能预测波动率会不会上升（波动率会均值回归）。模型必须赢过这一列才算有价值——赢不过就说明那套复杂机器还不如一行算术，系统会直接改报历史平均值。这是 Welch & Goyal (2008) 的标准检验。",
  "Brier": "衡量给出的概率靠不靠谱：大于0才算比无脑猜历史平均值更好。",
  "止损": "如果买了跌破/空了涨过某个价，就认赔离场，不硬扛，防止小亏拖成大亏。",
  "日均成交": "这只股票每天平均成交多少钱，反映能不能大额买卖不影响价格（流动性）。",
  "成本地图": "把过去3个月的成交量按价位摊开，看大家的筹码主要堆在哪个价格附近。柱子越高＝那个价位换手越多。这是对已发生成交的测量，不是预测，也不参与任何评分。",
  "套牢盘": "在比现价更高的价位买入、目前还浮亏的那部分成交量占比。占比高，意味着股价往上走时会不断遇到解套卖出的人。",
  "价值区": "成交量最集中的那一段价格区间（约占七成成交量）。价格待在里面通常代表买卖双方对价格有共识，冲出去则代表共识被打破。",
  "空头成本": "把全市场每天公开的空头总仓位当成一个仓库记账：仓位增加＝有人在当天价位新开空单，减少＝最早的空单被平掉。据此倒推出目前还没平掉的空单平均建在什么价。这是全体空头的平均数，不是某一家机构；澳洲不公开做空者身份。",
  "空头盈亏": "相对建仓本金算的。在 A$10 做空、股价跌到 A$5 就是赚 50%；涨到 A$20 就是亏 100%。注意做空的亏损没有上限——做多最多亏光本金，做空理论上可以亏更多，所以空头亏得越狠，被迫买回股票止损的压力越大。",
  "可追溯": "当前空头仓位中，有多大比例能追溯到具体建仓价。ASIC 数据从2022年才有，更早就存在的老仓位无法定价。低于80%时该股的成本估算要打折看。",
  "重叠样本": "本系统每天都要算一次「未来20天涨多少」，相邻两天的那20天几乎完全重合，所以这些数字并不是互相独立的。若当成独立来算统计显著性，会把 t 值放大3到5倍。本表已用 Newey-West 方法修正。"
};
var _glossaryKeys = Object.keys(GLOSSARY).sort(function (a, b) { return b.length - a.length; });
function annotate(text) {
  var out = esc(text);
  var hits = [];
  _glossaryKeys.forEach(function (k) {
    if (out.indexOf(k) >= 0) {
    var token = "" + hits.length + "";
      out = out.split(k).join(token);
      hits.push(k);
    }
  });
  hits.forEach(function (k, i) {
    var token = "" + i + "";
    out = out.split(token).join('<span class="term" title="' + esc(GLOSSARY[k]) + '">' + k + '</span>');
  });
  return out;
}
function money(v) {
  if (v == null || isNaN(v)) return "—";
  var a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "b";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "m";
  if (a >= 1e3) return (v / 1e3).toFixed(0) + "k";
  return v.toFixed(0);
}

function probBar(p, base, color) {
  var w = Math.max(0, Math.min(100, p * 100)), b = Math.max(0, Math.min(100, base * 100));
  return '<div class="mb"><i style="left:0;width:' + w.toFixed(1) + '%;background:' + color +
         ';opacity:.55"></i><u style="left:' + b.toFixed(1) + '%"></u><s>' + (p * 100).toFixed(1) + '%</s></div>';
}
function zBar(z, max) {
  max = max || 3;
  var v = Math.max(-max, Math.min(max, z || 0));
  var half = 50 * Math.abs(v) / max, left = v >= 0 ? 50 : 50 - half;
  return '<div class="zbar"><u></u><i style="left:' + left.toFixed(1) + '%;width:' + half.toFixed(1) +
         '%;background:' + (v >= 0 ? "var(--up)" : "var(--dn)") + ';opacity:.6"></i></div>';
}
function svgLine(o) {
  var W = 100, H = o.h || 30, s = (o.values || []).filter(function (x) { return x != null && !isNaN(x); });
  if (s.length < 2) return "";
  var mn = Math.min.apply(null, s), mx = Math.max.apply(null, s), rg = (mx - mn) || 1;
  var pts = s.map(function (v, i) {
    return (i / (s.length - 1) * W).toFixed(2) + "," + (H - (v - mn) / rg * H).toFixed(2);
  }).join(" ");
  return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="height:' + H + 'px;width:92px">' +
         '<polyline points="' + pts + '" fill="none" stroke="' + (o.color || "var(--blu)") +
         '" stroke-width="1.4" vector-effect="non-scaling-stroke"/></svg>';
}

function bigLineChart(cfg) {
  var W = 1000, H = cfg.h || 220, PL = 54, PR = 14, PT = 12, PB = 24;
  var iw = W - PL - PR, ih = H - PT - PB, all = [];
  cfg.series.forEach(function (s) { s.v.forEach(function (x) { if (x != null && !isNaN(x)) all.push(x); }); });
  if (!all.length) return "";
  var mn = Math.min.apply(null, all), mx = Math.max.apply(null, all);
  if (cfg.zero !== undefined) { mn = Math.min(mn, cfg.zero); mx = Math.max(mx, cfg.zero); }
  var pad = (mx - mn) * 0.08 || 1; mn -= pad; mx += pad;
  var rg = (mx - mn) || 1, n = cfg.dates.length;
  var X = function (i) { return PL + (n < 2 ? 0 : i / (n - 1) * iw); };
  var Y = function (v) { return PT + ih - (v - mn) / rg * ih; };
  var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="height:' + H + 'px">';
  for (var g = 0; g <= 4; g++) {
    var yy = PT + ih * g / 4, vv = mx - rg * g / 4;
    out += '<line class="gl" x1="' + PL + '" y1="' + yy.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + yy.toFixed(1) + '"/>' +
           '<text class="tick" x="' + (PL - 7) + '" y="' + (yy + 3.2).toFixed(1) + '" text-anchor="end">' +
           (cfg.fmt ? cfg.fmt(vv) : vv.toFixed(1)) + '</text>';
  }
  if (cfg.zero !== undefined && cfg.zero >= mn && cfg.zero <= mx) {
    out += '<line class="axl" x1="' + PL + '" y1="' + Y(cfg.zero).toFixed(1) + '" x2="' + (W - PR) +
           '" y2="' + Y(cfg.zero).toFixed(1) + '" stroke-dasharray="3 3"/>';
  }
  cfg.series.forEach(function (s) {
    var d = "", pen = false;
    s.v.forEach(function (v, i) {
      if (v == null || isNaN(v)) { pen = false; return; }
      d += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " "; pen = true;
    });
    out += '<path d="' + d + '" fill="none" stroke="' + s.color + '" stroke-width="' + (s.w || 1.7) +
           '"' + (s.dash ? ' stroke-dasharray="' + s.dash + '"' : "") + ' stroke-linejoin="round"/>';
  });
  var ticks = Math.min(6, n);
  for (var t = 0; t < ticks; t++) {
    var i2 = Math.round(t / Math.max(1, ticks - 1) * (n - 1));
    out += '<text class="tick" x="' + X(i2).toFixed(1) + '" y="' + (H - 6) +
           '" text-anchor="middle">' + esc(String(cfg.dates[i2]).slice(2)) + '</text>';
  }
  return out + "</svg>";
}

/* ---- flow x extension map: the early-vs-crowded picture ---- */
function stageMap(sectors) {
  var W = 1000, H = 440, P = 52, iw = W - P * 2, ih = H - P * 2;
  var fx = sectors.map(function (s) { return s.flow.score; });
  var ex = sectors.map(function (s) { return s.extension.score; });
  var fr = Math.max(1.6, Math.max.apply(null, fx.map(Math.abs)) * 1.18);
  var er = Math.max(1.6, Math.max.apply(null, ex.map(Math.abs)) * 1.18);
  var X = function (v) { return P + (v / fr / 2 + 0.5) * iw; };
  var Y = function (v) { return P + ih - (v / er / 2 + 0.5) * ih; };
  var cx = X(0), cy = Y(0);
  var quads = [
    { x: cx, y: P, w: W - P - cx, h: cy - P, c: "rgba(245,158,11,.06)", t: "资金已大幅流入 · 拥挤", tc: "#fbbf24", ax: "end", tx: W - P - 10, ty: P + 18 },
    { x: P, y: P, w: cx - P, h: cy - P, c: "rgba(239,68,68,.06)", t: "涨高但资金流出 · 派发", tc: "#f87171", ax: "start", tx: P + 10, ty: P + 18 },
    { x: cx, y: cy, w: W - P - cx, h: P + ih - cy, c: "rgba(34,197,94,.07)", t: "资金迹象流入 · 早期", tc: "#4ade80", ax: "end", tx: W - P - 10, ty: P + ih - 10 },
    { x: P, y: cy, w: cx - P, h: P + ih - cy, c: "rgba(148,163,184,.05)", t: "跌深且资金流出 · 超跌", tc: "#94a3b8", ax: "start", tx: P + 10, ty: P + ih - 10 }
  ];
  var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="height:' + H + 'px">';
  quads.forEach(function (r) {
    out += '<rect x="' + r.x.toFixed(1) + '" y="' + r.y.toFixed(1) + '" width="' + Math.max(0, r.w).toFixed(1) +
           '" height="' + Math.max(0, r.h).toFixed(1) + '" fill="' + r.c + '"/>' +
           '<text x="' + r.tx + '" y="' + r.ty + '" text-anchor="' + r.ax + '" fill="' + r.tc +
           '" font-size="12" font-weight="600" opacity=".9">' + r.t + '</text>';
  });
  out += '<line class="axl" x1="' + cx.toFixed(1) + '" y1="' + P + '" x2="' + cx.toFixed(1) + '" y2="' + (P + ih) + '"/>' +
         '<line class="axl" x1="' + P + '" y1="' + cy.toFixed(1) + '" x2="' + (W - P) + '" y2="' + cy.toFixed(1) + '"/>';
  sectors.forEach(function (s) {
    var x = X(s.flow.score), y = Y(s.extension.score);
    var hot = s.heat.score;
    var col = hot >= 1.2 ? "#fb7185" : hot >= 0.5 ? "#fbbf24" : s.flow.score >= 0.3 ? "#4ade80" :
              s.flow.score <= -0.3 ? "#94a3b8" : "#8790a5";
    var rad = 6 + Math.min(9, Math.max(0, hot + 1.5) * 2.6);
    out += '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + rad.toFixed(1) +
           '" fill="' + col + '" fill-opacity=".5" stroke="' + col + '" stroke-width="1.6"/>';
    var right = x < W / 2;
    out += '<text x="' + (x + (right ? rad + 6 : -rad - 6)).toFixed(1) + '" y="' + (y + 4).toFixed(1) +
           '" text-anchor="' + (right ? "start" : "end") + '" fill="var(--tx)" font-size="11.5" font-weight="560">' +
           esc(s.name) + '</text>';
  });
  out += '<text class="tick" x="' + (W - P) + '" y="' + (P + ih + 21) + '" text-anchor="end">资金复合评分 →</text>';
  out += '<text class="tick" x="' + (P - 40) + '" y="' + (P - 13) + '" text-anchor="start">↑ 价格延展度（已涨多少）</text>';
  return out + "</svg>";
}

function rrgChart(sectors) {
  var W = 1000, H = 420, P = 46, iw = W - P * 2, ih = H - P * 2;
  var xs = sectors.map(function (s) { return s.raw.rs_ratio; }).filter(function (v) { return v != null; });
  var ys = sectors.map(function (s) { return s.raw.rs_mom; }).filter(function (v) { return v != null; });
  if (!xs.length) return '<div class="empty">RRG 数据不足</div>';
  var xr = Math.max(2.2, Math.max.apply(null, xs.map(function (v) { return Math.abs(v - 100); })) * 1.15);
  var yr = Math.max(2.2, Math.max.apply(null, ys.map(function (v) { return Math.abs(v - 100); })) * 1.15);
  var X = function (v) { return P + ((v - 100) / xr / 2 + 0.5) * iw; };
  var Y = function (v) { return P + ih - ((v - 100) / yr / 2 + 0.5) * ih; };
  var cx = X(100), cy = Y(100);
  var q = [
    { x: cx, y: P, w: W - P - cx, h: cy - P, c: "rgba(34,197,94,.05)", t: "领先", tc: "#4ade80", ax: "end", tx: W - P - 10, ty: P + 17 },
    { x: P, y: P, w: cx - P, h: cy - P, c: "rgba(59,130,246,.05)", t: "改善", tc: "#60a5fa", ax: "start", tx: P + 10, ty: P + 17 },
    { x: cx, y: cy, w: W - P - cx, h: P + ih - cy, c: "rgba(245,158,11,.05)", t: "转弱", tc: "#fbbf24", ax: "end", tx: W - P - 10, ty: P + ih - 10 },
    { x: P, y: cy, w: cx - P, h: P + ih - cy, c: "rgba(239,68,68,.05)", t: "落后", tc: "#f87171", ax: "start", tx: P + 10, ty: P + ih - 10 }
  ];
  var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="height:' + H + 'px">';
  q.forEach(function (r) {
    out += '<rect x="' + r.x.toFixed(1) + '" y="' + r.y.toFixed(1) + '" width="' + Math.max(0, r.w).toFixed(1) +
           '" height="' + Math.max(0, r.h).toFixed(1) + '" fill="' + r.c + '"/>' +
           '<text x="' + r.tx + '" y="' + r.ty + '" text-anchor="' + r.ax + '" fill="' + r.tc +
           '" font-size="11.5" font-weight="600" opacity=".85">' + r.t + '</text>';
  });
  out += '<line class="axl" x1="' + cx.toFixed(1) + '" y1="' + P + '" x2="' + cx.toFixed(1) + '" y2="' + (P + ih) + '"/>' +
         '<line class="axl" x1="' + P + '" y1="' + cy.toFixed(1) + '" x2="' + (W - P) + '" y2="' + cy.toFixed(1) + '"/>';
  sectors.forEach(function (s) {
    if (s.raw.rs_ratio == null || s.raw.rs_mom == null) return;
    var x = X(s.raw.rs_ratio), y = Y(s.raw.rs_mom);
    var col = s.flow.score >= 0.3 ? "#22c55e" : s.flow.score <= -0.3 ? "#ef4444" : "#8790a5";
    var rad = 5 + Math.min(7, Math.abs(s.flow.score) * 3.4);
    out += '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + rad.toFixed(1) +
           '" fill="' + col + '" fill-opacity=".6" stroke="' + col + '" stroke-width="1.5"/>';
    var right = x < W / 2;
    out += '<text x="' + (x + (right ? rad + 6 : -rad - 6)).toFixed(1) + '" y="' + (y + 3.8).toFixed(1) +
           '" text-anchor="' + (right ? "start" : "end") + '" fill="var(--tx)" font-size="11.5">' + esc(s.name) + '</text>';
  });
  out += '<text class="tick" x="' + (W - P) + '" y="' + (P + ih + 20) + '" text-anchor="end">RS-Ratio →</text>';
  return out + "</svg>";
}

/* ---------------- sections ---------------- */
function renderQuote(b) {
  el("quote").innerHTML =
    '<div class="q"><span class="lbl">ASX 200</span><span class="val">' + num(b.last, 0) + '</span></div>' +
    '<div class="q"><span class="lbl">当日</span><span class="val sm ' + cls(b.chg_1d) + '">' + spct(b.chg_1d) + '</span></div>' +
    '<div class="q"><span class="lbl">20日</span><span class="val sm ' + cls(b.chg_20d) + '">' + spct(b.chg_20d) + '</span></div>' +
    '<div class="q"><span class="lbl">年初至今</span><span class="val sm ' + cls(b.chg_ytd) + '">' + spct(b.chg_ytd) + '</span></div>' +
    '<div class="q"><span class="lbl">20日波动率</span><span class="val sm">' + pct(b.rv20) + '</span></div>';
}

/* ============ 成本地图：过去3个月成交量分布（测量，不参与任何打分） ============ */
function apx(v) { return "A$" + (Math.abs(v) < 1 ? Number(v).toFixed(3) : Number(v).toFixed(2)); }

function costMap(p, px) {
  if (!p || !p.hist || !p.hist.length || px == null || isNaN(px)) return "";
  if (p.lo == null || p.hi == null || !(p.hi > p.lo)) return "";
  var W = 260, H = 52, FLOOR = 46, n = p.hist.length;
  var lo = p.lo, hi = p.hi, span = (hi - lo) || 1, step = span / n;
  var mx = Math.max.apply(null, p.hist) || 1;
  function X(v) { return Math.max(0, Math.min(W, (v - lo) / span * W)); }
  function seg(x0, x1, bh, over) {
    if (x1 - x0 <= 0) return "";
    return '<rect x="' + x0.toFixed(1) + '" y="' + (FLOOR - bh).toFixed(1) +
      '" width="' + (x1 - x0).toFixed(1) + '" height="' + bh.toFixed(1) +
      '" fill="' + (over ? "var(--amb)" : "var(--blu)") + '" opacity="' + (over ? ".62" : ".5") + '"/>';
  }
  var bars = "";
  for (var i = 0; i < n; i++) {
    // edges 不再随报告传输，这里按 lo/hi 还原（等距，和后端 linspace 完全一致）
    var e0 = lo + i * step, e1 = lo + (i + 1) * step;
    var bh = Math.max(1, p.hist[i] / mx * (FLOOR - 4));
    var x0 = X(e0), x1 = Math.max(X(e0) + 0.8, X(e1) - 0.4);
    if (px > e0 && px < e1) {
      // 现价落在这一箱内部：就地劈开，橙色面积才等于图例上写的百分比
      var xc = Math.min(Math.max(X(px), x0), x1);
      bars += seg(x0, xc, bh, false) + seg(xc, x1, bh, true);
    } else {
      bars += seg(x0, x1, bh, e0 >= px);
    }
  }
  var cx = X(px), pocx = X(p.poc), v0 = X(p.va_low), v1 = X(p.va_high);
  var svg = '<svg class="cmsvg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" aria-hidden="true">' +
    '<rect x="' + v0.toFixed(1) + '" y="1" width="' + Math.max(0, v1 - v0).toFixed(1) +
      '" height="' + (FLOOR - 1) + '" fill="var(--tx)" opacity=".055"/>' +
    bars +
    '<line x1="' + pocx.toFixed(1) + '" y1="1" x2="' + pocx.toFixed(1) + '" y2="' + FLOOR +
      '" stroke="var(--vio)" stroke-width="1" stroke-dasharray="3 2"/>' +
    '<line x1="' + cx.toFixed(1) + '" y1="0" x2="' + cx.toFixed(1) + '" y2="' + (FLOOR + 4) +
      '" stroke="var(--tx)" stroke-width="1.6"/>' +
    '<line x1="0" y1="' + FLOOR + '" x2="' + W + '" y2="' + FLOOR + '" stroke="var(--line)" stroke-width="1"/>' +
    '</svg>';

  var ov = p.overhead;
  return '<div class="cmap">' +
    '<div class="cmh">' + term("成本地图", GLOSSARY["成本地图"]) +
      '<span class="cmsub">过去 ' + p.days + ' 个交易日每个价位成交了多少</span></div>' +
    svg +
    '<div class="cmax"><span>' + apx(p.lo) + '</span>' +
      '<span class="cmnow">现价 ' + apx(px) + '</span>' +
      '<span>' + apx(p.hi) + '</span></div>' +
    '<div class="cmleg">' +
      '<span><i class="sw blu"></i>现价以下的成交</span>' +
      '<span><i class="sw amb"></i>' + term("上方套牢", GLOSSARY["套牢盘"]) + ' <b>' +
        (ov == null ? "—" : (ov * 100).toFixed(0) + "%") + '</b></span>' +
      '<span><i class="sw vio"></i>最密集价位 <b>' + apx(p.poc) + '</b></span>' +
      '<span>' + term("价值区", GLOSSARY["价值区"]) + ' <b>' + apx(p.va_low) + '–' + apx(p.va_high) + '</b></span>' +
    '</div></div>';
}

function verdictSection(r) {
  var rec = r.recommendation;
  if (!rec) return "";
  var v = r.validation || {};
  var icS = (v.ic_short || {}), icL = (v.ic_long || {}), legs = (v.legs || {});

  function pickHtml(p, side) {
    var lis = (p.reasons || []).map(function (x) { return "<li>" + annotate(x) + "</li>"; }).join("");
    var stop = p.stop_hint;
    var stopTxt = "";
    if (stop) {
      stopTxt = side === "long"
        ? '<span>' + term("认赔线", GLOSSARY["止损"]) + ' <b>跌破 A$' + num(stop.stop_px, 2) +
          '</b> 就走，别扛（约 -' + pct(stop.stop_pct, 1) + '）</span>'
        : '<span>' + term("认赔线", GLOSSARY["止损"]) + ' <b>涨过 A$' + num(stop.stop_px, 2) +
          '</b> 就走，别扛（约 +' + pct(stop.stop_pct, 1) + '）</span>';
    }
    return '<div class="pick"><div class="ph"><span class="pc">' + esc(p.code) + '</span>' +
      '<span class="pn">' + esc(p.ticker) + ' · 现价 A$' + num(p.px, 2) + '</span>' +
      '<span class="psc">' + (side === "long" ? "看多分" : "看空分") + ' ' + sgn(p.score, 2) + '</span></div>' +
      '<ul>' + lis + '</ul>' +
      '<div class="pm">' +
      '<span>近20天 <b class="' + cls(p.ret_20d) + '">' + spct(p.ret_20d) + '</b></span>' +
      '<span>' + term("空头", GLOSSARY["空头持仓"]) + ' <b>' + num(p.short_pct, 2) + '%</b>（20天' +
      (p.short_chg_20d >= 0 ? "增" : "减") + ' ' + num(Math.abs(p.short_chg_20d || 0), 2) + 'pp）</span>' +
      '<span>' + term("回补天数", GLOSSARY["回补天数"]) + ' <b>' + num(p.days_to_cover, 1) + '</b>天</span>' +
      '<span>' + term("日均成交", GLOSSARY["日均成交"]) + ' <b>A$' + money(p.adv_aud) + '</b></span>' +
      stopTxt + '</div>' + costMap(p.profile, p.px) + '</div>';
  }

  var L = rec.long, S = rec.short;
  return '<div class="verdict">' +
    '<div class="vcard long"><div class="vh"><span class="vside">建议看多方向</span>' +
    '<span class="vsec">' + esc(L.sector_name) + '</span>' +
    '<span class="stage-pill stage-early_in">' + esc(L.stage) + '</span></div>' +
    '<div class="vstage">资金在进（' + sgn(L.flow_score, 2) + '），' +
    (L.heat_score >= 0.5 ? "而且正被热炒" : "还没被炒热") + '，板块近20天 ' + spct(L.ret_20d) + '</div>' +
    '<div class="vnote">' + annotate(L.stage_note) +
    '<br><b style="color:#fbbf24">⚠️ 可信度：弱。</b>说实话，系统这套<b>挑"该买哪只"的方法，过去没被证明有效</b>' +
    '（统计上和碰运气差不多）。历史上照它买，一年赚 ' + spct((legs.long || {}).cagr) +
    '，其实和"随便买一篮子股票"（' + spct((legs.market || {}).cagr) + '）差不多，还更颠簸。' +
    '<b>所以下面这三只当"值得研究的名单"就好，别照着直接下单。</b></div>' +
    L.picks.map(function (p) { return pickHtml(p, "long"); }).join("") + '</div>' +

    '<div class="vcard short"><div class="vh"><span class="vside">建议看空 / 回避方向</span>' +
    '<span class="vsec">' + esc(S.sector_name) + '</span>' +
    '<span class="stage-pill stage-distribution">' + esc(S.stage) + '</span></div>' +
    '<div class="vstage">越来越多机构在赌它跌，同时资金在撤（' + sgn(S.flow_score, 2) +
    '），板块近20天 ' + spct(S.ret_20d) + '</div>' +
    '<div class="vnote">' + annotate(S.stage_note) +
    '<br><b style="color:#fbbf24">⚠️ 可信度：' + esc(icS.verdict_cn || "—") + '。</b>' +
    '系统这套<b>挑"该躲哪只"的方法，在4年半样本里站得住，但只是勉强站住</b>。' +
    '之前报的 t 值 ' + num(icS.t_naive, 1) + ' 是把' + term("重叠样本", GLOSSARY["重叠样本"]) +
    '当成互相独立算出来的，虚高约 ' + num(icS.se_inflation, 1) + ' 倍；' +
    '按规矩修正后是 <b>' + num(icS.t_stat, 2) + '</b>（常规门槛 2）。' +
    '不过<b>"空头篮子确实跑输大盘"这条事实不受影响</b>——那是每' + (v.horizon || 20) +
    '天换一次仓算出来的，本来就没有重叠问题。' +
    '<br><b style="color:#fbbf24">三点务必记住：</b>①"跑输大盘"<b>不等于"一定下跌"</b>——' +
    '历史上这批股票整体自身的收益是 ' + spct((legs.short_basket || {}).cagr) +
    '/年（同期大盘 ' + spct((legs.market || {}).cagr) + '）——' +
    (((legs.short_basket || {}).cagr || 0) >= 0
      ? '也就是说它们<b>照样在涨</b>，只是涨得比大盘慢。'
      : '它们确实在跌，但跌幅远小于「跑输大盘」给人的印象。') + '真要' +
    term("做空", GLOSSARY["做空"]) + '，你还得自己判断大盘方向。' +
    '②<b>真照它做一多一空的中性组合，扣掉手续费后一年只剩 ' +
    spct((legs.long_short_net || {}).cagr) + '（' + term("夏普", GLOSSARY["夏普"]) + ' ' +
    num((legs.long_short_net || {}).sharpe, 2) + '），一年要换 ' + num(v.turnover_pa, 0) +
    ' 批仓</b>。所以它的正确用法是<b>"回避 / 少配一点"，不是"靠它做空赚钱"</b>。' +
    '③这种信号<b>大约每月就会失效一次，名单要经常刷新</b>，拿着不动一两个月反而可能被' +
    term("轧空", GLOSSARY["轧空"]) + '。</div>' +
    S.picks.map(function (p) { return pickHtml(p, "short"); }).join("") + '</div></div>';
}

/* ============ 最顶部：钱正在进哪 / 正在撤哪（按板块 + 具体股票） ============ */
function moneyFlowPanel(r) {
  var mf = r.money_flow;
  if (!mf || (!mf.inflow.length && !mf.outflow.length)) return "";

  function aud(m) {
    if (m == null || isNaN(m)) return "—";
    if (Math.abs(m) >= 1000) return (m / 1000).toFixed(2) + "十亿";
    return m.toFixed(0) + "百万";
  }
  function stk(k, side) {
    var why = (k.why || []).map(function (w) { return annotate(w); }).join("；");
    return '<div class="mstk"><div class="mc">' + esc(k.code) + '</div><div class="mb2">' +
      '<div class="mr">' + (why || "综合多项指标排名靠前") + '</div>' +
      '<div class="mnum">' +
      '<span>价 <b>A$' + num(k.px, 2) + '</b></span>' +
      '<span>近20天 <b class="' + cls(k.ret_20d) + '">' + spct(k.ret_20d) + '</b></span>' +
      (k.net_flow_20d_m != null ? '<span>净' + (side === "in" ? "买" : "卖") + '盘 <b class="' +
        cls(side === "in" ? k.net_flow_20d_m : -k.net_flow_20d_m) + '">A$' + aud(Math.abs(k.net_flow_20d_m)) + '</b></span>' : "") +
      (k.short_pct != null ? '<span>' + term("被做空", GLOSSARY["空头持仓"]) + ' <b>' + num(k.short_pct, 2) + '%</b>' +
        (k.short_chg_20d != null ? '（20天' + (k.short_chg_20d >= 0 ? "增" : "减") + num(Math.abs(k.short_chg_20d), 2) + '）' : "") + '</span>' : "") +
      '</div>' + costMap(k.profile, k.px) + '</div></div>';
  }
  function sec(s, side) {
    return '<div class="msec"><div class="sh"><span class="sn">' + esc(s.name) + '</span>' +
      '<span class="stage-pill stage-' + (side === "in" ? "early_in" : "distribution") + '">' + esc(s.stage) + '</span>' +
      '<span class="sm">板块近20天 ' + spct(s.ret_20d) + '</span></div>' +
      s.stocks.map(function (k) { return stk(k, side); }).join("") + '</div>';
  }

  return '<div class="mflow">' +
    '<div class="mcol in"><h3>💰 钱正在进这里</h3>' +
    '<div class="sub">按板块排序，每个板块列出资金迹象最明显的几只股票。下面每条都是<b>当前实测</b>，不是预测。</div>' +
    (mf.inflow.map(function (s) { return sec(s, "in"); }).join("") || '<div class="sub">当前没有明显流入</div>') +
    '</div>' +
    '<div class="mcol out"><h3>🚪 钱正在从这里撤</h3>' +
    '<div class="sub">卖压最重、或机构正在加码看跌的地方。<b>手上有这些要留意。</b></div>' +
    (mf.outflow.map(function (s) { return sec(s, "out"); }).join("") || '<div class="sub">当前没有明显流出</div>') +
    '</div></div>' +
    '<div class="mflow-note">📖 <b>这个面板怎么来的：</b>"净买/卖盘"是用每天收盘价靠近当天最高还是最低、' +
    '再乘以成交额累加出来的（估算买卖压力，不是结算数据）；"被做空"是<b>澳洲法律要求机构每天申报的真实仓位</b>。' +
    '<br><b>⚠️ 重要：</b>板块排序是<b>对现在的测量</b>，不是"下个月这个板块会涨/跌"的预测——' +
    '实测显示板块层面的资金变化<b>落后</b>于价格（相关峰值在滞后4周），没有领先性。' +
    '真正经过验证的是<b>板块内部哪只股票会跑输</b>（这个统计上显著）。</div>';
}

/* 机构持股/董事公告存档 —— 只展示，不参与打分（历史不足以验证） */
function announcementSection(r) {
  var a = r.announcements;
  if (!a || !a.available) return "";
  var rows = (a.sectors || []).slice(0, 6).map(function (s) {
    return '<tr><td class="name">' + esc(s.name) + '</td>' +
      '<td class="up">' + s.n_603 + '</td><td>' + s.n_604 + '</td>' +
      '<td class="dn">' + s.n_605 + '</td><td>' + s.n_3y + '</td>' +
      '<td class="' + cls(s.net_count) + '">' + sgn(s.net_count, 0) + '</td>' +
      '<td class="mut">' + s.n_stocks + '</td></tr>';
  }).join("");
  var recent = (a.recent || []).slice(0, 8).map(function (x) {
    return '<tr><td class="name">' + esc(x.code) + '</td><td>' + esc(x.form) + '</td>' +
      '<td class="mut">' + esc(x.ts) + '</td><td class="name mut" style="white-space:normal">' +
      esc((x.headline || "").slice(0, 60)) + '</td></tr>';
  }).join("");

  var ar = r.archive || {};
  var prog = ar.progress_pct || 0;
  var growth = '<div class="annbox" style="margin:12px 0;background:rgba(59,130,246,.07);' +
    'border:1px solid rgba(59,130,246,.25);border-radius:9px;padding:11px 14px">' +
    '📈 <b>累积进度</b>：每次更新系统都会把当天新数据存进档案，<b>越用越准</b>。' +
    '<div style="margin:9px 0 6px;height:7px;background:var(--panel2);border-radius:4px;overflow:hidden">' +
    '<i style="display:block;height:100%;width:' + prog + '%;background:linear-gradient(90deg,#2563eb,#22d3ee)"></i></div>' +
    '<span style="font-family:var(--mono);font-size:11px">已存 <b>' + (ar.days || 0) + '</b> 个交易日快照' +
    (ar.since ? '（自 ' + esc(ar.since) + '）' : '') + ' · 公告 <b>' + (ar.filings || 0) + '</b> 条 · ' +
    '距离可做有效性检验还差 <b>' + Math.max(0, 60 - (ar.days || 0)) + '</b> 天（' + prog + '%）</span>' +
    (ar.ready_for_validation ? '<br><b style="color:#4ade80">✅ 数据已够，可以检验这些信号到底有没有预测力了。</b>'
      : '<br>攒够约60个交易日后，就能跑规格要求的检验，通过了才纳入评分。') + '</div>';

  return '<details class="sec" style="margin-bottom:22px"><summary>🏛️ 机构举牌与董事增减持（存档累积中，暂不参与打分）' +
    '<span class="annpill">已收 ' + a.total + ' 条 · 快照 ' + (ar.days || 0) + ' 天</span></summary>' +
    '<div class="dbody">' + growth + '<div class="annbox" style="margin:12px 0">' +
    '👉 <b>这是什么：</b>澳洲法律要求任何人持股跨过 5% 必须在 <b>2 个工作日内</b>公告（全球最快），' +
    '董事买卖自家股票也要在 5 天内申报。这是"大资金真的下手了"最硬的证据。<br>' +
    '<b>⚠️ 为什么现在不参与打分：</b>ASX 的公开接口<b>每家公司只能取到最近 5 条</b>，无法回溯历史。' +
    '没有历史就<b>无法检验它到底有没有预测力</b>——你给我的规格里也把这一条列为"生死判决点"。' +
    '所以系统从今天起<b>每天自动采集存档</b>，等积累够（大约1–3个月）再来检验，通过了才纳入评分。' +
    '<br>已自动剔除指数基金（Vanguard/BlackRock 等被动持仓）和证券借贷产生的名义持仓。</div>' +
    '<div class="scroll"><table><thead><tr><th>板块</th>' +
    '<th title="新进：有机构首次买过5%">新举牌</th><th title="已是大股东，持股又变动超过1%">增减持</th>' +
    '<th title="持股跌破5%，机构退出">退出</th><th title="董事买卖自家股票">董事</th>' +
    '<th>净活跃度</th><th>涉及股票</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
    (recent ? '<div style="height:12px"></div><div class="scroll"><table><thead><tr>' +
      '<th>代码</th><th>类型</th><th>日期</th><th>公告标题</th></tr></thead><tbody>' + recent +
      '</tbody></table></div>' : "") + '</div></details>';
}

/* 顶部大白话结论卡：先给一句最直白的行动方向 */
function plainSummary(r) {
  var rec = r.recommendation;
  var d = r.direction || { p_up: 0.5, confidence: 0 };
  var vol = r.forecasts.filter(function (f) { return f.key === "vol_up_20d"; })[0];
  var hot = r.sectors.slice().sort(function (a, b) { return b.heat.score - a.heat.score; })[0];

  var actTxt = "";
  if (rec) {
    var L = rec.long, S = rec.short;
    var longNames = L.picks.map(function (p) { return p.code; }).join("、");
    var shortNames = S.picks.map(function (p) { return p.code; }).join("、");
    actTxt = "系统现在的倾向是：<b class=\"big-in\">偏看多</b> " + esc(L.sector_name) +
      " 板块的 <b>" + esc(longNames) + "</b>；<b class=\"big-out\">偏看空/回避</b> " +
      esc(S.sector_name) + " 板块的 <b>" + esc(shortNames) + "</b>。";
  }
  var hotTxt = hot ? ("现在被炒得最" + term("热", GLOSSARY["热度"]) + "的板块是 <b>" + esc(hot.name) +
    "</b>（成交暴增、价格加速），但热不等于该追——也可能是高位在出货。") : "";
  var volTxt = vol ? ("接下来一个月，市场<b>" +
    (vol.p_final >= 0.55 ? "多半会更颠簸（大涨大跌）" : vol.p_final <= 0.45 ? "多半会更平稳" : "颠簸程度和现在差不多") +
    "</b>（这是全系统最靠谱的一项判断）。") : "";

  return '<div class="plain"><h2>📌 一句话结论（行情 ' + esc(r.as_of) + ' · 空头数据 ' + esc(r.short_as_of || "—") + '）</h2>' +
    (actTxt ? '<div class="pl"><div class="ico">🎯</div><div class="txt">' + actTxt + '</div></div>' : "") +
    (hotTxt ? '<div class="pl"><div class="ico">🔥</div><div class="txt">' + hotTxt + '</div></div>' : "") +
    (volTxt ? '<div class="pl"><div class="ico">🌊</div><div class="txt">' + volTxt + '</div></div>' : "") +
    '<div class="warn-line"><b>最重要的一句：看空的建议靠谱，看多的建议只是参考。</b>' +
    '系统"挑哪只该躲"过去4年半被证明有效，但"挑哪只该买"没被证明有效。而且看空≠预测下跌，只是"会跑输大盘"，' +
    '真做空还得看大盘脸色、且名单要每月刷新。这是参考工具，不是投资建议，盈亏自负。</div></div>';
}

function headCards(r) {
  var d = r.direction || { p_up: .5, stance: "—", cls: "neutral", confidence: 0 };
  var vol = r.forecasts.filter(function (f) { return f.key === "vol_up_20d"; })[0];
  var hot = r.sectors.slice().sort(function (a, b) { return b.heat.score - a.heat.score; })[0];
  var shrt = r.sectors.slice().sort(function (a, b) { return b.short.score - a.short.score; })[0];
  var h = "";
  h += '<div class="head ' + esc(d.cls) + '"><div class="k">未来1–20天 · 大盘涨的可能性</div>' +
    '<div class="big">' + pct(d.p_up) + '</div><div class="st">' + esc(d.stance) + '</div>' +
    '<div class="note">' + (d.confidence < 0.15 ?
      "系统实测自己猜大盘方向不比抛硬币强，所以不给追涨杀跌的建议。" :
      "综合了几个时间尺度的判断，并按各自实测准确度加权。") + '</div>' +
    '<div class="foot"><span>' +
    (d.base_rate != null
      ? term("历史平均 " + pct(d.base_rate), "同样长度的时间窗里，历史上大盘上涨的比例。大盘本来就涨多跌少，所以这个数天然高于 50%——它不是模型的功劳。旁边的「模型」才是模型在历史平均之外多说出来的那一点点。")
        + ' · 模型 ' + (d.edge_vs_base_pp >= 0 ? '+' : '') + num(d.edge_vs_base_pp, 2) + 'pp'
      : '上涨概率') +
    '</span><span>' + term("把握度", "系统对这个方向判断有多少信心，0=完全没把握") + ' ' + num(d.confidence, 2) + '</span></div></div>';
  if (vol) {
    h += '<div class="head ' + (vol.p_final >= .6 ? "warn" : vol.p_final <= .4 ? "info" : "") + '">' +
      '<div class="k">未来20天 · 会不会变颠簸</div><div class="big">' + pct(vol.p_final) + '</div>' +
      '<div class="st">' + (vol.p_final >= .6 ? "多半会更颠簸（大涨大跌）" : vol.p_final <= .4 ? "多半会更平稳" : "颠簸程度无明显变化") + '</div>' +
      '<div class="note">这是<b>全系统最靠谱的一项判断</b>——实测准确度明显高于瞎猜。颠簸变大时记得减仓。</div>' +
      '<div class="foot"><span>' + term("历史平均", "历史上会变颠簸的日子占比") + ' ' + pct(vol.base_rate) + '</span><span>当前颠簸度 ' + pct(r.benchmark.rv20) + '</span></div></div>';
  }
  h += '<div class="head warn"><div class="k">现在被炒得最火的板块</div>' +
    '<div class="big" style="font-size:25px">' + esc(hot.name) + '</div>' +
    '<div class="st">' + esc(hot.heat.label) + '（' + term("热度", GLOSSARY["热度"]) + ' ' + sgn(hot.heat.score, 2) + '）</div>' +
    '<div class="note">成交额比平时明显放大、价格在加速，板块里 ' + pct(hot.breadth.pct_overbought, 0) +
    ' 的股票已经超买。<b>火不等于该追</b>，也可能是高位在出货。</div>' +
    '<div class="foot"><span>' + esc(hot.stage.label) + '</span><span>近20天 ' + spct(hot.perf.ret_20d) + '</span></div></div>';
  h += '<div class="head bear"><div class="k">被机构下注看跌最多的板块</div>' +
    '<div class="big" style="font-size:25px">' + esc(shrt.name) + '</div>' +
    '<div class="st">' + esc(shrt.short.label) + '</div>' +
    '<div class="note">这个板块被' + term("机构做空", GLOSSARY["空头持仓"]) + '的规模约 ' + num(shrt.raw.short_pct, 2) +
    '%，过去20天' + (shrt.raw.short_chg_20d >= 0 ? "还在增加" : "在减少") + '（' + sgn(shrt.raw.short_chg_20d, 2) + 'pp）。</div>' +
    '<div class="foot"><span>' + esc(shrt.stage.label) + '</span><span>近20天 ' + spct(shrt.perf.ret_20d) + '</span></div></div>';
  return '<div class="grid heads">' + h + '</div>';
}

function sectorTable(r) {
  var rows = r.sectors.map(function (s, i) {
    return '<tr class="clk" onclick="toggleSector(' + i + ')">' +
      '<td class="name">' + esc(s.name) + '<span class="mut" style="font-weight:400;font-size:11px"> · ' + s.n + '只</span></td>' +
      '<td>' + zBar(s.flow.score, 2.5) + '</td>' +
      '<td class="' + cls(s.flow.score) + '">' + sgn(s.flow.score, 2) + '</td>' +
      '<td><span class="pill heat-' + esc(s.heat.cls) + '">' + esc(s.heat.label) + ' ' + sgn(s.heat.score, 1) + '</span></td>' +
      '<td class="' + cls(s.extension.score) + '">' + sgn(s.extension.score, 2) + '</td>' +
      '<td><span class="stage-pill stage-' + esc(s.stage.key) + '">' + esc(s.stage.label) + '</span></td>' +
      '<td><span class="pill sh-' + esc(s.short.cls) + '">' + sgn(s.short.score, 2) + '</span></td>' +
      '<td>' + num(s.raw.short_pct, 2) + '%</td>' +
      '<td class="' + cls(s.raw.short_chg_20d) + '">' + sgn(s.raw.short_chg_20d, 2) + '</td>' +
      '<td><span class="pill ' + esc(s.rotation.quadrant) + '">' + esc(s.rotation.quadrant_cn) + '</span></td>' +
      '<td>' + num((s.breadth.pct_above_ma50 || 0) * 100, 0) + '%</td>' +
      '<td class="' + cls(s.perf.ret_5d) + '">' + spct(s.perf.ret_5d) + '</td>' +
      '<td class="' + cls(s.perf.ret_20d) + '">' + spct(s.perf.ret_20d) + '</td>' +
      '<td>' + svgLine({ values: (s.short_history || {}).v || [], color: "var(--dn)", h: 24 }) + '</td></tr>' +
      '<tr id="secdet' + i + '" style="display:none"><td colspan="14" style="padding:0;background:var(--panel2)">' +
      sectorDetail(s) + '</td></tr>';
  }).join("");
  return '<div class="panel scroll"><table><thead><tr><th>板块</th><th style="text-align:center">资金强弱</th>' +
    '<th title="综合多项指标算出的资金总分，正=在进，负=在撤">资金总分</th>' +
    '<th class="term" title="' + GLOSSARY["热度"] + '">被炒热度</th>' +
    '<th class="term" title="' + GLOSSARY["延展度"] + '">涨太多没</th><th>资金阶段</th>' +
    '<th title="综合算出的被机构看跌压力，越高越被看空">看空压力</th>' +
    '<th class="term" title="' + GLOSSARY["空头持仓"] + '">被做空规模</th>' +
    '<th title="被做空规模过去20天变了多少个百分点，正=机构在加码看空">近20天变化</th>' +
    '<th class="term" title="' + GLOSSARY["象限"] + '">轮动阶段</th>' +
    '<th title="板块里有多大比例的股票在走强（站上50日均线）">走强股占比</th>' +
    '<th>近5天</th><th>近20天</th><th>被做空趋势</th></tr></thead><tbody>' +
    rows + '</tbody></table></div>';
}

function sectorDetail(s) {
  var names = { cmf_z: "蔡金资金流", signed_z: "主动买盘", obv_z: "OBV斜率", dollarvol_z: "成交额异动",
                mfi_z: "MFI", rs_ratio_z: "RS相对强度", rs_mom_z: "RS动量", shortcover_z: "空头回补" };
  var bars = Object.keys(names).map(function (k) {
    var z = (s.flow.components || {})[k];
    return '<tr><td class="name">' + names[k] + '</td><td>' + zBar(z, 2.5) + '</td><td class="' + cls(z) + '">' + sgn(z, 2) + '</td></tr>';
  }).join("");
  var st = (s.stocks || []).map(function (k) {
    return '<tr><td class="name">' + esc(k.code) + '</td>' +
      '<td class="' + cls(k.ret_20d) + '">' + spct(k.ret_20d) + '</td>' +
      '<td class="' + cls(k.ret_60d) + '">' + spct(k.ret_60d) + '</td>' +
      '<td>' + num(k.rsi14, 0) + '</td>' +
      '<td class="' + cls(k.cmf20) + '">' + num(k.cmf20, 3) + '</td>' +
      '<td>' + num(k.short_pct, 2) + '%</td>' +
      '<td class="' + cls(k.short_chg_20d) + '">' + sgn(k.short_chg_20d, 2) + '</td>' +
      '<td>' + num(k.days_to_cover, 1) + '</td>' +
      '<td class="' + cls(k.dist_ma50) + '">' + spct(k.dist_ma50) + '</td>' +
      '<td class="mut">' + money(k.adv_aud) + '</td></tr>';
  }).join("");
  return '<div style="padding:14px 18px;display:grid;grid-template-columns:.85fr 1.6fr;gap:22px">' +
    '<div><div style="font-size:11.5px;color:var(--mut);margin-bottom:7px">资金评分构成（横截面Z分）</div>' +
    '<table><thead><tr><th>分项</th><th style="text-align:center">强度</th><th>Z分</th></tr></thead><tbody>' + bars + '</tbody></table>' +
    '<div style="font-size:11.5px;color:var(--mut);margin-top:11px;line-height:1.6">' + esc(s.stage.note) + '</div></div>' +
    '<div><div style="font-size:11.5px;color:var(--mut);margin-bottom:7px">成分股（按20日涨跌排序）</div>' +
    '<table><thead><tr><th>代码</th><th>20日</th><th>60日</th><th>RSI</th><th>CMF</th><th>空头%</th>' +
    '<th>Δ20日</th><th>回补天</th><th>距MA50</th><th>日均成交</th></tr></thead><tbody>' + st + '</tbody></table></div></div>';
}

function shortSection(r) {
  function tbl(list, key, title, hint) {
    var rows = list.map(function (k) {
      return '<tr><td class="name">' + esc(k.code) + '<span class="mut" style="font-size:11px"> ' + esc(k.sector_name) + '</span></td>' +
        '<td>' + num(k.short_pct, 2) + '%</td>' +
        '<td class="' + cls(k.short_chg_20d) + '">' + sgn(k.short_chg_20d, 2) + '</td>' +
        '<td class="mut">' + pct(k.short_pctile_1y, 0) + '</td>' +
        '<td>' + num(k.days_to_cover, 1) + '</td>' +
        '<td class="' + cls(k.ret_20d) + '">' + spct(k.ret_20d) + '</td>' +
        '<td class="' + cls(k.cmf20) + '">' + num(k.cmf20, 3) + '</td>' +
        '<td class="' + cls(k.short_score) + '">' + sgn(k.short_score, 2) + '</td></tr>';
    }).join("");
    return '<div class="chartbox"><h3>' + title + '</h3><div class="cs">' + hint + '</div>' +
      '<div class="scroll"><table><thead><tr><th>代码</th>' +
      '<th class="term" title="' + GLOSSARY["空头持仓"] + '">被做空</th>' +
      '<th title="被做空规模过去20天变化了多少个百分点，正=机构在加码看空">近20天变化</th>' +
      '<th title="现在的被做空规模，在这只股票过去1年里算高还是低。90%=比过去九成时间都高">1年内高低</th>' +
      '<th class="term" title="' + GLOSSARY["回补天数"] + '">回补天数</th><th>近20天涨跌</th>' +
      '<th class="term" title="' + GLOSSARY["蔡金资金流"] + '">买盘力度</th><th>看空分</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
  }
  return '<div class="grid" style="grid-template-columns:1fr 1fr">' +
    tbl(r.most_shorted, "short_pct", "被机构做空最重的股票", "机构申报的看跌仓位占总股本的比例，越高说明越多机构在赌它跌") +
    tbl(r.short_building, "short_chg_20d", "机构最近加码看空最猛的股票", "过去20天被做空规模上升最多——机构正在新建看跌仓位，这往往比“早就被看跌”更有信息量") + '</div>';
}

function validationSection(r) {
  var v = r.validation;
  if (!v) return "";
  var legs = v.legs || {};
  var chart = bigLineChart({
    dates: v.curve.dates, h: 240, fmt: function (x) { return x.toFixed(1) + "x"; },
    series: [
      { v: v.curve.lng, color: "#22c55e", w: 1.8 },
      { v: v.curve.sht, color: "#ef4444", w: 1.8 },
      { v: v.curve.ew, color: "#8790a5", w: 1.4, dash: "4 3" }
    ]
  });
  function legRow(k) {
    var s = legs[k]; if (!s) return "";
    return '<tr><td class="name">' + esc(s.label) + '</td><td class="' + cls(s.cagr) + '">' + spct(s.cagr) + '</td>' +
      '<td>' + pct(s.vol) + '</td><td>' + num(s.sharpe, 2) + '</td><td class="dn">' + pct(s.max_dd) + '</td></tr>';
  }
  function icRow(nm, s, want) {
    if (!s) return "";
    var good = want === "neg" ? (s.mean_ic < 0 && Math.abs(s.t_stat) >= 2) : (s.mean_ic > 0 && s.t_stat >= 2);
    return '<tr><td class="name">' + nm + '</td><td class="' + (good ? "up" : "mut") + '">' + num(s.mean_ic, 4) + '</td>' +
      '<td class="' + (good ? "up" : "mut") + '"><b>' + num(s.t_stat, 2) + '</b></td>' +
      '<td class="mut">' + num(s.t_naive, 2) + '</td><td>' + pct(s.hit_rate, 1) + '</td>' +
      '<td class="mut">' + s.n_days + '</td><td><span class="pill ' + esc(s.verdict || "none") + '">' +
      esc(s.verdict_cn || "—") + '</span></td></tr>';
  }
  return '<div class="grid" style="grid-template-columns:1.5fr 1fr">' +
    '<div class="chartbox"><h3>如果一直照系统信号操作，历史上会怎样 · ' + v.start + ' 至 ' + v.end + '</h3>' +
    '<div class="cs">每边各挑 ' + v.n_side + ' 只，每 ' + v.horizon + ' 个交易日换一批。只用当天之前看得到的信息，机构做空数据也按它实际公开的日期对齐（不作弊看未来）</div>' +
    chart + '<div class="legend"><span><i style="background:#22c55e"></i>系统看多的那批</span>' +
    '<span><i style="background:#ef4444"></i>系统看空的那批（它们自己的涨跌）</span>' +
    '<span><i style="background:#8790a5"></i>随便买一篮子做对比</span></div></div>' +
    '<div class="panel"><table><thead><tr><th>组合</th><th class="term" title="' + GLOSSARY["年化"] +
    '">一年赚多少</th><th class="term" title="' + GLOSSARY["波动率"] + '">颠簸</th>' +
    '<th class="term" title="' + GLOSSARY["夏普"] + '">稳不稳</th>' +
    '<th class="term" title="' + GLOSSARY["最大回撤"] + '">最惨亏多少</th></tr></thead>' +
    '<tbody>' + legRow("long") + legRow("short_basket") + legRow("market") +
    legRow("long_short_gross") + legRow("long_short_net") + '</tbody></table>' +
    '<div style="height:14px"></div>' +
    '<table><thead><tr><th>这套打分</th><th class="term" title="' + GLOSSARY["IC"] + '">吻合度(IC)</th>' +
    '<th class="term" title="' + GLOSSARY["t值"] + '">是不是运气(t)</th>' +
    '<th class="term" title="' + GLOSSARY["重叠样本"] + '">未修正的t</th>' +
    '<th>每日猜对率</th><th>用了多少天</th><th>结论</th></tr></thead>' +
    '<tbody>' + icRow("挑该买的（看多分）", v.ic_long, "pos") + icRow("挑该躲的（看空分，越负越好）", v.ic_short, "neg") + '</tbody></table>' +
    '<div class="foot-note" style="margin-top:12px">一年换 <b>' + num(v.turnover_pa, 1) + '</b> 批，已扣手续费。可选股票约 ' + v.universe_median + ' 只。' +
    '<b>"未修正的t"是把' + term("重叠样本", GLOSSARY["重叠样本"]) + '当成互相独立算出来的，会虚高 3–5 倍；请看加粗那一列。</b>' +
    '修正后：看空分勉强达标，看多分依旧和运气差不多——这就是为什么上面说"看空名单可参考、看多名单只作研究线索"。</div></div></div>';
}

function forecastTable(r) {
  var rows = r.forecasts.map(function (f) {
    var m = f.metrics || {}, mr = f.metrics_recent || {};
    var col = f.group === "risk" ? "var(--amb)" : "var(--blu)";
    var edge = f.p_final - f.base_rate;
    return '<tr><td class="name">' + esc(f.name) + '</td><td>' + probBar(f.p_final, f.base_rate, col) + '</td>' +
      '<td class="' + cls(edge) + '">' + (Math.abs(edge) < .002 ? "0.0" : sgn(edge * 100, 1)) + 'pp</td>' +
      '<td class="mut">' + pct(f.base_rate) + '</td><td>' + num(f.shrink_lambda, 2) + '</td>' +
      '<td>' + num(m.auc, 3) + '</td>' +
      '<td class="' + (f.beats_naive === false ? "dn" : "mut") + '"' +
        (f.naive_name ? ' title="' + esc(f.naive_name) + '"' : "") + '>' +
        (f.naive_auc == null ? "—" : num(f.naive_auc, 3)) + '</td>' +
      '<td class="' + ((f.bss_published != null ? f.bss_published : (m.brier_skill_score || 0)) > 0 ? "up" : "mut") + '"' +
        (f.bss_published != null
          ? ' title="这是你实际看到的那个概率（已按信任度收缩后）的成绩。收缩前是 ' + num(m.brier_skill_score, 4) + '。"'
          : "") + '>' +
        (f.bss_published != null ? sgn(f.bss_published, 4) : sgn(m.brier_skill_score, 4)) + '</td>' +
      '<td class="mut">' + num(mr.auc, 3) + '</td><td class="mut">' + (m.n || "—") + '</td>' +
      '<td><span class="pill ' + esc(f.skill) + '">' + esc(f.skill_cn) + '</span>' +
      (f.source && f.source !== "model"
        ? '<div class="mut" style="font-size:10px;margin-top:3px" title="43因子模型在同样本上输给了这个简单方法，按 Welch-Goyal 规则由它接管。表中 AUC/BSS 即它的实测成绩。">已改用：' + esc(f.source_cn || f.source) + '</div>'
        : "") + '</td></tr>';
  }).join("");
  return '<div class="panel scroll"><table><thead><tr><th>预测什么</th>' +
    '<th style="text-align:center">系统给的概率（灰线=历史平均）</th>' +
    '<th title="这个概率比历史平均高/低了多少个百分点">比平时高多少</th>' +
    '<th class="term" title="' + GLOSSARY["基准率"] + '">历史平均</th>' +
    '<th class="term" title="系统对这条预测有多信任：0=实测没优势，直接用历史平均值">信任度</th>' +
    '<th class="term" title="' + GLOSSARY["AUC"] + '">准不准(AUC)</th>' +
    '<th class="term" title="' + GLOSSARY["朴素基线"] + '">不用模型能到多少</th>' +
    '<th class="term" title="' + GLOSSARY["Brier"] + '">靠不靠谱(BSS)</th>' +
    '<th title="最近3年的准确度，看这条优势现在是不是还在">近3年准不准</th><th>用了多少历史</th><th>总评</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

function signalSection(r) {
  var bl = r.block_labels || {};
  var cards = Object.keys(r.blocks || {}).map(function (k) {
    var v = r.blocks[k];
    return '<div class="bcard"><div class="bn">' + esc(bl[k] || k) + '</div><div class="bv ' + cls(v) + '">' +
      sgn(v, 2) + '</div>' + zBar(v, 2) + '</div>';
  }).join("");
  var byBlock = {};
  (r.signals || []).forEach(function (s) { (byBlock[s.block] = byBlock[s.block] || []).push(s); });
  var tabs = Object.keys(byBlock).map(function (b) {
    var rows = byBlock[b].sort(function (a, c) { return c.stance - a.stance; }).map(function (s) {
      return '<tr><td class="name">' + esc(s.label) + '</td><td>' + zBar(s.stance, 3) + '</td>' +
        '<td class="' + cls(s.stance) + '">' + sgn(s.stance, 2) + '</td><td class="mut">' + num(s.zscore, 2) + '</td>' +
        '<td class="mut">' + pct(s.percentile, 0) + '</td><td class="mut">' + (s.prior_sign > 0 ? "顺向" : "反向") + '</td>' +
        '<td>' + (s.hit_rate_2y == null ? "—" : pct(s.hit_rate_2y, 1)) + '</td></tr>';
    }).join("");
    return '<details class="sec"><summary>' + esc(bl[b] || b) +
      '<span class="mut" style="font-weight:400;font-size:11.5px">（' + byBlock[b].length + ' 项，板块得分 ' +
      sgn((r.blocks || {})[b], 2) + '）</span></summary><div class="dbody scroll"><table><thead><tr>' +
      '<th>因子</th><th style="text-align:center">当前立场</th><th>立场分</th><th>Z分</th><th>分位</th>' +
      '<th>先验</th><th>2年胜率</th></tr></thead><tbody>' + rows + '</tbody></table></div></details>';
  }).join("");
  return '<div class="blocks">' + cards + '</div>' + tabs;
}

function backtestSection(r) {
  var b = r.backtest;
  if (!b) return "";
  var st = b.stats_strategy, sg = b.stats_strategy_gross, bm = b.stats_benchmark;
  var chart = bigLineChart({
    dates: b.dates, h: 230, fmt: function (v) { return v.toFixed(1) + "x"; },
    series: [{ v: b.strategy, color: "#22c55e", w: 1.9 }, { v: b.benchmark, color: "#8790a5", w: 1.4 }]
  });
  function row(n, s) {
    return '<tr><td class="name">' + n + '</td><td class="' + cls(s.cagr) + '">' + spct(s.cagr) + '</td>' +
      '<td>' + pct(s.vol) + '</td><td>' + num(s.sharpe, 2) + '</td><td class="dn">' + pct(s.max_dd) + '</td></tr>';
  }
  return '<div class="grid" style="grid-template-columns:1.5fr 1fr">' +
    '<div class="chartbox"><h3>大盘择时样本外净值 · ' + esc(b.target) + '模型</h3>' +
    '<div class="cs">' + b.years + ' 年样本外，概率次日生效，已扣 ' + b.cost_bps + 'bp/边</div>' + chart +
    '<div class="legend"><span><i style="background:#22c55e"></i>策略（净）</span>' +
    '<span><i style="background:#8790a5"></i>ASX200 买入持有</span></div></div>' +
    '<div class="panel"><table><thead><tr><th>组合</th><th>年化</th><th>波动</th><th>夏普</th><th>最大回撤</th></tr></thead><tbody>' +
    row("策略（扣成本）", st) + row("策略（毛）", sg) + row("买入持有", bm) + '</tbody></table>' +
    '<div class="foot-note" style="margin-top:13px">平均仓位 <b>' + pct(b.exposure, 0) + '</b>　年换手 <b>' +
    num(b.turnover_pa, 1) + '</b> 次　阈值 <b>' + b.threshold + '</b></div></div></div>';
}

function macroSection(r) {
  var rows = (r.macro || []).map(function (m) {
    return '<tr><td class="name">' + esc(m.name) + '<span class="mut" style="font-size:11px"> ' + esc(m.ticker) + '</span></td>' +
      '<td>' + num(m.last, m.last > 500 ? 0 : 4) + '</td>' +
      '<td class="' + cls(m.chg_1d) + '">' + spct(m.chg_1d) + '</td>' +
      '<td class="' + cls(m.chg_5d) + '">' + spct(m.chg_5d) + '</td>' +
      '<td class="' + cls(m.chg_20d) + '">' + spct(m.chg_20d) + '</td></tr>';
  }).join("");
  return '<div class="panel scroll"><table><thead><tr><th>标的</th><th>最新</th><th>当日</th><th>5日</th><th>20日</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>';
}

function methodology(r) {
  var c = r.coverage, v = r.validation || {};
  return '<details class="sec"><summary>它是怎么算的、数据哪来的、有哪些做不到（点击展开）</summary><div class="dbody">' +
  '<div class="warnbox" style="margin:13px 0"><b>一句话：这是参考工具，不是投资建议。</b>系统猜大盘涨跌的实测优势极其微弱；' +
  '挑股票方面，<b>只有"该躲哪只"（看空）通过了严格检验，"该买哪只"（看多）没有</b>。' +
  '做空风险理论上无上限。任何据此下单的决定和后果都由你自己承担。</div>' +

  '<div class="foot-note"><b>数据来源</b><br>' +
  '· 行情：Yahoo Finance 日线 OHLCV，' + c.stocks + ' 只个股 + 指数与商品，' + c.trading_days +
  ' 个交易日（自 ' + c.history_from + '）<br>' +
  '· 空头持仓：<b>ASIC 强制披露的每日个股空头仓位</b>（download.asic.gov.au 年度汇总 CSV），' +
  c.short_days + ' 个交易日（自 ' + c.short_from + '），当前覆盖 ' + c.short_stocks + ' 只。' +
  'ASIC 按 T+4 个营业日发布，系统已把数据对齐到<b>它实际公开的那一天</b>，避免使用当时还看不到的信息<br>' +
  '· 板块：' + c.sectors + ' 个 GICS 板块由成分股自下而上合成（市值加权，单一权重上限25%）。' +
  '市值由「空头股数 ÷ 空头占比」反推出总股本再乘以股价<br><br>' +

  '<b>四个分数各自回答什么</b><br>' +
  '· <b>资金</b>：钱在进还是在出（蔡金资金流、主动买盘、OBV、成交额、相对强度、空头回补）<br>' +
  '· <b>热度</b>：是否正在被炒作（成交额异动、价格加速、波动扩张、超买占比、近52周高点占比、振幅）。' +
  '热度不等于资金——一个板块可以在高换手中被派发<br>' +
  '· <b>延展度</b>：价格已经走了多远。与资金交叉，就把「资金刚开始进」和「资金已经进完且拥挤」分开<br>' +
  '· <b>做空压力</b>：空头水平、空头增仓、资金流出、广度恶化、相对强度破位、超涨<br><br>' +

  '<b>建模纪律</b><br>' +
  '· 全部为<b>样本外</b>：扩张窗口每21个交易日重训，只能看到预测日之前的数据<br>' +
  '· <b>净化与禁运</b>：h 日前瞻标签会泄漏 h 日未来信息，训练集截止到 t−h−5（López de Prado 2018）<br>' +
  '· <b>三模型等权组合</b>：L2 逻辑回归 + 梯度提升树 + 单因子预测均值组合（Rapach, Strauss & Zhou 2010）<br>' +
  '· <b>滚动 Platt 校准</b>：原始模型普遍「有区分度但过度自信」<br>' +
  '· <b>按技能收缩</b>：λ 取全样本与近3年 AUC 中较保守者；AUC≤0.5 时 λ=0，直接报告基准率<br>' +
  '· 选股评分用<b>横截面 IC、五分位价差、多空组合</b>三种口径交叉验证，' +
  '而不是只看一条净值曲线<br><br>' +

  '<b>已知局限（请务必读）</b><br>' +
  '· <b>做多评分未通过显著性检验</b>（IC t=' + num((v.ic_long || {}).t_stat, 2) +
  '）。做多篮子年化 ' + spct(((v.legs || {}).long || {}).cagr) + ' vs 等权市场 ' +
  spct(((v.legs || {}).market || {}).cagr) + '，超额接近零且波动更高<br>' +
  '· <b>做空评分只是边缘显著</b>（修正重叠样本后 t=' + num((v.ic_short || {}).t_stat, 2) +
  '，未修正时看起来是 ' + num((v.ic_short || {}).t_naive, 2) + '），<b>且衡量的是「跑输」而非「下跌」。</b>' +
  '样本期内空头篮子自身的收益是 ' + spct(((v.legs || {}).short_basket || {}).cagr) +
  '/年（同期大盘 ' + spct(((v.legs || {}).market || {}).cagr) + '）。裸空需要额外的大盘方向判断<br>' +
  '· <b>空头信号衰减很快</b>：实测 20 日再平衡时空头篮子跑输约 10pp/年，40 日降到约 6pp，' +
  '60 日及以上信号消失甚至反转（轧空）。<b>名单需要大约每月刷新一次</b><br>' +
  '· 市场中性多空组合扣成本后年化仅约 ' + spct(((v.legs || {}).long_short_net || {}).cagr) +
  '——换手成本吃掉了大部分毛收益<br>' +
  '· 板块成分为静态名单，存在幸存者偏差<br>' +
  '· 未纳入基本面（估值、盈利修正）、期权隐含波动率曲面、融券成本与可借额度。' +
  '<b>实际做空还需确认券源与融券费率</b><br>' +
  '· 澳股派息集中且金额大，本系统所有<b>跨日</b>的价格指标均使用复权价；' +
  '若改回未复权价，银行与 REITs 会在除息日被系统性误判为资金流出<br><br>' +

  '<b>主要文献依据</b><ol class="refs">' +
  '<li>Welch, I. &amp; Goyal, A. (2008). A Comprehensive Look at the Empirical Performance of Equity Premium Prediction. <i>RFS</i>.</li>' +
  '<li>Campbell, J. &amp; Thompson, S. (2008). Predicting Excess Stock Returns Out of Sample. <i>RFS</i>.</li>' +
  '<li>Rapach, D., Strauss, J. &amp; Zhou, G. (2010). Out-of-Sample Equity Premium Prediction: Combination Forecasts. <i>RFS</i>.</li>' +
  '<li>Gu, S., Kelly, B. &amp; Xiu, D. (2020). Empirical Asset Pricing via Machine Learning. <i>RFS</i>.</li>' +
  '<li>Asquith, P., Pathak, P. &amp; Ritter, J. (2005). Short Interest, Institutional Ownership, and Stock Returns. <i>JFE</i>.</li>' +
  '<li>Boehmer, E., Jones, C. &amp; Zhang, X. (2008). Which Shorts Are Informed? <i>Journal of Finance</i>.</li>' +
  '<li>Moskowitz, T., Ooi, Y.H. &amp; Pedersen, L. (2012). Time Series Momentum. <i>JFE</i>.</li>' +
  '<li>Jegadeesh, N. &amp; Titman, S. (1993); Moskowitz &amp; Grinblatt (1999). 动量与行业动量。</li>' +
  '<li>Lee, C. &amp; Ready, M. (1991). Inferring Trade Direction from Intraday Data. <i>JF</i>.</li>' +
  '<li>Amihud, Y. (2002). Illiquidity and Stock Returns. <i>JFM</i>.</li>' +
  '<li>Corsi, F. (2009). A Simple Approximate Long-Memory Model of Realized Volatility. <i>JFEC</i>.</li>' +
  '<li>López de Prado, M. (2018). <i>Advances in Financial Machine Learning</i>.</li>' +
  '<li>de Kempenaer, J. — Relative Rotation Graphs（本系统为公开复现版）。</li>' +
  '</ol></div></div></details>';
}

/* ---------------- master render ---------------- */
/* ============ 轧空压力榜：空头建在哪个价位、现在是赚是亏 ============ */
function squeezeSection(r) {
  var rows = r.short_cost || [];
  var dh = r.data_health || {};
  var halted = (dh.halted || []).map(function (h) {
    return esc(h.code) + '（最后成交 ' + esc(h.last_trade) + '，已停 ' + h.stale_days + ' 天）';
  }).join("、");
  var haltNote = halted
    ? '<div class="foot-note" style="margin-top:10px">🚫 <b>已停止成交、已从所有名单中剔除：</b>' + halted +
      '。停牌或退市的股票会永远停在最后一个价位上，若不剔除，系统会把一个死掉的价格当成今天的价格。</div>'
    : "";
  if (!rows.length) return haltNote ? '<section>' + haltNote + '</section>' : "";

  function row(k) {
    var loss = k.pnl < 0;
    var thin = k.coverage < 0.8;
    var adds = k.adds || [];
    var big = adds[0];
    var addTxt = "—", addTip = "";
    if (big) {
      addTxt = esc(big.date) + ' @ A$' + num(big.px, 2);
      addTip = adds.map(function (a) {
        return a.date + " 加空 " + (a.shares / 1e4).toFixed(0) + "万股（占现仓位 " +
          (a.pct_of_pos * 100).toFixed(1) + "%），当天价 A$" + Number(a.px).toFixed(2);
      }).join(String.fromCharCode(10));
    }
    return '<tr><td class="name"><b>' + esc(k.code) + '</b></td>' +
      '<td>' + num(k.short_pct, 2) + '%</td>' +
      '<td>A$' + num(k.px, 2) + '</td>' +
      '<td>A$' + num(k.cost, 2) + '</td>' +
      '<td class="' + (loss ? "dn" : "up") + '"><b>' + (k.pnl >= 0 ? "+" : "") + num(k.pnl * 100, 1) + '%</b></td>' +
      '<td class="' + (thin ? "amb" : "mut") + '">' + num(k.coverage * 100, 0) + '%</td>' +
      '<td class="mut"' + (addTip ? ' title="' + esc(addTip) + '"' : "") + '>' + addTxt +
        (big ? ' <span class="mut">(' + num(big.pct_of_pos * 100, 0) + '%)</span>' : "") + '</td>' +
      '<td class="mut">' + esc(k.since || "—") + '</td>' +
      '<td>' + (loss
        ? '<span class="pill strong-out">浮亏 · 有轧空压力</span>'
        : '<span class="pill neutral">浮盈 · 无平仓压力</span>') + '</td></tr>';
  }

  return '<section><h2>赌它跌的机构，建仓在什么价位 <span class="tag meas">测量</span></h2>' +
    '<p class="lead">👉 澳洲每天公开全市场的空头总仓位。把它当成一个仓库来记账——' +
    '哪天仓位增加就是<b>有人在那天的价位新开了空单</b>，哪天减少就是<b>最早的空单被平掉</b>——' +
    '就能倒推出<b>目前还没平的空单，平均建在什么价</b>。' +
    '空头<b>亏钱</b>时最危险：他们越亏越可能被迫买回股票止损，把价格越推越高（' +
    term("轧空", GLOSSARY["轧空"]) + '）。</p>' +
    '<div class="panel"><table><thead><tr><th>代码</th>' +
    '<th class="term" title="' + GLOSSARY["空头持仓"] + '">空头占股本</th>' +
    '<th>现价</th><th class="term" title="' + GLOSSARY["空头成本"] + '">估算建仓均价</th>' +
    '<th class="term" title="' + GLOSSARY["空头盈亏"] + '">空头盈亏</th>' +
    '<th class="term" title="' + GLOSSARY["可追溯"] + '">可追溯</th>' +
    '<th class="term" title="近120个交易日里空头加仓最猛的一天：日期与当天均价。鼠标停上去看前三笔。">最大一笔加空</th>' +
    '<th>最早未平仓</th><th>状态</th></tr></thead><tbody>' +
    rows.map(row).join("") + '</tbody></table>' +
    '<div class="foot-note" style="margin-top:12px">' +
    '⚠️ <b>三件必须知道的事：</b>①这是<b>全市场空头的平均数，不是某一家机构</b>——' +
    '<b>澳洲不公开谁在做空</b>（欧盟和英国会公布超过0.5%的持有人姓名，澳洲不会），' +
    '所以"哪家基金、他的成本多少"在澳洲无法得知，这里给的是所有空头合起来的平均建仓价。' +
    '②当天新开的空单按<b>当天均价</b>估算，真实成交价散布在全天，做不到更细。' +
    '<b>「空头盈亏」是相对建仓本金算的</b>：在 A$10 做空、股价涨到 A$20，就是亏掉 100% 本金——' +
    '做空的亏损<b>没有上限</b>，可以超过 100%（做多最多亏光，做空不是）。' +
    '③ASIC 是 <b>T+4</b> 公布，最新一天的数据是4个交易日前的。' +
    '"可追溯"低于80%说明有一批2022年前就存在的老仓位查不到建仓价，该股估算要打折看。' +
    '<b>本表只是测量，不参与任何评分。</b></div>' + haltNote + '</div></section>';
}

function render(r) {
  R = r;
  if (STATIC_MODE) {
    ["btnExport", "btnHard"].forEach(function (id) {
      var b = el(id); if (b) b.style.display = "none";
    });
    var br = el("btnRefresh"); if (br) br.textContent = "⟳ 获取最新";
  }
  renderQuote(r.benchmark);
  el("asof").textContent = "行情截至 " + r.as_of + " · ASIC空头截至 " + (r.short_as_of || "—") +
    " · 生成于 " + r.generated_at + " · 界面 " + buildLabel() +
    (SNAPSHOT ? " · 云盘快照（只读）" : " · 耗时 " + r.runtime_sec + "s");

  var idx = bigLineChart({
    dates: r.benchmark.history.dates, h: 200,
    fmt: function (v) { return (v / 1000).toFixed(1) + "k"; },
    series: [{ v: r.benchmark.history.v, color: "#3b82f6", w: 1.8 }]
  });
  var dirF = r.forecasts.filter(function (f) { return f.group === "direction"; });
  var probHist = bigLineChart({
    dates: (dirF[0] || { history: { dates: [] } }).history.dates, h: 200, zero: .5,
    fmt: function (v) { return (v * 100).toFixed(0) + "%"; },
    series: dirF.map(function (f, i) {
      return { v: f.history.p, color: ["#22d3ee", "#a78bfa", "#f59e0b", "#4ade80"][i % 4], w: 1.4 };
    })
  });

  el("app").innerHTML =
    (SNAPSHOT ? '<div class="snapshot" style="margin-bottom:18px">📁 这是云盘快照版：数据为生成当时的定格，' +
      '在任何电脑上双击即可查看，无需安装任何软件。要更新数据，请回到装有本程序的电脑点击「更新全部指标」，' +
      '再重新导出覆盖此文件。</div>' : "") +
    moneyFlowPanel(r) +
    plainSummary(r) +
    announcementSection(r) +

    '<section><h2>建议：买哪个方向 / 空哪个方向 <span class="tag fcst">推荐</span></h2>' +
    '<p class="lead">👉 下面两张卡就是最终建议，每只股票都写清了"为什么选它"。' +
    '<b>务必先看每张卡里的"可信度"那一行——看空的建议靠谱，看多的建议只是参考，两边差别很大。</b>' +
    '数字都算过过去 ' + ((r.validation || {}).years || "—") + ' 年的实际表现。</p>' +
    verdictSection(r) + '</section>' +

    squeezeSection(r) +

    '<section>' + headCards(r) + '</section>' +

    '<section><h2>钱是"刚开始进"还是"已经进完" <span class="tag meas">实测</span></h2>' +
    '<p class="lead">👉 这张图区分"资金<b>刚有迹象</b>流入"和"资金<b>已经大量</b>流入"。' +
    '左右看资金在进还是在撤，上下看价格已经涨了多少。' +
    '<b>右下角</b>=钱刚进、价格还没怎么涨（最值得关注的买入位置）；' +
    '<b>右上角</b>=钱进完了、价格也高了（追高要小心）；' +
    '<b>左上角</b>=价格还在高位但钱在撤（典型的出货，做空首选）。圆点越大越红=越被炒作。</p>' +
    '<div class="chartbox">' + stageMap(r.sectors) + '</div></section>' +

    '<section><h2>11个板块全景一览 <span class="tag meas">实测</span></h2>' +
    '<p class="lead">👉 每一列表头都可以把鼠标移上去看解释。<b>点任意一行可以展开</b>，看这个板块里每只股票的' +
    '资金、被做空规模、' + term("回补天数", GLOSSARY["回补天数"]) + '等细节。</p>' +
    sectorTable(r) + '</section>' +

    '<section><h2>谁被机构下注看跌最多 · 官方每日披露 <span class="tag meas">实测</span></h2>' +
    '<p class="lead">👉 澳洲法律要求机构每天申报做空仓位，所以这是<b>真实的机构持仓，不是猜的</b>。' +
    '最值得看的是"近20天变化"这一列：机构正在<b>新建</b>看跌仓位，比"早就看跌"更有信息量。</p>' +
    shortSection(r) + '</section>' +

    '<section><h2>板块轮动图：钱在从哪转到哪 <span class="tag meas">实测</span></h2>' +
    '<p class="lead">👉 一个板块通常按顺时针走：<b>改善→领先→转弱→落后</b>。越靠右上越强，越靠左下越弱。</p>' +
    '<div class="chartbox">' + rrgChart(r.sectors) + '</div></section>' +

    '<section><h2>拿历史检验：这套挑股法真管用吗 <span class="tag fcst">证据</span></h2>' +
    '<p class="lead">👉 一套方法如果没在"它从没见过的历史"上验证过，就只是空口白话。这一节就是检验——' +
    '<b>也包括它不管用的部分</b>（看多那套确实没通过检验，系统不藏着）。</p>' + validationSection(r) + '</section>' +

    '<section><h2>大盘涨跌与风险预测 <span class="tag fcst">预测</span></h2>' +
    '<p class="lead">👉 先看最后一列的"总评"——写着"无统计显著预测力"的那几行，系统实测猜不准，别太当真；' +
    '只有标了"显著/中等"的才值得看它的概率。</p>' + forecastTable(r) + '</section>' +

    '<section><h2>系统在看的各项指标 <span class="tag meas">实测</span></h2>' +
    '<p class="lead">👉 喂给系统的 ' + r.coverage.features + ' 个原始信号。"' +
    term("立场分", GLOSSARY["立场分"]) + '"为正=这个信号当前偏多，为负=偏空。给想深入研究的人看的原料清单。</p>' +
    signalSection(r) + '</section>' +

    '<section><h2>历史走势</h2><div class="grid" style="grid-template-columns:1fr 1fr">' +
    '<div class="chartbox"><h3>ASX 200</h3><div class="cs">近 260 个交易日</div>' + idx + '</div>' +
    '<div class="chartbox"><h3>方向概率历史轨迹</h3><div class="cs">各期限校准后概率，虚线为50%中性</div>' +
    probHist + '</div></div></section>' +

    '<section><h2>大盘择时验证</h2>' + backtestSection(r) + '</section>' +
    '<section><h2>跨市场与大宗商品</h2>' + macroSection(r) + '</section>' +
    '<section>' + methodology(r) + '</section>';
}

window.toggleSector = function (i) {
  var d = el("secdet" + i);
  if (d) d.style.display = d.style.display === "none" ? "" : "none";
};

/* ---------------- live-mode refresh ---------------- */
var polling = null;
function setBusy(on) {
  ["btnRefresh", "btnHard", "btnExport"].forEach(function (id) {
    var b = el(id); if (b) b.disabled = on;
  });
  el("btnRefresh").innerHTML = on ? '<span class="spin"></span>更新中 ...' : "⟳ 更新全部指标";
  el("prog").className = on ? "prog on" : "prog";
}
function poll() {
  fetch("api/status").then(function (x) { return x.json(); }).then(function (s) {
    el("progBar").style.width = (s.pct || 0) + "%";
    el("progMsg").textContent = s.msg + (s.error ? "  ✕ " + s.error : "");
    if (!s.running) {
      clearInterval(polling); polling = null; setBusy(false);
      if (s.error) { el("progMsg").textContent = "失败：" + s.error; el("prog").className = "prog on"; }
      else { load(); setTimeout(function () { el("prog").className = "prog"; }, 1400); }
    }
  }).catch(function () { clearInterval(polling); polling = null; setBusy(false); });
}
function refresh(force) {
  setBusy(true);
  el("progBar").style.width = "2%";

  if (STATIC_MODE) {
    // 云端版：没有后台可跑模型，"更新"= 取回服务器上最新一次自动运行的结果
    el("progMsg").textContent = "正在获取最新数据 ...";
    el("progBar").style.width = "45%";
    load();
    setTimeout(function () {
      el("progBar").style.width = "100%";
      el("progMsg").textContent = "已是最新（云端每个交易日自动更新一次）";
      setBusy(false);
      setTimeout(function () { el("prog").className = "prog"; }, 2200);
    }, 900);
    return;
  }

  el("progMsg").textContent = force ? "强制重新下载全部数据 ..." : "启动 ...";
  fetch("api/refresh" + (force ? "?force=1" : ""), { method: "POST" })
    .then(function () { if (!polling) polling = setInterval(poll, 700); })
    .catch(function () { setBusy(false); });
}
var STATIC_MODE = false;
function load() {
  var bust = "?t=" + Date.now();
  fetch("api/report" + bust).then(function (x) {
    if (!x.ok) throw new Error("no api");
    return x.json();
  }).catch(function () {
    STATIC_MODE = true;                       // 云端静态托管：直接取发布好的 report.json
    return fetch("report.json" + bust).then(function (x) {
      if (!x.ok) throw new Error("no report");
      return x.json();
    });
  }).then(function (d) {
    try {
      render(d);
    } catch (err) {
      // 数据明明取到了、却渲染不出来，几乎总是「浏览器缓存里的旧 app.js 碰上新格式的
      // report.json」。以前这里会把它伪装成"尚无数据"，让人以为是没数据，白等半天。
      el("app").innerHTML = '<div class="empty">页面脚本和数据对不上，通常是浏览器缓存了旧版本。' +
        '<br><br><b>请强制刷新一次</b>（手机：关掉再打开；电脑：Ctrl+F5）。' +
        '<br><br><span style="font-size:12px;color:var(--dim)">技术细节：' + esc(String(err && err.message || err)) + '</span></div>';
      throw err;
    }
  }).catch(function (e) {
    if (el("app").innerHTML.indexOf("对不上") >= 0) return;   // 上面已经报过了
    el("app").innerHTML = '<div class="empty">尚无数据。<br><br>点击右上角 <b>更新全部指标</b> 开始。' +
      '<br><span style="font-size:12px">首次运行需下载12年行情与5年ASIC空头数据并训练模型，约 3–5 分钟；之后每次约数秒。</span></div>';
  });
}

if (SNAPSHOT) {
  var lv = el("live"); if (lv) lv.style.display = "none";
  render(window.__REPORT__);
} else {
  el("btnRefresh").onclick = function () { refresh(false); };
  el("btnHard").onclick = function () {
    if (confirm("将丢弃缓存并重新下载全部原始数据与重训模型，约 3–5 分钟。确定？")) refresh(true);
  };
  el("btnExport").onclick = function () {
    var b = el("btnExport"); b.disabled = true; b.textContent = "导出中 ...";
    fetch("api/export", { method: "POST" }).then(function (x) { return x.json(); }).then(function (j) {
      b.disabled = false; b.textContent = "⬇ 导出云盘版";
      alert(j.ok ? ("已导出：\n" + j.path + "\n\n把这个文件放进 OneDrive / Google Drive / 百度网盘，\n" +
        "在任何电脑上双击即可打开，无需安装任何软件。") : ("导出失败：" + (j.error || "未知错误")));
    }).catch(function (e) { b.disabled = false; b.textContent = "⬇ 导出云盘版"; alert("导出失败：" + e); });
  };
  load();
  fetch("api/status").then(function (x) { return x.json(); }).then(function (s) {
    if (s.running) { setBusy(true); polling = setInterval(poll, 700); }
  });
}
