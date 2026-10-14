"""AU-only, synthetic-first research evaluation. No broker/data access or tuning.

Frozen production math is called, never trained here. Labels use raw-price cash
and quantity ledgers; unknown outcomes remain explicit unresolved observations.
"""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from math import ceil, erfc, isfinite, sqrt
from statistics import mean, median
from pathlib import Path
import hashlib
import json
import random
import subprocess

from .analysis import indicators
from .engine import trend, distribution, score, WEIGHTS
from .models import instant

VERSION = 'stage01-v1'
HORIZONS = (5, 10, 20)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def visible_versions(rows, cutoff):
    """Filter BOTH clocks first, then select the latest local revision per ID/day."""
    cutoff = instant(cutoff)
    selected = {}
    for row in rows:
        if instant(row['available_at']) > cutoff or instant(row['observed_at']) > cutoff:
            continue
        key = (row['instrument_id'], row['session_date'])
        previous = selected.get(key)
        if previous is None or (row['revision'], instant(row['observed_at'])) > (
                previous['revision'], instant(previous['observed_at'])):
            selected[key] = row
    return sorted(selected.values(), key=lambda b: (b['instrument_id'], b['session_date']))


def visible_members(members, cutoff, session_date):
    """Versioned membership, including securities which later leave the pool."""
    cutoff = instant(cutoff)
    latest = {}
    for row in members:
        if instant(row['available_at']) > cutoff or instant(row['observed_at']) > cutoff:
            continue
        iid = row['instrument_id']
        if iid not in latest or row['revision'] > latest[iid]['revision']:
            latest[iid] = row
    return {iid for iid, row in latest.items() if row['start'] <= session_date and
            (row['end'] is None or session_date <= row['end'])}


def split_history_asof(rows, actions, cutoff, instrument_id):
    """PIT split-only prices AND shares; future-effective splits are not applied."""
    from fractions import Fraction
    cutoff_dt = instant(cutoff)
    day = cutoff_dt.astimezone(__import__('zoneinfo').ZoneInfo('Australia/Sydney')).date().isoformat()
    latest = {}
    for a in actions:
        if a['instrument_id'] != instrument_id or instant(a['available_at']) > cutoff_dt or instant(a['observed_at']) > cutoff_dt:
            continue
        key = a['event_id']
        if key not in latest or a['revision'] > latest[key]['revision']:
            latest[key] = a
    effective = [a for a in latest.values() if a['kind'] == 'split' and a['session_date'] <= day]
    out = []
    for b in rows:
        factor = Fraction(1)
        for a in effective:
            if b['session_date'] < a['session_date']:
                factor *= Fraction(a['numerator'], a['denominator'])
        # Research math accepts fractional split-aligned shares. Original bars
        # are immutable and raw-price filtering happens before this function.
        out.append({**b, **{k: float(Fraction(b[k]) / factor) for k in
                           ('open_micros', 'high_micros', 'low_micros', 'close_micros')},
                    'volume': float(Fraction(b['volume']) * factor)})
    return out


def technical_signal(stock, benchmark, horizon=10):
    """Adapter for the unchanged production technical-v1 (252-bar warm-up).

    The distribution window deliberately matches analysis.radar, including its
    present exclusion of today's volume from the up/down-20 aggregation.
    """
    if horizon not in HORIZONS:
        raise ValueError('unsupported evaluation horizon')
    stock, benchmark = stock[-252:], benchmark[-252:]
    if len(stock) < 252 or len(benchmark) < 21:
        return None
    if any(b.get('quality', 'valid') != 'valid' for b in stock + benchmark[-21:]):
        return None
    if [b['session_date'] for b in stock[-21:]] != [b['session_date'] for b in benchmark[-21:]]:
        return None
    metric = indicators(stock)
    c = [b['close_micros'] / 1e6 for b in stock]
    v = [b['volume'] for b in stock]
    bc = [b['close_micros'] / 1e6 for b in benchmark]
    relative = c[-1] / c[-21] - 1 - (bc[-1] / bc[-21] - 1)
    t = trend(c[-1], metric['ma20'], metric['ma60'], relative, metric['ma20_change5'])
    down = sum(v[i] for i in range(len(c)-21, len(c)-1) if c[i] < c[i-1])
    up = sum(v[i] for i in range(len(c)-21, len(c)-1) if c[i] > c[i-1])
    reference = sum(v[-21:-1]) / 20
    if reference <= 0:
        return None
    d = distribution(down, up, v[-1], reference, c[-1] < c[-2])
    result = score([t, d, None, None, None], horizon)
    # Keep the exact production rounding and explicitly partial contribution.
    return {'value': result['research_score'], 'factors': [t, d, None, None, None],
            'coverage': result['coverage'], 'research_score': None,
            'momentum5': -(c[-1]/c[-6]-1), 'momentum20': -(c[-1]/c[-21]-1),
            'liquidity_proxy': mean(b['close_micros']/1e6*b['volume'] for b in stock[-20:])}


