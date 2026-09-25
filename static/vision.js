(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const known = v => typeof v === 'number' && Number.isFinite(v);
  const eur = (v,precision=2) => known(v) ? new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',minimumFractionDigits:0,maximumFractionDigits:precision}).format(v) : 'Non établi';
  const num = v => known(v) ? new Intl.NumberFormat('fr-FR',{maximumFractionDigits:1}).format(v) : '—';
  const month = value => /^\d{4}-\d{2}$/.test(value || '') ? new Intl.DateTimeFormat('fr-FR',{month:'short',year:'2-digit'}).format(new Date(value+'-01T12:00:00')) : '—';
  const date = value => value && !Number.isNaN(new Date(value).getTime()) ? new Intl.DateTimeFormat('fr-FR',{day:'numeric',month:'long',year:'numeric'}).format(new Date(value)) : 'date non renseignée';
  const key = 'tesign.scenario.v1';
  let data, inputs = {}, currentPane = 'overview';
  try { inputs = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) {}
  function metric(label, value, note, badge, cls='') {
    return `<article class="summary-metric"><span class="label">${safe(label)}${badge?` <span class="small-badge">${safe(badge)}</span>`:''}</span><strong class="${cls}">${safe(value)}</strong><small>${safe(note)}</small></article>`;
  }
  function selectPane(name, focus=false) {
    if (!['overview','history','future','details','enzo'].includes(name)) name='overview';
    currentPane=name;
    document.querySelectorAll('header .controls label').forEach(label=>label.hidden=name!=='details');
    if($('period'))$('period').hidden=name!=='details';
    document.querySelectorAll('.vision-pane').forEach(p => {p.hidden=p.id!==`view-${name}`});
    document.querySelectorAll('.vision-nav button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===name)));
    document.querySelector('header .controls').hidden=name==='enzo';
    document.querySelector('main > .status').hidden=name==='enzo';
    $('loadingState').classList.toggle('other-view-loading',name==='enzo');
    if($('qualityDetails'))$('qualityDetails').hidden=name==='enzo';
    if(name==='enzo') {
      const frame=$('enzoFrame');
      if(frame && !frame.hasAttribute('src'))frame.src='/enzo';
      if(location.hash!=='#enzo')history.replaceState(null,'',location.pathname+location.search+'#enzo');
    } else if(location.hash==='#enzo')history.replaceState(null,'',location.pathname+location.search);
    if(focus) {$(`view-${name}`).focus({preventScroll:true}); window.scrollTo({top:0,behavior:'smooth'})}
  }
  function fold(container, title, sections, open=false) {
    const details=document.createElement('details'); details.className='detail-group'; details.open=open;
    const summary=document.createElement('summary'); summary.textContent=title; details.append(summary);
    sections.filter(Boolean).forEach(node=>details.append(node));container.append(details);
  }
  function init() {
    const main=document.querySelector('main'), original=[...main.children];
    const nav=document.createElement('nav');nav.className='vision-nav';nav.setAttribute('aria-label','Vues du tableau de bord');
    nav.innerHTML=[['overview','L’essentiel'],['history','Historique détaillé'],['future','Cap 2027'],['details','Gestion & sources'],['enzo','Enzo']].map(([id,label])=>`<button type="button" data-view="${id}" aria-controls="view-${id}" aria-pressed="${id==='overview'}">${label}</button>`).join('');
    main.before(nav);
    nav.addEventListener('click',event=>{const b=event.target.closest('[data-view]');if(b)selectPane(b.dataset.view)});
    ['overview','history','future','details','enzo'].forEach(name=>{
      const pane=document.createElement('section');pane.id=`view-${name}`;pane.className='vision-pane';pane.tabIndex=-1;pane.hidden=name!=='overview';main.append(pane);
    });
    $('view-enzo').innerHTML='<p class="enzo-intro">Ton ancien espace de travail, avec les données actuelles. <a href="/enzo" target="_blank" rel="noopener">Ouvrir en pleine page ↗</a></p><iframe id="enzoFrame" class="enzo-frame" title="Enzo : Stock, marge et acquisition"></iframe>';
    window.addEventListener('message',event=>{
      const frame=$('enzoFrame'),height=event.data?.height;
      if(event.origin!==location.origin || event.source!==frame.contentWindow || event.data?.type!=='tesign-enzo-height')return;
      if(typeof height==='number' && Number.isFinite(height))frame.style.height=Math.min(50000,Math.max(700,height))+'px';
    });
    $('view-overview').innerHTML=`
      <section class="overview-hero" aria-label="Ambition de TESIGN">
        <div><span class="vision-tag">Le cap de la marque</span><h2>Faire vivre la marque.<br>En vivre, durablement.</h2><p>Construire des ventes régulières, une marge positive et assez de trésorerie pour financer les prochaines pièces.</p></div>
        <div class="hero-target"><span id="visionTargetDate">Objectif de revenu personnel</span><strong id="visionIncome">—</strong><span>pour Enzo, après les dépenses de TESIGN</span><button type="button" id="openTrajectory">Voir le chemin vers l’objectif ↗</button></div>
      </section>
      <div class="section-title"><h2>Où en est TESIGN ?</h2><p id="overviewPeriod">Chargement des données…</p></div>
      <div id="overviewMetrics" class="summary-metrics" aria-live="polite"></div>
      <div class="overview-charts">
        <article class="panel"><h3>Les ventes dans le temps</h3><p class="chart-note">CA Shopify · six derniers mois, mois en cours partiel</p><div id="overviewRevenue" class="chart-visual"></div><p id="overviewRevenueReadout" class="chart-readout">Survolez ou touchez un mois pour lire sa valeur.</p></article>
        <article class="panel"><h3>Ce qui reste après les coûts <span class="small-badge">Estimation partielle</span></h3><p class="chart-note">Après coûts renseignés et publicité · avant coûts inconnus</p><div id="overviewResult" class="chart-visual"></div><p id="overviewResultReadout" class="chart-readout">Survolez ou touchez un mois pour lire sa valeur.</p></article>
      </div>
      <p id="overviewInsight" class="overview-note"></p>
      <div class="section-title"><h2>Trois leviers pour avancer</h2><p>Priorités définies avec Enzo · avancement à documenter</p></div>
      <div class="levers">
        <article class="lever"><span class="step">01 / PRODUIT</span><h3>La production en Turquie</h3><p>Préparer les prochaines pièces avec une qualité maîtrisée et un coût complet connu.</p><small>À suivre : coût livré, réception et stock vendable.</small></article>
        <article class="lever"><span class="step">02 / VISIBILITÉ</span><h3>Développer TikTok</h3><p>Installer un rythme de contenus qui amène des visites et des clients vers TESIGN.</p><small>À suivre : visites, commandes et marge du canal.</small></article>
        <article class="lever"><span class="step">03 / CONVERSION</span><h3>Appliquer LiveMentor</h3><p>Utiliser la formation pour structurer le parcours de découverte, d’achat et de fidélisation.</p><small>À suivre : conversion et ventes répétées.</small></article>
      </div>`;
    const evolution=document.querySelector('.evolution');if(evolution)$('view-history').append(evolution);
    $('view-future').innerHTML=`
      <div class="pane-intro"><span class="vision-tag">Scénario de travail</span><h2 id="trajectoryTitle">Le chemin vers l’autonomie</h2><p>Un objectif traduit en commandes et en chiffre d’affaires. Change les hypothèses pour voir l’effort commercial nécessaire.</p></div>
      <div id="trajectoryMetrics" class="summary-metrics trajectory-metrics" aria-live="polite"></div>
      <div class="trajectory-layout">
        <article class="panel trajectory-plot"><h3>Le rythme de commandes à construire</h3><p class="chart-note" id="trajectoryBaseline"></p><div class="legend"><span><i></i>Commandes observées</span><span><i class="dashed"></i>Trajectoire indicative</span></div><div id="trajectoryChart" class="chart-visual"></div><p id="trajectoryChartReadout" class="chart-readout">Chaque point représente un mois. La ligne pointillée est un scénario, pas une prévision de demande.</p><details class="scenario-detail"><summary>Voir les étapes chiffrées</summary><div class="table-wrap"><table id="trajectoryTable"></table></div></details></article>
        <aside class="panel scenario-inputs"><h3>Les hypothèses du scénario</h3><p>Sauvegardées dans ce navigateur. Aucun changement de budget publicitaire réel.</p>
          <label>Publicité mensuelle (€)<input id="scenarioAds" type="number" min="0" step="10" inputmode="decimal"></label>
          <label>Commandes en point relais (%)<input id="scenarioRelay" type="number" min="0" max="100" step="5" inputmode="decimal"></label>
          <label>Autres charges / commissions (€ par mois)<input id="scenarioAdditional" type="number" min="0" step="10" inputmode="decimal"></label>
          <label>Réserve supplémentaire (€ par mois)<input id="scenarioReserve" type="number" min="0" step="10" inputmode="decimal"></label><small>Cash mis de côté pour développer le stock, en plus des coûts de production déjà déduits.</small><button type="button" id="scenarioReset">Rétablir les hypothèses de départ</button>
        </aside>
      </div>
      <p class="scenario-disclosure" id="scenarioDisclosure"></p>
      <details class="scenario-detail"><summary>Comprendre le calcul et ses limites</summary><div id="scenarioMethod"></div></details>
      <div class="section-title"><h2>Une progression en trois étapes</h2><p>Ordre de travail proposé · pas un calendrier déjà engagé</p></div>
      <div class="roadmap"><article><span class="eyebrow">01 / FIABILISER</span><h3>Connaître la marge réelle</h3><p>Rapprocher le stock, les coûts livrés de la production et les charges. Confirmer les commissions.</p><strong>Repère : un coût complet par pièce vendue.</strong></article><article><span class="eyebrow">02 / RENDRE RÉGULIER</span><h3>Répéter ce qui vend</h3><p>Tester les contenus TikTok et appliquer LiveMentor. Suivre les commandes et la contribution après acquisition.</p><strong id="roadmapBreakEven">Repère : couvrir les charges chaque mois.</strong></article><article><span class="eyebrow">03 / CONSOLIDER</span><h3>Financer un revenu personnel</h3><p>Atteindre le volume cible plusieurs mois de suite et conserver la trésorerie nécessaire aux commandes et au stock.</p><strong id="roadmapTarget">Repère : un revenu durable à l’échéance.</strong></article></div>`;
    // Keep the goal and the roadmap in their own view; lead with the actual history.
    $('view-future').prepend(document.querySelector('.overview-hero'));
    $('view-future').append(document.querySelector('.levers'));
    globalThis.TesignAnnualView.init();
    const detail=$('view-details');detail.innerHTML='<div class="pane-intro"><h2>Gestion & sources</h2><p>Les données détaillées restent disponibles ici : trésorerie, stock, acquisition et hypothèses de calcul.</p></div>';
    const section=id=>$(id)?.closest('section');
    fold(detail,'Trésorerie professionnelle',[section('bankAccounts')],true);
    fold(detail,'Coûts, marge et commissions',[section('costAssumptions')]);
    fold(detail,'Stock & commandes',[section('stockFreshness'),section('stock')]);
    fold(detail,'Ventes & publicité',[section('healthCards'),section('salesCards'),section('metaCards')]);
    fold(detail,'Affiliation & influenceurs',[section('affiliateCards')]);
    fold(detail,'Actions à suivre',[section('todoList')]);
    fold(detail,'Méthode de lecture',[section('aiCoachCards')]);
    const quality=document.createElement('details');quality.className='quality-summary';quality.id='qualityDetails';
    quality.innerHTML='<summary id="qualityLabel">Sources et limites : résultat estimatif, solde bancaire daté, stock à rapprocher.</summary>';
    ['warnings','cards','historyNotice'].forEach(id=>{if($(id))quality.append($(id))});main.append(quality);
    const oldGoal=document.querySelector('.plan-box');if(oldGoal){quality.append(oldGoal);oldGoal.hidden=true}
    original.filter(n=>n.parentElement===main && n.tagName==='SECTION').forEach(n=>{n.hidden=true});
    const footer=document.createElement('footer');footer.className='vision-footer';footer.innerHTML='<span>TESIGN · Une vision, des chiffres, des décisions.</span><span>Consultation des sources en lecture seule.</span>';main.append(footer);
    $('openTrajectory').onclick=()=>selectPane('future',true);
    const defaults=globalThis.TesignTrajectory.DEFAULT_INPUTS;
    const fields={scenarioAds:'adBudgetMonthly',scenarioRelay:'relayShare',scenarioAdditional:'additionalMonthly',scenarioReserve:'stockReserveMonthly'};
    function fillInputs(){for(const [id,k]of Object.entries(fields)){const v=inputs[k]===undefined?defaults[k]:inputs[k];$(id).value=v===null?'':id==='scenarioRelay'?v*100:v}}
    fillInputs();
    Object.entries(fields).forEach(([id,k])=>$(id).addEventListener('input',()=>{
      const value=$(id).value;inputs[k]=value===''?null:Number(value)/(id==='scenarioRelay'?100:1);
      try{localStorage.setItem(key,JSON.stringify(inputs))}catch(_){}
      if(data)render(data);
    }));
    $('scenarioReset').onclick=()=>{inputs={};try{localStorage.removeItem(key)}catch(_){}fillInputs();if(data)render(data)};
    selectPane(location.hash==='#enzo'?'enzo':'overview');
  }
  function drawBars(id,rows,valueKey,signed=false){
    const el=$(id); const vals=rows.map(r=>r[valueKey]).filter(known);
    if(!vals.length){el.innerHTML='<div class="chart-empty">Données insuffisantes pour ce graphique.</div>';return}
    const W=660,H=218,left=62,right=15,top=20,bottom=35,low=Math.min(0,...vals)*1.12,high=Math.max(0,...vals)*1.15||(low===0?1:0);
    const y=v=>top+(high-v)/(high-low)*(H-top-bottom),step=(W-left-right)/rows.length;
    let svg=`<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="${signed?'Résultat mensuel estimé':'CA Shopify mensuel'}">`;
    for(let i=0;i<4;i++){const v=low+(high-low)*i/3,yy=y(v);svg+=`<line x1="${left}" y1="${yy}" x2="${W-right}" y2="${yy}" stroke="#e7ebe3"/><text x="${left-9}" y="${yy+4}" text-anchor="end" fill="#77816f" font-size="10">${safe(eur(v,0))}</text>`}
    rows.forEach((r,i)=>{const v=r[valueKey],x=left+step*(i+.5),w=Math.min(42,step*.5);if(known(v))svg+=`<rect x="${x-w/2}" y="${Math.min(y(0),y(v))}" width="${w}" height="${Math.max(1,Math.abs(y(0)-y(v)))}" rx="3" fill="${signed&&v<0?'#b48067':'#6c8961'}"/>`;
      const label=`${month(r.month)}${r.completeMonth?'':' (partiel)'} : ${eur(v)}${signed?' · estimation partielle':''}`;
      svg+=`<text x="${x}" y="${H-10}" text-anchor="middle" fill="#77816f" font-size="10">${safe(month(r.month))}</text><rect class="chart-hit" data-label="${safe(label)}" x="${left+step*i}" y="${top}" width="${step}" height="${H-top-bottom}" tabindex="0" role="img" aria-label="${safe(label)}"><title>${safe(label)}</title></rect>`});
    el.innerHTML=svg+'</svg>';bindReadout(el,$(id+'Readout'));
  }
  function bindReadout(el,output){
    el.querySelectorAll('[data-label]').forEach(hit=>{for(const event of ['mouseenter','focus','click'])hit.addEventListener(event,()=>{output.textContent=hit.dataset.label})});
  }
  function drawTrajectory(m){
    const el=$('trajectoryChart'), actual=m.actual.filter(r=>r.completeMonth&&r.month<m.startMonth).slice(-3);
    const rows=[...actual.map(r=>({...r,kind:'actual'})),...m.trajectory.map(r=>({...r,kind:'plan'}))];
    if(!m.calculable||!rows.some(r=>known(r.orders))){el.innerHTML='<div class="chart-empty">Compléter les hypothèses pour calculer la trajectoire.</div>';return}
    const W=800,H=280,L=42,R=28,T=24,B=42,max=Math.max(1,...rows.map(r=>r.orders).filter(known))*1.16;
    const x=i=>L+(W-L-R)*i/Math.max(1,rows.length-1),y=v=>H-B-v/max*(H-T-B);
    let s=`<svg viewBox="0 0 ${W} ${H}" role="group" aria-label="Commandes observées et trajectoire indicative">`;
    for(let i=0;i<5;i++){const v=Math.round(max*i/4);s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="#e5e9e0"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" fill="#7a826f" font-size="10">${num(v)}</text>`}
    if(actual.length&&actual.length<rows.length){const split=(x(actual.length-1)+x(actual.length))/2;s+=`<line x1="${split}" x2="${split}" y1="${T}" y2="${H-B}" stroke="#d5dccc" stroke-dasharray="3 4"/>`}
    for(const kind of ['actual','plan']){let path='',segment=false;rows.forEach((r,i)=>{if(r.kind!==kind||!known(r.orders)){segment=false;return}path+=`${segment?'L':'M'}${x(i)},${y(r.orders)} `;segment=true});s+=`<path d="${path}" fill="none" stroke="${kind==='actual'?'#315f4d':'#bd8652'}" stroke-width="2.5" ${kind==='plan'?'stroke-dasharray="5 5"':''}/>`}
    rows.forEach((r,i)=>{if(known(r.orders))s+=`<circle cx="${x(i)}" cy="${y(r.orders)}" r="3.5" fill="${r.kind==='actual'?'#315f4d':'#bd8652'}"/>`;if(i%2===0||i===rows.length-1)s+=`<text x="${x(i)}" y="${H-12}" text-anchor="middle" fill="#78816e" font-size="10">${safe(month(r.month))}</text>`;
      const label=`${month(r.month)} : ${num(r.orders)} commandes · ${r.kind==='actual'?'observées':'scénario indicatif'} · ${eur(r.revenue)} de CA`;
      s+=`<rect class="chart-hit" data-label="${safe(label)}" x="${Math.max(0,x(i)-20)}" y="${T}" width="40" height="${H-T-B}" role="img" tabindex="0" aria-label="${safe(label)}"><title>${safe(label)}</title></rect>`});
    el.innerHTML=s+'</svg>';bindReadout(el,$('trajectoryChartReadout'));
  }
  function renderFuture(m){
    $('trajectoryTitle').textContent=`Viser ${eur(m.target.incomeMonthly)} par mois d’ici ${date(m.target.date)}`;
    $('trajectoryMetrics').innerHTML=[
      metric('Seuil de commandes / mois',m.calculable?num(m.monthly.targetOrders):'Non établi','Seuil calculé selon les hypothèses, pas une prévision de ventes','Scénario'),
      metric('CA mensuel correspondant',m.calculable?eur(m.monthly.targetRevenue):'Non établi','Une commande = un t-shirt ; port inclus'),
      metric('Contribution par commande',eur(m.unit.weightedContribution),'Après coûts unitaires et livraison, avant pub et fixes')].join('');
    $('trajectoryBaseline').textContent=`Base : ${num(m.baseline.ordersMonthly)} commandes / mois. ${m.baseline.sourceLabel}${m.baseline.months.length?` (${m.baseline.months.map(month).join(', ')})`:''}.`;
    drawTrajectory(m);
    $('scenarioDisclosure').textContent=m.missing.length?m.missing.join(' '):`Le seuil de commandes est calculé selon les hypothèses ci-dessus : ce n’est pas une prévision de ventes. Charges fixes : ${eur(m.monthly.fixedCosts)}/mois, montant configuré à confirmer. Les commissions non renseignées, l’impôt personnel et les besoins réels de trésorerie peuvent relever l’objectif. Une réserve à 0 € n’établit pas l’absence de besoin.`;
    $('scenarioMethod').innerHTML=`<p>Commandes cibles = (objectif personnel + charges fixes + publicité + charges complémentaires + réserve) ÷ contribution par commande, arrondi au supérieur. Coûts unitaires déclarés le ${safe(date(m.unit.reportedAt))}.</p><ul>${m.warnings.map(w=>`<li>${safe(w)}</li>`).join('')}</ul>`;
    $('trajectoryTable').innerHTML='<thead><tr><th>Mois</th><th>Commandes visées</th><th>CA indicatif</th><th>Reste partiel après réserve</th></tr></thead><tbody>'+m.trajectory.map(r=>`<tr><td>${safe(month(r.month))}</td><td>${num(r.orders)}</td><td>${eur(r.revenue)}</td><td>${eur(r.availableAfterReserve)}</td></tr>`).join('')+'</tbody>';
    $('roadmapBreakEven').textContent=`Repère du scénario : ${num(m.monthly.breakEvenOrders)} commandes/mois pour couvrir les charges incluses, hors revenu et réserve.`;
    $('roadmapTarget').textContent=`Seuil calculé selon les hypothèses : ${num(m.monthly.targetOrders)} commandes/mois à l’échéance. Pas une prévision de ventes ; trésorerie à vérifier.`;
  }
  function render(d){
    data=d;const m=globalThis.TesignTrajectory.buildModel(d,inputs);
    $('visionTargetDate').textContent=`Objectif au ${date(m.target.date)}`;
    $('visionIncome').innerHTML=`${safe(eur(m.target.incomeMonthly))} <small>/ mois</small>`;
    globalThis.TesignAnnualView.render(d,m);
    $('qualityLabel').textContent=`Sources & limites · résultat partiel · solde bancaire daté · stock à rapprocher · ${$('warnings').children.length} points de lecture`;
    renderFuture(m);
  }
  init();
  globalThis.TesignVision={render,selectPane};
})();
