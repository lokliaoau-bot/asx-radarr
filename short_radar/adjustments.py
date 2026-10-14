"""Declared adjustment consistency, separate from independent verification."""


def adjustment_issue(bars):
    """One documented price/share revision in the used symbol window."""
    if any(not b.get('adjustment_id') or b.get('volume_basis') != 'split' for b in bars):
        return 'adjustment_metadata_missing'
    if len({b['adjustment_id'] for b in bars}) > 1:
        return 'adjustment_version_mismatch'
    return None


def requires_adjustment_check(market, bars):
    # AU keeps its mandatory metadata gate. Older HK/US files remain usable
    # with an explicit unknown warning, but declarations cannot contradict it.
    return market == 'AU' or any(b.get('adjustment_id') or b.get('volume_basis') == 'raw' for b in bars)


def peer_adjustment_issue(left, right):
    """Different or missing declared anchors are not comparable price levels.

    Equal IDs are declarations only. They do not certify the supplier, split
    history or independence of the two feeds.
    """
    if (left.get('market') == 'AU' or right.get('market') == 'AU'
            or left.get('adjustment_id') or right.get('adjustment_id')
            or left.get('volume_basis') == 'raw' or right.get('volume_basis') == 'raw'):
        if (not left.get('adjustment_id') or not right.get('adjustment_id')
                or left.get('volume_basis') != 'split' or right.get('volume_basis') != 'split'
                or left['adjustment_id'] != right['adjustment_id']):
            return 'peer_adjustment_not_comparable'
    return None
