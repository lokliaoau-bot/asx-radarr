"""Official read-only data endpoints. Inject transport for offline contract tests."""
import os,json,time,re,hashlib,math
from decimal import Decimal
from itertools import chain
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,urlparse,parse_qsl,urlunparse
from .models import instant,stamp,market_zone,micros,shares
from .jsonutil import unique_fields

class ProviderError(RuntimeError):
    def __init__(self,message,code='provider_error',retry_after=None):
        super().__init__(message);self.code=code;self.retry_after=retry_after

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def finite_float(value):
    result=float(value)
    if not math.isfinite(result):raise ValueError('nonfinite JSON number')
    return result

def inspect_json(value,secrets):
    # Bound document depth explicitly, independently of Python's global limit.
    pending=[(iter((value,)),0)];found=False
    while pending:
        iterator,depth=pending[-1]
        try:item=next(iterator)
        except StopIteration:pending.pop();continue
        if isinstance(item,(dict,list)):
            if depth>=128:raise ValueError('unsupported JSON nesting depth')
            children=chain(item.keys(),item.values()) if isinstance(item,dict) else iter(item)
            pending.append((children,depth+1))
        elif isinstance(item,str) and any(secret in item for secret in secrets):found=True
    return found

def transport(url,headers):
    try:
        with build_opener(NoRedirect).open(Request(url,headers=headers),timeout=25) as r:
            body=r.read(32*1024*1024+1)
            return r.status,dict(r.headers),body
    except HTTPError as e:
        try:
            with e:return e.code,dict(e.headers),e.read(32*1024*1024+1)
        except (URLError,TimeoutError,OSError):
            raise ProviderError('error response body could not be received') from None
    except (URLError,TimeoutError,OSError):raise ProviderError('network request failed; credentials are never included in errors') from None