def signal_asof(all_rows, actions, members, planned_sessions, cutoff, instrument_id,
                benchmark_id, horizon=10, minimum_raw_price=None):
    """Full-snapshot entrypoint: all future data are filtered internally.

    planned_sessions is an explicit versioned input supplied by the snapshot,
    not a weekday guess. This function cannot authorize a real data snapshot.
    """
    from zoneinfo import ZoneInfo
    day = instant(cutoff).astimezone(ZoneInfo('Australia/Sydney')).date().isoformat()
    if instrument_id not in visible_members(members, cutoff, day):
        return None
    expected = [d for d in planned_sessions if d <= day][-252:]
    visible = visible_versions(all_rows, cutoff)
    visible = [r for r in visible if r['session_date'] <= day]
    if any(r.get('market') != 'AU' for r in visible):
        raise ValueError('this evaluation is AU only')
    stock = [r for r in visible if r['instrument_id'] == instrument_id][-252:]
    bench = [r for r in visible if r['instrument_id'] == benchmark_id][-252:]
    if len(expected) < 252 or [r['session_date'] for r in stock] != expected or [r['session_date'] for r in bench] != expected:
        return None
    if minimum_raw_price is not None and stock[-1]['close_micros']/1e6 < minimum_raw_price:
        return None
    stock = split_history_asof(stock, actions, cutoff, instrument_id)
    bench = split_history_asof(bench, actions, cutoff, benchmark_id)
    return technical_signal(stock, bench, horizon)


@dataclass(frozen=True)
class Cost:
    annual_debit: float = 0.0
    side_bps: float = 0.0
    hedge_annual_financing: float = 0.0

    def __post_init__(self):
        for x in asdict(self).values():
            if isinstance(x, bool) or not isfinite(x) or x < 0:
                raise ValueError('invalid assumed cost')


COST_GRID = tuple(Cost(a, b) for a in (0., .05, .10, .20) for b in (5., 20., 50.))


