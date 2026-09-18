# Tableau de bord TESIGN

Consultation seule : Shopify, Meta, coûts, stock et relevé professionnel daté.

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

## Exécution

Python 3.12+ ; bibliothèque standard (tzdata nécessaire sur Windows pour les fuseaux IANA).
Configuration privée TESIGN_CONFIG_JSON ou config.json, et variables Shopify/Meta existantes.
Render démarre python app.py ; actualisation des sources toutes les dix minutes. Le service gratuit peut nécessiter un réveil.

## Vérifications

python -m unittest discover -s tests -v
node tests/test_frontend.cjs

Tests déterministes sans secrets ni appels réseau. Le déploiement doit aussi être vérifié sur les vraies sources.