class JsonClient:
    def __init__(self,headers=None,request=transport,sleep=time.sleep,max_retry_wait=30,clock=None):
        if not math.isfinite(max_retry_wait) or not 0<=max_retry_wait<=60:raise ValueError('retry wait budget must be between 0 and 60 seconds')
        self.headers=headers or {};self.request=request;self.sleep=sleep;self.receipts=[];self.before_request=None
        self.max_retry_wait=max_retry_wait;self.clock=clock or (lambda:datetime.now(timezone.utc))
    def _retry_delay(self,value,attempt):
        text=str(value).strip()
        try:
            if text.isascii() and text.isdigit():return int(text)
            at=parsedate_to_datetime(text)
            if at.tzinfo is None:at=at.replace(tzinfo=timezone.utc)
            return max(0,math.ceil((at-self.clock()).total_seconds()))
        except (ValueError,TypeError,OverflowError):return 2**attempt
    def get(self,url,*,exact_prices=False):
        try:parsed=urlparse(url);port=parsed.port
        except (ValueError,TypeError):raise ProviderError('host not allowed') from None
        if (parsed.scheme!='https' or parsed.hostname not in {'data.alpaca.markets','paper-api.alpaca.markets','data.sec.gov','eodhd.com'}
                or parsed.username is not None or parsed.password is not None or port not in (None,443)):
            raise ProviderError('host not allowed')
        safe_query=urlencode([(k,'[redacted]' if k.lower() in {'api_token','apikey','token','key'} else v) for k,v in parse_qsl(parsed.query,keep_blank_values=True)])
        safe_url=urlunparse(parsed._replace(query=safe_query))
        secrets={v for k,v in parse_qsl(parsed.query,keep_blank_values=True)
                 if k.lower() in {'api_token','apikey','token','key'} and v}
        for key,value in self.headers.items():
            if key.upper() in {'APCA-API-KEY-ID','APCA-API-SECRET-KEY','AUTHORIZATION'} and value:
                secrets.add(str(value).split(' ',1)[-1] if key.upper()=='AUTHORIZATION' else str(value))
        for attempt in range(4):
            if self.before_request:self.before_request()
            status,headers,body=self.request(url,{'Accept':'application/json',**self.headers})
            receipt={'url':safe_url,'http_status':status,'raw_sha256':hashlib.sha256(body).hexdigest(),
                     'observed_at':stamp(self.clock()),'body_bytes':len(body),
                     'body_truncated':len(body)>32*1024*1024,'accepted_json':False}
            self.receipts.append(receipt)
            if status in (401,403):raise ProviderError('permission denied; no IEX fallback is allowed',code='permission_denied')
            if status in (429,500,502,503,504):
                if attempt==3:raise ProviderError('retry limit reached')
                retry=next((v for k,v in headers.items() if k.lower()=='retry-after'),str(2**attempt))
                delay=max(1,self._retry_delay(retry,attempt))
                if delay>self.max_retry_wait:
                    raise ProviderError('provider retry deferred; Retry-After exceeds local wait budget',code='retry_deferred',retry_after=delay)
                self.sleep(delay);continue
            if status!=200:raise ProviderError('provider returned HTTP '+str(status))
            if len(body)>32*1024*1024:raise ProviderError('response too large')
            ctype=next((v for k,v in headers.items() if k.lower()=='content-type'),'')
            if 'json' not in ctype.lower():raise ProviderError('expected JSON; HTTP200 HTML is not market data')
            try:
                result=json.loads(body,object_pairs_hook=unique_fields,
                                  parse_float=Decimal if exact_prices else finite_float,
                                  parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
                echoed=inspect_json(result,secrets)
                text=body.decode('utf8')
                receipt['accepted_json']=True
                if echoed:receipt['credential_echo_omitted']=True
                else:receipt['body_utf8']=text
                return result
            except (ValueError,UnicodeError,RecursionError):raise ProviderError('malformed JSON') from None

class Alpaca:
    def __init__(self,key=None,secret=None,client=None):
        key=key or os.environ.get('APCA_API_KEY_ID');secret=secret or os.environ.get('APCA_API_SECRET_KEY')
        if not client and (not key or not secret):raise ProviderError('Alpaca credentials not configured')
        self.client=client or JsonClient({'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret})
    def minutes(self,symbols,start,end,now):
        start_at,end_at=instant(start),instant(end)
        if end_at>now-timedelta(minutes=16):raise ProviderError('end must be at least 16 minutes old')
        if start_at>end_at:raise ProviderError('start is after end')
        if not symbols or len(set(symbols))!=len(symbols) or any(not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',s) for s in symbols):raise ProviderError('invalid or duplicate symbols')
        query={'symbols':','.join(symbols),'start':start,'end':end,'timeframe':'1Min','feed':'sip','adjustment':'split','sort':'asc','limit':10000,'asof':instant(end).date().isoformat()}
        result={s:[] for s in symbols};seen=set();token=None
        for _ in range(1000):
            if token:query['page_token']=token
            payload=self.client.get('https://data.alpaca.markets/v2/stocks/bars?'+urlencode(query),exact_prices=True)
            if not isinstance(payload,dict) or not isinstance(payload.get('bars'),dict):raise ProviderError('invalid bars response')
            for symbol,bars in payload['bars'].items():
                if symbol not in result or not isinstance(bars,list):raise ProviderError('unexpected symbol or bars shape')
                result[symbol].extend(bars)
            token=payload.get('next_page_token')
            if token is not None and (not isinstance(token,str) or len(token)>4096):raise ProviderError('invalid pagination token')
            if not token:break
            if token in seen:raise ProviderError('pagination token cycle')
            seen.add(token)
        else:raise ProviderError('pagination safety limit reached')
        missing=[s for s in symbols if not result[s]]
        if missing:raise ProviderError('no bars for: '+','.join(missing))
        for bars in result.values():
            try:
                if not all(isinstance(b,dict) for b in bars):raise ValueError()
                times=[instant(b['t']) for b in bars]
            except (ValueError,KeyError,TypeError):raise ProviderError('invalid minute timestamps') from None
            if len(times)!=len(set(times)):raise ProviderError('duplicate minute bars')
            if any(not start_at<=at<=end_at for at in times):
                raise ProviderError('minute timestamp outside requested inclusive window',code='unexpected_data_window')
        return result
    def calendar(self,start,end):
        data=self.client.get('https://paper-api.alpaca.markets/v2/calendar?'+urlencode({'start':start,'end':end}))
        if not isinstance(data,list):raise ProviderError('invalid calendar response')
        if not all(isinstance(d,dict) and {'date','open','close'}<=set(d) and isinstance(d['date'],str) for d in data):raise ProviderError('invalid calendar session shape')
        if len({d.get('date') for d in data})!=len(data):raise ProviderError('duplicate calendar sessions')
        return data
    def daily_rows(self,symbols,start,end,now):
        data=self.minutes(symbols,start,end,now)
        calendar=self.calendar(instant(start).date().isoformat(),instant(end).date().isoformat())
        zone=market_zone('US');end_at=instant(end);sessions={};rows=[]
        observed_at=stamp(self.client.clock())
        for value in calendar:
            try:
                op=datetime.fromisoformat(value['date']+'T'+value['open']).replace(tzinfo=zone)
                cl=datetime.fromisoformat(value['date']+'T'+value['close']).replace(tzinfo=zone)
                if op>=cl or (cl-op).total_seconds()%60:raise ValueError()
            except (ValueError,KeyError,TypeError):raise ProviderError('invalid calendar session boundaries') from None
            if cl<=end_at:sessions[value['date']]=(op,cl,int((cl-op).total_seconds()/60))
        seen_symbols=set()
        for symbol,bars in data.items():
            buckets={}
            for bar in bars:
                at=instant(bar['t']);day=at.astimezone(zone).date().isoformat()
                bounds=sessions.get(day)
                if bounds is None or not bounds[0]<=at<bounds[1]:continue
                try:
                    o,h,l,c=(micros(bar[key]) for key in ('o','h','l','c'))
                    volume=shares(bar['v'])
                    if not l<=min(o,c)<=max(o,c)<=h:raise ValueError()
                except (ValueError,KeyError,TypeError):raise ProviderError('invalid regular minute OHLC or share volume') from None
                elapsed=(at-bounds[0]).total_seconds()
                aligned=elapsed%60==0
                bucket=buckets.get(day)
                if bucket is None:
                    bucket={'first':at,'last':at,'o':o,'h':h,'l':l,'c':c,'v':0,'slots':set(),'misaligned':False}
                    buckets[day]=bucket
                if at<bucket['first']:bucket['first']=at;bucket['o']=o
                if at>bucket['last']:bucket['last']=at;bucket['c']=c
                bucket['h']=max(bucket['h'],h);bucket['l']=min(bucket['l'],l);bucket['v']+=volume
                if aligned:bucket['slots'].add(int(elapsed/60))
                else:bucket['misaligned']=True
            for day,bucket in sorted(buckets.items()):
                op,cl,expected=sessions[day]
                prices={key:f"{bucket[code]//1000000}.{bucket[code]%1000000:06d}" for key,code in (('open','o'),('high','h'),('low','l'),('close','c'))}
                rows.append({'market':'US','symbol':symbol,'name':symbol,'sector':'基准' if symbol=='SPY' else '其他','currency':'USD','session_date':day,**prices,'volume':bucket['v'],'volume_unit':'shares','session_open_utc':stamp(op),'session_close_utc':stamp(cl),'available_at':observed_at,'availability_kind':'observation','source':'alpaca-sip-minutes','basis':'split','close_definition':'regular_minute_aggregate','calendar_source':'alpaca-calendar','calendar_verified':'true','demo':'false','incomplete':str(len(bucket['slots'])!=expected or bucket['misaligned']).lower()})
                seen_symbols.add(symbol)
        if not rows:raise ProviderError('no completed regular sessions in range')
        missing=set(symbols)-seen_symbols
        if missing:raise ProviderError('no completed regular sessions for: '+','.join(sorted(missing)),code='missing_regular_data')
        return rows

class SEC:
    def __init__(self,user_agent=None,client=None):
        ua=user_agent or os.environ.get('SEC_USER_AGENT','')
        if not client and not re.search(r'[^\s@]+@[^\s@]+\.[^\s@]+',ua):raise ProviderError('SEC_USER_AGENT must include a real contact email')
        self.client=client or JsonClient({'User-Agent':ua})
    def fetch(self,cik):
        if not re.fullmatch(r'\d{1,10}',str(cik)):raise ProviderError('invalid CIK')
        cik=str(cik).zfill(10)
        submissions=self.client.get(f'https://data.sec.gov/submissions/CIK{cik}.json')
        # Slow default single-company access. No parallel SEC request storm.
        self.client.sleep(1)
        facts=self.client.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json')
        if not isinstance(submissions,dict) or not isinstance(facts,dict) or not isinstance(facts.get('facts'),dict):raise ProviderError('invalid SEC response')
        if int(submissions.get('cik',-1))!=int(cik) or int(facts.get('cik',-1))!=int(cik):raise ProviderError('SEC CIK mismatch')
        return submissions,facts

def extract_sec_facts(submissions,companyfacts,retrieved_at):
    recent=submissions.get('filings',{}).get('recent',{});accepted={}
    accessions=recent.get('accessionNumber',[]);timestamps=recent.get('acceptanceDateTime',[])
    if len(accessions)!=len(timestamps):raise ProviderError('SEC accession/acceptance columns mismatch')
    for acc,ts in zip(accessions,timestamps):accepted[acc]=stamp(instant(ts))
    rows=[]
    for taxonomy,tags in companyfacts.get('facts',{}).items():
        for concept,meta in tags.items():
            for unit,values in meta.get('units',{}).items():
                for fact in values:
                    row={'taxonomy':taxonomy,'concept':concept,'unit':unit,'value':fact.get('val'),'period_start':fact.get('start'),'period_end':fact.get('end'),'accession':fact.get('accn'),'form':fact.get('form'),'filed':fact.get('filed'),'published_at':accepted.get(fact.get('accn')),'available_at':stamp(retrieved_at),'status':'needs_period_and_context_review'}
                    rows.append(row)
    return rows
