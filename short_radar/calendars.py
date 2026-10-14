"""Reviewed cash-market schedules, independent of observed price rows.

Snapshots describe reviewed schedules, not historical calendar knowledge. They
do not discover unexpected closures or security-specific suspensions. Reviewed
local overrides are supplied by Store and selected using both knowledge clocks.
"""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse
import json

from .models import MARKET_TIMEZONE, instant, market_zone, stamp, ValidationError
from . import asx_calendar
from .jsonutil import unique_fields

CALENDAR_VERSION = 'cash-hk-us-au-reviewed-2026-10-13-v2'
REVIEWED_AT = '2026-10-13'
YEARS = (2025, 2026, 2027)
HK_HOLIDAYS = {
    2025: {'01-01', '01-29', '01-30', '01-31', '04-04', '04-18', '04-19',
           '04-21', '05-01', '05-05', '05-31', '07-01', '10-01', '10-07',
           '10-29', '12-25', '12-26'},
    2026: {'01-01', '02-17', '02-18', '02-19', '04-03', '04-04', '04-06',
           '04-07', '05-01', '05-25', '06-19', '07-01', '09-26', '10-01',
           '10-19', '12-25', '12-26'},
    2027: {'01-01', '02-06', '02-08', '02-09', '03-26', '03-27', '03-29',
           '04-05', '05-01', '05-13', '06-09', '07-01', '09-16', '10-01',
           '10-08', '12-25', '12-27'},
}
HK_EARLY = {2025: {'01-28', '12-24', '12-31'},
            2026: {'02-16', '12-24', '12-31'},
            2027: {'02-05', '12-24', '12-31'}}
US_HOLIDAYS = {
    # January 9 was a separately announced national day of mourning.
    2025: {'01-01', '01-09', '01-20', '02-17', '04-18', '05-26', '06-19',
           '07-04', '09-01', '11-27', '12-25'},
    2026: {'01-01', '01-19', '02-16', '04-03', '05-25', '06-19', '07-03',
           '09-07', '11-26', '12-25'},
    2027: {'01-01', '01-18', '02-15', '03-26', '05-31', '06-18', '07-05',
           '09-06', '11-25', '12-24'},
}
US_EARLY = {2025: {'07-03', '11-28', '12-24'},
            2026: {'11-27', '12-24'}, 2027: {'11-26'}}
SOURCES = {
    'HK': [
        'https://www.hkex.com.hk/Services/Trading-hours-and-Severe-Weather-Arrangements/Trading-Hours/Securities-Market?sc_lang=en',
        'https://www.info.gov.hk/gia/general/202405/03/P2024043000417.htm',
        'https://www.info.gov.hk/gia/general/202505/16/P2025051300353.htm',
        'https://www.info.gov.hk/gia/general/202605/15/P2026051400300.htm',
    ],
    'US': [
        'https://www.nyse.com/markets/hours-calendars',
        'https://ir.theice.com/press/news-details/2024/NYSE-Group-Announces-2025-2026-and-2027-Holiday-and-Early-Closings-Calendar/default.aspx',
        'https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx',
    ],
    'AU': asx_calendar.SOURCES,
}


class CalendarUnavailable(ValueError):
    pass


