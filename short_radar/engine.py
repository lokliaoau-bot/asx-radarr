"""Reference math only; no predictive accuracy or production data claims."""
from math import isfinite

WEIGHTS = {5: (40,20,15,5,20), 10: (35,15,25,10,15), 20: (25,10,35,15,15)}
FACTOR_NAMES = ('trend','distribution','fundamentals','valuation','catalyst')

def finite_inputs(*values):
    try:
        valid=all(not isinstance(value,bool) and isfinite(value) for value in values)
    except (TypeError,ValueError,OverflowError):
        valid=False
    if not valid:raise ValueError('numeric inputs must be finite numbers, not booleans')

def clip(x):
    finite_inputs(x)
    return max(0.0, min(1.0, x))

def rsi_wilder(closes, period=14):
    if isinstance(period,bool) or not isinstance(period,int) or period < 1 or len(closes) < period+1:
        raise ValueError('insufficient observations')
    finite_inputs(*closes)
    if any(x <= 0 for x in closes):
        raise ValueError('invalid prices')
    changes = [b-a for a,b in zip(closes,closes[1:])]
    gain = sum(max(x,0) for x in changes[:period])/period
    loss = sum(max(-x,0) for x in changes[:period])/period
    for x in changes[period:]:
        gain = (gain*(period-1)+max(x,0))/period
        loss = (loss*(period-1)+max(-x,0))/period
    finite_inputs(gain,loss)
    if gain == loss == 0: return 50.0
    if loss == 0: return 100.0
    return 100-100/(1+gain/loss)

def trend(close,ma20,ma60,relative_return20,ma20_change5):
    finite_inputs(close,ma20,ma60,relative_return20,ma20_change5)
    if min(close,ma20,ma60) <= 0: raise ValueError('invalid price or mean')
    return sum([clip((ma20-close)/(.08*ma20)),clip((ma60-close)/(.12*ma60)),clip(-relative_return20/.15),clip(-ma20_change5/.05)])/4

def distribution(down_volume20,up_volume20,today_volume,prior_mean_volume20,is_down_day):
    finite_inputs(down_volume20,up_volume20,today_volume,prior_mean_volume20)
    if not isinstance(is_down_day,bool):raise ValueError('down-day flag must be a boolean')
    if min(down_volume20,up_volume20,today_volume,prior_mean_volume20)<0 or prior_mean_volume20==0:
        raise ValueError('invalid volume')
    ratio = down_volume20/max(up_volume20,1)
    return .6*clip((ratio-1)/2)+.4*clip((today_volume/prior_mean_volume20-1)/1.5)*int(is_down_day)

def fundamentals(previous_revenue_yoy,current_revenue_yoy,margin_drop,net_income,cfo,average_assets,net_debt_ebitda):
    finite_inputs(previous_revenue_yoy,current_revenue_yoy,margin_drop,net_income,cfo,average_assets,net_debt_ebitda)
    if average_assets<=0: raise ValueError('invalid assets')
    return sum([clip((previous_revenue_yoy-current_revenue_yoy)/.30),clip(margin_drop/.10),clip(((net_income-cfo)/average_assets)/.15),clip((net_debt_ebitda-2)/4)])/4

def valuation(peer_percentile):
    finite_inputs(peer_percentile)
    if not 0<=peer_percentile<=1: raise ValueError('invalid percentile')
    return clip((peer_percentile-.5)/.45)

def catalyst(kind,age_sessions):
    severity={'guidance_cut':1,'profit_warning':.8,'dilution':.6,'none':0}
    if (not isinstance(kind,str) or kind not in severity or isinstance(age_sessions,bool)
            or not isinstance(age_sessions,int) or age_sessions<0): raise ValueError('unverified event')
    return severity[kind]*max(0,1-age_sessions/20)

def score(factors,horizon=10,risk_penalty=None,data_status='valid',history_sessions=252):
    from .contracts import STATE_LABELS
    if not isinstance(data_status,str) or data_status not in STATE_LABELS:raise ValueError('unknown data state')
    if isinstance(horizon,bool) or not isinstance(horizon,int) or horizon not in WEIGHTS:raise ValueError('unsupported horizon')
    if isinstance(history_sessions,bool) or not isinstance(history_sessions,int) or history_sessions<0:raise ValueError('history sessions must be a nonnegative integer')
    weights=WEIGHTS[horizon]
    present=[x is not None for x in factors]
    if len(present)!=5: raise ValueError('five factors required')
    for x in factors:
        if x is not None:
            finite_inputs(x)
            if not 0<=x<=1: raise ValueError('factor outside range')
    coverage=sum(w for w,p in zip(weights,present) if p)/100
    raw=sum(w*x for w,x in zip(weights,factors) if x is not None)
    if risk_penalty is not None:
        finite_inputs(risk_penalty)
        if not 0<=risk_penalty<=25: raise ValueError('invalid penalty')
    if data_status!='valid':
        return {'research_score':None,'risk_adjusted_score':None,'coverage':0,'ranking_eligible':False,'execution_verified':False}
    eligible=data_status=='valid' and history_sessions>=252 and coverage==1
    adjusted=max(0,raw-risk_penalty) if risk_penalty is not None and eligible else None
    return {'research_score':round(raw,2),'risk_adjusted_score':round(adjusted,2) if adjusted is not None else None,'coverage':coverage,'ranking_eligible':eligible,'execution_verified':False}

def short_pnl(entry,exit_price,quantity=1,dividend_per_share=0,annual_cost_rate=0,calendar_days=0,fees=0):
    """Constant quantity only. Split/merger handling belongs in event ledger."""
    finite_inputs(entry,exit_price,quantity,dividend_per_share,annual_cost_rate,calendar_days,fees)
    if min(entry,exit_price,quantity)<=0 or min(dividend_per_share,annual_cost_rate,calendar_days,fees)<0:
        raise ValueError('invalid execution/cost input')
    result=quantity*(entry-exit_price-dividend_per_share)-entry*quantity*annual_cost_rate*calendar_days/365-fees
    finite_inputs(result)
    return result

def short_stop_fill(open_price,high_price,stop_price):
    finite_inputs(open_price,high_price,stop_price)
    if min(open_price,high_price,stop_price)<=0 or high_price<open_price: raise ValueError('invalid prices')
    if open_price>=stop_price:return open_price
    return stop_price if high_price>=stop_price else None

def facts_available_asof(facts,cutoff):
    """Timezone-aware datetime required; returns only information then known."""
    if cutoff.tzinfo is None:raise ValueError('timezone required')
    return [f for f in facts if f['available_at']<=cutoff]
