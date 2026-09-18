/* Run with: node tests/test_frontend.cjs [optional-dashboard-payload.json] */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(
  /refresh\.onclick=\(\)=>load\(true\); load\(\); setInterval\(\(\)=>load\(false\),300000\);/,
  '',
);
new vm.Script(script);
const nodes = {};
const canvasContext = new Proxy({}, { get: (target, key) => target[key] || (() => {}) });
for (const match of html.matchAll(/id="([^"]+)"/g)) {
  nodes[match[1]] = {
    style: {}, value: '', innerHTML: '', textContent: '', hidden: false, width: 900, height: 270,
    addEventListener() {}, getContext() { return canvasContext; },
  };
}
const context = vm.createContext({
  ...nodes,
  document: { getElementById: id => nodes[id] },
  localStorage: { getItem() { return null; }, setItem() {} },
});
vm.runInContext(script, context);
function render(payload) {
  context.payload = payload;
  vm.runInContext('render(payload)', context);
}

const sample = {
  period: { since: '2026-09-01', until: '2026-09-18', days: 18 },
  generated_at: '2026-09-18T10:00:00Z',
  data_freshness: {
    selected_period_generated_at: '2026-09-17T08:00:00Z',
    cumulative_generated_at: '2026-09-17T09:00:00Z',
    stock_generated_at: '2026-09-17T09:00:00Z',
  },
  totals: {
    revenue: 450, orders: 9, ad_spend: 100, meta_purchases: 0, meta_purchase_value: 0,
    meta_roas: 0, meta_cpa: 0, blended_roas: 4.5, blended_cpa: 100 / 9,
    contribution_margin: 170, estimated_result: 30,
    geremy_commission: null, geremy_commission_status: 'unconfirmed',
  },
  cumulative: {
    totals: { estimated_result: 100 }, is_complete: false,
    shopify_history_complete: true, missing_data: 'Historique incomplet',
  },
  data_status: { meta_account: { status: 'available', checked_at: '2026-09-17T07:55:00Z' } },
  financial: {
    bank_accounts: [{ label: 'TESIGN', balance: 44, source: 'manual', recorded_at: '2026-08-01', stale: true }],
    banking: { status: 'manual', stale: true, updated_at: '2026-08-01' },
  },
  cost_assumptions: {
    reference_price: 45, known_unit_cost: 23.5, contribution_before_shipping: 21.5,
    contribution_after_shipping: 17, shipping_cost: 4.5, reference_shipping: 'mondial_relay',
    shipping_options: {
      mondial_relay: { cost: 4.5, customer_charge: 0 },
      home: { cost: null, customer_charge: 4.9 },
    },
    break_even_roas: 45 / 17,
    unit_components: { urssaf: 7, production: 13, fees: 2, packaging: 1.5 },
    historical_costs_verified: false,
  },
  campaign_performance: [{ campaign: 'Test', spend: 100, purchases: 0, purchase_value: 0, roas: 0, cpa: 0 }],
  daily: [], stock: [], orders: [], stock_totals: { available: 4, incoming: 2 },
  stock_as_of: '2026-09-18', stock_snapshot_at: '2026-07-24',
  fixed_costs_monthly: { test: 10 }, warnings: [],
};

render(sample);
assert.match(nodes.metaCards.innerHTML, /CPA Meta attribué<\/span><strong class="">Indisponible/);
assert.match(nodes.metaCards.innerHTML, /ROAS Meta attribué<\/span><strong class="">0<\/strong>/);
assert.match(nodes.healthCards.innerHTML, /MER<\/span><strong class="">4,5<\/strong>/);
assert.match(nodes.cards.innerHTML, /À recalculer/);
assert.match(nodes.costAssumptions.innerHTML, /Marge après Mondial Relay/);
assert.match(nodes.costAssumptions.innerHTML, /17,00/);
assert.match(nodes.costConclusion.innerHTML, /Commission Geremy non confirmée/);
assert.match(nodes.bankAccounts.innerHTML, /Solde saisi manuellement — ancien ou non daté/);
assert.match(nodes.updated.textContent, /17\/09\/2026/);
assert.match(nodes.stockFreshness.textContent, /24\/07\/2026/);
assert.match(nodes.historyNotice.textContent, /dates de lecture/);
assert.match(nodes.warnings.innerHTML, /Livraison à domicile/);
assert.doesNotMatch(nodes.metaConclusion.innerHTML, /développées progressivement|réduire le coût d.acquisition/);
assert.match(html, /<section class="panel kpi-block">\s*<h2>Trésorerie professionnelle TESIGN/);
assert.doesNotMatch(html, /\.warnings\s*\{\s*display\s*:\s*none/);

const unavailable = {
  ...sample,
  totals: {
    ...sample.totals, ad_spend: null, meta_purchases: null, meta_purchase_value: null,
    meta_roas: null, meta_cpa: null, blended_roas: null, blended_cpa: null, estimated_result: null,
  },
  daily: [
    { date: '2026-09-01', revenue: 40, contribution_margin: 10, ad_spend: null, estimated_result: null, orders: 1 },
    { date: '2026-09-02', revenue: 0, contribution_margin: 0, ad_spend: 0, estimated_result: 0, orders: 0 },
  ],
};
render(unavailable);
assert.match(nodes.metaCards.innerHTML, /Dépenses Meta<\/span><strong class="">Indisponible/);
assert.match(nodes.healthConclusion.innerHTML, /ne peut pas être établi/);
assert.equal(vm.runInContext('aggregateMonthly(payload.daily)[0].ad_spend', context), null);
assert.equal(vm.runInContext('aggregateMonthly(payload.daily)[0].estimated_result', context), null);
assert.match(nodes.trendCards.innerHTML, /Marge estimée − Meta<\/span><strong class="">Indisponible/);
assert.doesNotMatch(nodes.affiliateCards.innerHTML, />0,00/);

render({
  ...sample,
  financial: {
    bank_accounts: [{ label: 'TESIGN', balance: null, source: 'bridge_sandbox', is_demo: true, recorded_at: null }],
    banking: { status: 'unavailable', updated_at: '2026-09-18' },
  },
  cost_assumptions: {},
});
assert.match(nodes.bankAccounts.innerHTML, /Données de test/);
assert.match(nodes.bankAccounts.innerHTML, /date du solde inconnue/);
assert.doesNotMatch(nodes.bankAccounts.innerHTML, /18\/09\/2026/);
assert.match(nodes.costAssumptions.innerHTML, /Indisponible/);
assert.match(nodes.warnings.innerHTML, /Frais d’expédition non confirmés/);

if (process.argv[2]) {
  render(JSON.parse(fs.readFileSync(process.argv[2], 'utf8')));
  console.log('PASS: supplied dashboard payload renders.');
}
console.log('PASS: frontend syntax and rendering, Meta attribution versus MER, missing data, shipping costs, bank provenance and cache freshness.');
