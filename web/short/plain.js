(function(root){
'use strict';
// BEGIN GENERATED BACKEND CONTRACT
const contractData={"adjustmentStates":["synthetic_demo","declared_consistent","undeclared","invalid"],"asxWarnings":{"asx_dividends_rights_halts_not_verified":"还没核实分红、配股和停牌。价格下调可能另有原因，先查消息再判断。","asx_large_price_move":"最近有一天的价格变化达到20%，先查拆股、分红、配股或公司消息。","asx_low_trading_value":"最近成交金额偏少，价格容易受少数交易影响。","asx_many_unchanged_closes":"最近有多天收盘价不变，先确认是不是成交太少。"},"calendarStates":["synthetic_calendar","reviewed_schedule","unreviewed"],"closeDefinitions":["regular","regular_minute_aggregate","all_sessions","asx_eod"],"currencies":{"AU":"AUD"},"executionStates":["unknown"],"reasons":{"adjustment_metadata_missing":"没说明价格和股数按哪次拆股调整，需要补齐说明。","adjustment_metadata_undeclared":"这份文件没说明价格和股数的拆股版本；只能按导入文件做初步观察，还要核对。","adjustment_version_mismatch":"历史里混了不同拆股版本，要重新读取完整历史。","asx_calendar_not_reviewed":"这一年的澳股开休市安排还没查过。","asx_early_phases_not_reviewed":"这次澳股提前结束交易时，最后一轮成交的时间还没查清，先不算分。","bar_on_closed_session":"这一天市场休市，却出现了当天的行情。","behind_expected_asx_session":"还没拿到澳股最近一天的完整收盘价。","behind_expected_market_session":"应该有更新一天的收盘价，现在还没拿到。","benchmark_missing_or_invalid":"缺少同一天的市场对照价格，没法比较强弱。","borrow_and_cost_unknown":"券商能否借到股票、要花多少钱，还没查清。","calendar_boundary_mismatch":"这份当天价格记录的起止时间与市场安排对不上。","calendar_history_not_reviewed":"所需的更早开休市安排还没查过。","calendar_session_not_finalized":"还要等当天的收盘时最后一轮集中成交和数据整理结束。","calendar_year_not_reviewed":"这一年的开休市安排还没查过。","catalyst_not_verified":"可能影响股价的消息还没核实。","fundamentals_not_integrated":"公司的财务资料还没接齐。","history_below_252":"还不够252个完整交易日，暂不排进标准列表。","invalid_session_inside_history":"过去有些当天价格记录没通过检查，不能跳过后继续算。","missing_expected_history_sessions":"漏了应有的交易日，不能跳过这些天继续算。","older_than_96_hours_calendar_recheck_required":"需要按市场实际开休市日期重查这份旧行情。","peer_adjustment_not_comparable":"另一来源的拆股版本不同或没说明，价格不能直接比。它没有被当成核价通过，也没有被当成价格冲突。","short_halts_inside_history":"过去一年里有过短暂停牌（几天没有成交，通常是发公告或配股前的例行停牌）；停牌期间价格不变，照常计算。","stale_against_dataset_market":"这只股票的更新日期比其他已读取股票旧。","valuation_not_integrated":"股价相对公司价值贵不贵，还没查清。","zero_reference_volume":"用来比较的那段时间没有成交量，没法算。"},"sectors":["REIT","保险","信息科技","公用事业","其他","医药","半导体","原材料/矿业","基准","工业","必需消费","电动车","能源","通信服务","金融/银行","非必需消费","餐饮"],"states":{"conflict":"两份数据对不上","incomplete":"当天的数据没收齐","invalid_history":"有些历史数据不能用","missing_split_basis":"缺少处理拆股后的价格","no_trades":"当天没有成交","provisional":"当天还没收完盘","source_unverified":"价格还要找另一来源核对","stale":"行情还没更新","unverified_adjustment":"拆股前后的数据还没对齐","unverified_session":"交易时间还要核实","valid":"基本检查通过"},"timezones":{"AU":"Australia/Sydney"},"verificationStates":["synthetic_demo","single_source_or_user_declared"],"version":"cross-layer-v1","weights":{"5":[40,20,15,5,20],"10":[35,15,25,10,15],"20":[25,10,35,15,15]}};
function freezeContract(v){return Array.isArray(v)?Object.freeze(v.map(freezeContract)):
 v&&typeof v==='object'?Object.freeze(Object.assign(Object.create(null),Object.fromEntries(Object.entries(v).map(([k,x])=>[k,freezeContract(x)])))):v;}
const contract=freezeContract(contractData);
// END GENERATED BACKEND CONTRACT
const escapeHtml=value=>String(value??'—').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function csvCell(value){
 let text=value==null?'还没查清':String(value);
 // Quoting alone does not stop spreadsheet formula execution.
 if(typeof value==='string'&&(/^[\s\uFEFF]*[=+\-@]/.test(text)||/^[\t\r\n]/.test(text)))text="'"+text;
 return '"'+text.replaceAll('"','""')+'"';
}
const csvTable=rows=>'\uFEFF'+rows.map(row=>row.map(csvCell).join(',')).join('\r\n');
function price(value){
 if(typeof value!=='number'||!Number.isFinite(value))return '还算不了';
 if(value!==0&&Math.abs(value)<0.000001)return value>0?'小于0.000001':'大于-0.000001';
 return new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:6,useGrouping:false}).format(value);
}
function strength(rsi){
 if(typeof rsi!=='number'||!Number.isFinite(rsi))return '还没有可用的涨跌记录';
 return rsi>50?'近期上涨的力度较大':rsi<50?'近期下跌的力度较大':'近期涨跌力度接近平衡';
}
function nextStep(row){
 const codes=new Set(row.reasons??[]);
 if(row.state==='source_unverified'||row.state==='conflict')return '先找另一份行情核对价格，暂不看排名。';
 if(codes.has('adjustment_version_mismatch')||codes.has('adjustment_metadata_missing'))return '先核对股票拆分或合并后的换算，再读入整段历史。';
 if(row.state==='provisional'||row.state==='unverified_session')return '先确认当天已交易结束，价格记录包含完整一天。';
 if(row.state==='stale'||codes.has('behind_expected_market_session'))return '先补上最近应该有的收盘价。';
 if(codes.has('missing_expected_history_sessions')||row.state==='invalid_history')return '先补齐或修正缺少的交易记录。';
 if(codes.has('history_below_252'))return '先补齐252个市场交易日的价格，再作标准比较。';
 if(codes.has('benchmark_missing_or_invalid'))return '先补齐用来作市场对照的基金行情。';
 if(codes.has('peer_adjustment_not_comparable')||codes.has('adjustment_metadata_undeclared'))return '先核对价格和股数的拆股说明，再找能直接比较的行情。';
 if(!row.technical_eligible)return '先检查资料哪里没通过，暂不排名。';
 return '可以比较过去的价格变化；接着查公司经营、消息和券商费用。';
}
const glossary=[
 ['做空是什么？','常见股票做空是先借股票卖出，希望以后降价时买回并归还。价格上涨会亏，借股票也可能花钱。这里还没确认你的券商能否这样做；差价合约等产品要另查规则。'],
 ['交易日怎么算？','数的是市场开门交易的日子。周末和市场休市的假日不算，所以5个交易日不一定正好是一周。'],
 ['收盘价和成交量是什么？','收盘价是按说明确定的当天最后价格；成交量是成交的股票数量。买卖很多，不自动说明以后会涨或跌。'],
 ['为什么价格变低，未必是公司变差？','公司给股东发钱（分红）、发行新股筹钱，或把股票拆成多股、合并成少股，都可能影响价格数字。要先查这些变化。'],
 ['分数为什么不是下跌概率？','目前只用过去的股价和成交量按规则算分。公司经营、股价贵不贵、消息和交易费用还没接齐。它没有证明下一周会跌，更不能把30分读成30%的下跌概率。'],
 ['市场对照和房地产收租基金是什么？','市场对照是拿一只覆盖较多股票的基金作参照，看看某只股票涨跌是否比整体更强。房地产收租基金主要持有物业并收租，经营风险还要单独查。']
];
function guideHtml(){return '<details class="beginnerGuide"><summary>第一次用？点这里，先看六个白话解释</summary>'+glossary.map(([title,body])=>'<article><h3>'+escapeHtml(title)+'</h3><p>'+escapeHtml(body)+'</p></article>').join('')+'<p>词语说明参考<a href="https://www.asx.com.au/investors/investment-tools-and-resources/glossary" target="_blank" rel="noopener">澳洲交易所的基础解释</a>。这里的数字用来研究，券商规则和费用要另行确认。</p></details>';}
const dictionary=value=>Object.freeze(Object.assign(Object.create(null),value));
const api={csvCell,csvTable,price,strength,nextStep,guideHtml,contract,dictionary};
root.RadarPlain=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
