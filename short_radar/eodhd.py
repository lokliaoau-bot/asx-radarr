"""Candidate ASX EOD source. Every returned row is quarantined for reconciliation.

Provider docs describe ASX licensing while generic pricing disclaimers describe
aggregated indicative feeds. Do not silently certify this as official ASX close.
The account's actual ASX/splits rights and independent prices remain unverified.
"""
import os,re,hashlib,json
from datetime import date
from decimal import Decimal,InvalidOperation
from fractions import Fraction
from urllib.parse import urlencode
from .providers import JsonClient,ProviderError
from .models import symbol_id,stamp,market_zone,micros,shares
from .asx_calendar import session,HOLIDAYS

def checked_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(value)):raise ProviderError('date must be YYYY-MM-DD')
    try:return date.fromisoformat(value)
    except ValueError:raise ProviderError('invalid date') from None

def split_ledger(actions,today):
    """Exact ratios, canonical event revision and bounded hostile input."""
    if not isinstance(actions,list) or len(actions)>1024:
        raise ProviderError('invalid splits response; cannot align OHLC and volume')
    earliest=date(min(HOLIDAYS),1,1);splits=[];seen=set()
    for action in actions:
        try:
            day=checked_date(action['date']);text=action['split']
            if not isinstance(text,str) or len(text)>80:raise ValueError()
            n,d=text.split('/');num,den=Decimal(n),Decimal(d)
            for value in (num,den):
                if (not value.is_finite() or value<=0 or len(value.as_tuple().digits)>32
                        or abs(value.as_tuple().exponent)>24):raise ValueError()
            if not earliest<=day<=today or day in seen:raise ValueError()
            ratio=Fraction(num)/Fraction(den)
            splits.append((day,ratio));seen.add(day)
        except (KeyError,ValueError,InvalidOperation,TypeError,ZeroDivisionError):
            raise ProviderError('invalid, future, or duplicate split event') from None
    splits.sort()
    canonical={'format':'eodhd-asx-splits-v1','from':earliest.isoformat(),
               'events':[(d.isoformat(),r.numerator,r.denominator) for d,r in splits]}
    return splits,hashlib.sha256(json.dumps(canonical,sort_keys=True).encode()).hexdigest()

def adjusted_price(raw_micros,factor):
    # Round the exact rational only once, to integer micro-AUD, ties to even.
    value=Fraction(raw_micros)/factor
    whole,remainder=divmod(value.numerator,value.denominator)
    if remainder*2>value.denominator or remainder*2==value.denominator and whole%2:
        whole+=1
    if not 0<whole<=10**15:raise ValueError('adjusted price outside supported precision/range')
    return f'{whole//1000000}.{whole%1000000:06d}'

class EODHD:
    def __init__(self,token=None,client=None,max_requests=8):
        self.token=token or os.environ.get('EODHD_API_TOKEN')
        if not self.token:raise ProviderError('EODHD_API_TOKEN is not configured; no subscription is created automatically')
        if not 1<=max_requests<=20:raise ProviderError('request budget must be between 1 and 20')
        self.client=client or JsonClient();self.requests=0;self.max_requests=max_requests
        self.client.before_request=self._budget
    def _budget(self):
        if self.requests>=self.max_requests:raise ProviderError('per-command API request budget reached; no paid upgrade or source fallback')
        if self.requests:self.client.sleep(3.2) # conservative <=20 requests/min incl. retries
        self.requests+=1
    def _get(self,endpoint,symbol,start,end):
        return self.client.get('https://eodhd.com/api/'+endpoint+'/'+symbol+'.AU?'+urlencode({'api_token':self.token,'from':start,'to':end,'period':'d','order':'a','fmt':'json'}),exact_prices=endpoint=='eod')
    def daily_rows(self,symbols,start,end,now):
        begin=checked_date(start);finish=checked_date(end);today=now.astimezone(market_zone('AU')).date()
        if begin>finish or finish>today:raise ProviderError('invalid or future EOD window')
        if begin.year not in HOLIDAYS or finish.year not in HOLIDAYS:
            raise ProviderError('ASX price-window calendar year not reviewed')
        codes=[symbol_id('AU',s).split('.',1)[1] for s in symbols]
        if not codes or len(codes)!=len(set(codes)):raise ProviderError('empty or duplicate ASX symbols')
        if len(codes)*2>self.max_requests:raise ProviderError('need two calls per symbol: EOD + splits; reduce watchlist or set a budget <=20')
        rows=[]
        for code in codes:
            data=self._get('eod',code,start,end)
            if not isinstance(data,list) or not data:raise ProviderError('no EOD rows; do not treat empty history as successful coverage')
            # Fixed action coverage keeps revision IDs stable for smaller daily
            # refreshes. All supported price dates are >= this reviewed start.
            actions=self._get('splits',code,date(min(HOLIDAYS),1,1).isoformat(),today.isoformat())
            received=self.client.clock()
            if received.astimezone(market_zone('AU')).date()!=today:
                raise ProviderError('responses crossed a Sydney date; repeat for one adjustment snapshot')
            observed_at=stamp(received)
            splits,adjustment_id=split_ledger(actions,today)
            seen_dates=set();dated=[]
            for bar in data:
                try:
                    day=checked_date(bar['date'])
                    if not begin<=day<=finish or day in seen_dates:raise ValueError()
                    dated.append((day,bar));seen_dates.add(day)
                except (KeyError,ValueError,TypeError):raise ProviderError('invalid or duplicate EOD date') from None
            factor=Fraction(1);action_index=len(splits)-1;symbol_rows=[]
            for day,bar in sorted(dated,reverse=True):
                try:
                    s=session(day)
                    if not s:raise ValueError()
                    while action_index>=0 and splits[action_index][0]>day:
                        factor*=splits[action_index][1];action_index-=1
                        if max(factor.numerator.bit_length(),factor.denominator.bit_length())>4096:
                            raise ValueError('unsupported accumulated split scale')
                    raw={key:micros(bar[key]) for key in ('open','high','low','close')}
                    if not raw['low']<=min(raw['open'],raw['close'])<=max(raw['open'],raw['close'])<=raw['high']:
                        raise ValueError('invalid raw OHLC')
                    prices={key:adjusted_price(value,factor) for key,value in raw.items()}
                    # Never substitute dividend-adjusted adjusted_close or double-adjust volume.
                    symbol_rows.append({'market':'AU','symbol':code,'name':code,'sector':'基准' if code=='STW' else '其他','currency':'AUD','session_date':day.isoformat(),**prices,'volume':shares(bar['volume']),'volume_unit':'shares','session_open_utc':stamp(s['open']),'session_close_utc':stamp(s['close']),'available_at':observed_at,'availability_kind':'observation','source':'eodhd-asx-candidate','basis':'split','adjustment_id':adjustment_id,'volume_basis':'split','close_definition':'asx_eod','calendar_source':s['calendar_version'],'calendar_verified':'true','demo':'false','incomplete':'false','source_verified':'false'})
                except (KeyError,ValueError,InvalidOperation,TypeError):raise ProviderError('invalid EOD bar, range, OHLC, volume, adjustment precision, or ASX calendar') from None
            rows.extend(reversed(symbol_rows))
        return rows
