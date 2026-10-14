import hashlib
import json
from .models import stamp,MARKET_CURRENCY,SECTORS
from . import __version__
from .engine import rsi_wilder,trend,distribution,score,WEIGHTS
from .calendars import calendar_status,previous_sessions,bar_calendar_issue,CalendarUnavailable
from .quality import select_history,inspect_history
from .adjustments import adjustment_issue,requires_adjustment_check
from .asx_review import describe as describe_asx
from .contracts import ACTIVE_MARKETS,active_market

BENCHMARKS={'HK':'HK.02800','US':'US.SPY','AU':'AU.STW'}

def ema(values,period):
    v=values[0];out=[];a=2/(period+1)
    for p in values:v=a*p+(1-a)*v;out.append(v)
    return out

def indicators(bars):
    if len(bars)<60:raise ValueError('at least 60 observations are required for indicators')
    closes=[b['close_micros']/1e6 for b in bars]
    # Keep calculation values separate from UI rounding, including micro-price
    # stocks. Integer sums also avoid accumulating converted-price rounding.
    ma20=sum(b['close_micros'] for b in bars[-20:])/20000000
    ma60=sum(b['close_micros'] for b in bars[-60:])/60000000
    line=[a-b for a,b in zip(ema(closes,12),ema(closes,26))];signal=ema(line,9)
    tr=[max((b['high_micros']-b['low_micros'])/1e6,abs(b['high_micros']/1e6-closes[i-1]),abs(b['low_micros']/1e6-closes[i-1])) for i,b in enumerate(bars) if i>0]
    atr=sum(tr[:14])/14
    for x in tr[14:]:atr=(atr*13+x)/14
    return {'rsi14':round(rsi_wilder(closes),3),'ma20':ma20,'ma60':ma60,'macd':line[-1],'macd_signal':signal[-1],'macd_histogram':line[-1]-signal[-1],'atr14':atr,'ma20_change5':ma20/(sum(b['close_micros'] for b in bars[-25:-5])/20000000)-1}

def compatible_history(item):
    bars,state,_=select_history(item)
    return bars,state

def audit_history(item,mode,cutoff,calendar,overrides,count=252,context=None):
    selection=inspect_history(item,count if mode=='imported' else None)
    bs,state,conflicts=selection['bars'],selection['state'],selection['conflicts'];reasons=[]
    skips=selection['comparison_skips']
    if skips:reasons.append('peer_adjustment_not_comparable')
    context=context if context is not None else {'windows':{},'calendar_issues':{}}
    history=[b for b in bs if b['quality']=='valid'];latest=bs[-1] if bs else None
    missing=[];issues=[]
    if state!='valid':reasons.append(state)
    if any(b['quality']!='valid' for b in bs[-count:]):
        reasons.append('invalid_session_inside_history')
        if state=='valid':state='invalid_history'
    if mode=='imported':
        if item['market']=='AU' and calendar.get('phase_times_verified') is False:
            reasons.append('asx_early_phases_not_reviewed')
        if calendar['expected_session'] is None:
            reasons.append('calendar_year_not_reviewed')
            missing=None
            if state=='valid':state='unverified_session'
            history=[]
        else:
            if latest and latest['session_date']<calendar['expected_session']:
                reasons.append('behind_expected_market_session')
                if state=='valid':state='stale'
            checked=[]
            for b in bs[-count:]:
                if b['quality']!='valid':continue
                key=(item['market'],b['session_date'],b['session_open_utc'],b['session_close_utc'],b['available_at'],b['close_definition'])
                cache=context['calendar_issues']
                if key in cache:issue=cache[key]
                else:
                    issue=bar_calendar_issue(b,item['market'],cutoff,overrides)
                    if len(cache)<4096:cache[key]=issue
                if issue:
                    issues.append(issue)
                else:checked.append(b)
            history=checked
            if issues:
                reasons.extend(issues)
                if state=='valid':state='unverified_session' if 'calendar_year_not_reviewed' in issues else 'invalid_history'
            if bs:
                try:
                    key=(item['market'],count)
                    if key not in context['windows']:
                        try:context['windows'][key]=previous_sessions(item['market'],calendar['expected_session'],count,overrides)
                        except CalendarUnavailable:context['windows'][key]=None
                    required=context['windows'][key]
                    if required is None:raise CalendarUnavailable('history window not reviewed')
                    required_set=set(required)
                    dates={b['session_date'] for b in history}
                    # Missing leading dates may be a new listing; internal gaps
                    # remain failures, never silently compress trading horizons.
                    missing=[d for d in required if d>=bs[0]['session_date'] and d not in dates]
                    if missing:
                        reasons.append('missing_expected_history_sessions')
                        if state=='valid':state='invalid_history'
                    history=[b for b in history if b['session_date'] in required_set]
                except CalendarUnavailable:
                    reasons.append('calendar_history_not_reviewed')
                    missing=None
                    history=[]
                    if state=='valid':state='unverified_session'
    if mode=='imported' and bs and requires_adjustment_check(item['market'],bs[-count:]):
        issue=adjustment_issue(bs[-count:])
        if issue:
            reasons.append(issue)
            if state=='valid':state='unverified_adjustment'
            history=[]
    elif mode=='imported' and bs:
        reasons.append('adjustment_metadata_undeclared')
    return bs,history,state,reasons,missing,conflicts,skips