def _day(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def session(market, value, overrides=None):
    """Return scheduled bounds or None for a reviewed closed day."""
    if market not in MARKET_TIMEZONE:
        raise ValueError('unsupported market')
    d = _day(value)
    if d.year not in YEARS:
        raise CalendarUnavailable('calendar year not reviewed')
    override = (overrides or {}).get((market, d.isoformat()))
    if override:
        if override['status'] == 'closed':
            return None
        return {
            'date': d.isoformat(), 'market': market,
            **{key: instant(override[key]) for key in
               ('open', 'normal_end', 'auction_end', 'close')},
            'early_close': override['status'] == 'early',
            'calendar_version': CALENDAR_VERSION,
            'override_sha256': override['sha256'],
        }
    if market == 'AU':
        s = asx_calendar.session(d)
        return {**s, 'market': market, 'calendar_version': CALENDAR_VERSION} if s else None
    holidays = HK_HOLIDAYS if market == 'HK' else US_HOLIDAYS
    if d.weekday() >= 5 or d.strftime('%m-%d') in holidays[d.year]:
        return None
    early = d.strftime('%m-%d') in (HK_EARLY if market == 'HK' else US_EARLY)[d.year]
    zone = market_zone(market)
    op = datetime.combine(d, time(9, 30), zone)
    normal_end = datetime.combine(d, time(12 if early else 16) if market == 'HK'
                                  else time(13 if early else 16), zone)
    auction_end = normal_end + timedelta(minutes=10 if market == 'HK' else 0)
    return {'date': d.isoformat(), 'market': market, 'open': op,
            'normal_end': normal_end, 'auction_end': auction_end, 'close': auction_end,
            'early_close': early, 'calendar_version': CALENDAR_VERSION}


def latest_completed(market, cutoff, overrides=None):
    if cutoff.tzinfo is None:
        raise ValueError('cutoff must include timezone')
    d = cutoff.astimezone(market_zone(market)).date()
    # Unknown years fail immediately, even if the previous year was reviewed.
    for _ in range(40):
        s = session(market, d, overrides)
        if s and s['close'] <= cutoff:
            return s
        d -= timedelta(days=1)
    raise CalendarUnavailable('no completed session in reviewed calendar window')


def previous_sessions(market, end, count, overrides=None):
    """Exactly count expected sessions ending on/before end; never infer gaps."""
    result = []
    d = _day(end)
    while len(result) < count:
        s = session(market, d, overrides)
        if s:
            result.append(s['date'])
        d -= timedelta(days=1)
    return list(reversed(result))


def calendar_status(market, cutoff, overrides=None):
    result = {'market': market, 'calendar_version': CALENDAR_VERSION,
              'reviewed_at': REVIEWED_AT, 'reviewed_years': list(YEARS),
              'sources': SOURCES[market], 'unexpected_closures': 'manual_review_required',
              'historical_calendar_knowledge_verified': False}
    try:
        s = latest_completed(market, cutoff, overrides)
        result.update(state='reviewed_schedule', expected_session=s['date'],
                      expected_close_utc=stamp(s['close']), early_close=s['early_close'],
                      phase_times_verified=s.get('phase_times_verified',True),
                      override_sha256=s.get('override_sha256'))
    except CalendarUnavailable:
        result.update(state='unreviewed', expected_session=None,
                      expected_close_utc=None, early_close=None,phase_times_verified=None, override_sha256=None)
    return result


def parse_override(raw, now):
    """The same strict document boundary for preview and database writes."""
    if not isinstance(raw, str) or len(raw.encode('utf8')) > 65536:
        raise ValidationError('calendar override exceeds 64 KiB')
    try:
        payload = json.loads(raw, object_pairs_hook=unique_fields,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError):
        raise ValidationError('calendar override JSON is malformed or has duplicate fields') from None
    return normalize_override(payload, now)


def normalize_override(payload, now):
    """Strict, manually reviewed market-wide override. This is not a suspension."""
    if not isinstance(payload, dict):
        raise ValidationError('calendar override must be an object')
    allowed = {'market', 'session_date', 'status', 'reason', 'source_url',
               'available_at', 'open', 'normal_end', 'auction_end', 'close'}
    if set(payload) - allowed:
        raise ValidationError('unknown calendar override fields')
    required={'market','session_date','status','reason','source_url','available_at'}
    if required-set(payload):
        raise ValidationError('missing calendar override fields: '+','.join(sorted(required-set(payload))))
    if any(not isinstance(payload[key], str) for key in required):
        raise ValidationError('开休市说明的市场、日期、状态、原因、出处和时间必须是文字。')
    market = payload.get('market')
    if market not in MARKET_TIMEZONE:
        raise ValidationError('unsupported market')
    try:d = date.fromisoformat(payload['session_date'])
    except ValueError:raise ValidationError('开休市日期格式有问题，请使用年-月-日。') from None
    if d.year not in YEARS:
        raise ValidationError('calendar year not reviewed; override cannot unlock a year')
    status = payload.get('status')
    if status not in {'closed', 'regular', 'early'}:
        raise ValidationError('override status must be closed, regular or early')
    reason = payload.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValidationError('override requires a concise reason')
    url = payload.get('source_url', '')
    if not isinstance(url, str) or len(url) > 2048:
        raise ValidationError('invalid source URL')
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname not in
            {'www.hkex.com.hk', 'www.nyse.com', 'ir.theice.com', 'www.nasdaqtrader.com',
             'www.asx.com.au', 'asxonline.com'} or parsed.username or parsed.password):
        raise ValidationError('override requires an official HTTPS source URL')
    av = instant(payload['available_at'])
    if av > now:
        raise ValidationError('future override availability')
    result = dict(payload, session_date=d.isoformat(), available_at=stamp(av))
    bounds = ('open', 'normal_end', 'auction_end', 'close')
    if status == 'closed':
        if any(payload.get(key) is not None for key in bounds):
            raise ValidationError('closed override must not include trading times')
        for key in bounds:
            result.pop(key, None)
    else:
        if set(bounds)-set(payload):
            raise ValidationError('open override requires all four session times')
        times = [instant(payload[key]) for key in bounds]
        if not times[0] < times[1] <= times[2] <= times[3]:
            raise ValidationError('invalid override session boundaries')
        if any(t.astimezone(market_zone(market)).date() != d for t in times):
            raise ValidationError('override date/timezone mismatch')
        result.update({key: stamp(t) for key, t in zip(bounds, times)})
    return result


def bar_calendar_issue(bar, market, cutoff, overrides=None):
    """Recheck persisted rows, including databases imported with older code."""
    try:
        s = session(market, bar['session_date'], overrides)
    except CalendarUnavailable:
        return 'calendar_year_not_reviewed'
    if s is None:
        return 'bar_on_closed_session'
    op, cl = instant(bar['session_open_utc']), instant(bar['session_close_utc'])
    if cutoff < s['close'] or instant(bar['available_at']) < s['close']:
        return 'calendar_session_not_finalized'
    if market == 'AU':
        return asx_calendar.boundary_issue(s,op,cl,bar['close_definition'])
    elif op != s['open'] or cl != s['close']:
        return 'calendar_boundary_mismatch'
    return None
