/* Offline UI regressions. jsdom is a development dependency, never a runtime dependency. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');

const script = fs.readFileSync(path.join(__dirname, '../web/stock.js'), 'utf8');
function fixture(ticker = 'BHP.AX') {
  return {ticker, name: '合成边界测试股票', market: 'AU', currency: 'AUD', mode: 'local',
    as_of: '2026-10-15', valid_until: '2099-01-01T00:00:00Z',
    source: {source: 'synthetic_test', fetched_at: '2026-10-15T08:00:00Z', sha256: 'synthetic'},
    quote: {}, quality: {state: 'single_source', issues: [], excluded_unfinished_bars: 0},
    scores: {method: 'existing-observation-conditions-v1', as_of: '2026-10-15', state: 'ready',
      max: 4, blockers: [], meaning: '满足项数，不是概率',
      long: {value: 1, checks: ['close_ma20','ma20_ma50','relative_stw','liquidity'].map((key,i)=>({key,label:'多头条件'+i,met:i===3}))},
      short: {value: 4, checks: ['close_ma20','ma20_ma50','relative_stw','liquidity'].map((key,i)=>({key,label:'空头条件'+i,met:true}))}},
    facts: {close: 100, change_1d: -.01, return_20d: -.05, relative_stw_20d: -.04,
      ma20: 110, ma50: 120, rsi14: 30, atr14: 2, adv20_aud: 10_000_000,
      prior20_low: 98, prior20_high: 125},
    chart: [{date: '2026-10-14', open: 103, high: 104, low: 100, close: 101, volume: 500,
      ma20: 110, ma50: 120, rsi14: 32},
    {date: '2026-10-15', open: 102, high: 103, low: 99, close: 100, volume: 600,
      ma20: 109, ma50: 119, rsi14: 30}],
    assessment: {bias: 'short_watch', headline: '偏空，可关注做空条件',
      long: '暂不符合买入观察条件', short: '借券及费用未确认',
      reasons: ['合成测试：价格和均线偏弱'], next_step: '下一交易时段核对报价', method: 'synthetic_test'}};
}
const answer = data => new Response(JSON.stringify(data), {status: 200});
function setup(t, fetcher, snapshot = false) {
  const dom = new JSDOM('<section id="stockLookup"></section>', {
    url: 'http://localhost/', runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window;
  let expiryCheck;
  w.setInterval = fn => { expiryCheck = fn; };
  w.AbortController = AbortController;
  w.fetch = fetcher;
  if (snapshot) w.__REPORT__ = {};
  w.eval(script);
  const get = id => w.document.getElementById(id);
  return {w, get, tick: () => expiryCheck(), text: () => get('stockResult').textContent,
    submit(code) {
      get('stockCode').value = code;
      get('stockForm').dispatchEvent(new w.Event('submit', {cancelable: true}));
    },
    async done() {
      for (let i = 0; i < 100; i++) {
        await new Promise(setImmediate);
        if (!get('stockSubmit').disabled) return;
      }
      throw Error('UI did not settle');
    }};
}

test('valid source displays facts and technical curves without a market report', async t => {
  const ui = setup(t, async () => answer(fixture()));
  ui.submit('BHP'); await ui.done();
  assert.match(ui.text(), /偏空，可关注/);
  assert.equal(ui.w.document.querySelectorAll('.stock-chart svg path').length, 3);
  assert.ok(ui.w.document.querySelectorAll('.stock-metrics b').length > 0);
  assert.equal(ui.w.document.querySelectorAll('.stock-score-value').length, 2);
  assert.match(ui.w.document.querySelector('[data-side="long"]').textContent, /1\s*\/\s*4/);
  assert.match(ui.w.document.querySelector('[data-side="short"]').textContent, /4\s*\/\s*4/);
});

for (const state of ['unavailable', 'constructor', 'future_unknown_state']) {
  test(`${state}: status overrides contradictory facts and direction`, async t => {
    const data = fixture(); data.quality.state = state;
    data.quality.issues = ['合成状态冲突：检查未通过'];
    const ui = setup(t, async () => answer(data));
    ui.submit('BHP'); await ui.done();
    assert.match(ui.text(), /日线检查未通过，暂停判断/);
    assert.doesNotMatch(ui.text(), /偏空，可关注|合成测试：价格和均线偏弱/);
    assert.equal(ui.w.document.querySelectorAll('.stock-metrics b').length, 0);
    assert.equal(ui.w.document.querySelectorAll('.stock-chart svg path').length, 0);
    assert.equal(ui.w.document.querySelectorAll('.stock-score-value').length, 0);
    assert.ok(ui.w.document.querySelectorAll('.stock-chart svg rect').length > 0);
  });
}

test('expiry hides computed indicators, preserves historical candles and stays paused after range changes', async t => {
  const data = fixture(); data.valid_until = '2000-01-01T00:00:00Z';
  const ui = setup(t, async () => answer(data));
  ui.submit('BHP'); await ui.done();
  ui.w.document.querySelector('[data-range="252"]').click();
  assert.match(ui.text(), /数据过期，暂停判断/);
  assert.doesNotMatch(ui.text(), /偏空，可关注/);
  assert.equal(ui.w.document.querySelectorAll('.stock-chart svg path').length, 0);
  assert.equal(ui.w.document.querySelectorAll('.stock-metrics b').length, 0);
  assert.equal(ui.w.document.querySelectorAll('.stock-score-value').length, 0);
  assert.ok(ui.w.document.querySelector('.stock-chart svg'));
});

test('timer cannot resurrect an old ticker during a new pending query', async t => {
  let n = 0, release;
  const ui = setup(t, () => ++n === 1 ? Promise.resolve(answer(fixture())) :
    new Promise(resolve => { release = () => resolve(answer(fixture('CSL.AX'))); }));
  ui.submit('BHP'); await ui.done();
  ui.w.Date.now = () => Date.parse('2100-01-01T00:00:00Z');
  ui.submit('CSL'); ui.tick();
  assert.equal(ui.get('stockResult').innerHTML, '');
  assert.equal(ui.get('stockCode').value, 'CSL');
  release(); await ui.done();
  assert.match(ui.text(), /CSL.AX/);
  assert.doesNotMatch(ui.text(), /BHP.AX/);
});

test('illegal input clears old output and prevents a late response from returning', async t => {
  let release;
  const ui = setup(t, () => new Promise(resolve => { release = () => resolve(answer(fixture())); }));
  ui.submit('BHP'); ui.submit('constructor'); release(); await ui.done();
  assert.equal(ui.get('stockResult').innerHTML, '');
  assert.match(ui.get('stockMessage').textContent, /请输入澳股代码/);
});

test('a failed new query clears the previous conclusion', async t => {
  let n = 0;
  const ui = setup(t, async () => ++n === 1 ? answer(fixture()) :
    new Response(JSON.stringify({error: '合成网络失败'}), {status: 502}));
  ui.submit('BHP'); await ui.done();
  ui.submit('CSL'); await ui.done();
  assert.equal(ui.get('stockResult').innerHTML, '');
  assert.match(ui.get('stockMessage').textContent, /合成网络失败/);
});

for (const offline of [false, true]) {
  test(`${offline ? 'offline API' : 'static 404'} uses stable daily snapshot URL`, async t => {
    const requests = [], data = fixture(); data.mode = 'daily_snapshot';
    const ui = setup(t, async url => {
      requests.push(url);
      if (url.startsWith('api/')) {
        if (offline) throw new TypeError('offline');
        return new Response('', {status: 404});
      }
      return answer(data);
    });
    ui.submit('BHP'); await ui.done();
    assert.equal(requests[1], 'stocks/ticker-BHP.AX.json');
    assert.match(ui.text(), /每日快照/);
  });
}

test('an exported snapshot uses embedded data without any fetch', async t => {
  const ui = setup(t, () => { throw Error('must not fetch'); }, true);
  ui.w.__STOCKS__ = {'BHP.AX': fixture()};
  ui.submit('BHP'); await ui.done();
  assert.ok(ui.w.document.querySelector('svg'));
});

test('provider HTML is escaped', async t => {
  const data = fixture(); data.name = '<img src=x onerror=alert(1)>';
  data.assessment.reasons = ['<svg onload=alert(1)>'];
  data.scores.long.checks[0].label = '<img src=x onerror=alert(1)>';
  const ui = setup(t, async () => answer(data));
  ui.submit('BHP'); await ui.done();
  assert.equal(ui.w.document.querySelectorAll('img,svg[onload]').length, 0);
  assert.match(ui.text(), /<img src=x/);
});

test('old daily snapshots do not acquire invented scores in the browser', async t => {
  const data = fixture(); delete data.scores;
  const ui = setup(t, async () => answer(data));
  ui.submit('BHP'); await ui.done();
  assert.match(ui.text(), /旧快照尚未包含条件分/);
  assert.equal(ui.w.document.querySelectorAll('.stock-score-value').length, 0);
});

test('scores and directional conclusion must agree before either is presented', async t => {
  const data = fixture();
  data.assessment.bias = 'long_watch';
  data.assessment.headline = '偏多，可关注买入条件';
  const ui = setup(t, async () => answer(data));
  ui.submit('BHP'); await ui.done();
  assert.doesNotMatch(ui.w.document.querySelector('.stock-verdict').textContent, /偏多，可关注/);
  assert.equal(ui.w.document.querySelectorAll('.stock-score-value').length, 0);
  assert.match(ui.w.document.querySelector('.stock-verdict').textContent, /暂停/);
});

for (const defect of ['zero', 'bad_count', 'bad_date', 'unknown_state', 'no_stw', 'action']) {
  test(`${defect}: score display preserves zero and rejects inconsistent or blocked payloads`, async t => {
    const data = fixture();
    if (defect === 'zero') {
      for (const side of ['long','short']) {
        data.scores[side].value = 0;
        for (const check of data.scores[side].checks) check.met = false;
      }
      data.assessment.bias = 'observe';
    }
    if (defect === 'bad_count') data.scores.long.value = 4;
    if (defect === 'bad_date') data.scores.as_of = '2026-10-14';
    if (defect === 'unknown_state') data.scores.state = 'constructor';
    if (defect === 'no_stw') data.facts.relative_stw_20d = null;
    if (defect === 'action') data.recent_events = [{kind:'dividends'}];
    const ui = setup(t, async () => answer(data));
    ui.submit('BHP'); await ui.done();
    assert.equal(ui.w.document.querySelectorAll('.stock-score-value').length, defect === 'zero' ? 2 : 0);
    if (defect === 'zero') assert.match(ui.w.document.querySelector('[data-side="long"]').textContent, /0\s*\/\s*4/);
    else assert.match(ui.w.document.querySelector('.stock-verdict').textContent, /暂停/);
  });
}

test('cold local report does not masquerade as static mode or disable first refresh', async t => {
  const dom = new JSDOM('<div id="asof">载入中 ...</div><div id="quote"></div><div id="app"></div>' +
    '<div id="prog"></div><div id="progBar"></div><div id="progMsg"></div>' +
    '<button id="btnRefresh"></button><button id="btnHard"></button><button id="btnExport"></button>',
  {url: 'http://localhost/', runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const requests = [];
  dom.window.fetch = async (url, opts) => {
    requests.push({url, method: opts?.method || 'GET'});
    if (url.startsWith('api/status')) return answer({running: false});
    if (url.startsWith('api/refresh')) return answer({started: true});
    return new Response('', {status: 404});
  };
  dom.window.eval(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'));
  for (let i = 0; i < 20; i++) await new Promise(setImmediate);
  dom.window.document.getElementById('btnRefresh').click();
  for (let i = 0; i < 10; i++) await new Promise(setImmediate);
  assert.ok(requests.some(r => r.url === 'api/refresh' && r.method === 'POST'));
  assert.doesNotMatch(dom.window.document.getElementById('asof').textContent, /载入中/);
});
