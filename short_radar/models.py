from dataclasses import dataclass, asdict
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
from .contracts import SECTORS, MARKET_CURRENCY, MARKET_TIMEZONE, CLOSE_DEFINITIONS

class ValidationError(ValueError): pass

def instant(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if d.tzinfo is None: raise ValueError()
        return d.astimezone(UTC)
    except (ValueError, TypeError): raise ValidationError('timestamp must include timezone') from None

def stamp(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError('timestamp must include timezone')
    return value.astimezone(UTC).isoformat(timespec='microseconds')

def micros(value):
    try:
        text=str(value)
        if len(text)>128:raise ValueError()
        n=Decimal(text)
        if not n.is_finite() or n<Decimal('0.000001') or n>Decimal('1000000000'): raise ValueError()
        numerator,denominator=n.as_integer_ratio()
        scaled,remainder=divmod(numerator*1000000,denominator)
        if remainder:raise ValueError()
        return scaled
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('price must be positive, finite and use at most 6 decimals') from None

def shares(value):
    try:
        n=Decimal(str(value))
        if not n.is_finite() or n<0 or n!=n.to_integral_value() or n>2**63-1:raise ValueError()
        return int(n)
    except (InvalidOperation,ValueError,TypeError):raise ValidationError('volume must be a nonnegative integer of shares') from None

def symbol_id(market,symbol):
    market=str(market).upper().strip();symbol=str(symbol).upper().strip()
    if market=='HK':
        symbol=symbol.removeprefix('HK.').removesuffix('.HK')
        if not re.fullmatch(r'[0-9]{1,5}',symbol) or int(symbol)==0:raise ValidationError('invalid HK symbol')
        return 'HK.'+symbol.zfill(5)
    if market=='US':
        symbol=symbol.removeprefix('US.')
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',symbol):raise ValidationError('invalid US symbol')
        return 'US.'+symbol
    if market=='AU':
        for prefix in ('XASX:','ASX:','AU.'):
            if symbol.startswith(prefix):symbol=symbol[len(prefix):];break
        for suffix in ('.AX','.AU'):
            if symbol.endswith(suffix):symbol=symbol[:-len(suffix)];break
        if not re.fullmatch(r'[A-Z0-9]{3,6}',symbol):raise ValidationError('invalid ASX symbol')
        return 'AU.'+symbol
    raise ValidationError('only HK, US and AU are supported')

def market_zone(market):
    if market not in MARKET_TIMEZONE:raise ValidationError('unsupported market timezone')
    try:return ZoneInfo(MARKET_TIMEZONE[market])
    except ZoneInfoNotFoundError:raise ValidationError('timezone database missing; install tzdata: python -m pip install tzdata') from None

def truth(value):
    v=str(value).lower().strip()
    if v not in {'true','false'}:raise ValidationError('boolean must be true or false')
    return v=='true'

@dataclass(frozen=True)
class Bar:
    symbol_id: str
    market: str
    name: str
    sector: str
    currency: str
    session_date: str
    open_micros: int
    high_micros: int
    low_micros: int
    close_micros: int
    volume: int
    session_open_utc: str
    session_close_utc: str
    available_at: str
    source: str
    basis: str
    close_definition: str
    calendar_source: str
    mode: str
    quality: str
    availability_kind: str = 'declared'
    adjustment_id: str = ''
    volume_basis: str = ''

    @classmethod
    def parse(cls,row,now,mode,calendar_overrides=None):
        if mode not in {'demo','imported'}:raise ValidationError('invalid mode')
        market=str(row['market']).strip().upper()
        sid=symbol_id(market,row['symbol'])
        currency=str(row['currency']).upper()
        if currency!=MARKET_CURRENCY[market]:raise ValidationError('currency/market mismatch')
        d=date.fromisoformat(row['session_date']);op=instant(row['session_open_utc']);cl=instant(row['session_close_utc']);av=instant(row['available_at'])
        if op>=cl:raise ValidationError('invalid session boundaries')
        if op.astimezone(market_zone(market)).date()!=d:raise ValidationError('session date/timezone mismatch')
        if cl.astimezone(market_zone(market)).date()!=d:raise ValidationError('session close date/timezone mismatch')
        if av>now:raise ValidationError('future availability timestamp')
        if truth(row['demo'])!=(mode=='demo'):raise ValidationError('demo and imported data cannot be mixed')
        o,h,l,c=[micros(row[k]) for k in ('open','high','low','close')]
        if not l<=min(o,c)<=max(o,c)<=h:raise ValidationError('invalid OHLC bounds')
        v=shares(row['volume'])
        if row['volume_unit']!='shares':raise ValidationError('volume unit must be shares, not lots')
        if row['basis'] not in {'raw','split'}:raise ValidationError('basis must be raw or split')
        if row['sector'] not in SECTORS:raise ValidationError('unsupported sector')
        for key in ('source','name','calendar_source'):
            if not str(row[key]).strip() or len(str(row[key]))>160:raise ValidationError('invalid '+key)
        if row['close_definition'] not in CLOSE_DEFINITIONS:raise ValidationError('unknown close definition')
        if row['close_definition']=='asx_eod' and market!='AU':raise ValidationError('ASX close definition requires AU market')
        incomplete=truth(row['incomplete']) if str(row.get('incomplete','')).strip() else False
        source_verified=truth(row['source_verified']) if str(row.get('source_verified','')).strip() else None
        availability_kind=str(row.get('availability_kind','')).strip() or 'declared'
        if availability_kind not in {'declared','publication','observation'}:raise ValidationError('unknown availability timestamp kind')
        adjustment_id=str(row.get('adjustment_id','')).strip()
        if adjustment_id and not re.fullmatch(r'[A-Za-z0-9_.:\-]{1,128}',adjustment_id):raise ValidationError('invalid adjustment version ID')
        volume_basis=str(row.get('volume_basis','')).strip()
        if volume_basis not in {'','raw','split'}:raise ValidationError('volume basis must be raw or split')
        quality='valid'
        if av<cl or now<cl:quality='provisional'
        elif not truth(row['calendar_verified']) or row['close_definition']=='all_sessions':quality='unverified_session'
        elif v==0:quality='no_trades'
        elif incomplete:quality='incomplete'
        if mode=='imported':
            from .calendars import session,CalendarUnavailable
            try:known_session=session(market,d,calendar_overrides)
            except CalendarUnavailable:known_session='unknown'
            if known_session is None:raise ValidationError('not a '+market+' cash trading day')
            if known_session=='unknown':quality='unverified_session'
            elif av<known_session['close'] or now<known_session['close']:quality='provisional'
            elif market=='AU':
                from .asx_calendar import boundary_issue
                if boundary_issue(known_session,op,cl,row['close_definition']):quality='unverified_session'
            elif op!=known_session['open'] or cl!=known_session['close']:quality='unverified_session'
        source=str(row['source']).strip()
        from .quality import UNVERIFIED_SOURCES
        if quality=='valid' and (source_verified is False or source in UNVERIFIED_SOURCES):quality='source_unverified'
        return cls(sid,market,row['name'],row['sector'],currency,d.isoformat(),o,h,l,c,v,stamp(op),stamp(cl),stamp(av),source,row['basis'],row['close_definition'],row['calendar_source'],mode,quality,availability_kind,adjustment_id,volume_basis)

    def asdict(self):return asdict(self)
