"""Deterministic series selection and conflict alarms, separate from scoring.

Tolerance is an engineering alarm, not independent price verification. Tick
rules, provider independence and actual permissions still require acceptance.
"""
PRICE_FIELDS = ('open_micros', 'high_micros', 'low_micros', 'close_micros')
UNVERIFIED_SOURCES = frozenset({'eodhd-asx-candidate'})
from .adjustments import peer_adjustment_issue


def differences(left, right):
    fields = [key for key in PRICE_FIELDS
              if abs(left[key]-right[key])*2000 > min(left[key],right[key])]
    if abs(left['volume']-right['volume'])*100 > max(min(left['volume'],right['volume']),1):
        fields.append('volume')
    return fields


def inspect_history(item, window=252):
    groups = {}
    for bar in item['bars']:
        if bar['basis'] == 'split':
            groups.setdefault((bar['source'],bar['close_definition']),[]).append(bar)
    if not groups:
        return {'bars':[], 'state':'missing_split_basis', 'conflicts':[], 'comparison_skips':[]}
    histories = {key:sorted(rows,key=lambda b:b['session_date']) for key,rows in groups.items()}
    # Ties are deterministic; this does not select a source by assumed accuracy.
    key = max(histories, key=lambda k:(histories[k][-1]['session_date'],
              len(histories[k]),histories[k][-1]['available_at'],k))
    selected = histories[key]
    scope = selected[-window:] if window is not None else selected
    dates = {b['session_date']:b for b in scope}
    conflicts = []
    skips = []
    for peer_key, peers in histories.items():
        if peer_key[0] == key[0] or peer_key[1] != key[1]:
            continue
        for peer in peers:
            reference = dates.get(peer['session_date'])
            if (reference is None or peer['quality'] != 'valid' or reference['quality'] != 'valid'
                    or peer['source'] in UNVERIFIED_SOURCES or reference['source'] in UNVERIFIED_SOURCES):
                continue
            issue = peer_adjustment_issue(reference,peer)
            if issue:
                skips.append({'date':peer['session_date'],'source':key[0],
                              'peer_source':peer_key[0],'reason':issue})
                continue
            fields = differences(reference,peer)
            if fields:
                conflicts.append({'date':peer['session_date'],'source':key[0],
                                  'peer_source':peer_key[0],'close_definition':key[1],'fields':fields})
    conflicts.sort(key=lambda c:(c['date'],c['peer_source']))
    skips.sort(key=lambda c:(c['date'],c['peer_source']))
    state=selected[-1]['quality']
    if state=='valid' and selected[-1]['source'] in UNVERIFIED_SOURCES:state='source_unverified'
    return {'bars':selected, 'state':'conflict' if conflicts else state,
            'conflicts':conflicts, 'comparison_skips':skips}


def select_history(item, window=252):
    result = inspect_history(item,window)
    return result['bars'],result['state'],result['conflicts']