def label(raw_bars, signal_index, horizon, actions=(), cost=Cost(), actions_complete=True):
    """Fixed-entry notional cash ledger; null rather than dropping bad outcomes.

    actions contain index/kind plus numerator,denominator OR cash_micros.
    Cash payments on entry's ex-date are excluded (short begins at ex-open).
    """
    if (isinstance(horizon,bool) or not isinstance(horizon,int) or horizon not in HORIZONS
            or isinstance(signal_index, bool) or not isinstance(signal_index, int) or signal_index < 0):
        raise ValueError('invalid label window')
    entry_index, exit_index = signal_index + 1, signal_index + horizon
    unresolved = {'status': 'unresolved', 'total_return': None, 'short_net': None,
                  'mae_short_loss': None, 'reason': None}
    if not actions_complete:
        return {**unresolved, 'reason': 'corporate_action_coverage_unknown'}
    if exit_index >= len(raw_bars):
        return {**unresolved, 'reason': 'missing_exit_or_delisting_settlement'}
    held = raw_bars[entry_index:exit_index+1]
    if any(b is None or b.get('quality', 'valid') != 'valid' for b in held):
        return {**unresolved, 'reason': 'missing_or_nontrading_session'}
    from fractions import Fraction
    try:
        dates = [date.fromisoformat(b['session_date']) for b in held]
        if any(b <= a for a, b in zip(dates, dates[1:])):
            raise ValueError('non-increasing dates')
        entry = Fraction(held[0]['open_micros'])
        if entry <= 0 or any(b['high_micros'] < max(b['open_micros'], b['close_micros']) or
                             b['low_micros'] > min(b['open_micros'], b['close_micros']) or
                             b['low_micros'] <= 0 for b in held):
            raise ValueError('invalid raw OHLC')
        quantity, cash = Fraction(1), Fraction(0)
        worst = 0.
        entry_day = date.fromisoformat(held[0]['session_date'])
        for idx, bar in enumerate(held, start=entry_index):
            # Splits happen before that day's cash entitlement/price. Entry
            # quantity is already in the entry day's post-action units.
            events = [a for a in actions if a['index'] == idx]
            if any(a['kind'] not in {'split', 'dividend'} for a in events):
                return {**unresolved, 'reason': 'unsupported_corporate_action'}
            if idx > entry_index:
                for a in events:
                    if a['kind'] == 'split':
                        ratio = Fraction(a['numerator'], a['denominator'])
                        if ratio <= 0:
                            raise ValueError('invalid split')
                        quantity *= ratio
                if any(a['cash_micros'] < 0 for a in events if a['kind'] == 'dividend'):
                    raise ValueError('negative dividend')
                cash += sum((quantity*Fraction(a['cash_micros']) for a in events
                             if a['kind'] == 'dividend'), Fraction(0))
            days = (date.fromisoformat(bar['session_date'])-entry_day).days
            if days < 0:
                raise ValueError('backwards dates')
            funding = cost.annual_debit*days/365
            worst = max(worst, float((quantity*bar['high_micros']+cash)/entry-1)+funding+2*cost.side_bps/10000)
        total = float((quantity*held[-1]['close_micros']+cash)/entry-1)
        days = (date.fromisoformat(held[-1]['session_date'])-entry_day).days
        net = -total-cost.annual_debit*days/365-2*cost.side_bps/10000
        return {'status': 'resolved', 'total_return': total, 'short_net': net,
                'mae_short_loss': worst, 'calendar_days': days, 'reason': None}
    except (ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError):
        return {**unresolved, 'reason': 'invalid_price_action_or_date'}


def hedged_excess(stock_label, benchmark_label, cost):
    if stock_label['status'] != 'resolved' or benchmark_label['status'] != 'resolved':
        return None
    return (stock_label['short_net']+benchmark_label['total_return']-
            cost.hedge_annual_financing*benchmark_label['calendar_days']/365-2*cost.side_bps/10000)


def ranks(values):
    if any(isinstance(v, bool) or not isfinite(v) for v in values):
        raise ValueError('ranks require finite observations')
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.]*len(values)
    p = 0
    while p < len(order):
        end = p+1
        while end < len(order) and values[order[end]] == values[order[p]]:
            end += 1
        for i in order[p:end]:
            out[i] = (p+1+end)/2
        p = end
    return out


def rank_ic(scores, outcomes):
    if len(scores) != len(outcomes) or len(scores) < 3:
        return None
    a, b = ranks(scores), ranks(outcomes)
    ma, mb = mean(a), mean(b)
    va, vb = sum((x-ma)**2 for x in a), sum((x-mb)**2 for x in b)
    return sum((x-ma)*(y-mb) for x, y in zip(a, b))/sqrt(va*vb) if va*vb else None


def ic_inference(ics, horizon, spacing=1):
    """Date-level Newey-West; no stock-day independence assumption."""
    if spacing < 1 or horizon not in HORIZONS:
        raise ValueError('invalid inference window')
    # Missing dates are not compressed into adjacent observations for HAC.
    if any(v is None for v in ics):
        return {'n': len(ics), 'mean': None, 'se': None, 'p': None,
                'reason': 'missing_ic_dates', 'lag': ceil(horizon/spacing)-1}
    if not ics:
        return {'n': 0, 'mean': None, 'se': None, 'p': None, 'lag': 0}
    if any(not isfinite(v) for v in ics):
        raise ValueError('nonfinite IC')
    n, avg = len(ics), mean(ics)
    lag = min(ceil(horizon/spacing)-1, n-1)
    residual = [v-avg for v in ics]
    lrv = sum(x*x for x in residual)/n
    for k in range(1, lag+1):
        lrv += 2*(1-k/(lag+1))*sum(residual[i]*residual[i-k] for i in range(k, n))/n
    se = sqrt(max(0, lrv)/(n-1)) if n > 1 else None
    p = erfc(abs(avg/se)/sqrt(2)) if n >= 30 and se else None
    return {'n': n, 'mean': avg, 'se': se, 'p': p, 'lag': lag,
            'method': 'date_level_Newey_West_normal_approximation' if lag else 'nonoverlap_date_mean_normal_approximation'}


