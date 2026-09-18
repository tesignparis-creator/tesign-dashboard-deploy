/* Graph aggregation and missing-data regressions. Run: node tests/test_charts.cjs */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/refresh\.onclick=\(\)=>load\(true\); load\(\); setInterval\(\(\)=>load\(false\),300000\);/, '');
const nodes = {};
for (const match of html.matchAll(/id="([^"]+)"/g)) {
  nodes[match[1]] = { style: {}, value: '', innerHTML: '', textContent: '', hidden: false, clientWidth: 340, attributes: {}, addEventListener() {}, setAttribute(key, value) { this.attributes[key] = value; } };
}
nodes.chartRange.value = 'all';
nodes.chartGrouping.value = 'month';
const context = vm.createContext({ ...nodes, document: { getElementById: id => nodes[id] }, localStorage: { getItem() { return null; }, setItem() {} } });
vm.runInContext(script, context);
const run = code => vm.runInContext(code, context);
const row = (date, revenue, orders, result) => ({ date, revenue, orders, estimated_result: result, contribution_margin: revenue / 2, variable_costs: revenue / 2, ad_spend: 30, fixed_costs: 20, business_expenses: 0, geremy_commission: null });
context.payload = {
  chart_history: { period: { since: '2025-01-01', until: '2025-03-18' }, monthly: [row('2025-01-01', 100, 2, 0), row('2025-02-01', 900, 10, -35), row('2025-03-01', 200, 2, null), row('2099-01-01', 999999, 9, 999999)], cost_completeness: false },
  capital_history: { status: 'unknown', is_complete: false, flows: [], monthly: [], totals: { contributions: null, withdrawals: null, net_contributions: null }, observed_totals: { contributions: null, withdrawals: null, net_contributions: null } },
};
run('renderEvolution(payload)');
assert.match(nodes.revenueChartTotal.textContent, /1\s?200,00/);
assert.doesNotMatch(nodes.revenueChart.innerHTML, /2099|999999|NaN|Infinity/);
assert.match(nodes.resultChart.innerHTML, /fill="#b44235"/);
assert.match(nodes.resultChart.innerHTML, /Indisponible/);
assert.equal(nodes.resultChartTotal.textContent, 'Non calculable');
assert.match(nodes.capitalHistoryCards.innerHTML, /À renseigner/);
assert.doesNotMatch(nodes.capitalHistoryCards.innerHTML, />0,00/);
assert.equal(nodes.capitalHistoryChart.hidden, true);
assert.equal(run('chartMonthlyRows(payload).length'), 3);
assert.equal(run('chartMonthlyRows(payload)[2].partial'), true);
context.payload.chart_history.period.since = '2025-01-03';
assert.equal(run('chartMonthlyRows(payload)[0].partial'), true);
assert.equal(run("selectChartRows(chartMonthlyRows(payload),'all','year')[0].estimated_result"), null);
assert.equal(run("selectChartRows(chartMonthlyRows(payload),'all','year')[0].orders"), 14);
assert.equal(run("selectChartRows(chartMonthlyRows(payload),'all','year')[0].average_order_value"), 85.71);

nodes.chartBasketMetric.onclick();
assert.match(nodes.ordersChartTotal.textContent, /85,71/);
assert.equal(nodes.chartBasketMetric.attributes['aria-pressed'], 'true');
assert.match(nodes.ordersChartNote.textContent, /pondéré/);
run("chartFocus('resultChart', 1)");
assert.match(nodes.resultChartReadout.textContent, /février 2025/);
assert.match(nodes.resultChartReadout.textContent, /-35,00/);
assert.match(nodes.resultChartReadout.textContent, /non confirmés/);
let focusedChartIndex = null;
nodes.revenueChart.querySelector = selector => ({ focus() { focusedChartIndex = selector; } });
nodes.revenueChart.onkeydown({ key: 'ArrowRight', preventDefault() {}, target: { closest() { return { getAttribute() { return '0'; } }; } } });
assert.equal(focusedChartIndex, '[data-chart-index="1"]');
assert.match(nodes.revenueChartReadout.textContent, /février 2025/);
assert.match(nodes.revenueChartReadout.textContent, /900,00/);

context.payload.chart_history.monthly.splice(1, 1);
run('renderEvolution(payload)');
assert.equal(run('chartMonthlyRows(payload)[1].missing'), true);
assert.equal(run('chartMonthlyRows(payload)[1].revenue'), null);
assert.equal(nodes.revenueChartTotal.textContent, 'Indisponible');
assert.match(nodes.revenueChart.innerHTML, /février 2025 : CA Shopify Indisponible/);
assert.doesNotMatch(nodes.revenueChart.innerHTML, /NaN|Infinity/);

context.payload.capital_history = { status: 'partial', is_complete: false, totals: { contributions: null, withdrawals: null, net_contributions: null }, observed_totals: { contributions: 500, withdrawals: 50, net_contributions: 450 }, flows: [{ date: '2025-01-03', type: 'contribution', amount: 500, source: 'Relevé professionnel' }], monthly: [{ date: '2025-01-01', cumulative_contributions: 500, cumulative_withdrawals: 50 }] };
run('renderEvolution(payload)');
assert.match(nodes.capitalHistoryCards.innerHTML, /500,00/);
assert.match(nodes.capitalHistoryCards.innerHTML, /Montant documenté seulement/);
assert.match(nodes.capitalHistoryCards.innerHTML, /Retraits personnels<\/span><strong>À renseigner/);
assert.match(nodes.capitalHistoryCards.innerHTML, /Apports − retraits<\/span><strong>Non calculable/);
assert.doesNotMatch(nodes.capitalHistoryChart.innerHTML, /Retraits documentés cumulés/);
assert.match(nodes.capitalHistoryNote.textContent, /ne peut pas être établi/);
assert.equal(nodes.capitalHistoryChart.hidden, false);
context.payload.capital_history.monthly.unshift({ date: '2023-01-01', cumulative_contributions: 0, cumulative_withdrawals: 0 });
run('renderEvolution(payload)');
assert.doesNotMatch(nodes.capitalHistoryChart.innerHTML, /2023|Retraits documentés cumulés/);

if (process.argv[2]) {
  context.payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  nodes.chartRange.value = 'all'; nodes.chartGrouping.value = 'year';
  run('renderEvolution(payload)');
  for (const id of ['revenueChart', 'resultChart', 'costsChart', 'ordersChart']) assert.doesNotMatch(nodes[id].innerHTML, /NaN|Infinity|undefined/);
  assert.ok(run('selectChartRows(chartMonthlyRows(payload),"all","year").length') > 1);
}
console.log('PASS: charts preserve unknowns and future exclusions, negative results, weighted baskets, annual aggregation, accessible details and unconfirmed capital.');
