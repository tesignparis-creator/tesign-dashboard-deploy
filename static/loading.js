(function (root) {
  'use strict';
  function create({ render, since, until, refresh, error }) {
    const panel = document.getElementById('loadingState');
    const title = document.getElementById('loadingTitle');
    const message = document.getElementById('loadingMessage');
    const retry = document.getElementById('loadingRetry');
    let pending = false, hasData = false;
    async function load(force = false) {
      if (pending) return;
      pending = true;
      error.hidden = true;
      panel.hidden = false;
      panel.dataset.state = 'loading';
      title.textContent = hasData ? 'Actualisation des chiffres…' : 'Chargement de tes chiffres…';
      message.textContent = hasData
        ? 'Les derniers chiffres restent affichés pendant la mise à jour.'
        : 'Nous récupérons les ventes, les dépenses publicitaires et l’historique de TESIGN. Les chiffres et les graphiques vont apparaître ici.';
      retry.hidden = true;
      refresh.disabled = true;
      refresh.textContent = 'Chargement…';
      const controller = new AbortController();
      const slow = setTimeout(() => {
        message.textContent = 'Le chargement prend plus de temps que prévu. Il continue automatiquement : laisse cette page ouverte, sans la recharger.';
      }, 12000);
      const timeout = setTimeout(() => controller.abort(), 180000);
      try {
        const path = force ? '/api/refresh' : '/api/dashboard';
        const response = await fetch(`${path}?since=${since.value}&until=${until.value}`, { signal: controller.signal });
        if (!response.ok) throw new Error('source-unavailable');
        const data = await response.json();
        render(data);
        hasData = true;
        panel.hidden = true;
      } catch (_) {
        panel.dataset.state = 'error';
        title.textContent = 'Les chiffres n’ont pas pu être chargés.';
        message.textContent = hasData
          ? 'Les chiffres de la dernière lecture restent affichés. Réessaie pour les actualiser.'
          : 'Le serveur ou une source de données met trop de temps à répondre. Tu peux réessayer ci-dessous.';
        retry.hidden = false;
      } finally {
        clearTimeout(slow);
        clearTimeout(timeout);
        pending = false;
        refresh.disabled = false;
        refresh.textContent = 'Actualiser';
      }
    }
    retry.onclick = () => load(false);
    return { load };
  }
  root.TesignLoader = { create };
})(globalThis);