def wilson(successes, n, z=2.5758293035489004):
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError('invalid binomial count')
    p = successes/n
    center = (p+z*z/(2*n))/(1+z*z/n)
    radius = z*sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [center-radius, center+radius]


def summarize(records):
    """Retain unresolved records; resolved-only estimates expose denominator."""
    usable = [r for r in records if r['status'] == 'resolved']
    values = [r['short_net'] for r in usable]
    tail = sorted(values)[:max(1, ceil(len(values)*.05))] if values else []
    return {'expected': len(records), 'resolved': len(usable),
            'coverage': len(usable)/len(records) if records else None,
            'hit_rate': mean(v > 0 for v in values) if values else None,
            'base_decline_rate': mean(r['total_return'] < 0 for r in usable) if usable else None,
            'mean_short_net': mean(values) if values else None,
            'median_short_net': median(values) if values else None,
            'worst_5pct_mean': mean(tail) if tail else None,
            'largest_mae': max((r['mae_short_loss'] for r in usable), default=None),
            'surges': [r for r in usable if r['total_return'] >= .20],
            'unresolved': [r for r in records if r['status'] != 'resolved']}


def turnover(previous, current):
    # Target weights; not realized turnover of overlapping live positions.
    if not previous or not current:
        return None
    a, b = set(previous), set(current)
    return .5*sum(abs((1/len(a) if x in a else 0)-(1/len(b) if x in b else 0)) for x in a|b)


def walk_forward(n_sessions, train=252, validation=63, test=63, final=126,
                 warmup=252, purge=20, embargo=20):
    """Indices of signal dates, including enough future sessions for labels."""
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in
           (n_sessions, train, validation, test, final, warmup, purge, embargo)):
        raise ValueError('positive integer split configuration required')
    final_start = n_sessions-final
    folds, start = [], warmup
    while start+train+validation+test+purge <= final_start:
        v, t, end = start+train, start+train+validation, start+train+validation+test
        # End indices exclusive. Purge 20 from every labeled segment's end.
        folds.append({'train': [start, v-purge], 'validation': [v, t-purge],
                      'test': [t, end-purge], 'embargo': [end, end+embargo]})
        start += test
    if not folds:
        return {'status': 'insufficient_history', 'folds': [], 'final': [max(0, final_start), n_sessions-purge]}
    # Exclude earlier fold-test embargo from later train indices, preserving
    # chronology; a later fold may use earlier resolved tests after the embargo.
    for i, fold in enumerate(folds):
        excluded = [f['embargo'] for f in folds[:i]]
        fold['train_indices'] = [d for d in range(*fold['train']) if
                                 not any(a <= d < b for a, b in excluded)]
    return {'status': 'planned_only', 'folds': folds, 'final': [final_start, n_sessions-purge]}


def claim_final_holdout(path, manifest):
    """Project-wide once-only claim. Even failed consumption remains claimed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as f:
        f.write(canonical({'claimed_at': datetime.now(timezone.utc).isoformat(), **manifest}))


def run_identity(root, config, data_hash, seeds):
    root = Path(root)
    def git(*args):
        try:
            p = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
        except OSError:
            return None
        return p.stdout.strip() if p.returncode == 0 else None
    files = sorted([*root.glob('short_radar/*.py'), *root.glob('scripts/*.py'), root/'docs/EVAL-SPEC.md'])
    tree = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()}
    status=git('status','--porcelain')
    return {'evaluation_version': VERSION, 'config': config, 'config_sha256': digest(config),
            'data_snapshot_sha256': data_hash, 'git_commit': git('rev-parse', 'HEAD'),
            'dirty': bool(status) if status is not None else None, 'source_tree_sha256': digest(tree),
            'source_files': tree, 'seeds': seeds}


def append_experiment(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf8') as f:
        f.write(canonical({'recorded_at': datetime.now(timezone.utc).isoformat(), **record})+'\n')
