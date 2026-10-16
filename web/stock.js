/* 单股入口独立于全市场报告：没下载12年数据、没训练模型，也能查一只澳股。 */
(function () {
  "use strict";
  var root = document.getElementById("stockLookup");
  if (!root) return;
  var current = null, requestId = 0, range = 126;
  var byId = function (id) { return document.getElementById(id); };
  function safe(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c];
    });
  }
  function number(v, digits) { return v == null || !Number.isFinite(v) ? "—" : v.toFixed(digits == null ? 2 : digits); }
  function percent(v) { return v == null ? "—" : (v > 0 ? "+" : "") + number(v * 100) + "%"; }
  function price(v) { return v == null ? "—" : "A$" + number(v, v < 1 ? 4 : 2); }
  function ticker(value) {
    var t = String(value || "").trim().toUpperCase().replace(/^ASX:/, "").replace(/\.AX$/, "");
    if (!/^[A-Z0-9]{2,6}$/.test(t)) throw new Error("请输入澳股代码，例如 BHP、CSL、GYG 或 360。");
    return t + ".AX";
  }
  function time(value) {
    var d = new Date(value);
    return !value || isNaN(d.getTime()) ? "未提供" : d.toLocaleString("zh-CN", {timeZone:"Australia/Sydney", hour12:false}) + " 悉尼";
  }
  function expired(data) {
    return !data.valid_until || !Number.isFinite(Date.parse(data.valid_until)) || Date.now() >= Date.parse(data.valid_until);
  }
  function observed(data) {
    var t=data.source && data.source.fetched_at;
    return typeof t==="string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(t) && Number.isFinite(Date.parse(t)) && Date.parse(t)<=Date.now();
  }
  root.innerHTML = '<div class="stock-search"><div><h2>查一只澳股</h2>' +
    '<p class="lead">输入澳股代码，看多空条件分、指标解释和技术图。</p></div>' +
    '<form id="stockForm"><label for="stockCode">ASX 股票代码</label><div class="stock-input">' +
    '<input id="stockCode" name="ticker" placeholder="例如 BHP / CSL / GYG / 360" maxlength="14" autocomplete="off" required>' +
    '<button class="refresh" id="stockSubmit" type="submit">查询分析</button></div></form>' +
    '<div class="stock-examples">试一试：' + ["BHP", "CSL", "GYG", "360"].map(function (t) {
      return '<button type="button" class="ghost" data-stock="' + t + '">' + t + '</button>';
    }).join(" ") + '</div><p id="stockMessage" role="status" aria-live="polite">' +
    '电脑本地版按需拉取；手机每日版读取已发布行情。报价和完整收盘分析分别标时。</p>' +
    '<div id="stockResult"></div></div>';

  function validScores(data) {
    var s=data.scores;
    function validSide(side) {
      var v=s[side];
      return v && Number.isInteger(v.value) && v.value>=0 && v.value<=s.max &&
        Array.isArray(v.checks) && v.checks.length===s.max &&
        v.checks.every(function(c){return c && typeof c.key==="string" && typeof c.label==="string" && typeof c.met==="boolean";}) &&
        new Set(v.checks.map(function(c){return c.key;})).size===s.max &&
        v.checks.filter(function(c){return c.met;}).length===v.value;
    }
    if(!(s && s.method==="existing-observation-conditions-v1" && s.state==="ready" &&
      s.as_of===data.as_of && s.max===4 &&
      Array.isArray(s.blockers) && s.blockers.length===0 &&
      !(data.recent_events || []).length && data.facts && Number.isFinite(data.facts.relative_stw_20d) &&
      validSide("long") && validSide("short"))) return false;
    var long=s.long.value===s.max, short=s.short.value===s.max;
    return !(long && short) && data.assessment.bias===(long?"long_watch":short?"short_watch":"observe");
  }

  function scoreCards(data, usable) {
    var s=data.scores, message="条件分不完整或与结论不一致，暂停评分；请更新数据。";
    if(!s) message="这份旧快照尚未包含条件分，请更新程序和每日行情。";
    else if(Array.isArray(s.blockers) && s.blockers.length) message=s.blockers.join("；");
    if(!usable) message=expired(data)?"行情已过期，暂停评分。":"日线检查未通过，暂停评分。";
    var ready=usable && validScores(data);
    if(!ready) return '<section class="stock-scores paused" aria-label="多空条件分"><h4>多空条件分：—</h4><p>'+safe(message)+'</p></section>';
    return '<section class="stock-scores" aria-label="多空技术条件评分"><h4>已满足多少个观察条件</h4><div class="stock-score-grid">'+
      [["long","多头条件分"],["short","空头条件分"]].map(function(pair){
        var v=s[pair[0]];
        return '<article class="stock-score" data-side="'+pair[0]+'"><h5>'+pair[1]+'</h5>'+
          '<strong class="stock-score-value">'+v.value+'<small> / '+s.max+'</small></strong><span>项已满足</span><ul>'+
          v.checks.map(function(c){return '<li class="'+(c.met?'met':'unmet')+'"><span>'+(c.met?'✓ 已满足':'○ 未满足')+'</span> '+safe(c.label)+'</li>';}).join("")+'</ul></article>';
      }).join("")+'</div><p>'+safe(s.meaning)+' 满分也要核对公告、成交价和费用；做空另需确认借券或CFD可用。</p></section>';
  }

  function chart(data, size, technical) {
    var rows = (data.chart || []).slice(-size), W = 960, H = technical ? 400 : 305, L = 65, R = 18;
    var values = [];
    rows.forEach(function (r) { (technical ? [r.low, r.high, r.ma20, r.ma50] : [r.low, r.high]).forEach(function (v) { if (v != null && Number.isFinite(v)) values.push(v); }); });
    if (!values.length) return '<p>暂无可绘制的日线。</p>';
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values), pad = (hi-lo) * .06 || 1;
    lo -= pad; hi += pad;
    var X = function (i) { return L + (i+.5) / rows.length * (W-L-R); };
    var Y = function (v) { return 15 + (hi-v) / (hi-lo) * 204; };
    var maxVol = Math.max.apply(null, rows.map(function (r) { return r.volume || 0; })) || 1;
    var bar = Math.max(.8, Math.min(7, (W-L-R) / rows.length * .65));
    var svg = '<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+safe(data.ticker)+(technical?' 日线蜡烛图、20及50日均线、成交量与RSI':' 历史价格与成交量，指标已暂停')+'">';
    for (var g=0; g<=4; g++) {
      var v = lo + (hi-lo)*g/4, y = Y(v);
      svg += '<line class="gl" x1="'+L+'" x2="'+(W-R)+'" y1="'+y+'" y2="'+y+'"/>' +
        '<text class="tick" x="'+(L-7)+'" y="'+(y+4)+'" text-anchor="end">'+number(v,v<1?3:2)+'</text>';
    }
    rows.forEach(function (r,i) {
      var x=X(i), color=r.close>=r.open ? "#22c55e" : "#ef4444";
      if ([r.open,r.high,r.low,r.close].every(function (v) { return v != null && Number.isFinite(v); })) {
        svg += '<g><title>'+safe(r.date)+' 开 '+price(r.open)+' 高 '+price(r.high)+' 低 '+price(r.low)+' 收 '+price(r.close)+' 成交量 '+number(r.volume,0)+'</title>' +
          '<line x1="'+x+'" x2="'+x+'" y1="'+Y(r.high)+'" y2="'+Y(r.low)+'" stroke="'+color+'"/>' +
          '<rect x="'+(x-bar/2)+'" y="'+Math.min(Y(r.open),Y(r.close))+'" width="'+bar+'" height="'+Math.max(1,Math.abs(Y(r.open)-Y(r.close)))+'" fill="'+color+'"/></g>';
      }
      if (r.volume != null && r.volume > 0) svg += '<rect x="'+(x-bar/2)+'" y="'+(280-r.volume/maxVol*42)+'" width="'+bar+'" height="'+(r.volume/maxVol*42)+'" fill="'+color+'" opacity=".6"/>';
    });
    (technical ? [["ma20","#fbbf24",Y],["ma50","#60a5fa",Y],["rsi14","#a78bfa",function(v){return 372-v*.72;}]] : []).forEach(function (s) {
      var path="", pen=false;
      rows.forEach(function(r,i){ var v=r[s[0]]; if(v==null || !Number.isFinite(v)){pen=false;return;} path+=(pen?"L":"M")+X(i)+" "+s[2](v)+" "; pen=true; });
      svg += '<path d="'+path+'" fill="none" stroke="'+s[1]+'" stroke-width="1.7"/>';
    });
    (technical ? [30,70] : []).forEach(function (v) {
      var y=372-v*.72;
      svg+='<line class="gl" x1="'+L+'" x2="'+(W-R)+'" y1="'+y+'" y2="'+y+'" stroke-dasharray="4 4"/><text class="tick" x="'+(L-7)+'" y="'+(y+4)+'" text-anchor="end">'+v+'</text>';
    });
    svg += '<text class="tick" x="8" y="248">成交量</text>'+(technical?'<text class="tick" x="8" y="314">RSI14</text>':'');
    for (var t=0;t<5;t++) {
      var i=Math.round(t/4*(rows.length-1));
      svg+='<text class="tick" x="'+X(i)+'" y="'+(H-5)+'" text-anchor="'+(t===0?'start':t===4?'end':'middle')+'">'+safe(rows[i].date)+'</text>';
    }
    return svg+'</svg>';
  }

  function diagnosticCards(data, usable) {
    var d=data.diagnostics;
    if(!usable || !d || !Array.isArray(d.cards) || !Array.isArray(d.conflicts)) return "";
    if(!d.cards.every(function(c){return c && typeof c.title==="string" && typeof c.summary==="string";}))
      return '<p class="stock-scope">解读明细不完整，暂不显示；价格图与已通过检查的指标保留。</p>';
    return '<section class="stock-diagnostics"><h4>把指标放在一起看</h4>'+
      d.conflicts.map(function(s){return '<p class="stock-conflict">'+safe(s)+'</p>';}).join("")+
      '<div class="stock-diagnostic-grid">'+d.cards.map(function(c){return '<article><h5>'+safe(c.title)+'</h5><p>'+safe(c.summary)+'</p></article>';}).join("")+
      '</div><p class="stock-scope">'+safe(d.scope)+'</p></section>';
  }

  function dataHealth(data) {
    var q=data.quality, revision=q.history_revision, warnings=(q.issues || []).concat(q.calculation_warnings || []);
    if(q.quote_issue) warnings.push(q.quote_issue);
    if(!observed(data)) warnings.push("抓取时刻缺失、无时区或晚于当前时刻，需要核对设备时间和来源记录。");
    var changed=revision && revision.state==="compared" && Number.isInteger(revision.changed_sessions) && revision.changed_sessions>=0;
    var revisionText=changed ? "与上次已完成日线比较，历史行有 "+revision.changed_sessions+" 个交易日发生变化；原文均保留。" : "本快照尚未完成历史修订比较。";
    var actions=q.corporate_actions_state==="provider_reported" ? "来源报告了公司行动，尚未独立核实。" : "公司行动明细未提供或状态未知，需核对公告。";
    return '<details class="stock-data-health"'+(warnings.length?' open':'')+'><summary>数据检查：单一来源，独立核价未完成</summary>'+
      '<p>抓取于 '+safe(time(data.source.fetched_at))+'。报价时点与完整日线分别显示。</p>'+
      '<p>市场最近应有完整交易日：'+safe(data.expected_session || "未知")+'；最近字段完整且有成交的日线：'+safe(q.latest_complete_traded_session || "未知")+'。</p><p>'+safe(actions)+'</p><p>'+safe(revisionText)+'</p>'+
      warnings.map(function(s){return '<p>'+safe(s)+'</p>';}).join("")+
      '<p><a href="https://www.asx.com.au/markets/company/'+encodeURIComponent(data.ticker.replace(/\.AX$/,""))+'" target="_blank" rel="noopener noreferrer">打开ASX官网核对标的 ↗</a>；先核对报价日期、币种和收盘口径，数值相似不等于完成核价。</p></details>';
  }

  function renderStock(data) {
    var a=data.assessment, old=expired(data), q=data.quote || {};
    var usable=!old && observed(data) && data.quality.state==="single_source" && data.facts!=null;
    var directionUsable=usable && (!data.scores || validScores(data));
    var f=usable ? data.facts : null;
    var paused=old ? "数据过期，暂停判断" : !observed(data) ? "抓取时刻未能核对，暂停判断" : !usable ? "日线检查未通过，暂停判断" : "条件分暂停，先核对数据与结论";
    var headline=old ? "行情已超过下一次收盘时点，请先更新再判断" : !directionUsable ? paused : a.headline;
    var reasons=directionUsable || (usable && data.scores && data.scores.state==="paused") ? a.reasons : (data.quality.issues || []).concat([paused+"；保留历史价格供核对。"]);
    var cell=function(label,value){return '<div><span>'+safe(label)+'</span><b>'+safe(value)+'</b></div>';};
    var metrics=f ? cell("完整收盘",price(f.close))+cell("当日涨跌",percent(f.change_1d))+cell("20日价格涨跌",percent(f.return_20d))+
      cell("相对STW · 20日",f.relative_stw_20d==null?"缺少对照":number(f.relative_stw_20d*100)+" 个百分点")+
      cell("20日均线",price(f.ma20))+cell("50日均线",price(f.ma50))+cell("200日均线",price(f.ma200))+cell("RSI14",number(f.rsi14,1))+
      cell("MACD线 / 信号线",number(f.macd,4)+" / "+number(f.macd_signal,4))+cell("MACD柱",number(f.macd_hist,4))+
      cell("ADX14 / DI+ / DI−",number(f.adx14,1)+" / "+number(f.di_plus14,1)+" / "+number(f.di_minus14,1))+
      cell("CMF20 · 价量估算",number(f.cmf20,3))+cell("20日跌/涨日成交量比",number(f.down_up_volume20))+
      cell("成交量 / 此前20日均量",number(f.volume_ratio20)+" 倍")+cell("20日波动 · 年化",percent(f.volatility20_annualized))+
      cell("ATR14 · 日波动",price(f.atr14)+" / "+percent(f.atr_pct))+cell("20日日均成交额",price(f.adv20_aud))+
      cell("前20日低点 / 高点",price(f.prior20_low)+" / "+price(f.prior20_high)) : '<p>'+safe(paused)+'，不显示计算指标。</p>';
    byId("stockResult").innerHTML = '<div class="stock-heading"><div><h3>'+safe(data.ticker)+' <span>'+safe(data.name)+'</span></h3>' +
      '<p>'+(usable?'完整日线截至 ':'来源日线到 ')+safe(data.as_of || "未知")+(usable?'':' · 分析暂停')+' · '+(data.mode==="local"?"按需读取":"每日快照")+'</p></div>' +
      (q.price!=null?'<div class="stock-quote"><b>'+price(q.price)+'</b><span>来源报价 · 延迟未核实<br>'+safe(time(q.as_of))+'<br>报价不参与完整日线结论</span></div>':"")+'</div>'+
      dataHealth(data)+'<div class="stock-verdict '+(directionUsable&&a.bias==="long_watch"?"positive":directionUsable&&a.bias==="short_watch"?"negative":"")+'"><strong>'+safe(headline)+'</strong>'+
      '<div><b>买入：</b>'+safe(directionUsable?a.long:paused)+'</div><div><b>做空：</b>'+safe(directionUsable?a.short:paused)+'</div></div>'+
      scoreCards(data,usable)+diagnosticCards(data,usable)+'<div class="stock-metrics">'+metrics+'</div><div class="stock-chart-controls">'+[63,126,252,510].map(function(n,i){
        return '<button type="button" class="ghost '+(range===n?"selected":"")+'" data-range="'+n+'">'+["3个月","6个月","1年","2年"][i]+'</button>';
      }).join(" ")+'<span>'+(usable?'日线 · <i style="color:#fbbf24">MA20</i> / <i style="color:#60a5fa">MA50</i>':'历史价格 · 指标已暂停')+' · 鼠标停在蜡烛上查看价格</span></div>'+
      '<div class="stock-chart">'+chart(data,range,usable)+'</div><div class="stock-reasons"><h4>为什么这样判断</h4><ul>'+reasons.map(function(s){return '<li>'+safe(s)+'</li>';}).join("")+'</ul>'+
      '<p>'+safe(a.next_step)+'</p><p>观察位：前20日低点与高点是历史价位，ATR是日波动幅度，均不是保证可成交的入场或止损价。</p></div>'+
      '<details><summary>数据来源、口径与判断边界</summary><p>'+safe(data.source.source)+' · 抓取于 '+safe(time(data.source.fetched_at))+'</p>'+
      '<p>日线为来源所报拆股调整价，未由第二来源核价；股息及其他公司行动、调整声明仍需核实。涨跌幅不含股息、借券、融资或交易费。</p>'+
      '<p>均线是完整窗口简单平均；RSI/ATR沿用本项目递归平滑，以首个观察值初始化，可能与用初始简单平均的看盘软件略有差别。</p>'+
      '<p>MACD为12/26/9 EMA（从最早历史价递推），柱值为MACD线减信号线；量比用当日成交量除以前20日均量，不含当日。年化波动为20日简单收益标准差（ddof=0）乘√252。</p>'+
      '<p>ADX/DI沿用本项目Wilder递推；CMF20按20个完整日线的收盘位置乘成交量估算，高低相同的日线贡献定义为0。跌/涨日成交量比只计最近20日涨跌日，平盘不计；无上涨日时比值留空。它们不参与条件分。</p>'+
      '<p>'+safe(a.method)+' 借券 / CFD可用性、净预期收益、预测准确率均未知；本页没有生成胜率。</p>'+
      '<p>日历 '+safe(data.quality.calendar_version)+' · 独立核价：未完成 · 已排除未收盘日线 '+safe(data.quality.excluded_unfinished_bars)+' 根</p>'+
      '<p class="stock-hash">快照 SHA-256：'+safe(data.source.sha256 || "未提供")+'</p></details>';
    byId("stockResult").querySelectorAll("[data-range]").forEach(function(b){b.onclick=function(){range=Number(b.dataset.range);renderStock(current);};});
  }

  function query(value) {
    var code, id=++requestId;
    current=null; // The expiry timer must never redraw a previous ticker while this query waits.
    try { code=ticker(value); } catch(e) {
      current=null; byId("stockResult").innerHTML=""; byId("stockMessage").textContent=e.message;
      byId("stockSubmit").disabled=false; byId("stockSubmit").textContent="查询分析"; return;
    }
    byId("stockCode").value=code.replace(/\.AX$/, "");
    var button=byId("stockSubmit"), message=byId("stockMessage");
    button.disabled=true; button.textContent="读取中…";
    byId("stockResult").innerHTML="";
    message.textContent="正在读取 "+code+" 的行情、历史日线和 STW 对照…";
    var controller=new AbortController(), timer=setTimeout(function(){controller.abort();},40000);
    var offline = typeof window.__REPORT__ !== "undefined";
    var task = offline ? Promise.resolve(null) : fetch("api/stock?ticker="+encodeURIComponent(code), {cache:"no-store",signal:controller.signal}).then(function(r){
      if(r.status===404) return null;
      return r.json().then(function(d){if(!r.ok) throw new Error(d.error||"行情读取失败");return d;});
    }, function(e){
      if(e.name==="AbortError") throw e;
      return null; // Offline installed phone: try the last published stock snapshot.
    });
    task.then(function(d){
      if(d) return d;
      if(offline) {
        var items=window.__STOCKS__ || {};
        if(Object.prototype.hasOwnProperty.call(items,code)) return items[code];
        throw new Error("这份离线快照没有该股数据，请在电脑本地版查询，或更新后重新导出。");
      }
      return fetch("stocks/ticker-"+code+".json",{cache:"no-store",signal:controller.signal}).then(function(r){
        if(!r.ok) throw new Error("手机每日版暂未收录 "+code+"。可在电脑运行本程序后按代码查询；当前手机页面只显示每日发布的股票。");
        return r.json();
      });
    }).then(function(d){
      if(id!==requestId) return;
      if(d.ticker!==code || d.market!=="AU" || d.currency!=="AUD" || !d.assessment || !d.quality) throw new Error("返回的股票或数据格式不符，停止显示判断。");
      current=d; renderStock(d);
      message.textContent=(d.mode==="local"?"已读取来源数据。":"这是每日发布的行情快照，无法实时按需刷新。")+
        (d.quality.state==="single_source"&&d.facts&&observed(d)&&!expired(d)?" 完整日线截至 ":" 来源日线到 ")+(d.as_of||"未知")+"；抓取于 "+time(d.source.fetched_at)+"。";
      try { localStorage.setItem("asx-last-ticker",code); } catch(e) { /* Storage may be disabled. */ }
    }).catch(function(e){
      if(id!==requestId) return;
      current=null; byId("stockResult").innerHTML="";
      message.textContent=e.name==="AbortError"?"读取超时，请稍后重试。":e.message;
    }).finally(function(){
      clearTimeout(timer); if(id!==requestId) return;
      button.disabled=false; button.textContent="查询分析";
    });
  }
  byId("stockForm").onsubmit=function(e){e.preventDefault();query(byId("stockCode").value);};
  root.querySelectorAll("[data-stock]").forEach(function(b){b.onclick=function(){query(b.dataset.stock);};});
  // A saved ticker only pre-fills the box; opening the app never triggers downloads.
  try { var last=localStorage.getItem("asx-last-ticker"); if(last) byId("stockCode").value=ticker(last).replace(/\.AX$/,""); } catch(e) { /* ignore */ }
  setInterval(function(){if(current && expired(current)) renderStock(current);},60000);
})();
