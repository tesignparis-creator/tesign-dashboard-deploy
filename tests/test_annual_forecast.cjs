/* Run with node tests/test_annual_forecast.cjs. Synthetic, deterministic annual data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { buildModel } = require('../static/annual-summary.js');
const { buildForecast } = require('../static/annual-forecast.js');
const round = value => Math.round((value + Number.EPSILON) * 100) / 100;
function data() {
  return {
    chart_history: {
      period: { since: '2023-08-08', until: '2026-09-22' },
      annual: [
        { period: '2023', days: 146, revenue: 6157, orders: 119, contribution_margin: 2000 },
        { period: '2024', days: 366, revenue: 8278, orders: 124, contribution_margin: 2500 },
        { period: '2025', days: 365, revenue: 2094.3, orders: 34, contribution_margin: 500 },
        { period: '2026', days: 265, revenue: 5336.1, orders: 89, contribution_margin: 1600 },
      ],
    },
  };
}
function forecast(source = data(), goal = {}) {
  return buildForecast(source, buildModel(source), goal);
}
function assertUnavailable(result) {
  assert.equal(result.complete, false);
  for (const key of ['orders', 'revenue', 'contribution_margin', 'margin_rate']) assert.equal(result[key], null);
  assert.ok(result.missing.length);
}
const source = data(), sourceCopy = JSON.stringify(source), annual = buildModel(source), annualCopy = JSON.stringify(annual);
const result = buildForecast(source, annual, { monthly: { targetOrders: 81 }, unit: { weightedContribution: 17.78 } });
assert.equal(result.year, 2027);
assert.equal(result.isForecast, true);
assert.equal(result.kind, 'historical_scenario');
assert.equal(result.complete, true);
assert.equal(result.revenue, round((8278 + 2094.3 + 5336.1 * 365 / 265) / 3));
assert.equal(result.orders, Math.round((124 + 34 + 89 * 365 / 265) / 3));
assert.equal(result.contribution_margin, round((2500 + 500 + 1600 * 365 / 265) / 3));
assert.equal(result.margin_rate, round(result.contribution_margin / result.revenue * 100));
assert.deepEqual(result.basis.map(row => row.year), [2024, 2025, 2026]);
assert.equal(result.basis[0].calendarDays, 366, 'Leap years are full 366-day years.');
assert.equal(result.basis[0].factor, 1);
assert.equal(result.basis[2].factor, 365 / 265);
assert.equal(result.basis[2].annualized, true);
assert.deepEqual(result.range.revenue, { low: 2094.3, high: 8278 });
assert.deepEqual(result.range.orders, { low: 34, high: 124 });
assert.match(result.assumptions.join(' '), /pas un intervalle de confiance/);
assert.match(result.methodLabel, /Moyenne des 3/);
assert.equal(result.missing.length, 0);
assert.equal(JSON.stringify(source), sourceCopy);
assert.equal(JSON.stringify(annual), annualCopy, 'Neither source nor historical totals may be mutated.');
const differentGoal = forecast(data(), { monthly: { targetOrders: 100000 }, unit: { weightedContribution: 1000 } });
assert.deepEqual(differentGoal, forecast(data(), {}), 'Goals and current single-shirt costs never affect historical projections.');
let changed = data(); changed.chart_history.annual[0].revenue = 1000000;
assert.equal(forecast(changed).revenue, result.revenue, 'Never annualize or include the launch year.');
changed = data(); changed.chart_history.annual[3].days = 264;
assertUnavailable(forecast(changed));
changed = data(); changed.chart_history.annual.splice(1, 1);
assertUnavailable(forecast(changed));
changed = data(); changed.chart_history.annual.push({ ...changed.chart_history.annual[1] });
assertUnavailable(forecast(changed));
changed = data(); changed.chart_history.period.since = '2024-02-01';
assertUnavailable(forecast(changed));
for (const invalid of [null, undefined, NaN, Infinity, '2500']) {
  changed = data(); changed.chart_history.annual[1].contribution_margin = invalid;
  const missingMargin = forecast(changed);
  assert.equal(missingMargin.contribution_margin, null);
  assert.equal(missingMargin.margin_rate, null);
  assert.equal(missingMargin.revenue, result.revenue, 'Unavailable margin does not destroy known revenue.');
  assert.equal(missingMargin.range.contribution_margin.low, null);
  assert.equal(missingMargin.complete, false);
}
for (const year of [null, '2027', 2027.5, 2026, 2028, 99, NaN]) {
  assertUnavailable(buildForecast(source, annual, {}, year));
}
assertUnavailable(buildForecast(null, null, null));
changed = data(); changed.chart_history.period.until = '2026-02-30';
assertUnavailable(forecast(changed));
changed = data();
for (const row of changed.chart_history.annual) { row.revenue = 0; row.orders = 0; row.contribution_margin = 0; }
let zero = forecast(changed);
assert.equal(zero.complete, true);
assert.equal(zero.revenue, 0);
assert.equal(zero.orders, 0);
assert.equal(zero.contribution_margin, 0);
assert.equal(zero.margin_rate, null, 'Zero revenue has no defined margin rate.');
changed = data();
for (const row of changed.chart_history.annual) row.contribution_margin = -100;
assert.ok(forecast(changed).contribution_margin < 0, 'Do not hide historical negative margins.');
changed = data();
changed.chart_history.period.until = '2026-12-31';
changed.chart_history.annual[3].days = 365;
assert.equal(forecast(changed).basis[2].factor, 1, 'A completed latest year is never extrapolated.');
changed = data(); changed.chart_history.period.until = '2027-01-01';
assertUnavailable(forecast(changed), 'A year with actual observations cannot be shown as an entirely future projection.');
const browser = {};
vm.runInNewContext(fs.readFileSync(require.resolve('../static/annual-forecast.js'), 'utf8'), browser);
assert.equal(typeof browser.TesignAnnualForecast.buildForecast, 'function');
assert.equal(browser.TesignAnnualForecast.buildForecast(source, annual, {}).revenue, result.revenue);
console.log('Historical forecast: 3-year average, explicit normalization and range, missing data, actual separation and goal independence passed.');
