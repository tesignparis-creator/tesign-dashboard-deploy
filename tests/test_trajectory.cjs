/* Run with node tests/test_trajectory.cjs. Synthetic financial planning fixtures. */
const assert = require('node:assert/strict');
const { buildModel, monthsBetween } = require('../static/trajectory.js');
function fixture() {
  return {
    business_plan: { personal_monthly_income_target: 1000, target_date: '2027-08-31', target_context: 'Test' },
    cost_assumptions: {
      reference_price: 45, known_unit_cost: 23.5,
      unit_components: { production: 13, urssaf: 7, fees: 2, packaging: 1.5 },
      shipping_options: {
        mondial_relay: { cost: 4.5, customer_charge: 0, status: 'confirmed' },
        home: { cost: 5.5, customer_charge: 4.9, status: 'confirmed' },
      },
    },
    fixed_costs_metadata: { other_monthly_amount: 139.41, favikon_monthly_amount: 200, status: 'unverified_config' },
    fixed_costs_monthly: { ignoredDuplicate: 339.41 },
    chart_history: {
      period: { until: '2026-09-18' }, result_is_complete: false,
      monthly: [
        { period: '2026-05', orders: 999, revenue: 9999, days: 1 },
        { period: '2026-06', orders: 5, revenue: 225, days: 30 },
        { period: '2026-07', orders: 9, revenue: 405, days: 31 },
        { period: '2026-08', orders: 10, revenue: 450, days: 31 },
        { period: '2026-09', orders: 1, revenue: 45, days: 18 },
      ],
    },
  };
}
const now = { now: '2026-09-22' };
const initial = fixture(), snapshot = JSON.stringify(initial);
let model = buildModel(initial, now);
assert.equal(JSON.stringify(initial), snapshot, 'The model must not mutate the API source.');
assert.equal(model.unit.relayContribution, 17);
assert.equal(model.unit.homeContribution, 20.9);
assert.equal(model.unit.weightedContribution, 17.78);
assert.equal(model.unit.averageOrderRevenue, 45.98);
assert.equal(model.monthly.fixedCosts, 339.41, 'Favikon and tools counted once.');
assert.equal(model.monthly.breakEvenOrders, 25);
assert.equal(model.monthly.targetOrders, 81);
assert.equal(model.monthly.targetRevenue, 3724.38);
assert.equal(model.baseline.ordersMonthly, 8, 'Partial source months excluded from the baseline.');
assert.equal(model.baseline.estimatedResultMonthly, null);
assert.equal(model.trajectory.length, 12);
assert.equal(model.trajectory.at(-1).orders, 81);
assert.equal(model.trajectory.at(-1).availableAfterReserve, 1000.77);
assert.equal(model.ageDays, 4);
assert.deepEqual(monthsBetween('2026-12', '2027-02'), ['2026-12', '2027-01', '2027-02']);
assert.deepEqual(monthsBetween('2027-02', '2026-12'), []);
assert.deepEqual(monthsBetween('2026-00', '2027-02'), []);
assert.deepEqual(monthsBetween('2026-09', '2036-09'), [], 'Do not generate unbounded scenarios.');

model = buildModel(fixture(), { ...now, stockReserveMonthly: 130 });
assert.equal(model.monthly.breakEvenOrders, 25, 'Reserves are cash allocation, not an extra operating cost.');
assert.equal(model.monthly.targetOrders, 89);
assert.equal(model.unit.knownUnitCost, 23.5, 'No second deduction of production for replenishment.');
assert.equal(model.trajectory.at(-1).modeledRemainderBeforeReserve - model.trajectory.at(-1).availableAfterReserve, 130);
assert.equal(model.trajectory.at(-1).availableAfterReserve, 1013.01);

let data = fixture(); delete data.business_plan;
model = buildModel(data, now);
assert.equal(model.target.incomeMonthly, null, 'No hardcoded personal income fallback.');
assert.equal(model.monthly.targetOrders, null);
assert.equal(model.calculable, false);
data = fixture(); data.business_plan.personal_monthly_income_target = 2000;
assert.equal(buildModel(data, now).monthly.targetOrders, 138, 'Use configured target.');
data.business_plan.target_date = '2027-02-30';
assert.equal(buildModel(data, now).target.date, null, 'Reject rolled-over calendar dates.');

data = fixture(); delete data.fixed_costs_metadata.favikon_monthly_amount;
assert.equal(buildModel(data, now).monthly.fixedCosts, null, 'Unknown fixed cost is not zero.');
assert.equal(buildModel(data, now).monthly.targetOrders, null);
data = fixture(); data.cost_assumptions.unit_components.fees = null;
assert.equal(buildModel(data, now).unit.knownUnitCost, null);
assert.equal(buildModel(data, now).monthly.targetOrders, null);
data = fixture(); data.cost_assumptions.shipping_options.home.cost = null;
assert.equal(buildModel(data, now).unit.weightedContribution, null);
assert.equal(buildModel(data, { ...now, relayShare: 1 }).unit.weightedContribution, 17, 'Unused delivery route does not block a 100% relay scenario.');
data = fixture(); data.cost_assumptions.shipping_options.home.status = 'unconfirmed';
assert.equal(buildModel(data, now).unit.homeContribution, null);
data = fixture(); data.cost_assumptions.unit_components.production = 100;
assert.equal(buildModel(data, now).monthly.targetOrders, null, 'Nonpositive contribution has no viable volume.');

for (const value of [null, '', -5, NaN, Infinity, true]) {
  assert.equal(buildModel(fixture(), { ...now, adBudgetMonthly: value }).monthly.targetOrders, null);
}
model = buildModel(fixture(), { ...now, adBudgetMonthly: null });
assert.ok(model.missing.some(text => text.includes('budget publicitaire mensuel')));
assert.ok(model.missing.every(text => !text.includes('adBudgetMonthly')));
data = fixture(); data.chart_history.monthly[1].revenue = -30;
model = buildModel(data, now);
assert.equal(model.actual.find(row => row.month === '2026-06').revenue, -30, 'Preserve any negative revenue supplied by the source.');
assert.equal(model.baseline.revenueMonthly, 275);
assert.equal(buildModel(fixture(), { ...now, relayShare: 1.5 }).monthly.targetOrders, null);
assert.equal(buildModel(fixture(), { ...now, adBudgetMonthly: '100' }).monthly.targetOrders, 81);
model = buildModel(fixture(), { now: '2026-10-02' });
assert.equal(model.startMonth, '2026-10', 'A stale snapshot must not start the forecast in the past.');
assert.equal(model.trajectory.length, 11);
assert.ok(model.warnings.some(text => text.includes('7 jours')));
model = buildModel(fixture(), { now: '2027-09-01' });
assert.equal(model.calculable, false);
assert.deepEqual(model.trajectory, []);
data = fixture(); data.business_plan.target_date = '2026-09-01';
assert.equal(buildModel(data, now).calculable, false, 'A passed goal date is expired even inside the current month.');
data = fixture(); data.chart_history.monthly[2].orders = null;
model = buildModel(data, now);
assert.equal(model.baseline.ordersMonthly, null, 'Missing actual month is not skipped or turned into zero.');
assert.equal(model.trajectory[0].orders, null);
assert.equal(model.trajectory.at(-1).orders, 81, 'The target remains calculable independently of a missing historical baseline.');
assert.equal(buildModel({}, now).unit.weightedContribution, null);
console.log('Trajectory planning: arithmetic, unknown values, delivery mix, dates and reserve allocation passed.');
