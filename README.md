# Tableau de bord TESIGN

Consultation seule : Shopify, Meta, coûts, stock et relevé professionnel daté.

## Interface de pilotage

L’essentiel affiche trois cumuls depuis le début de l’historique Shopify (CA, commandes, marge contributive estimée), puis un seul graphique couvrant toutes les années. Les euros et le nombre de commandes ont deux axes explicitement distincts. Le tableau sous le graphique fournit les valeurs et les variations annuelles ; l’année en cours est comparée aux mêmes dates de l’année précédente, jamais à son total annuel complet. La marge est calculée avant publicité et charges fixes, pas assimilée au bénéfice net.

La projection 2027 hachurée est une référence historique : moyenne des trois dernières années éligibles, dont l’année courante ramenée sur une année entière au rythme journalier observé. Les bornes sont le minimum et le maximum de ces trois repères, pas un intervalle de confiance. L’année de lancement incomplète est exclue de ce calcul. Les objectifs personnels et hypothèses d’action restent séparés dans Cap 2027 et n’influencent pas cette projection. Les coûts historiques restent estimatifs ; saisonnalité et effets des actions futures ne sont pas prédits.

Les onglets séparent la vue d'ensemble, la trajectoire de TESIGN et les détails opérationnels. La synthèse met en avant l'objectif de revenu personnel, les indicateurs de la période et les graphiques historiques. Les détails et limites des sources restent consultables avec les chiffres concernés.

La trajectoire future est un scénario de travail estimatif, distinct des ventes constatées. Les hypothèses du simulateur sont sauvegardées dans le navigateur : elles ne modifient ni les ventes Shopify, ni les stocks, ni les budgets publicitaires réels. Le résultat simulé ne constitue pas une rémunération disponible ou un prévisionnel de trésorerie complet ; les commissions non confirmées, les achats de stock et les autres coûts manquants doivent encore être rapprochés.

Les fichiers CSS et JavaScript du tableau de bord sont servis par une liste explicite de routes statiques ; aucun chemin de fichier arbitraire n'est exposé.

## Indicateurs corrigés — 18 septembre 2026

- ROAS Meta = CA attribué Meta / dépenses Meta ; CPA Meta = dépenses / achats attribués.
- Sans achat attribué, le CPA Meta est indéterminé. Une panne de source reste inconnue.
- MER = tout le CA Shopify / dépenses Meta, sans présumer l'attribution.
- Le résultat est partiel : coûts historiques, abonnements et commissions restent estimatifs.
- Le cumul conserve Shopify avant la limite de conservation de Meta ; le résultat incomplet n'est pas déclaré certain.
- Charges mensuelles au prorata calendaire ; Favikon inclus une seule fois.
- Les campagnes sont identifiées par ID. Une commission ne devient une déduction que si contrat, périmètre et base sont explicitement confirmés dans la configuration.

## Coûts actuels

T-shirt à 45 euros : URSSAF estimée 7, fabrication/impression 13, frais 2, emballage 1,50. Total 23,50 euros par unité.
Mondial Relay offert : transport 4,50 euros par commande, contribution 17 euros avant publicité et charges fixes.
Domicile : facturé 4,90 euros, coût transporteur 5,50 euros, contribution 20,90 euros pour un T-shirt.
Ce sont des coûts déclarés, pas un taux fiscal certifié. Ils s'appliquent à partir du 18/09/2026 ; les commandes historiques conservent leur modèle estimatif antérieur.
La configuration facultative current_cost_reference permet une autre date d'effet explicitement confirmée.

## Banque et données sensibles

Seuls les comptes professionnels identifiés sont exposés. Les soldes de démonstration et comptes personnels sont exclus.
TESIGN_BANK_SNAPSHOT_JSON permet un relevé ponctuel : scope business, source enable_banking_snapshot, recorded_at, balance, currency_code.
Ce relevé ne constitue pas une synchronisation permanente. Aucune clé bancaire n'est transférée depuis le PC.
Les transactions sont masquées sur le site public. Les erreurs de connecteurs ne contiennent ni jeton ni réponse brute.
Ne jamais committer des configurations ou relevés réels. Les fichiers *.local.json sont ignorés.

## Stock

Les lots sont estimés à partir de l'inventaire physique daté ; le filtre de dates ne change pas le stock actuel calculé.
Les différences avec Shopify et les lots entrants non rapprochés restent explicitement signalés.

## Graphiques et apports

`chart_history` agrège tout l'historique disponible en mois et années, indépendamment du filtre journalier. Une valeur inconnue reste `null`, même dans un total. Le résultat reste une estimation partielle selon la couverture des sources et des coûts.

Les apports personnels sont distincts du CA, des dépenses et du solde bancaire. La configuration privée `business_capital_flows` accepte des flux documentés : `scope: business`, `type: contribution|withdrawal`, `date`, `amount` positif, `currency: EUR`, `source` et `id` facultatif pour dédoublonner. `TESIGN_CAPITAL_FLOWS_JSON` accepte cette liste ou `{flows, coverage}`. Ne pas placer de libellé bancaire sensible dans `source`.

L'objectif de revenu est lu depuis `business_plan` ou la variable privée `TESIGN_BUSINESS_PLAN_JSON` : `personal_monthly_income_target`, `target_date` (ISO) et `target_context`. Les valeurs personnelles ne sont pas inscrites dans les sources publiques. Les priorités de développement sont affichées séparément des tâches sauvegardées dans le navigateur.

Sans rapprochement intégral attesté par `business_capital_coverage` (`since`, `until`, `is_complete: true`), les flux ne sont qu'un minimum documenté : les totaux complets restent inconnus. Aucun versement Shopify, dépense ou perte n'est transformé automatiquement en apport. Aucun formulaire public ne modifie ces données.

## Exécution

Python 3.12+ ; bibliothèque standard (tzdata nécessaire sur Windows pour les fuseaux IANA).
Configuration privée TESIGN_CONFIG_JSON ou config.json, et variables Shopify/Meta existantes.
Render démarre python app.py ; actualisation des sources toutes les dix minutes. Le service gratuit peut nécessiter un réveil.

## Vérifications

python -m unittest discover -s tests -v
node tests/test_frontend.cjs
node tests/test_charts.cjs

Tests déterministes sans secrets ni appels réseau. Le déploiement doit aussi être vérifié sur les vraies sources.
