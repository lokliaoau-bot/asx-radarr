"""ASX data gates and descriptive checks, separate from score/forecast math."""
from fractions import Fraction
from .adjustments import adjustment_issue


def describe(bars, usable):
    result = {'review_version':'asx-review-v1', 'trading_value_proxy_20d_aud':None,
              'unchanged_close_days_20':None, 'largest_close_move_20_pct':None,
              'event_context_verified':False,
              'warnings':['asx_dividends_rights_halts_not_verified'],
              'thresholds':{'low_value_proxy_aud':200000, 'unchanged_days':5,
                            'large_move_pct':20},
              'thresholds_validated_for_prediction':False}
    if not usable or len(bars) < 21:
        return result
    window = bars[-21:]
    moves = [Fraction(abs(b['close_micros']-a['close_micros']),a['close_micros'])
             for a,b in zip(window,window[1:])]
    value = Fraction(sum(b['close_micros']*b['volume'] for b in window[1:]),20000000)
    flat = sum(b['close_micros']==a['close_micros'] for a,b in zip(window,window[1:]))
    largest = max(moves)
    result.update(trading_value_proxy_20d_aud=round(float(value),2),
                  unchanged_close_days_20=flat, largest_close_move_20_pct=round(float(largest*100),3))
    if value < 200000:result['warnings'].append('asx_low_trading_value')
    if flat >= 5:result['warnings'].append('asx_many_unchanged_closes')
    if largest >= Fraction(1,5):result['warnings'].append('asx_large_price_move')
    return result
