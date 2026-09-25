/* Run with: node tests/test_loading.cjs. No network or account data is used. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'loading.js'), 'utf8');
new vm.Script(source);

function harness() {
  let now = 0, timerId = 0;
  const timers = new Map();
  const nodes = Object.fromEntries(
    ['loadingState', 'loadingTitle', 'loadingMessage', 'loadingRetry'].map(id => [id, {
      hidden: true, textContent: '', dataset: {}, onclick: null,
    }]),
  );
  const since = { value: '2026-09-01' }, until = { value: '2026-09-25' };
  const refresh = { disabled: false, textContent: 'Actualiser' };
  const error = { hidden: false, textContent: 'Previous error' };
  const requests = [], rendered = [];
  const context = vm.createContext({
    document: { getElementById: id => nodes[id] },
    AbortController,
    setTimeout(callback, delay) {
      const id = ++timerId;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      // Every request is intercepted here; unexpected external URLs fail the test.
      assert.match(url, /^\/api\/(dashboard|refresh)\?since=\d{4}-\d{2}-\d{2}&until=\d{4}-\d{2}-\d{2}$/);
      let resolve, reject;
      const response = new Promise((yes, no) => { resolve = yes; reject = no; });
      options.signal.addEventListener('abort', () => {
        const failure = new Error('Synthetic aborted request');
        failure.name = 'AbortError';
        reject(failure);
      }, { once: true });
      requests.push({ url, signal: options.signal, resolve, reject });
      return response;
    },
  });
  vm.runInContext(source, context);
  const loader = context.TesignLoader.create({
    render: payload => rendered.push(payload), since, until, refresh, error,
  });
  function tick(duration) {
    const end = now + duration;
    while (true) {
      const next = [...timers.entries()].filter(([, value]) => value.at <= end)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!next) break;
      const [id, timer] = next;
      now = timer.at;
      timers.delete(id);
      timer.callback();
    }
    now = end;
  }
  return { loader, nodes, since, until, refresh, error, requests, rendered, timers, tick };
}

function succeed(request, payload = { fixture: 'Current verified response' }) {
  request.resolve({ ok: true, json: async () => payload });
  return payload;
}

function assertFailure(h) {
  assert.equal(h.nodes.loadingState.hidden, false);
  assert.equal(h.nodes.loadingState.dataset.state, 'error');
  assert.match(h.nodes.loadingTitle.textContent, /n’ont pas pu être chargés/);
  assert.equal(h.nodes.loadingRetry.hidden, false);
  assert.equal(h.refresh.disabled, false);
  assert.equal(h.refresh.textContent, 'Actualiser');
  assert.equal(h.timers.size, 0);
  assert.equal(h.error.hidden, true);
}

async function run() {
  {
    const h = harness();
    const first = h.loader.load();
    assert.equal(h.nodes.loadingState.hidden, false);
    assert.equal(h.nodes.loadingState.dataset.state, 'loading');
    assert.match(h.nodes.loadingTitle.textContent, /Chargement de tes chiffres/);
    assert.match(h.nodes.loadingMessage.textContent, /vont apparaître ici/);
    assert.equal(h.nodes.loadingRetry.hidden, true);
    assert.equal(h.refresh.disabled, true);
    assert.equal(h.error.hidden, true);
    assert.equal(h.rendered.length, 0, 'Pending data must not render invented values');
    assert.equal(h.requests[0].url, '/api/dashboard?since=2026-09-01&until=2026-09-25');
    await h.loader.load(true);
    await h.loader.load();
    assert.equal(h.requests.length, 1, 'Concurrent calls must share one in-flight request');
    const initialMessage = h.nodes.loadingMessage.textContent;
    h.tick(11999);
    assert.equal(h.nodes.loadingMessage.textContent, initialMessage);
    h.tick(1);
    assert.match(h.nodes.loadingMessage.textContent, /continue automatiquement/);
    assert.equal(h.requests[0].signal.aborted, false);
    const payload = succeed(h.requests[0]);
    await first;
    assert.deepEqual(h.rendered, [payload]);
    assert.equal(h.nodes.loadingState.hidden, true);
    assert.equal(h.refresh.disabled, false);
    assert.equal(h.refresh.textContent, 'Actualiser');
    assert.equal(h.timers.size, 0);
    const finishedMessage = h.nodes.loadingMessage.textContent;
    h.tick(180000);
    assert.equal(h.requests[0].signal.aborted, false, 'Completed fetch must not be aborted later');
    assert.equal(h.nodes.loadingMessage.textContent, finishedMessage);
    assert.equal(h.nodes.loadingState.hidden, true);
  }

  {
    const h = harness();
    const pending = h.loader.load(true);
    assert.equal(h.requests[0].url, '/api/refresh?since=2026-09-01&until=2026-09-25');
    let bodyRead = false;
    h.requests[0].resolve({ ok: false, status: 503, json: async () => {
      bodyRead = true;
      throw new Error('Synthetic private connector details');
    } });
    await pending;
    assertFailure(h);
    assert.equal(bodyRead, false, 'HTTP error bodies must not be exposed or parsed as dashboard data');
    assert.equal(h.rendered.length, 0);
    assert.doesNotMatch(h.nodes.loadingMessage.textContent, /private|connector/);

    h.since.value = '2026-08-01';
    h.until.value = '2026-08-31';
    const retry = h.nodes.loadingRetry.onclick();
    assert.equal(h.nodes.loadingState.dataset.state, 'loading');
    assert.equal(h.nodes.loadingRetry.hidden, true);
    assert.equal(h.requests[1].url, '/api/dashboard?since=2026-08-01&until=2026-08-31');
    const payload = succeed(h.requests[1]);
    await retry;
    assert.deepEqual(h.rendered, [payload]);
    assert.equal(h.nodes.loadingState.hidden, true);
    assert.equal(h.timers.size, 0);
  }

  for (const type of ['non-json', 'network']) {
    const h = harness();
    const pending = h.loader.load();
    const failure = new Error('Unexpected token <html> Synthetic private connector details');
    if (type === 'non-json') h.requests[0].resolve({ ok: true, json: async () => { throw failure; } });
    else h.requests[0].reject(failure);
    await pending;
    assertFailure(h);
    assert.match(h.nodes.loadingMessage.textContent, /Tu peux réessayer/);
    assert.doesNotMatch(h.nodes.loadingMessage.textContent, /Unexpected|html|private/);
    assert.equal(h.rendered.length, 0);
  }

  {
    const h = harness();
    const pending = h.loader.load();
    h.tick(179999);
    assert.equal(h.requests[0].signal.aborted, false);
    assert.equal(h.refresh.disabled, true);
    h.tick(1);
    assert.equal(h.requests[0].signal.aborted, true, 'Fetch must be aborted after 180 seconds');
    await pending;
    assertFailure(h);
    assert.equal(h.rendered.length, 0);
    const retry = h.nodes.loadingRetry.onclick();
    assert.equal(h.requests.length, 2, 'A timeout must release the pending guard for retry');
    assert.equal(h.requests[1].signal.aborted, false, 'Retry needs a new controller');
    succeed(h.requests[1]);
    await retry;
    assert.equal(h.nodes.loadingState.hidden, true);
  }

  {
    const h = harness();
    const first = h.loader.load();
    const previous = succeed(h.requests[0], { fixture: 'Previous dated numbers' });
    await first;
    h.since.value = '2026-07-01';
    h.until.value = '2026-07-31';
    const refresh = h.loader.load(true);
    assert.equal(h.requests[1].url, '/api/refresh?since=2026-07-01&until=2026-07-31');
    assert.match(h.nodes.loadingTitle.textContent, /Actualisation/);
    assert.match(h.nodes.loadingMessage.textContent, /derniers chiffres restent affichés/);
    assert.deepEqual(h.rendered, [previous]);
    h.requests[1].reject(new Error('Synthetic temporary outage'));
    await refresh;
    assertFailure(h);
    assert.match(h.nodes.loadingMessage.textContent, /dernière lecture restent affichés/);
    assert.deepEqual(h.rendered, [previous], 'A failed refresh must preserve the previous render');
    const retry = h.nodes.loadingRetry.onclick();
    const current = succeed(h.requests[2], { fixture: 'New dated numbers' });
    await retry;
    assert.deepEqual(h.rendered, [previous, current]);
    assert.equal(h.nodes.loadingState.hidden, true);
    assert.equal(h.timers.size, 0);
  }

  console.log('PASS: loading visibility, request deduplication, 12s notice, success cleanup, HTTP/non-JSON/network errors, 180s abort, retry, dated-data retention and period/force parameters.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
