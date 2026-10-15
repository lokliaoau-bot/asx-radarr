"""Canonical backend enums and plain-language labels; frontend is generated."""

STATE_LABELS = {'source_unverified': '价格还要找另一来源核对', 'valid': '基本检查通过', 'conflict': '两份数据对不上', 'stale': '行情还没更新', 'provisional': '当天还没收完盘', 'incomplete': '当天的数据没收齐', 'invalid_history': '有些历史数据不能用', 'unverified_session': '交易时间还要核实', 'no_trades': '当天没有成交', 'missing_split_basis': '缺少处理拆股后的价格', 'unverified_adjustment': '拆股前后的数据还没对齐'}

REASON_LABELS = {'behind_expected_market_session': '应该有更新一天的收盘价，现在还没拿到。', 'missing_expected_history_sessions': '漏了应有的交易日，不能跳过这些天继续算。', 'calendar_year_not_reviewed': '这一年的开休市安排还没查过。', 'calendar_history_not_reviewed': '所需的更早开休市安排还没查过。', 'calendar_boundary_mismatch': '这份当天价格记录的起止时间与市场安排对不上。', 'calendar_session_not_finalized': '还要等当天的收盘时最后一轮集中成交和数据整理结束。', 'bar_on_closed_session': '这一天市场休市，却出现了当天的行情。', 'asx_early_phases_not_reviewed': '这次澳股提前结束交易时，最后一轮成交的时间还没查清，先不算分。', 'adjustment_metadata_missing': '没说明价格和股数按哪次拆股调整，需要补齐说明。', 'adjustment_version_mismatch': '历史里混了不同拆股版本，要重新读取完整历史。', 'adjustment_metadata_undeclared': '这份文件没说明价格和股数的拆股版本；只能按导入文件做初步观察，还要核对。', 'peer_adjustment_not_comparable': '另一来源的拆股版本不同或没说明，价格不能直接比。它没有被当成核价通过，也没有被当成价格冲突。', 'behind_expected_asx_session': '还没拿到澳股最近一天的完整收盘价。', 'asx_calendar_not_reviewed': '这一年的澳股开休市安排还没查过。', 'history_below_252': '还不够252个完整交易日，暂不排进标准列表。', 'benchmark_missing_or_invalid': '缺少同一天的市场对照价格，没法比较强弱。', 'fundamentals_not_integrated': '公司的财务资料还没接齐。', 'valuation_not_integrated': '股价相对公司价值贵不贵，还没查清。', 'catalyst_not_verified': '可能影响股价的消息还没核实。', 'borrow_and_cost_unknown': '券商能否借到股票、要花多少钱，还没查清。', 'invalid_session_inside_history': '过去有些当天价格记录没通过检查，不能跳过后继续算。', 'zero_reference_volume': '用来比较的那段时间没有成交量，没法算。', 'older_than_96_hours_calendar_recheck_required': '需要按市场实际开休市日期重查这份旧行情。', 'stale_against_dataset_market': '这只股票的更新日期比其他已读取股票旧。', 'short_halts_inside_history': '过去一年里有过短暂停牌（几天没有成交，通常是发公告或配股前的例行停牌）；停牌期间价格不变，照常计算。'}

ASX_WARNING_LABELS = {'asx_dividends_rights_halts_not_verified': '还没核实分红、配股和停牌。价格下调可能另有原因，先查消息再判断。', 'asx_low_trading_value': '最近成交金额偏少，价格容易受少数交易影响。', 'asx_many_unchanged_closes': '最近有多天收盘价不变，先确认是不是成交太少。', 'asx_large_price_move': '最近有一天的价格变化达到20%，先查拆股、分红、配股或公司消息。'}

MARKET_CURRENCY = {'HK': 'HKD', 'US': 'USD', 'AU': 'AUD'}
MARKET_TIMEZONE = {'HK': 'Asia/Hong_Kong', 'US': 'America/New_York', 'AU': 'Australia/Sydney'}
# Legacy dictionaries above are retained for existing raw-record parsing/tests.
# Only this scope is exposed by the running app and generated frontend.
ACTIVE_MARKETS = ('AU',)


def active_market(value='AU'):
    if value not in ('AU', 'ALL'):
        raise ValueError('本版只研究澳股（AU/ASX）。')
    return 'AU'
# The six focus sectors come first; the rest are the GICS groups used by the
# ASX market radar universe this copy now reads (asxquant.shortradar maps them).
SECTORS = frozenset({'餐饮', '医药', '半导体', '电动车', '保险', 'REIT', '基准', '其他',
                     '原材料/矿业', '金融/银行', '非必需消费', '必需消费', '能源', '工业',
                     '信息科技', '通信服务', '公用事业'})
ADJUSTMENT_STATES = ('synthetic_demo', 'declared_consistent', 'undeclared', 'invalid')
CALENDAR_STATES = ('synthetic_calendar', 'reviewed_schedule', 'unreviewed')
VERIFICATION_STATES = ('synthetic_demo', 'single_source_or_user_declared')
EXECUTION_STATES = ('unknown',)
CLOSE_DEFINITIONS = ('regular', 'regular_minute_aggregate', 'all_sessions', 'asx_eod')
API_PARAMS = {
    '/v1/status': frozenset(),
    '/v1/calendars': frozenset({'asof', 'market'}),
    '/v1/coverage': frozenset({'mode', 'asof', 'horizon'}),
    '/v1/instruments/{id}': frozenset({'mode', 'asof', 'horizon'}),
    '/v1/radar': frozenset({'mode', 'asof', 'horizon', 'market', 'sector', 'query'}),
}


def frontend_contract():
    from .engine import WEIGHTS
    return {'version': 'cross-layer-v1', 'states': STATE_LABELS, 'reasons': REASON_LABELS,
            'asxWarnings': ASX_WARNING_LABELS,
            'currencies': {m: MARKET_CURRENCY[m] for m in ACTIVE_MARKETS},
            'timezones': {m: MARKET_TIMEZONE[m] for m in ACTIVE_MARKETS}, 'sectors': sorted(SECTORS),
            'weights': WEIGHTS, 'adjustmentStates': ADJUSTMENT_STATES,
            'calendarStates': CALENDAR_STATES, 'verificationStates': VERIFICATION_STATES,
            'executionStates': EXECUTION_STATES, 'closeDefinitions': CLOSE_DEFINITIONS}
