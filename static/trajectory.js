/* TESIGN planning model. Scenarios are neither forecasts of demand nor net income. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.TesignTrajectory = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const DEFAULT_INPUTS = Object.freeze({
    adBudgetMonthly: 100, relayShare: 0.8, additionalMonthly: 0, stockReserveMonthly: 0,
  });
  const INPUT_LABELS = Object.freeze({
    adBudgetMonthly: 'budget publicitaire mensuel',
    relayShare: 'part des livraisons en point relais',
    additionalMonthly: 'charges mensuelles supplémentaires',
    stockReserveMonthly: 'réserve mensuelle pour le stock',
  });
  const finite = value => (typeof value === 'number' && Number.isFinite(value)) ? value : null;
  const nonnegative = value => finite(value) !== null && value >= 0 ? value : null;
  const round = value => finite(value) === null ? null : Math.round((value + Number.EPSILON) * 100) / 100;
  const sumKnown = values => values.every(value => finite(value) !== null) ? values.reduce((sum, value) => sum + value, 0) : null;
  function inputNumber(value) {
    if (typeof value === 'string' && value.trim() !== '') value = Number(value);
    return nonnegative(value);
  }
  function isoDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const date = new Date(value + 'T00:00:00Z');
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : null;
  }
  function monthIndex(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}$/.test(value)) return null;
    const [year, month] = value.split('-').map(Number);
    return year >= 1000 && month >= 1 && month <= 12 ? year * 12 + month - 1 : null;
  }
  function monthKey(index) {
    return String(Math.floor(index / 12)).padStart(4, '0') + '-' + String(index % 12 + 1).padStart(2, '0');
  }
  function monthDays(period) {
    const index = monthIndex(period);
    return index === null ? null : new Date(Date.UTC(Math.floor(index / 12), index % 12 + 1, 0)).getUTCDate();
  }
  function monthsBetween(start, end) {
    const first = monthIndex(start), last = monthIndex(end);
    if (first === null || last === null || last < first || last - first >= 120) return [];
    return Array.from({ length: last - first + 1 }, (_, index) => monthKey(first + index));
  }
  function weighted(left, right, share) {
    if (share === null) return null;
    if (share === 1) return left;
    if (share === 0) return right;
    return finite(left) !== null && finite(right) !== null ? left * share + right * (1 - share) : null;
  }
  function requiredOrders(amount, contribution) {
    if (finite(amount) === null || finite(contribution) === null || contribution <= 0) return null;
    return Math.ceil(Math.max(0, amount) / contribution - 1e-10);
  }
  function buildModel(data, supplied) {
    data = data || {};
    supplied = supplied || {};
    const missing = [], warnings = [];
    const inputs = {};
    for (const key of Object.keys(DEFAULT_INPUTS)) {
      inputs[key] = inputNumber(supplied[key] === undefined ? DEFAULT_INPUTS[key] : supplied[key]);
      if (inputs[key] === null) missing.push('Hypothèse à renseigner : ' + INPUT_LABELS[key] + '.');
    }
    if (inputs.relayShare !== null && inputs.relayShare > 1) {
      inputs.relayShare = null;
      missing.push('Le mix Mondial Relay doit être compris entre 0 et 100 %.');
    }
    const plan = data.business_plan || {};
    const target = {
      incomeMonthly: nonnegative(plan.personal_monthly_income_target),
      date: isoDate(plan.target_date),
      context: typeof plan.target_context === 'string' ? plan.target_context : null,
    };
    if (target.incomeMonthly === null) missing.push('Objectif de revenu personnel non renseigné.');
    if (target.date === null) missing.push('Date cible non renseignée ou invalide.');
    const costs = data.cost_assumptions || {};
    const components = costs.unit_components || {};
    const price = nonnegative(costs.reference_price);
    const knownUnitCost = sumKnown(['production', 'urssaf', 'fees', 'packaging'].map(key => nonnegative(components[key])));
    if (price === null || knownUnitCost === null) missing.push('Prix ou coûts unitaires manquants.');
    if (knownUnitCost !== null && finite(costs.known_unit_cost) !== null && Math.abs(knownUnitCost - costs.known_unit_cost) > 0.01) {
      warnings.push('Le total de coûts fourni diffère de ses composantes : les composantes détaillées sont utilisées.');
    }
    function delivery(key) {
      const option = (costs.shipping_options || {})[key] || {};
      const transport = nonnegative(option.cost), charge = nonnegative(option.customer_charge);
      const valid = option.status === 'confirmed' && transport !== null && charge !== null;
      return {
        transport: valid ? transport : null, customerCharge: valid ? charge : null,
        revenue: valid && price !== null ? price + charge : null,
        contribution: valid && price !== null && knownUnitCost !== null ? price + charge - knownUnitCost - transport : null,
      };
    }
    const relay = delivery('mondial_relay'), home = delivery('home');
    const contribution = weighted(relay.contribution, home.contribution, inputs.relayShare);
    const averageOrderRevenue = weighted(relay.revenue, home.revenue, inputs.relayShare);
    if (contribution === null) missing.push('Frais de livraison ou mix non confirmés.');
    else if (contribution <= 0) missing.push('Marge par commande nulle ou négative : aucun volume ne finance cet objectif.');
    const metadata = data.fixed_costs_metadata || {};
    // The API splits tools and Favikon; never add fixed_costs_monthly again.
    const fixedCosts = sumKnown([nonnegative(metadata.other_monthly_amount), nonnegative(metadata.favikon_monthly_amount)]);
    if (fixedCosts === null) missing.push('Charges fixes mensuelles indisponibles.');
    if (metadata.status !== 'confirmed') warnings.push('Charges fixes issues de la configuration : montants à rapprocher des factures.');
    const operatingCosts = sumKnown([fixedCosts, inputs.adBudgetMonthly, inputs.additionalMonthly]);
    const requiredContribution = sumKnown([operatingCosts, inputs.stockReserveMonthly, target.incomeMonthly]);
    const targetOrders = requiredOrders(requiredContribution, contribution);
    const breakEvenOrders = requiredOrders(operatingCosts, contribution);
    const history = data.chart_history || {};
    const asOfDate = isoDate((history.period || {}).until);
    const now = isoDate(supplied.now === undefined ? new Date().toISOString().slice(0, 10) : supplied.now);
    const sourceMonth = asOfDate ? asOfDate.slice(0, 7) : null;
    const currentMonth = now ? now.slice(0, 7) : null;
    const startMonth = supplied.startMonth === undefined ? [sourceMonth, currentMonth].filter(Boolean).sort().at(-1) || null : supplied.startMonth;
    const endMonth = target.date ? target.date.slice(0, 7) : null;
    const periods = target.date && now && target.date < now ? [] : monthsBetween(startMonth, endMonth);
    if (!periods.length) missing.push('Horizon de projection invalide, dépassé ou supérieur à 120 mois.');
    const monthlyRows = Array.isArray(history.monthly) ? history.monthly : [];
    const actual = monthlyRows.filter(row => monthIndex(row.period) !== null).slice().sort((a, b) => a.period.localeCompare(b.period)).map(row => ({
      month: row.period, orders: nonnegative(row.orders), revenue: finite(row.revenue),
      estimatedResult: finite(row.estimated_result),
      completeMonth: row.days === monthDays(row.period),
      isActual: true,
    }));
    // A partial current month is never compared as a complete month of sales.
    const completeRows = actual.filter(row => row.completeMonth && sourceMonth && row.month < sourceMonth && (!startMonth || row.month < startMonth)).slice(-3);
    function mean(key) {
      const total = sumKnown(completeRows.map(row => row[key]));
      return completeRows.length && total !== null ? total / completeRows.length : null;
    }
    const baseline = {
      ordersMonthly: round(mean('orders')), revenueMonthly: round(mean('revenue')),
      estimatedResultMonthly: history.result_is_complete === true ? round(mean('estimatedResult')) : null,
      months: completeRows.map(row => row.month),
      sourceLabel: completeRows.length ? 'Moyenne de ' + completeRows.length + ' mois calendaires complets Shopify' : 'Historique mensuel complet insuffisant',
    };
    if (baseline.ordersMonthly === null) warnings.push('Pas de base de commandes suffisante pour tracer une progression indicative.');
    const trajectory = periods.map((month, index) => {
      const progress = periods.length === 1 ? 1 : index / (periods.length - 1);
      const orders = targetOrders === null ? null : baseline.ordersMonthly === null ? (progress === 1 ? targetOrders : null) : Math.ceil(baseline.ordersMonthly + (targetOrders - baseline.ordersMonthly) * progress);
      const remainder = orders !== null && contribution !== null && operatingCosts !== null ? orders * contribution - operatingCosts : null;
      return {
        month, orders,
        revenue: orders !== null && averageOrderRevenue !== null ? round(orders * averageOrderRevenue) : null,
        modeledRemainderBeforeReserve: round(remainder),
        availableAfterReserve: remainder !== null && inputs.stockReserveMonthly !== null ? round(remainder - inputs.stockReserveMonthly) : null,
        isIndicative: true,
      };
    });
    const ageDays = now && asOfDate ? Math.max(0, Math.floor((Date.parse(now) - Date.parse(asOfDate)) / 86400000)) : null;
    if (ageDays !== null && ageDays > 7) warnings.push('Les données commerciales ont plus de 7 jours : actualiser avant de décider.');
    warnings.push(
      'Scénario de volume, pas prévision de ventes : progression linéaire illustrative et hypothèses constantes.',
      'Une commande = un t-shirt au prix de référence, sans remise ni retour. Le CA inclut le port facturé.',
      'Cotisations : montant unitaire déclaré, pas taux fiscal certifié. Impôt personnel et coûts inconnus non intégrés.',
      'La réserve stock est du cash mis de côté en plus des coûts de production déjà déduits, pas une charge comptable supplémentaire.',
      'Le reste calculé est partiel et ne garantit ni bénéfice net, ni trésorerie disponible, ni rémunération personnelle.'
    );
    return {
      target, inputs,
      unit: {
        price, knownUnitCost: round(knownUnitCost), relayContribution: round(relay.contribution), homeContribution: round(home.contribution),
        weightedContribution: round(contribution), averageOrderRevenue: round(averageOrderRevenue),
        reportedAt: isoDate(costs.reported_at),
      },
      monthly: {
        fixedCosts: round(fixedCosts), fixedCostsStatus: metadata.status || 'unknown',
        adBudget: inputs.adBudgetMonthly, additionalCosts: inputs.additionalMonthly, stockReserve: inputs.stockReserveMonthly,
        breakEvenOrders, targetOrders,
        targetRevenue: targetOrders !== null && averageOrderRevenue !== null ? round(targetOrders * averageOrderRevenue) : null,
        requiredContribution: round(requiredContribution),
      },
      baseline, actual, trajectory, startMonth, endMonth, asOfDate, ageDays,
      calculable: targetOrders !== null && periods.length > 0,
      missing, warnings,
      assumptionsLabel: 'Hypothèses modifiables du scénario, pas dépenses observées',
    };
  }
  return { buildModel, DEFAULT_INPUTS, monthsBetween, requiredOrders };
});
