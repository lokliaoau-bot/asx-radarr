"""Reviewed ASX cash calendar snapshot. No automatic scraping or future-year guess.

Date closures: ASX 2025 notice and 2026/2027 cash calendars reviewed 2026-10-13.
Session bounds are conservative EOD ingestion envelopes, not precise trade times.
Calendar changes/unscheduled halts still require refresh and independent data QA.
"""
from datetime import date,datetime,timedelta,time
from zoneinfo import ZoneInfo

CALENDAR_VERSION='asx-cash-reviewed-2026-10-13-v2'
SOURCES=[
 'https://asxonline.com/content/dam/asxonline/public/notices/2024/august/asx-trade-2025-attach.pdf',
 'https://www.asx.com.au/markets/market-resources/trading-hours-calendar/cash-market-trading-hours/trading-calendar',
 'https://www.asx.com.au/markets/market-resources/trading-hours-calendar/cash-market-trading-hours',
 'https://www.asx.com.au/blog/listed-at-asx/changes-to-equity-market-structure',
 'https://asxonline.com/content/dam/asxonline/public/notices/2025/november/asx-trade-xmas.pdf']
HOLIDAYS={
 2025:{'01-01','01-27','04-18','04-21','04-25','06-09','12-25','12-26'},
 2026:{'01-01','01-26','04-03','04-06','04-25','06-08','12-25','12-28'},
 2027:{'01-01','01-26','03-26','03-29','06-14','12-27','12-28'},
}

def session(value):
    d=date.fromisoformat(value) if isinstance(value,str) else value
    if d.year not in HOLIDAYS:raise ValueError('ASX calendar year not reviewed; add verified calendar before ranking')
    if d.weekday()>=5 or d.strftime('%m-%d') in HOLIDAYS[d.year]:return None
    early=d.strftime('%m-%d') in {'12-24','12-31'}
    zone=ZoneInfo('Australia/Sydney')
    # The 2025 special notice, not the generic calendar footnote, gives these
    # phases. Do not extrapolate this notice into later Christmas schedules.
    phases_verified=not early or d.year==2025
    normal_end=datetime.combine(d,time(14,0) if early and phases_verified
                                else time(14,10) if early else time(16,0),zone)
    auction_end=datetime.combine(d,time(14,11) if early and phases_verified
                                 else time(14,21) if early else time(16,11),zone)
    # Engineering wait envelopes; neither guarantees provider volume is final.
    finalization=datetime.combine(d,time(14,52) if early else time(16,42),zone)
    opening_latest=time(10,0) if d>=date(2025,6,23) else time(10,10)
    return {'date':d.isoformat(),'open':datetime.combine(d,time(9,59),zone),
            'open_latest':datetime.combine(d,opening_latest,zone),
            'normal_end':normal_end,'auction_end':auction_end,'close':finalization,
            'early_close':early,'phase_times_verified':phases_verified,
            'calendar_version':CALENDAR_VERSION}

def boundary_issue(s,op,cl,definition):
    """Day envelopes, not a claim of each stock's actual first/auction trade."""
    if not s.get('phase_times_verified',True):return 'asx_early_phases_not_reviewed'
    # A reviewed market override has exact supplied opening bounds.
    if not s['open']<=op<=s.get('open_latest',s['open']):return 'calendar_boundary_mismatch'
    if definition!='asx_eod' or not s['auction_end']<=cl<=s['close']:
        return 'calendar_boundary_mismatch'
    return None

def latest_completed(now):
    local=now.astimezone(ZoneInfo('Australia/Sydney'));d=local.date()
    for _ in range(40):
        s=session(d)
        if s and s['close']<=now:return s
        d-=timedelta(days=1)
    raise ValueError('no completed ASX session in reviewed window')