def _radar(store,mode,cutoff,horizon=10,market='ALL',sector='ALL',query='',markets=None):
    markets=tuple(MARKET_CURRENCY) if markets is None else tuple(markets)
    if horizon not in WEIGHTS:raise ValueError('horizon must be 5, 10 or 20')
    if mode not in {'demo','imported'}:raise ValueError('mode must be demo or imported')
    if market not in {'ALL',*MARKET_CURRENCY}:raise ValueError('unsupported market filter')
    if sector not in {'ALL',*SECTORS}:raise ValueError('unsupported sector filter')
    items,overrides=store.analysis_inputs(mode,cutoff,history_limit=252 if mode=='imported' else None,markets=markets);all_rows=[]
    items={iid:item for iid,item in items.items() if item['market'] in markets}
    by_symbol={item['symbol_id']:item for item in items.values()}
    calendars={m:calendar_status(m,cutoff,overrides) for m in markets} if mode=='imported' else {
        m:{'state':'synthetic_calendar','expected_session':None,'calendar_version':None,
           'reviewed_at':None,'sources':[],'historical_calendar_knowledge_verified':False} for m in markets}
    context={'windows':{},'calendar_issues':{}}
    audits={iid:audit_history(item,mode,cutoff,calendars[item['market']],overrides,
                             21 if item['symbol_id'] in BENCHMARKS.values() else 252,context) for iid,item in items.items()}
    for item in items.values():
        bs,history,state,reasons,missing,conflicts,skips=audits[item['id']];latest=bs[-1] if bs else None
        # Names and sectors are versioned in each bar payload. The initial
        # registration is not an authoritative, permanent label. Follow the
        # same selected visible price series, without changing stable IDs.
        if latest:item={**item,'name':latest['name'],'sector':latest['sector']}
        if item['symbol_id'] in BENCHMARKS.values() or item['sector']=='基准':continue
        if len(history)<252:reasons.append('history_below_252')
        metric=None;t=d=None
        if len(history)>=65 and not missing and state=='valid':
            metric=indicators(history);cs=[b['close_micros']/1e6 for b in history];vol=[b['volume'] for b in history]
            bench=by_symbol.get(BENCHMARKS[item['market']])
            _,bbs,bstate,_,bmissing,_,_=audits[bench['id']] if bench else ([],[],None,[],[],[],[])
            bm={b['session_date']:b['close_micros']/1e6 for b in bbs}
            if (all(b['session_date'] in bm for b in history[-21:]) and bstate=='valid'
                    and not bmissing and bbs and bbs[-1]['close_definition']==latest['close_definition']):
                rs=cs[-1]/cs[-21]-1-(bm[history[-1]['session_date']]/bm[history[-21]['session_date']]-1)
                t=trend(cs[-1],metric['ma20'],metric['ma60'],rs,metric['ma20_change5']);metric['relative_return20']=round(rs,6)
            else:reasons.append('benchmark_missing_or_invalid')
            up=down=0
            for i in range(len(history)-21,len(history)-1):
                if cs[i]<cs[i-1]:down+=vol[i]
                elif cs[i]>cs[i-1]:up+=vol[i]
            avg=sum(vol[-21:-1])/20
            if avg>0:d=distribution(down,up,vol[-1],avg,cs[-1]<cs[-2])
            else:reasons.append('zero_reference_volume')
        factors=[t,d,None,None,None]
        scoring=score(factors,horizon,data_status=state,history_sessions=len(history))
        ready=state=='valid' and len(history)>=252 and t is not None and d is not None
        reasons.extend(['fundamentals_not_integrated','valuation_not_integrated','catalyst_not_verified','borrow_and_cost_unknown'])
        row={k:v for k,v in item.items() if k!='bars'}
        row.update(mode=mode,demo=mode=='demo',state=state,session_date=latest['session_date'] if latest else None,close=latest['close_micros']/1e6 if latest else None,source=latest['source'] if latest else None,available_at=latest['available_at'] if latest else None,close_definition=latest['close_definition'] if latest else None,basis='split' if latest else None,history_sessions=len(history),horizon=horizon,technical_eligible=ready,known_contribution=scoring['research_score'],known_contribution_max=WEIGHTS[horizon][0]+WEIGHTS[horizon][1],coverage=round(scoring['coverage']*100),research_score=None,risk_adjusted_score=None,execution_status='unknown',verification='synthetic_demo' if mode=='demo' else 'single_source_or_user_declared',factors={'trend':t,'distribution':d,'fundamentals':None,'valuation':None,'catalyst':None},indicators=metric,reasons=sorted(set(reasons)),chart=[{'date':b['session_date'],'close':b['close_micros']/1e6} for b in history[-100:]],expected_session=calendars[item['market']]['expected_session'],calendar_status=calendars[item['market']]['state'],calendar_version=calendars[item['market']]['calendar_version'],missing_history_sessions=missing)
        all_rows.append(row)
        if t is None and d is None:row['known_contribution']=None
        if item['market']=='AU':
            row['asx_review']=describe_asx(history,state=='valid' and not missing)
            row['adjustment_id']=(latest.get('adjustment_id') or None) if latest else None
            row['volume_basis']=(latest.get('volume_basis') or None) if latest else None
        # The manifest covers the records consulted for quality and the price
        # benchmark. It is reproducibility metadata, not verified market evidence.
        benchmark=by_symbol.get(BENCHMARKS[item['market']])
        inputs=[(x['payload_sha256'],x['revision'],x['observed_at'])
                for x in item['bars']+(benchmark['bars'] if benchmark else [])]
        manifest={'bar_inputs':sorted(inputs),'cutoff':stamp(cutoff),
                  'expected_session':calendars[item['market']]['expected_session'],
                  'calendar':calendars[item['market']]['calendar_version'],
                  'calendar_overrides':sorted(x['sha256'] for x in overrides.values()),
                  'horizon':horizon,'weights':WEIGHTS[horizon],'rule_version':'technical-v1',
                  'quality_version':'series-quality-v7','calculation_version':'technical-math-v3',
                  'read_consistency':'sqlite-transaction-v1',
                  'asx_review_version':'asx-review-v1'}
        row.update(conflicts=conflicts,comparison_skips=skips,adjustment_metadata_verified=False,
                   adjustment_metadata_status='synthetic_demo' if mode=='demo' else
                   'invalid' if any(x in reasons for x in ('adjustment_metadata_missing','adjustment_version_mismatch')) else
                   'declared_consistent' if bs and adjustment_issue(bs[-252:]) is None else 'undeclared',
                   observed_at=latest['observed_at'] if latest else None,
                   price_revision=latest['revision'] if latest else None,
                   availability_kind=latest.get('availability_kind','declared') if latest else None,
                   price_payload_sha256=latest['payload_sha256'] if latest else None,
                   batch_id=latest['batch_id'] if latest else None,
                   input_manifest_sha256=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest())
    coverage={m:{'observed_instruments':sum(x['market']==m for x in all_rows),'technical_eligible':sum(x['market']==m and x['technical_eligible'] for x in all_rows),'universe_coverage_pct':None} for m in markets}
    for m,c in coverage.items():
        c.update(expected_session=calendars[m]['expected_session'],calendar_status=calendars[m]['state'],
                 stale_instruments=sum(x['market']==m and 'behind_expected_market_session' in x['reasons'] for x in all_rows) if calendars[m]['state']=='reviewed_schedule' else None,
                 invalid_history_instruments=sum(x['market']==m and bool(x['missing_history_sessions']) for x in all_rows) if calendars[m]['state']=='reviewed_schedule' else None)
    selected=[r for r in all_rows if (market=='ALL' or r['market']==market) and (sector=='ALL' or r['sector']==sector) and query.strip().lower() in (r['name']+' '+r['symbol_id']).lower()]
    selected.sort(key=lambda x:(not x['technical_eligible'],-(x['known_contribution'] or 0),x['symbol_id']))
    return {'version':__version__,'rule_version':'technical-v1','calculation_version':'technical-math-v3','quality_version':'series-quality-v7','read_consistency':'sqlite-transaction-v1','mode':mode,'asof':stamp(cutoff),'horizon':horizon,'coverage':coverage,'calendars':calendars,'rows':selected,'note':'现在只按规则记录过去的价格和买卖数量；资料没拿到就留空，分数不是下跌概率。'}


def radar(store,mode,cutoff,horizon=10,market='AU',sector='ALL',query=''):
    """Public product path: AU only, including omitted/legacy ALL filters."""
    return _radar(store,mode,cutoff,horizon,active_market(market),sector,query,ACTIVE_MARKETS)


def legacy_radar(store,mode,cutoff,horizon=10,market='ALL',sector='ALL',query=''):
    """Archived regression fixtures only; never exposed by API/CLI/UI."""
    return _radar(store,mode,cutoff,horizon,market,sector,query)
