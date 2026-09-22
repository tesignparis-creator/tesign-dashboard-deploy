(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const known = value => typeof value === 'number' && Number.isFinite(value);
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const euro = value => known(value) ? new Intl.NumberFormat('fr-FR', {style:'currency',currency:'EUR',maximumFractionDigits:2}).format(value) : 'Non établi';
  const number = value => known(value) ? new Intl.NumberFormat('fr-FR', {maximumFractionDigits:0}).format(value) : 'Non établi';
  const forecastEuro = value => known(value) ? `≈ ${number(Math.round(value / 100) * 100)} €` : 'Non établi';
  const rowValue = (row, item) => row.isForecast ? item.key === 'orders' ? known(row.orders) ? `≈ ${number(row.orders)}` : 'Non établi' : forecastEuro(row[item.key]) : item.format(row[item.key]);
  const percent = value => known(value) ? `${value > 0 ? '+' : ''}${new Intl.NumberFormat('fr-FR',{maximumFractionDigits:1}).format(value)} %` : '—';
  const shortDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? new Intl.DateTimeFormat('fr-FR',{day:'numeric',month:'short',year:'numeric'}).format(new Date(value+'T12:00:00')) : 'date inconnue';
  const series = [
    {key:'revenue',label:'Chiffre d’affaires',color:'#315f4d',format:euro},
    {key:'contribution_margin',label:'Marge estimée',color:'#be995d',format:euro},
    {key:'orders',label:'Commandes',color:'#666bbb',format:number}
  ];
  const selected = new Set(series.map(s => s.key));
  let model, plotRows=[];
  function markup() {
    return `<div class="annual-intro"><div><span class="vision-tag">Depuis le début de TESIGN</span><h2>TESIGN, en un coup d’œil.</h2><p id="annualCoverage">Chargement de l’historique…</p></div></div>
      <div id="annualTotals" class="summary-metrics annual-totals" aria-live="polite"></div>
      <div id="annualFinancialNote" class="annual-financial-note"></div>
      <section class="annual-panel" aria-labelledby="annualChartTitle"><div class="annual-heading"><div><h3 id="annualChartTitle">L’évolution, année après année</h3><p>Chaque année : ce que TESIGN vend, le nombre de commandes et la marge estimée.</p></div><div class="annual-legend" aria-label="Valeurs affichées">${series.map(s=>`<button type="button" data-annual-series="${s.key}" aria-pressed="true"><i style="--series-color:${s.color}" class="${s.key==='orders'?'line':''}"></i>${s.label}</button>`).join('')}</div></div>
      <p class="annual-axes">Barres : euros, à gauche · Courbe : commandes, à droite · * année partielle · Hachures : repère historique pour 2027</p>
      <div class="annual-chart-scroll"><div id="annualChart"></div></div>
      <p class="annual-mobile-hint">Fais glisser le graphique pour voir toutes les années, jusqu’à 2027 →</p>
      <div id="annualReadout" class="annual-readout" aria-live="polite"></div>
      <div id="annualForecastNote" class="annual-forecast-note"></div>
      <div class="annual-table-scroll"><table id="annualTable" class="annual-table"><caption>Montants annuels et évolution par rapport à l’année précédente</caption></table></div>
      <p class="annual-comparison-note">Les pourcentages comparent des périodes identiques. Une année en cours n’est pas comparée à une année entière.</p></section>
      <details class="annual-method"><summary>Que signifient ces chiffres ?</summary><p><b>CA :</b> valeur des commandes Shopify après ajustements et remboursements, livraison incluse. Ce n’est pas le solde bancaire.</p><p><b>Commandes :</b> commandes retenues par Shopify dans l’historique, y compris celles remboursées ensuite.</p><p><b>Marge estimée :</b> CA moins les coûts variables renseignés (production, frais, emballage, livraison et cotisations selon le modèle applicable à la vente). Elle est calculée avant publicité, abonnements et autres charges : ce n’est pas le bénéfice net. Les coûts historiques restent à confirmer.</p><p>Les totaux utilisent tout l’historique Shopify disponible, indépendamment du filtre de dates des détails. « Non établi » signifie qu’une donnée manque ; ce n’est pas zéro.</p></details>`;
  }
  function init() {
    $('view-overview').innerHTML = markup();
    document.querySelectorAll('[data-annual-series]').forEach(button => button.addEventListener('click', () => {
      const key=button.dataset.annualSeries;
      if(selected.has(key)) { if(selected.size===1)return; selected.delete(key); } else selected.add(key);
      button.setAttribute('aria-pressed',String(selected.has(key)));
      if(model)draw();
    }));
  }
  function growth(row,key) {
    const value=row.comparison?.pct?.[key];
    return `<small class="annual-growth ${known(value)?value<0?'negative':value>0?'positive':'':''}">${safe(percent(value))}${known(value)?' vs '+safe(row.comparison.previousYear):''}</small>`;
  }
  function readout(index) {
    const row=plotRows[index];if(!row)return;
    $('annualReadout').innerHTML=`<strong>${safe(row.year)}${row.isForecast?' · repère historique':row.isPartial?' · année partielle':''}</strong><div>${series.map(s=>`<span><i style="background:${s.color}"></i>${s.label} <b>${safe(rowValue(row,s))}</b> <small>${row.isForecast?'':safe(percent(row.comparison?.pct?.[s.key]))}</small></span>`).join('')}</div><small>${safe(row.isForecast?'Repère fondé sur les trois dernières années, sans croissance supplémentaire supposée. Ce n’est pas une prévision de ventes.':row.comparison?.label || 'Comparaison non disponible')}</small>`;
  }
  function draw() {
    const rows=plotRows, visible=series.filter(s=>selected.has(s.key)), money=visible.filter(s=>s.key!=='orders');
    if(!rows.length || !rows.some(r=>visible.some(s=>known(r[s.key])))) {
      $('annualChart').innerHTML='<p class="chart-empty">Historique indisponible pour ces valeurs.</p>';
      $('annualReadout').textContent='';return;
    }
    const W=1080,H=365,L=88,R=68,T=42,B=48;
    const values=rows.flatMap(r=>money.map(s=>r[s.key])).filter(known);
    const low=Math.min(0,...values), rawHigh=Math.max(0,...values);
    const magnitude=Math.pow(10,Math.floor(Math.log10(Math.max(rawHigh,Math.abs(low),1))));
    const high=Math.ceil(Math.max(rawHigh,1)/magnitude)*magnitude;
    const bottom=low<0?-Math.ceil(Math.abs(low)/magnitude)*magnitude:0;
    const countMax=Math.max(4,Math.ceil(Math.max(0,...rows.map(r=>known(r.orders)?r.orders:0))/4/10)*40);
    const plot=H-T-B,step=(W-L-R)/rows.length;
    const y=v=>T+(high-v)/(high-bottom)*plot,cy=v=>T+(countMax-v)/countMax*plot,x=i=>L+step*(i+.5);
    let svg=`<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="CA, marge estimée et commandes par année, puis repère historique pour 2027"><defs>${money.map(s=>`<pattern id="forecast-${s.key}" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(35)"><rect width="7" height="7" fill="${s.color}" fill-opacity=".1"/><line y2="7" stroke="${s.color}" stroke-width="2"/></pattern>`).join('')}</defs><text x="${L}" y="19" fill="#58675c" font-size="13">${money.length?'EUROS':''}</text><text x="${W-R}" y="19" text-anchor="end" fill="#666bbb" font-size="13">${selected.has('orders')?'COMMANDES':''}</text>`;
    rows.forEach((r,i)=>{if(r.isForecast)svg+=`<rect x="${L+step*i}" y="${T-20}" width="${step}" height="${plot+45}" fill="#fbf7eb"/><line x1="${L+step*i}" x2="${L+step*i}" y1="${T-20}" y2="${T+plot+30}" stroke="#bda782" stroke-dasharray="4 5"/><text x="${x(i)}" y="${T-7}" text-anchor="middle" fill="#856b41" font-size="11">REPÈRE HISTORIQUE</text>`});
    for(let i=0;i<=4;i++){
      const yy=T+plot*i/4,v=high-(high-bottom)*i/4;
      svg+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#e8ebe5"/>`;
      if(money.length)svg+=`<text x="${L-12}" y="${yy+4}" text-anchor="end" fill="#687168" font-size="13">${safe(number(v))} €</text>`;
      if(selected.has('orders'))svg+=`<text x="${W-R+12}" y="${yy+4}" fill="#666bbb" font-size="13">${number(countMax*(1-i/4))}</text>`;
    }
    rows.forEach((row,i)=>{
      const width=Math.min(45,step*.19);
      money.forEach((s,j)=>{
        const v=row[s.key];if(!known(v))return;
        const xx=x(i)+(j-(money.length-1)/2)*(width+7);
        svg+=`<rect x="${xx-width/2}" y="${Math.min(y(v),y(0))}" width="${width}" height="${Math.max(1,Math.abs(y(v)-y(0)))}" rx="3" fill="${row.isForecast?'url(#forecast-'+s.key+')':s.color}" ${row.isForecast?`stroke="${s.color}" stroke-dasharray="4 3"`:''} opacity="${row.isPartial?.72:1}"/><text x="${xx}" y="${v>=0?y(v)-9:y(v)+18}" text-anchor="middle" fill="${s.color}" font-size="12" font-weight="600">${safe(row.isForecast?forecastEuro(v):number(v)+' €')}</text>`;
      });
      svg+=`<text x="${x(i)}" y="${H-18}" text-anchor="middle" fill="#343f37" font-size="15" font-weight="600">${safe(row.year)}${row.isPartial?'*':''}</text>`;
    });
    if(selected.has('orders')){
      let path='';rows.forEach((r,i)=>{if(!known(r.orders)||r.isForecast){path+=' ';return;}const prev=i>0&&known(rows[i-1].orders)&&!rows[i-1].isForecast;path+=`${prev?'L':'M'}${x(i)},${cy(r.orders)} `;});
      svg+=`<path d="${path}" fill="none" stroke="#666bbb" stroke-width="2.5"/>`;
      rows.forEach((r,i)=>{if(r.isForecast&&i>0&&known(r.orders)&&known(rows[i-1].orders))svg+=`<path d="M${x(i-1)},${cy(rows[i-1].orders)} L${x(i)},${cy(r.orders)}" fill="none" stroke="#666bbb" stroke-width="2.5" stroke-dasharray="5 6"/>`});
      rows.forEach((r,i)=>{if(known(r.orders))svg+=`<circle cx="${x(i)}" cy="${cy(r.orders)}" r="5" fill="#666bbb" stroke="white" stroke-width="2"/><text x="${x(i)+10}" y="${cy(r.orders)+19}" fill="#55599b" font-size="13" font-weight="700">${r.isForecast?'≈ ':''}${number(r.orders)}</text>`});
    }
    rows.forEach((r,i)=>{const label=`${r.year}${r.isForecast?' (repère historique, pas une prévision de ventes)':r.isPartial?' (partiel)':''} : ${series.map(s=>`${s.label} ${rowValue(r,s)}`).join(', ')}`;svg+=`<rect class="annual-hit" data-annual-index="${i}" x="${L+step*i}" y="${T-20}" width="${step}" height="${plot+50}" fill="transparent" tabindex="0" role="button" aria-label="${safe(label)}"><title>${safe(label)}</title></rect>`;});
    $('annualChart').innerHTML=svg+'</svg>';
    const focus=event=>{const hit=event.target.closest('[data-annual-index]');if(hit)readout(Number(hit.dataset.annualIndex))};
    $('annualChart').onpointerover=focus;$('annualChart').onfocusin=focus;$('annualChart').onclick=focus;
    $('annualChart').onkeydown=event=>{
      const hit=event.target.closest('[data-annual-index]');if(!hit)return;
      if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const i=Math.max(0,Math.min(rows.length-1,Number(hit.dataset.annualIndex)+(event.key==='ArrowLeft'?-1:1)));$('annualChart').querySelector(`[data-annual-index="${i}"]`).focus();}
    };
    readout(Math.max(0,model.years.length-1));
  }
  function render(data,trajectory) {
    model=globalThis.TesignAnnualSummary.buildModel(data);
    const forecast=globalThis.TesignAnnualForecast.buildForecast(data,model,trajectory,2027);
    const showForecast=!model.years.some(r=>Number(r.year)>=2027);
    plotRows=[...model.years,...(showForecast?[forecast]:[])];
    const t=model.totals,c=model.coverage,f=model.financial,b=f.bank;
    $('annualCoverage').textContent=`Du ${shortDate(c.since)} au ${shortDate(c.until)} · ${c.shopifyComplete?'tout l’historique Shopify disponible':'historique disponible, couverture à confirmer'}`;
    $('annualTotals').innerHTML=[
      {label:'CA total',value:euro(t.revenue),note:'Depuis le début · livraison incluse',key:'revenue'},
      {label:'Commandes totales',value:number(t.orders),note:'Depuis le début de TESIGN',key:'orders'},
      {label:'Marge avant publicité',value:euro(t.contribution_margin),note:`Estimée · avant charges fixes${known(t.margin_rate)?' · '+new Intl.NumberFormat('fr-FR',{maximumFractionDigits:1}).format(t.margin_rate)+' % du CA':''}`,key:'margin'},
      {label:'Résultat cumulé estimé',value:euro(f.result),note:'Après publicité et charges renseignées · partiel',key:'result',tone:known(f.result)?f.result<0?'negative':f.result>0?'positive':'':''},
      {label:'Dépenses Meta observées',value:euro(f.adSpend),note:f.metaPartial?'Historique incomplet · dépenses connues seulement':'Depuis le début · dépenses constatées',key:'advertising'},
      {label:b.count>1?'Soldes bancaires datés':'Solde bancaire daté',value:euro(b.balance),note:known(b.balance)?`Au ${shortDate(b.recordedAt)} · ${b.datedSnapshot?'à actualiser':'vérifier avant utilisation'}`:'À vérifier dans Gestion & sources',key:'bank'}
    ].map(s=>`<article class="summary-metric annual-total-${s.key} ${s.tone||''}"><span class="label">${s.label}</span><strong>${safe(s.value)}</strong><small>${safe(s.note)}</small></article>`).join('');
    const financialRows=[['Marge avant publicité',f.components.margin],['Publicité Meta observée',f.components.advertising],['Charges fixes estimées',f.components.fixed],['Autres charges renseignées',f.components.other],['Commission confirmée déduite',f.components.commission]];
    $('annualFinancialNote').innerHTML=`<p><strong>${known(f.result)?f.result<0?'TESIGN reste déficitaire sur les postes connus.':'Résultat estimé sur les postes connus.':'Le résultat cumulé ne peut pas encore être établi.'}</strong> La marge avant publicité n’est pas un bénéfice ; le solde bancaire est un relevé à sa date.</p><details><summary>Voir le calcul du résultat et ce qui manque</summary><dl>${financialRows.map(([label,value],i)=>`<div><dt>${safe(label)}</dt><dd>${i>0&&known(value)?'− ':''}${safe(euro(value))}</dd></div>`).join('')}<div class="result-line"><dt>Résultat partiel estimé</dt><dd>${safe(euro(f.result))}</dd></div></dl><p>Coûts historiques estimés, à rapprocher des factures. ${f.metaPartial?`Dépenses Meta connues ${f.metaSince?'à partir du '+safe(shortDate(f.metaSince)): 'sur une partie de l’historique'} : la publicité manquante reste exclue du calcul. `:''}${f.commissionExcluded?'Commission non confirmée exclue. ':''}Les cotisations sont déjà comprises dans la marge. Ce résultat n’est ni un bénéfice net certifié ni la trésorerie disponible.</p></details>`;
    $('annualTable').innerHTML='<caption>Montants annuels et évolution par rapport à l’année précédente</caption><thead><tr><th scope="col">Année</th><th scope="col">CA</th><th scope="col">Commandes</th><th scope="col">Marge estimée</th></tr></thead><tbody>'+plotRows.map(r=>`<tr class="${r.isForecast?'forecast-row':''}"><th scope="row"><b>${safe(r.year)}${r.isForecast?' · Repère historique':r.isPartial?'*':''}</b><small>${safe(r.isForecast?'Pas une prévision de ventes':r.comparison?.label || 'Première période disponible')}</small></th><td>${safe(rowValue(r,series[0]))}${r.isForecast?'':growth(r,'revenue')}</td><td>${safe(rowValue(r,series[2]))}${r.isForecast?'':growth(r,'orders')}</td><td>${safe(rowValue(r,series[1]))}${r.isForecast?'':growth(r,'contribution_margin')}</td></tr>`).join('')+'</tbody>';
    $('annualForecastNote').hidden=!showForecast;
    const range=forecast.range||{};
    $('annualForecastNote').innerHTML=`<strong>2027 · Un repère historique, pas une prévision de ventes</strong><p>${forecast.complete?`Repère central : <b>${forecastEuro(forecast.revenue)} de CA</b>. Repères historiques bas / haut : <b>${forecastEuro(range.revenue?.low)} à ${forecastEuro(range.revenue?.high)}</b>.`:'Calcul incomplet : '+safe((forecast.missing||[]).join(' '))}</p><p>Moyenne des trois dernières années : les années complètes et le rythme de l’année en cours ramené sur douze mois. Aucune croissance supplémentaire n’est supposée. Les montants sont arrondis à 100 € près ; les repères bas et haut reflètent la variation passée, pas des ventes garanties.</p><details><summary>Voir le calcul et les limites</summary><div id="annualForecastBasis"></div><p>2023 reste dans le graphique, mais son année de lancement incomplète est exclue de la projection. L’annualisation ne prévoit pas la saisonnalité. La publicité, les nouveautés, les prix et les stocks futurs peuvent changer les ventes ; leurs effets ne sont pas chiffrés ici. La marge projetée reprend les coûts historiques estimés.</p></details><button type="button" id="annualEditForecast">Voir mon objectif séparément dans Cap 2027 ↗</button>`;
    $('annualForecastBasis').innerHTML=`<p>${safe(forecast.methodLabel||'Moyenne historique sur trois années, dont la dernière annualisée.')}</p><ul>${(forecast.basis||[]).map(b=>`<li>${safe(b.year)}${b.annualized?' annualisée':''} : ${safe(euro(b.normalized?.revenue))} de CA · ${safe(number(b.normalized?.orders))} commandes · ${safe(euro(b.normalized?.contribution_margin))} de marge estimée${b.annualized?` (observé × ${b.calendarDays} ÷ ${b.days} jours)`:''}.</li>`).join('')}</ul>`;
    $('annualEditForecast').onclick=()=>globalThis.TesignVision.selectPane('future',true);
    draw();
  }
  globalThis.TesignAnnualView={init,render};
})();
