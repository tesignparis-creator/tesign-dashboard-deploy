/* Annual Shopify summary. Unknown amounts stay unknown; margin is an estimate. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.TesignAnnualSummary = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const METRICS = Object.freeze(['revenue', 'orders', 'contribution_margin']);
  const finite = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const round = value => finite(value) === null ? null : Math.round((value + Number.EPSILON) * 100) / 100;
  const empty = () => Object.fromEntries(METRICS.map(key => [key, null]));
  function isoDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const date = new Date(value + 'T00:00:00Z');
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : null;
  }
  const daysBetween = (since, until) => Math.round((Date.parse(until) - Date.parse(since)) / 86400000) + 1;
  const withRate = values => ({
    ...values,
    margin_rate: values.revenue > 0 && finite(values.contribution_margin) !== null
      ? round(values.contribution_margin / values.revenue * 100) : null,
  });
  function sumRows(rows) {
    return Object.fromEntries(METRICS.map(key => [key,
      rows.length && rows.every(row => finite(row[key]) !== null)
        ? round(rows.reduce((sum, row) => sum + row[key], 0)) : null,
    ]));
  }
  function percentChange(current, previous) {
    // A zero or negative baseline has no meaningful conventional growth rate.
    return finite(current) !== null && finite(previous) !== null && previous > 0
      ? round((current - previous) / previous * 100) : null;
  }
  function compare(current, previous) {
    return {
      pct: Object.fromEntries(METRICS.map(key => [key, percentChange(current[key], previous[key])])),
      delta: Object.fromEntries(METRICS.map(key => [key,
        finite(current[key]) !== null && finite(previous[key]) !== null
          ? round(current[key] - previous[key]) : null,
      ])),
      percentReasons: Object.fromEntries(METRICS.map(key => [key,
        finite(current[key]) === null || finite(previous[key]) === null ? 'Donnée indisponible.'
          : previous[key] === 0 ? 'Base précédente nulle : évolution en euros ou en commandes uniquement.'
            : previous[key] < 0 ? 'Base précédente négative : variation absolue uniquement.' : null,
      ])),
    };
  }
  function unavailable(reason, previousYear = null) {
    return {
      basis: 'unavailable', label: reason, reason, previousYear,
      current: empty(), previous: empty(), pct: empty(), delta: empty(),
      percentReasons: Object.fromEntries(METRICS.map(key => [key, reason])),
    };
  }
  function dailyIndex(data) {
    const history = data.trend_history || {};
    const period = history.period || {};
    const since = isoDate(period.since), until = isoDate(period.until);
    const index = new Map();
    for (const row of Array.isArray(history.daily) ? history.daily : []) {
      const date = isoDate(row.date);
      if (!date || !since || !until || date < since || date > until) continue;
      // Duplicate dates cannot silently double count a comparison period.
      index.set(date, index.has(date) ? null : row);
    }
    return { since, until, index };
  }
  function dailyTotals(source, since, until) {
    if (!source.since || !source.until || source.since > since || source.until < until) return null;
    const rows = [];
    for (let day = Date.parse(since); day <= Date.parse(until); day += 86400000) {
      const row = source.index.get(new Date(day).toISOString().slice(0, 10));
      if (!row) return null;
      rows.push(row);
    }
    return sumRows(rows);
  }
  function priorDate(date) {
    const year = Number(date.slice(0, 4)) - 1;
    const candidate = year + date.slice(4);
    // February 29 is compared with the last day of February in a non-leap year.
    return isoDate(candidate) || (date.slice(5) === '02-29' ? year + '-02-28' : null);
  }
  function formatDay(date) { return date.slice(8) + '/' + date.slice(5, 7); }
  function annualSource(history, since, until) {
    if (!since || !until || since > until) return [];
    const annual = Array.isArray(history.annual) ? history.annual : [];
    const monthly = Array.isArray(history.monthly) ? history.monthly : [];
    const rows = [];
    for (let year = Number(since.slice(0, 4)); year <= Number(until.slice(0, 4)); year++) {
      const start = since > year + '-01-01' ? since : year + '-01-01';
      const end = until < year + '-12-31' ? until : year + '-12-31';
      const matches = annual.filter(row => String(row.period || row.date || '').slice(0, 4) === String(year));
      let values = empty(), days = null;
      if (matches.length === 1) {
        values = Object.fromEntries(METRICS.map(key => [key, finite(matches[0][key])]));
        days = finite(matches[0].days);
      } else if (!matches.length) {
        const buckets = monthly.filter(row => {
          const month = String(row.period || row.date || '').slice(0, 7);
          return /^\d{4}-\d{2}$/.test(month) && month >= start.slice(0, 7) && month <= end.slice(0, 7);
        });
        const expectedMonths = Number(end.slice(5, 7)) - Number(start.slice(5, 7)) + 1;
        const months = new Set(buckets.map(row => String(row.period || row.date).slice(0, 7)));
        if (months.size === expectedMonths && buckets.length === expectedMonths) {
          values = sumRows(buckets);
          days = buckets.every(row => finite(row.days) !== null)
            ? buckets.reduce((sum, row) => sum + row.days, 0) : null;
        }
      }
      const expectedDays = daysBetween(start, end);
      const dataComplete = days === null ? null : days === expectedDays;
      if (dataComplete === false) values = empty();
      rows.push({
        year, since: start, until: end,
        isPartial: start !== year + '-01-01' || end !== year + '-12-31',
        isCurrent: year === Number(until.slice(0, 4)),
        dataComplete, days, expectedDays, ...withRate(values), yoy: empty(),
      });
    }
    return rows;
  }
  function buildModel(data) {
    data = data || {};
    const history = data.chart_history || {};
    const period = history.period || {};
    const since = isoDate(period.since), until = isoDate(period.until);
    const years = annualSource(history, since, until);
    const daily = dailyIndex(data);
    years.forEach((row, index) => {
      const previous = years[index - 1];
      row.comparison = unavailable(previous ? 'Périodes annuelles incomplètes : comparaison indisponible.' : 'Première année disponible.', previous ? previous.year : null);
      if (!previous || previous.year !== row.year - 1) return;
      if (!row.isPartial && !previous.isPartial) {
        const currentValues = Object.fromEntries(METRICS.map(key => [key, row[key]]));
        const previousValues = Object.fromEntries(METRICS.map(key => [key, previous[key]]));
        row.comparison = {
          basis: 'full_year', label: 'Année complète, par rapport à ' + previous.year,
          previousYear: previous.year, since: row.since, until: row.until,
          previousSince: previous.since, previousUntil: previous.until,
          current: currentValues, previous: previousValues, reason: null,
          ...compare(currentValues, previousValues),
        };
        row.yoy = { ...row.comparison.pct };
      } else if (row.isCurrent && row.since === row.year + '-01-01') {
        const previousSince = previous.year + '-01-01', previousUntil = priorDate(row.until);
        const currentValues = dailyTotals(daily, row.since, row.until);
        const previousValues = previousUntil && dailyTotals(daily, previousSince, previousUntil);
        if (!currentValues || !previousValues) {
          row.comparison = unavailable('Historique quotidien insuffisant pour comparer à la même date.', previous.year);
          return;
        }
        row.comparison = {
          basis: 'same_period', label: 'Du 01/01 au ' + formatDay(row.until) + ', par rapport à ' + previous.year,
          previousYear: previous.year, since: row.since, until: row.until,
          previousSince, previousUntil, current: currentValues, previous: previousValues, reason: null,
          ...compare(currentValues, previousValues),
        };
      }
    });
    const totalsSource = history.totals;
    const totals = totalsSource && typeof totalsSource === 'object'
      ? Object.fromEntries(METRICS.map(key => [key, finite(totalsSource[key])])) : sumRows(years);
    return {
      totals: withRate(totals), years,
      financial: financialSummary(data),
      coverage: {
        since, until, generatedAt: history.generated_at || null,
        shopifyComplete: history.shopify_history_complete === true,
        costComplete: history.cost_completeness === true,
        source: years.length ? 'chart_history' : 'unavailable',
        missingData: history.missing_data || null,
      },
      marginEstimated: true,
      marginLabel: 'Marge estimée avant publicité et charges fixes',
      marginBasis: 'CA moins coûts variables renseignés ou estimés. Ne correspond pas au bénéfice net.',
      revenueBasis: history.revenue_basis || 'Valeur actuelle des commandes Shopify, port inclus, rattachée à leur date de création.',
    };
  }
  function financialSummary(data) {
    const cumulative = data.cumulative || {}, totals = cumulative.totals || {};
    const historyPeriod = data.chart_history?.period || {}, period = cumulative.period || {};
    const aligned = !!isoDate(period.since) && !!isoDate(period.until) &&
      period.since === historyPeriod.since && period.until === historyPeriod.until;
    const status = cumulative.source_status || {}, metaStatus = status.meta_account?.status;
    // A failed Meta connector can leave an initialized zero in the observed field.
    const metaKnown = aligned && ['available', 'partial'].includes(metaStatus);
    const adSpend = metaKnown ? finite(totals.ad_spend) ?? finite(totals.ad_spend_observed) : null;
    const components = {
      margin: aligned && status.shopify_orders?.status === 'available' ? finite(totals.contribution_margin) : null,
      advertising: adSpend,
      fixed: aligned ? finite(totals.fixed_costs_prorated) : null,
      other: aligned ? finite(totals.business_expenses) : null,
      commission: aligned ? finite(totals.geremy_commission_deducted) : null,
    };
    // Summing daily results drops known costs on dates with unknown advertising.
    // Keep all known components, and explicitly label the remaining scope partial.
    const result = Object.values(components).every(value => value !== null)
      ? round(components.margin - components.advertising - components.fixed - components.other - components.commission) : null;
    const accounts = (Array.isArray(data.financial?.bank_accounts) ? data.financial.bank_accounts : [])
      .filter(a => a.scope === 'business' && !a.is_demo && !/demo|sandbox/i.test(a.source || ''));
    const datedAccounts = accounts.length > 0 && accounts.every(a =>
      finite(a.balance) !== null && a.currency_code === 'EUR' && isoDate(a.recorded_at) &&
      isoDate(historyPeriod.until) && a.recorded_at <= historyPeriod.until);
    const dates = new Set(accounts.map(a => a.recorded_at));
    const bankKnown = datedAccounts && dates.size === 1;
    return {
      result, components, adSpend, asOf: aligned ? period.until : null,
      isPartial: cumulative.is_complete !== true,
      metaPartial: metaStatus === 'partial' || cumulative.meta_history_truncated === true,
      metaSince: isoDate(cumulative.meta_history_available_since) || isoDate(status.meta_account?.available_since),
      commissionExcluded: totals.estimated_result_excludes_unconfirmed_commission === true,
      bank: {
        balance: bankKnown ? round(accounts.reduce((sum, a) => sum + a.balance, 0)) : null,
        recordedAt: bankKnown ? accounts[0].recorded_at : null,
        count: accounts.length,
        datedSnapshot: bankKnown && accounts.some(a => a.stale || a.source === 'manual' || /snapshot$/.test(a.source || '')),
      },
    };
  }
  return { buildModel, financialSummary, percentChange, METRICS };
});
