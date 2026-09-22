'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { buildModel, percentChange } = require('../static/annual-summary.js');

function fixture() {
  const daily = [];
  for (let time = Date.parse('2024-01-01'); time <= Date.parse('2025-03-18'); time += 86400000) {
    const date = new Date(time).toISOString().slice(0, 10), isCurrent = date.startsWith('2025');
    daily.push({ date, revenue: isCurrent ? 20 : 10, orders: isCurrent ? 2 : 1, contribution_margin: isCurrent ? 8 : 4 });
  }
  return {
    period: { since: '2025-03-01', until: '2025-03-18' }, totals: { revenue: 999999 },
    chart_history: {
      period: { since: '2023-08-08', until: '2025-03-18' },
      annual: [
        { period: '2023', revenue: 2000, orders: 100, contribution_margin: 500, days: 146 },
        { period: '2024', revenue: 3660, orders: 366, contribution_margin: 1464, days: 366 },
        { period: '2025', revenue: 1540, orders: 154, contribution_margin: 616, days: 77 },
      ],
      totals: { revenue: 7200, orders: 620, contribution_margin: 2580 },
      shopify_history_complete: true, cost_completeness: false,
    },
    trend_history: { period: { since: '2024-01-01', until: '2025-03-18' }, daily },
  };
}

let data = fixture(), model = buildModel(data);
assert.equal(model.totals.revenue, 7200, 'The selected date filter never replaces all-time history.');
assert.equal(model.totals.orders, 620);
assert.equal(model.totals.margin_rate, 35.83);
assert.equal(model.marginEstimated, true);
assert.equal(model.coverage.costComplete, false);
assert.deepEqual(model.years.map(row => row.isPartial), [true, false, true]);
assert.equal(model.years[1].comparison.basis, 'unavailable', 'A launch year cannot be compared as a full calendar year.');
assert.equal(model.years[2].yoy.revenue, null, 'Partial-year annual growth stays absent.');
assert.equal(model.years[2].comparison.basis, 'same_period');
assert.equal(model.years[2].comparison.previous.revenue, 780, '2024 includes leap day before the March cut-off.');
assert.equal(model.years[2].comparison.current.revenue, 1540);
assert.equal(model.years[2].comparison.pct.revenue, 97.44);

data = fixture();
data.chart_history.period = { since: '2023-01-01', until: '2025-12-31' };
data.chart_history.annual[0].days = 365;
data.chart_history.annual[2].days = 365;
model = buildModel(data);
assert.equal(model.years[1].comparison.basis, 'full_year');
assert.equal(model.years[1].yoy.revenue, 83);
assert.equal(model.years[2].yoy.contribution_margin, -57.92);

data = fixture();
data.chart_history.totals.contribution_margin = null;
data.chart_history.totals.contribution_margin_observed = 2580;
data.chart_history.annual[1].contribution_margin = null;
model = buildModel(data);
assert.equal(model.totals.contribution_margin, null, 'Observed partial sums never replace missing totals.');
assert.equal(model.totals.margin_rate, null);
assert.equal(model.years[1].contribution_margin, null);
assert.equal(model.years[1].revenue, 3660, 'One missing metric does not erase the other metrics.');

data = fixture();
data.trend_history.daily.find(row => row.date === '2024-02-20').contribution_margin = null;
model = buildModel(data);
assert.equal(model.years[2].comparison.pct.contribution_margin, null);
assert.equal(model.years[2].comparison.pct.revenue, 97.44);
assert.match(model.years[2].comparison.percentReasons.contribution_margin, /indisponible/);

data = fixture();
data.trend_history.daily = data.trend_history.daily.filter(row => row.date !== '2024-02-20');
assert.equal(buildModel(data).years[2].comparison.basis, 'unavailable', 'A missing day is not a day with zero sales.');
data = fixture();
data.trend_history.daily.push({ ...data.trend_history.daily[0] });
assert.equal(buildModel(data).years[2].comparison.basis, 'unavailable', 'Duplicate days cannot double-count comparison revenue.');

data = fixture();
data.chart_history.annual[1].days = 300;
model = buildModel(data);
assert.equal(model.years[1].dataComplete, false);
assert.equal(model.years[1].revenue, null, 'Incomplete annual coverage cannot be displayed as a complete amount.');

data = {
  chart_history: { period: { since: '2025-01-01', until: '2025-02-28' }, monthly: [
    { period: '2025-01', revenue: 100, orders: 2, contribution_margin: 20, days: 31 },
    { period: '2025-02', revenue: 200, orders: 3, contribution_margin: 30, days: 28 },
  ] },
};
model = buildModel(data);
assert.equal(model.years[0].revenue, 300);
assert.equal(model.totals.revenue, 300);
assert.equal(model.totals.orders, 5);
data.chart_history.monthly.pop();
assert.equal(buildModel(data).totals.revenue, null, 'A missing monthly bucket is not zero.');

for (const [current, previous, expected] of [[100, 50, 100], [25, 100, -75], [-10, 100, -110], [0, 0, null], [10, 0, null], [10, -10, null], [null, 2, null], [10, NaN, null], ['10', 2, null]]) {
  assert.equal(percentChange(current, previous), expected);
}
data = fixture();
data.chart_history.period = { since: '2023-01-01', until: '2025-12-31' };
data.chart_history.annual[0].days = 365;
data.chart_history.annual[2].days = 365;
data.chart_history.annual[0].contribution_margin = -500;
model = buildModel(data);
assert.equal(model.years[1].comparison.pct.contribution_margin, null);
assert.equal(model.years[1].comparison.delta.contribution_margin, 1964);
assert.match(model.years[1].comparison.percentReasons.contribution_margin, /négative/);

data = fixture();
const frozen = JSON.stringify(data);
buildModel(data);
assert.equal(JSON.stringify(data), frozen, 'Building a display model must not mutate financial source data.');
assert.equal(buildModel({}).totals.revenue, null);
assert.deepEqual(buildModel({}).years, []);
assert.equal(buildModel({ chart_history: { period: { since: '2025-02-30', until: '2025-12-31' } } }).coverage.since, null);

const browser = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/annual-summary.js'), 'utf8'), browser);
assert.equal(typeof browser.TesignAnnualSummary.buildModel, 'function');
console.log('Annual summary: all calculation, coverage, unknown-value and export checks passed.');
