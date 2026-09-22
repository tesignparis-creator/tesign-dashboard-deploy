/* Historical annual scenario. Goals never enter this calculation. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.TesignAnnualForecast = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const METRICS = ['revenue', 'orders', 'contribution_margin'];
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const round = value => finite(value) ? Math.round((value + Number.EPSILON) * 100) / 100 : null;
  const empty = () => Object.fromEntries(METRICS.map(key => [key, null]));
  function isoDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const date = new Date(value + 'T00:00:00Z');
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : null;
  }
  const daysBetween = (start, end) => Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;
  // data and trajectoryModel are accepted for the shared dashboard API. Neither goals
  // nor the current unit economics can overwrite the annual historical evidence.
  function buildForecast(data, annualModel, trajectoryModel, year = 2027) {
    const annual = annualModel || {}, coverage = annual.coverage || {};
    const validYear = Number.isInteger(year) && year >= 1000 && year <= 9999;
    const missing = [];
    const result = {
      year: validYear ? year : null, isForecast: true, kind: 'historical_scenario',
      label: 'Projection historique ' + (validYear ? year : 'annuelle'),
      methodLabel: 'Moyenne des 3 dernières années, année en cours ramenée à une année complète',
      source: 'Historique annuel Shopify ; marge issue des coûts historiques renseignés ou estimés.',
      assumptionsLabel: 'Projection indicative fondée sur l’historique, pas ventes garanties',
      assumptions: [
        'Poids égal pour chacune des trois dernières années ; l’année de lancement incomplète est exclue.',
        'Pour l’année en cours : montant observé × nombre de jours de l’année ÷ jours couverts depuis le 1er janvier.',
        'Cette annualisation suppose un rythme constant ; elle ne corrige ni saisonnalité ni pics de ventes.',
        'La fourchette est le minimum et le maximum des trois repères historiques, pas un intervalle de confiance.',
        'La marge reste une estimation avant publicité et charges fixes ; elle n’est pas un bénéfice net.',
        'L’objectif de revenu personnel et le budget futur n’entrent pas dans ces calculs.',
      ],
      basis: [], range: Object.fromEntries(METRICS.map(key => [key, { low: null, high: null }])),
      ...empty(), margin_rate: null, complete: false, missing,
      asOfDate: isoDate(coverage.until),
    };
    if (!validYear || !result.asOfDate || year !== Number(result.asOfDate.slice(0, 4)) + 1) {
      missing.push('La projection annuelle doit porter sur l’année qui suit la dernière période observée.');
      return result;
    }
    const rows = Array.isArray(annual.years) ? annual.years : [];
    const neededYears = [year - 3, year - 2, year - 1];
    let validCoverage = true;
    for (const historicalYear of neededYears) {
      const matches = rows.filter(row => row && row.year === historicalYear);
      const row = matches.length === 1 ? matches[0] : null;
      const lastYear = historicalYear === year - 1;
      const start = historicalYear + '-01-01';
      const end = lastYear ? result.asOfDate : historicalYear + '-12-31';
      const expectedDays = daysBetween(start, end);
      const calendarDays = daysBetween(start, historicalYear + '-12-31');
      const coverageValid = !!row && isoDate(row.since) === start && isoDate(row.until) === end &&
        row.dataComplete === true && row.days === expectedDays &&
        (lastYear || row.isPartial === false);
      const factor = coverageValid ? calendarDays / expectedDays : null;
      const basis = {
        year: historicalYear, since: row ? row.since : null, until: row ? row.until : null,
        days: coverageValid ? expectedDays : null, calendarDays,
        annualized: coverageValid && expectedDays !== calendarDays,
        factor, coverageComplete: coverageValid,
        actual: row ? Object.fromEntries(METRICS.map(key => [key, finite(row[key]) ? row[key] : null])) : empty(),
        normalized: empty(),
      };
      if (!coverageValid) {
        validCoverage = false;
        missing.push(historicalYear + ' : année complète ou couverture depuis janvier absente, dupliquée ou incomplète.');
      } else {
        for (const key of METRICS) {
          if (finite(row[key]) && (key === 'contribution_margin' || row[key] >= 0)) {
            basis.normalized[key] = round(row[key] * factor);
          }
        }
      }
      result.basis.push(basis);
    }
    if (!validCoverage) return result;
    for (const key of METRICS) {
      const values = result.basis.map(row => row.normalized[key]);
      if (!values.every(finite)) {
        missing.push((key === 'revenue' ? 'CA' : key === 'orders' ? 'Commandes' : 'Marge') + ' : données historiques insuffisantes pour les trois années.');
        continue;
      }
      // Average the unrounded normalized inputs; round only the displayed forecast.
      const exact = result.basis.map(row => row.actual[key] * row.factor);
      const average = exact.reduce((sum, value) => sum + value, 0) / exact.length;
      result[key] = key === 'orders' ? Math.round(average) : round(average);
      result.range[key] = {
        low: key === 'orders' ? Math.round(Math.min(...exact)) : round(Math.min(...exact)),
        high: key === 'orders' ? Math.round(Math.max(...exact)) : round(Math.max(...exact)),
      };
    }
    result.margin_rate = result.revenue > 0 && finite(result.contribution_margin)
      ? round(result.contribution_margin / result.revenue * 100) : null;
    result.complete = METRICS.every(key => finite(result[key]));
    return result;
  }
  return { buildForecast };
});
