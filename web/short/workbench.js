'use strict';
const $=id=>document.getElementById(id);
const P=globalThis.RadarPlain;
const escapeHtml=x=>String(x??'—').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
let snapshot=null,requestVersion=0;
const labels=P.contract.states;
const reasons=P.contract.reasons;
const asxWarnings=P.contract.asxWarnings;
const moneyName=P.dictionary({AUD:'澳元'});
const sectorName=value=>value==='REIT'?'房地产收租基金':value;
const sourceName=value=>value?.startsWith('synthetic-')?'虚构行情样本':value==='eodhd-asx-candidate'?'EODHD（价格仍需核对）':value==='yahoo-finance-daily'?'Yahoo（免费，未与第二来源核对）':'你导入的来源';
// 澳股最小价位是 0.001（10 分以下），显示到 3 位足够；Yahoo 的单精度尾巴（46.639999）只在界面上收起，原值不改。
const quote=value=>typeof value==='number'&&Number.isFinite(value)?new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:3,useGrouping:false}).format(value):'—';
const number=(value,digits=1)=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(digits):'—';
function marketTime(value,market='AU'){
 if(!value)return '还没查清';
 const time=new Date(value);if(Number.isNaN(time.getTime()))return '时间需要核实';
 const zones=P.dictionary({AU:['Australia/Sydney','悉尼']});
 const [zone,name]=Object.hasOwn(zones,market)?zones[market]:zones.AU;
 return new Intl.DateTimeFormat('zh-CN',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(time)+'（'+name+'时间）';
}
function meta(entries){return entries.map(([key,value])=>`<div class="metaRow"><span>${escapeHtml(key)}</span><b>${escapeHtml(value)}</b></div>`).join('');}
function renderCalendar(data){
 const names=P.dictionary({AU:'澳股（ASX）'});
 $('calendarSummary').innerHTML=Object.entries(names).map(([market,name])=>{
  const coverage=data.coverage?.[market]??{},calendar=data.calendars?.[market]??{};
  const expected=data.mode==='demo'?'虚构样本，不按真实日历检查':coverage.expected_session??'还没查清';
  const status=data.mode==='demo'?'仅用于演示':calendar.state==='reviewed_schedule'?
   calendar.phase_times_verified===false?'日期查过，提早结束交易的最后一轮成交时间还没查清':'计划日期已查过':'还没查清';
  return `<tr><td class="cellStock"><b>${name}</b></td><td data-label="应该有哪天的收盘价">${escapeHtml(expected)}</td><td data-label="已读入 / 可以比较">${coverage.observed_instruments??'—'} / ${coverage.technical_eligible??'—'}</td><td data-label="还没更新的股票">${data.mode==='demo'?'—':coverage.stale_instruments??'—'}</td><td data-label="开休市日期查过吗">${status}</td></tr>`;
 }).join('');
}
function render(data,local){
 validateSnapshot(data,$('mode').value,Number($('horizon').value),{market:$('market').value,query:$('query').value});
 snapshot=data;renderCalendar(data);const rows=data.rows;
 $('connection').textContent=local?'已连接你的电脑':'每日自动更新';
 $('connection').className='tag good';
 $('modeNotice').textContent=data.mode==='demo'?'这是演示，价格是虚构的':'这是真实行情';
 $('noticeDetail').textContent=data.mode==='demo'?'可以试用功能，不能拿来判断真实公司的走势。':'价格来自 Yahoo，每个交易日悉尼傍晚自动更新，还没有用第二个来源核对。数据检查没通过的股票不排名，原因写在详情里。';
 $('count').textContent=rows.length;$('ready').textContent=rows.filter(x=>x.technical_eligible).length;
 $('cutoff').textContent='这次查看的时间：'+marketTime(data.asof);
 $('rows').innerHTML=rows.length?rows.map((row,index)=>`<tr class="${row.technical_eligible?'':'muted'}">
 <td class="cellStock"><b>${row.technical_eligible?'<i class="rank">'+(index+1)+'</i>':''}${escapeHtml(row.name)}</b><small>${escapeHtml(row.symbol_id)} · ${escapeHtml(sectorName(row.sector))}${row.close==null?'':' · '+escapeHtml(quote(row.close))+' 澳元'}</small></td>
 <td data-label="价格日期">${escapeHtml(row.session_date)}<small>${escapeHtml(sourceName(row.source))}</small></td>
 <td data-label="数据能用吗"><span class="tag ${row.technical_eligible?'good':'warn'}">${escapeHtml(row.state==='valid'&&!row.technical_eligible?'还缺作比较所需的资料':labels[row.state]??'数据还要检查')}</span><small>${escapeHtml(row.history_sessions)} 个可用交易日 · ${row.technical_eligible?'可以比较过去的变化':'暂不排名'}</small></td>
 <td data-label="走势弱的分数"><span class="score">${row.technical_eligible?number(row.known_contribution):'—'}</span><small>${!row.technical_eligible||row.known_contribution==null?'资料不齐，还算不了':'最多 '+escapeHtml(row.known_contribution_max)+' 分 · 不是下跌概率'}</small></td>
 <td data-label="过去涨跌力度">${escapeHtml(P.strength(row.indicators?.rsi14))}<small>这是过去的变化，不是预测</small></td>
 <td data-label="完整分析 / 风险" class="hideSmall">还算不了 / 还没查清<small>没拿到资料就留空</small></td>
 <td class="cellAction"><button class="textButton" data-detail="${escapeHtml(row.id)}">看原因和下一步 ↗</button></td></tr>`).join(''):
 '<tr><td colspan="7" class="empty">当前筛选没有数据，换个筛选条件试试。</td></tr>';
 document.querySelectorAll('[data-detail]').forEach(button=>button.onclick=()=>detail(button.dataset.detail));
 $('export').disabled=false;$('exportRaw').disabled=false;
}
class BackendResponseError extends Error{}
function validateSnapshot(data,mode,horizon,filters={}){
 const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
 const finite=value=>typeof value==='number'&&Number.isFinite(value);
 const currencies=P.contract.currencies;
 const dateOnly=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&!value.startsWith('0000')&&
  Number.isFinite(Date.parse(value+'T00:00:00Z'))&&new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
 const symbol=value=>typeof value==='string'&&/^AU\.[A-Z0-9]{3,6}$/.test(value);
 const weights=P.contract.weights[horizon]?.slice(0,2);
 const ceiling=weights?.[0]+weights?.[1];
 const invalid=()=>{throw new BackendResponseError('收到的结果与所选数据不匹配，或内容不完整。这次先不显示，避免看错。');};
 if(!['demo','imported'].includes(mode)||!Number.isInteger(horizon)||!Object.hasOwn(P.contract.weights,horizon)||
  (filters.market&&!['ALL',...Object.keys(currencies)].includes(filters.market))||
  (filters.sector&&!['ALL',...P.contract.sectors].includes(filters.sector))||
  (filters.query!==undefined&&typeof filters.query!=='string'))invalid();
 if(!object(data)||data.mode!==mode||data.horizon!==horizon||!Array.isArray(data.rows)||!object(data.coverage)||!object(data.calendars)||typeof data.asof!=='string'||
  !/^\d{4}-\d{2}-\d{2}T/.test(data.asof)||!dateOnly(data.asof.slice(0,10))||!/(?:Z|[+\-]\d{2}:\d{2})$/.test(data.asof)||Number.isNaN(new Date(data.asof).getTime())||!weights)invalid();
 for(const market of Object.keys(currencies)){
  const coverage=data.coverage[market],calendar=data.calendars[market];
  if(!object(coverage)||!object(calendar)||typeof calendar.state!=='string'||!P.contract.calendarStates.includes(calendar.state)||calendar.historical_calendar_knowledge_verified!==false||
   !Number.isInteger(coverage.observed_instruments)||coverage.observed_instruments<0||
   !Number.isInteger(coverage.technical_eligible)||coverage.technical_eligible<0||coverage.technical_eligible>coverage.observed_instruments||
   (coverage.stale_instruments!==null&&(!Number.isInteger(coverage.stale_instruments)||coverage.stale_instruments<0||coverage.stale_instruments>coverage.observed_instruments))||
   (coverage.invalid_history_instruments!==null&&(!Number.isInteger(coverage.invalid_history_instruments)||coverage.invalid_history_instruments<0||coverage.invalid_history_instruments>coverage.observed_instruments))||
   (coverage.expected_session!==null&&!dateOnly(coverage.expected_session))||coverage.expected_session!==calendar.expected_session||coverage.calendar_status!==calendar.state||
   coverage.universe_coverage_pct!==null||
   (mode==='demo'&&(calendar.state!=='synthetic_calendar'||calendar.expected_session!==null))||
   (mode==='imported'&&!['reviewed_schedule','unreviewed'].includes(calendar.state))||
   (calendar.state==='reviewed_schedule'&&(calendar.expected_session===null||!dateOnly(calendar.expected_session)))||
   (calendar.state==='unreviewed'&&calendar.expected_session!==null))invalid();
 }
 const seen=new Set(),symbols=new Set(),counts=Object.fromEntries(Object.keys(currencies).map(m=>[m,{observed:0,eligible:0}]));
 for(const row of data.rows){
  if(!object(row)||typeof row.id!=='string'||!row.id||seen.has(row.id)||typeof row.name!=='string'||!symbol(row.symbol_id)||symbols.has(row.symbol_id)||typeof row.sector!=='string'||!P.contract.sectors.includes(row.sector)||typeof row.state!=='string'||
   currencies[row.market]!==row.currency||!Object.hasOwn(currencies,row.market)||row.demo!==(mode==='demo')||row.mode!==mode||row.horizon!==horizon||
   !row.symbol_id.startsWith(row.market+'.')||(row.session_date!==null&&!dateOnly(row.session_date))||((row.session_date===null)!==(row.close===null))||
   typeof row.technical_eligible!=='boolean'||!Number.isInteger(row.history_sessions)||row.history_sessions<0||
   row.known_contribution_max!==ceiling||
   (row.close!==null&&(!P.contract.closeDefinitions.includes(row.close_definition)||row.basis!=='split'||typeof row.source!=='string'||!row.source))||
   (mode==='imported'&&row.source==='eodhd-asx-candidate'&&row.state==='valid')||
   row.research_score!==null||row.risk_adjusted_score!==null||!P.contract.executionStates.includes(row.execution_status)||
   row.verification!==(mode==='demo'?'synthetic_demo':'single_source_or_user_declared')||
   !Object.prototype.hasOwnProperty.call(labels,row.state)||row.expected_session!==data.calendars[row.market].expected_session||row.calendar_status!==data.calendars[row.market].state||
   row.calendar_version!==data.calendars[row.market].calendar_version||
   (row.known_contribution!==null&&(!finite(row.known_contribution)||row.known_contribution<0||row.known_contribution>row.known_contribution_max))||
   (row.close!==null&&(!finite(row.close)||row.close<=0))||!Array.isArray(row.reasons)||!row.reasons.every(value=>typeof value==='string')||
   !Array.isArray(row.chart)||row.chart.length>100||!row.chart.every((value,index)=>object(value)&&dateOnly(value.date)&&finite(value.close)&&value.close>0&&
    row.session_date!==null&&value.date<=row.session_date&&(index===0||value.date>row.chart[index-1].date))||
   (row.indicators!==null&&!object(row.indicators)))invalid();
  if((filters.market&&filters.market!=='ALL'&&row.market!==filters.market)||
   (filters.sector&&filters.sector!=='ALL'&&row.sector!==filters.sector)||
   !(row.name+' '+row.symbol_id).toLowerCase().includes((filters.query??'').trim().toLowerCase()))invalid();
  if(row.technical_eligible&&(row.state!=='valid'||row.history_sessions<252||row.known_contribution===null||row.indicators===null||!row.chart.length||
   row.chart.at(-1).date!==row.session_date||row.chart.at(-1).close!==row.close))invalid();
  if(!object(row.factors)||['trend','distribution'].some(key=>row.factors[key]!==null&&(!finite(row.factors[key])||row.factors[key]<0||row.factors[key]>1))||
   ['fundamentals','valuation','catalyst'].some(key=>row.factors[key]!==null))invalid();
  if(row.state!=='valid'&&(row.indicators!==null||row.factors.trend!==null||row.factors.distribution!==null))invalid();
  if(mode==='imported'&&row.technical_eligible&&(row.calendar_status!=='reviewed_schedule'||row.expected_session!==row.session_date||data.calendars[row.market].phase_times_verified!==true))invalid();
  const technical=[row.factors.trend,row.factors.distribution];
  const known=technical.reduce((sum,value,index)=>sum+(value===null?0:weights[index]*value),0);
  const coverage=technical.reduce((sum,value,index)=>sum+(value===null?0:weights[index]),0);
  if(row.coverage!==coverage||(coverage===0?row.known_contribution!==null:row.known_contribution===null||Math.abs(row.known_contribution-known)>0.00500000001))invalid();
  if(row.indicators&&Object.values(row.indicators).some(value=>value!==null&&!finite(value)))invalid();
  if(row.indicators){
   const metrics=row.indicators;
   if(['rsi14','ma20','ma60','macd','macd_signal','macd_histogram','atr14','ma20_change5'].some(key=>!finite(metrics[key]))||
    metrics.rsi14<0||metrics.rsi14>100||metrics.ma20<=0||metrics.ma60<=0||metrics.atr14<0||metrics.ma20_change5<=-1||
    Math.abs(metrics.macd_histogram-(metrics.macd-metrics.macd_signal))>0.000002)invalid();
   if(row.technical_eligible){
    if(technical.some(value=>value===null)||!finite(metrics.relative_return20))invalid();
    const clip=value=>Math.max(0,Math.min(1,value));
    const trend=(clip((metrics.ma20-row.close)/(0.08*metrics.ma20))+clip((metrics.ma60-row.close)/(0.12*metrics.ma60))+
     clip(-metrics.relative_return20/0.15)+clip(-metrics.ma20_change5/0.05))/4;
    // The old API rounds relative_return20 to 6 places. Account for that
    // declared precision rather than imposing exact cross-language equality.
    if(Math.abs(technical[0]-trend)>0.000001)invalid();
   }
  }
  if(row.conflicts!=null&&(!Array.isArray(row.conflicts)||row.conflicts.some(value=>!object(value)||typeof value.date!=='string'||typeof value.peer_source!=='string'||!Array.isArray(value.fields)||!value.fields.every(field=>typeof field==='string'))))invalid();
  if(row.adjustment_metadata_verified!==false||!P.contract.adjustmentStates.includes(row.adjustment_metadata_status)||
   (mode==='demo'&&row.adjustment_metadata_status!=='synthetic_demo')||
   (mode==='imported'&&row.adjustment_metadata_status==='synthetic_demo')||
   !Array.isArray(row.comparison_skips)||row.comparison_skips.some(value=>!object(value)||!dateOnly(value.date)||
    typeof value.source!=='string'||typeof value.peer_source!=='string'||value.reason!=='peer_adjustment_not_comparable'))invalid();
  if(row.asx_review!=null){
   if(!object(row.asx_review)||row.asx_review.event_context_verified!==false||row.asx_review.thresholds_validated_for_prediction!==false||!Array.isArray(row.asx_review.warnings)||!row.asx_review.warnings.every(value=>typeof value==='string'))invalid();
   for(const key of ['trading_value_proxy_20d_aud','unchanged_close_days_20','largest_close_move_20_pct'])if(row.asx_review[key]!==null&&(!finite(row.asx_review[key])||row.asx_review[key]<0))invalid();
  }
  seen.add(row.id);symbols.add(row.symbol_id);counts[row.market].observed++;if(row.technical_eligible)counts[row.market].eligible++;
 }
 for(const market of Object.keys(currencies)){
  const c=data.coverage[market],n=counts[market];
  if(n.observed>c.observed_instruments||n.eligible>c.technical_eligible)invalid();
  const unfiltered=!(filters.query??'').trim()&&(!filters.sector||filters.sector==='ALL')&&(!filters.market||filters.market==='ALL'||filters.market===market);
  if(unfiltered&&(n.observed!==c.observed_instruments||n.eligible!==c.technical_eligible))invalid();
 }
 return data;
}
async function load(){
 const version=++requestVersion;$('refresh').disabled=true;$('export').disabled=true;$('exportRaw').disabled=true;
 const mode=$('mode').value,horizon=Number($('horizon').value),market=$('market').value,query=$('query').value.trim().toLowerCase();
 const qs=new URLSearchParams({mode,horizon,market,query});
 snapshot=null;if($('detailDialog').open)$('detailDialog').close();
 $('count').textContent=$('ready').textContent='—';$('cutoff').textContent='';$('calendarSummary').innerHTML='';
 $('modeNotice').textContent=mode==='demo'?'正在读取虚构演示样本':'正在读取你导入的行情';
 $('noticeDetail').textContent='等这次读取完成，再查看结果。';
 $('rows').innerHTML='<tr><td colspan="7" class="empty">正在读取你刚才选的资料…</td></tr>';
 try{
  // 并入澳股雷达后没有本机后端：每天的结果由 asxquant/shortradar.py 预先算好写成静态文件。
  // 校验照旧——先对整份结果做完整检查，再按搜索词筛选，筛选后的结果再检查一次。
  let data;
  const response=await fetch('data/radar-'+horizon+'.json',{cache:'no-store'});
  if(!response.ok)throw Error('今天的结果还没生成（错误编号 '+response.status+'）。请稍后再试，或回澳股雷达首页看看是否更新完成。');
  try{data=await response.json();}catch(error){throw new BackendResponseError('结果文件不完整，这次先不显示，请重新读取。');}
  validateSnapshot(data,mode,horizon);
  data.rows=data.rows.filter(row=>(market==='ALL'||row.market===market)&&(row.name+' '+row.symbol_id).toLowerCase().includes(query));
  if(version===requestVersion)render(data,false);
 }catch(error){
  if(version!==requestVersion)return;
  snapshot=null;$('calendarSummary').innerHTML='';
  $('connection').textContent=error instanceof BackendResponseError?'结果文件有问题':'这次没读到';
  $('connection').className='tag warn';$('modeNotice').textContent='这次没读到数据';$('noticeDetail').textContent=error.message;
  $('count').textContent=$('ready').textContent='—';$('cutoff').textContent='';
  $('rows').innerHTML=`<tr><td colspan="7" class="empty">${escapeHtml(error.message)}</td></tr>`;
 }finally{if(version===requestVersion)$('refresh').disabled=false;}
}
function asxDetails(row){
 const review=row.asx_review;if(row.market!=='AU'||!review)return '';
 return '<section class="detailSection"><h3>澳股还要多看这几件事</h3>'+meta([
  ['最近20个交易日，大概每天成交多少钱',review.trading_value_proxy_20d_aud==null?'还算不了':number(review.trading_value_proxy_20d_aud,0)+' 澳元'],
  ['最近20个交易日，有几天收盘价没变',review.unchanged_close_days_20==null?'还算不了':review.unchanged_close_days_20+' 天'],
  ['最近20个交易日，单日最大价格变化',review.largest_close_move_20_pct==null?'还算不了':number(review.largest_close_move_20_pct)+'%']
 ])+'<p>金额用收盘价乘成交股数粗算；实际成交额和能否借到股票还要查。澳股市场对照用STW基金的价格，只比较股价变化，分红另外查。这里的提醒没有证明能预测涨跌。</p><div class="reasons">'+(review.warnings??[]).map(code=>escapeHtml(asxWarnings[code]??'还有资料需要核实。')).join('<br>')+'</div></section>';
}
function safeSnapshot(){
 if(!snapshot)return false;
 try{validateSnapshot(snapshot,$('mode').value,Number($('horizon').value),{market:$('market').value,query:$('query').value});return true;}
 catch(error){snapshot=null;$('export').disabled=true;$('exportRaw').disabled=true;$('rows').innerHTML='<tr><td colspan="7">结果不匹配，先刷新再查看。</td></tr>';return false;}
}
function detail(id){
 if(!safeSnapshot())return;
 const row=snapshot?.rows.find(item=>item.id===id);if(!row)return;
 $('detailName').textContent=row.name;
 const values=(row.chart??[]).map(item=>item.close),lo=Math.min(...values),hi=Math.max(...values);
 const path=values.map((value,index)=>`${index?'L':'M'}${8+index*480/Math.max(values.length-1,1)},${140-(value-lo)/Math.max(hi-lo,1e-9)*120}`).join(' ');
 const graph=values.length?`<svg class="chart" viewBox="0 0 500 160" role="img" aria-label="这份数据里的收盘价格变化"><path d="M8 140H490M8 80H490M8 20H490" stroke="#e5edf1"/><path d="${path}" fill="none" stroke="#987168" stroke-width="2"/></svg>`:'<p>暂时没有通过检查的价格图。</p>';
 const missing=row.missing_history_sessions;
 const missingText=row.demo?'演示样本，不检查真实交易日':missing===null?'还没查清':missing?.length?missing.length+' 天：'+missing.slice(0,5).join('、')+(missing.length>5?'…':''):'已查范围内没有发现漏日';
 const closeNames=P.dictionary({asx_eod:'包含澳股收盘时最后一轮成交后的当天记录',regular:'正常交易时段的收盘价',regular_minute_aggregate:'把正常交易时段的每分钟价格汇总成当天记录',all_sessions:'含正常开收盘以外的交易，时段还要核实'});
 const fieldNames=P.dictionary({open_micros:'开盘价',high_micros:'当天最高价',low_micros:'当天最低价',close_micros:'收盘价',volume:'成交股数'});
 const conflicts=(row.conflicts??[]).length?'<div class="reasons">这些数据对不上：'+row.conflicts.slice(0,5).map(item=>escapeHtml(item.date+' · '+item.peer_source+' · '+item.fields.map(key=>fieldNames[key]??'行情字段').join(' / '))).join('<br>')+'</div>':'';
 const metrics='<details class="metricDetails"><summary>想看平均价格、涨跌力度这些数字？</summary>'+meta([
  ['价格变化按规则给的分',row.known_contribution==null?'还算不了':number(row.known_contribution)+' / 最多 '+row.known_contribution_max+' 分（只算已收到的部分）'],
  ['过去20个交易日的平均收盘价',row.indicators?P.price(row.indicators.ma20):'还算不了'],
  ['过去60个交易日的平均收盘价',row.indicators?P.price(row.indicators.ma60):'还算不了'],
  ['上涨与下跌力度的记录（0～100）',row.indicators?number(row.indicators.rsi14):'还算不了'],
  ['这项记录怎样看',P.strength(row.indicators?.rsi14)],
  ['短期均价减去长期均价',row.indicators?P.price(row.indicators.macd):'还算不了'],
  ['这个差值的近期平均',row.indicators?P.price(row.indicators.macd_signal):'还算不了'],
  ['差值比它的近期平均高多少',row.indicators?P.price(row.indicators.macd_histogram):'还算不了'],
  ['近期每天价格波动幅度的平均',row.indicators?P.price(row.indicators.atr14)+' '+(moneyName[row.currency]??row.currency):'还算不了'],
  ['这段时间表现比市场强弱多少',row.indicators?.relative_return20==null?'还算不了':number(row.indicators.relative_return20*100)+' 个百分点']
 ])+'<p>平均价格就是把那段价格加起来再平均。涨跌力度和波动记录用14个交易日作参考周期，旧记录也参与平滑计算；短期和长期均价用12、26个交易日作参考，越近的记录越重要。</p><p>“个百分点”是在比较两个百分数。举例：股票跌8%，市场对照跌3%，两者相差-5个百分点，说明这段时间股票跌得更多。市场对照用基金价格，分红还要另外查。</p><p>这些数字只描述已经发生的变化。涨得多不保证接着跌，跌得多也不保证会反弹。</p></details>';
 $('detailBody').innerHTML=`<span class="tag ${row.demo?'warn':''}">${row.demo?'虚构行情，用来演示功能':'Yahoo 真实行情，还没与第二来源核对'}</span>`+meta([
  ['股票代码',row.symbol_id],['最近的收盘价',row.close==null?'还没拿到':quote(row.close)+' '+(moneyName[row.currency]??row.currency)],
  ['价格是哪天的',row.session_date],['应该有哪天的收盘价',row.demo?'演示样本，不按真实日历检查':row.expected_session??'还没查清'],
  ['数据能用吗',labels[row.state]??'还要检查'],['有没有漏掉交易日',missingText],['数据从哪来',sourceName(row.source)],
  ['把各种资料一起算的总分','还算不了'],['能不能做空、要花多少钱','还没查清']
 ])+conflicts+'<div class="reasons"><b>下一步先做什么</b><p>'+escapeHtml(P.nextStep(row))+'</p>'+ (row.reasons??[]).map(code=>escapeHtml(reasons[code]??labels[code]??'这项资料还需要检查。')).join('<br>')+'</div><p>分数只描述过去的价格变化。分数高不能说明未来更容易跌，分数低也不能说明安全。</p>'+graph+'<p>'+(values.length?'图里记录从 '+escapeHtml(row.chart[0].date)+' 到 '+escapeHtml(row.chart.at(-1).date)+'，共 '+values.length+' 天。':'')+'图的左边较早、右边较晚，只显示已收到的价格记录。它没有画出未来的预测。</p>'+asxDetails(row)+metrics+
 '<details class="audit"><summary>查看数据从哪来、改过几次</summary>'+meta([
  ['来源的原始名称',row.source],['什么时候能拿到这份资料',marketTime(row.available_at,row.market)],
  ['这个时间是怎么记录的',row.availability_kind==='observation'?'第一次收到资料的时间':row.availability_kind==='publication'?'来源公布资料的时间':'由导入文件声明，还需要核实'],
  ['什么时候录入这台电脑',marketTime(row.observed_at,row.market)],['这是第几版价格',row.price_revision??'还没查清'],
  ['这次计算的核对编号',row.input_manifest_sha256],['开休市安排的核对版本',row.calendar_version??'演示样本'],
  ['拆股说明查到什么程度',row.adjustment_metadata_status==='declared_consistent'?'文件内的价格和股数说明一致，仍未独立核实':row.demo?'演示样本':row.adjustment_metadata_status==='invalid'?'说明缺失或前后矛盾，暂不计算':'文件没说明，还要核对'],
  ['另一来源有没有因为拆股说明而无法比较',row.comparison_skips?.length?row.comparison_skips.length+' 条记录不能直接比价':'没有记录；这不等于已经独立核价'],
  ['这份收盘价包含哪个时段',closeNames[row.close_definition]??'还没查清'],['历史价格怎样处理拆股',row.basis==='split'?'已按拆股调整；说明仍需核对':'还没说明'],
  ...(row.market==='AU'?[['拆股资料的版本编号',row.adjustment_id??(row.demo?'演示样本':'还没说明')],['成交股数怎样处理拆股',row.volume_basis==='split'?'按同一拆股版本调整':row.demo?'演示样本':'还没说明']]:[])
 ])+'</details>';
 $('detailDialog').showModal();
}
$('closeDetail').onclick=()=>$('detailDialog').close();
$('detailDialog').onclick=event=>{if(event.target===$('detailDialog'))$('detailDialog').close();};
$('refresh').onclick=load;
['mode','market','horizon'].forEach(id=>$(id).onchange=load);
let timer;$('query').oninput=()=>{clearTimeout(timer);timer=setTimeout(load,200);};
function downloadFile(body,type,name){const url=URL.createObjectURL(new Blob([body],{type})),anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export').onclick=()=>{
 if(!safeSnapshot())return;
 const title=snapshot.mode==='demo'?'演示样本':'导入行情';
 const rows=[['数据说明','公司名','股票代码','市场','价格日期','收盘价','货币','数据检查','下一步先查什么','价格变化给的分','这部分最多几分','可用交易日','完整研究分','能否做空'],
 ...snapshot.rows.map(row=>[title,row.name,row.symbol_id,P.dictionary({AU:'澳股'})[row.market],row.session_date,row.close,moneyName[row.currency],labels[row.state]??'还要检查',P.nextStep(row),row.known_contribution??'还算不了',row.known_contribution_max,row.history_sessions,'还算不了','还没查清'])];
 downloadFile(P.csvTable(rows),'text/csv;charset=utf-8','股票研究-'+title+'-'+snapshot.horizon+'个交易日.csv');
};
$('exportRaw').onclick=()=>{if(safeSnapshot())downloadFile(JSON.stringify(snapshot,null,2),'application/json;charset=utf-8','short-radar-'+snapshot.mode+'-'+snapshot.horizon+'d.json');};
// 实测结论：tools/eval_short_radar.py 生成的 evidence.json。数字照原样显示，结论用大白话。
async function loadEvidence(){
 try{
  const response=await fetch('evidence.json',{cache:'no-store'});if(!response.ok)throw Error();
  const e=await response.json(),h=e.horizons??{},pick=key=>h[key]??{};
  const pct=value=>typeof value==='number'?(value>0?'+':'')+value.toFixed(2)+'%':'—';
  const ten=pick('10');
  const gap=ten.top_minus_bottom_pct,wrongWay=typeof gap==='number'&&gap>0;
  $('verdictText').textContent='我们用'+e.window?.[0]?.slice(0,4)+'到'+e.window?.[1]?.slice(0,4)+'年、'+e.universe+'只澳股的真实行情，按这个页面一模一样的公式回测：'+
   '分数最高的那五分之一股票，之后10个交易日平均比分数最低的五分之一'+(wrongWay?'还多涨了 ':'少涨了 ')+(typeof gap==='number'?Math.abs(gap).toFixed(2)+'%':'—')+
   (wrongWay?'——方向是反的':'')+'，而且差距小到可以当作没有（可信度 t 只有 '+(ten.nw_t?.toFixed(1)??'—')+'）。'+
   (e.verdict==='none'?'所以它不是做空信号，只是帮你找出“最近走势弱”的股票，再去查原因。':'');
  const row=(label,key)=>{const r=pick(key);return `<tr><td>${label}</td><td>${r.mean_ic?.toFixed(3)??'—'}</td><td>${r.nw_t?.toFixed(2)??'—'}</td><td>${pct(r.top_minus_bottom_pct)}</td><td>${r.baseline_mom20_nw_t?.toFixed(2)??'—'}</td></tr>`;};
  $('verdictBody').innerHTML='<div class="tableWrap"><table class="evidenceTable"><thead><tr><th>持有</th><th>相关性</th><th>可信度 t</th><th>最高组减最低组</th><th>对照：只看20天跌幅 t</th></tr></thead><tbody>'+
   row('5个交易日','5')+row('10个交易日','10')+row('20个交易日','20')+'</tbody></table></div>'+
   '<ul class="plainList"><li><b>相关性</b>：分数高低和之后涨跌的关系，0 表示毫无关系，做空信号应该是负数。</li>'+
   '<li><b>可信度 t</b>：绝对值到 2 左右才算“可能不是巧合”。这里已按重叠持有期修正过，不修正会虚高三到五倍。</li>'+
   '<li><b>最高组减最低组</b>：正数表示“最该看空”的那组反而涨得更多。</li>'+
   '<li><b>对照</b>：一条最朴素的规则（过去20天跌得多就排前面），这个分数并没有比它更好。</li></ul>'+
   '<p class="small">'+escapeHtml('计算方法：'+e.entry_exit+'；逐点与原程序公式核对 '+e.pointwise_crosscheck+' 次全部一致。'+e.holdout+'。注意：'+(e.caveats??[]).join('；')+'。检验日期 '+e.generated+'。')+'</p>';
 }catch(error){$('verdictText').textContent='实测结果暂时读不到。结论不变：这个分数没有证明能预测涨跌。';}
}
async function loadImport(){
 try{
  const response=await fetch('data/import.json',{cache:'no-store'});if(!response.ok)throw Error();
  const s=await response.json();
  const when=s.fetched_at?.['yahoo-finance-daily'];
  $('importWhen').textContent=when?'抓取时间：'+marketTime(when):'—';
  const states=s.states??{},examples=[...new Set(s.issue_examples??[])];
  const names=P.dictionary({'invalid OHLC bounds':'开盘/最高/最低/收盘价互相矛盾','not a AU cash trading day':'休市日却有行情','price must be positive, finite and use at most 6 decimals':'价格缺失或不合理','volume must be a nonnegative integer of shares':'成交量缺失'});
  $('importBody').innerHTML=meta([
   ['今天读入的日线记录',(s.accepted??0).toLocaleString('zh-CN')+' 行'],
   ['被拒绝的记录',(s.rejected??0)+' 行'+(examples.length?'（'+examples.map(x=>names[x]??x).join('、')+'）':'')],
   ['当天没有成交的记录（停牌或交易暂停）',(states.no_trades??0)+' 行'],
  ])+'<p>被拒或没有成交的日子，会让那只股票“漏了交易日”，按规则暂不排名，而不是跳过那天继续算。长期停牌的股票（例如 CTD）就是这样被挡下来的。</p>';
 }catch(error){$('importBody').textContent='今天的数据检查摘要暂时读不到。';}
}
$('beginnerGuide').innerHTML=P.guideHtml();
loadEvidence();loadImport();
load();
