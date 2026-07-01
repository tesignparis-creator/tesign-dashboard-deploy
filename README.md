# Tesign Stock Dashboard

Local read-only dashboard that combines:

- Shopify orders, products, variants and inventory;
- Meta Ads spend, attributed purchases and purchase value;
- France/Turkey stock lots;
- product, URSSAF, packaging, payment, shipping and fixed costs;
- Geremy's 9% commission on Shopify revenue generated while his Meta campaigns are active;
- manual bank balance, URSSAF due/paid reconciliation and a manual expense ledger;
- revenue, contribution margin, estimated result, CPA and ROAS.

The daily view separates Shopify revenue/Meta spend lines from estimated-result bars. Profit bars
are green and loss bars are red around a dedicated zero line, avoiding a misleading shared scale.

The first KPI is cumulative from `business_started_at` and is never affected by the date filter.
All other KPIs use the selected period. KPI targets are configured in `kpi_targets`; the current
CPA target is 15 EUR. ROAS target is calculated from the current margin after Geremy's 9%
commission: with a 48% gross margin, the current target is 1 / (48% - 9%) = 2.56.
Fixed costs are never accrued before `business_started_at`, even when the selected period starts
several years earlier.
The Shopify app has read-only `read_all_orders` and `read_reports` scopes. The cumulative KPI starts
on the first paid Shopify order, 8 August 2023, and `historical_shopify_orders_complete` is true.
Each margin profile also projects the selected period's result by applying that margin to period
revenue, then subtracting Meta spend, Geremy commission, fixed costs and manual business expenses.
The dashboard also shows the selected period's T-shirt/sweatshirt sales mix beside the CPA target;
the 15 EUR operational target is intentionally conservative because T-shirts dominate sales.

Site visits, conversion rate and add-to-cart figures use Shopify Analytics sessions through the
read-only `read_reports` scope.

## Start

Open a new PowerShell terminal, then run:

```powershell
& .\run-dashboard.ps1
```

Open <http://127.0.0.1:8765>.

The desktop shortcut `Tesign Dashboard` runs `open-dashboard.ps1`: it starts the local service
when needed and opens the dashboard in the default browser.

The service refreshes its API data and `latest-dashboard.json` every 10 minutes, even when the
dashboard is not open. Its synchronization code only reads Shopify and uses Meta `ads_read`; it
contains no automatic inventory, product, order, advertisement or campaign mutation.

The Shopify app also has `write_inventory`, used for the confirmed 25-unit France initialization
and reserved for future confirmed stock receipts. The dashboard never calls it automatically.

`install-autostart.ps1` starts the service automatically when the current user signs in. It uses
a scheduled task when allowed, otherwise it creates a shortcut in the user's Startup folder.

## Generate one JSON snapshot

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  .\app.py --once --since 2026-03-20 --until 2026-06-18
```

## Current stock assumptions

- France stock snapshot: 25 available T-shirts on 16 June 2026.
- Turkey stock: 160 incoming T-shirts, not counted as available yet.
- France lots are consumed first.
- Turkey lots are intentionally not mapped to a Shopify product until the exact thermal
  product and color variants are confirmed.
- Shopify currently reports zero inventory for the catalog; the dashboard highlights this
  mismatch instead of silently overwriting either source.

`shopify-stock-adjustments-review.csv` lists the seven France variants and the 25-unit physical
stock target. It is a review file only and is not imported automatically.

Secrets are stored in Windows user environment variables and are not present in these files:
`SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, `META_ACCESS_TOKEN`,
`BRIDGE_CLIENT_ID`, `BRIDGE_CLIENT_SECRET`.

## Manual financial data

`config.json` contains three deliberately separate records:

- `bank_account`: last known balance and recording date. It is not live until a banking API is connected;
- `urssaf_payments`: URSSAF payments already made, so the dashboard can show the remaining estimate;
- `business_expenses`: samples and other expenses, using `date`, `amount`, `category` and `description`.

The historical Geremy payment is recorded as 387 EUR. The dashboard compares it with the contractual
9% calculation instead of treating the transfer as proof of the commission amount.

Bridge aggregation is optional. When `BRIDGE_CLIENT_ID` and `BRIDGE_CLIENT_SECRET` are present, the
dashboard uses Bridge accounts as the banking source. Detailed transactions are hidden on public
deployments unless `BRIDGE_SHOW_TRANSACTIONS=true` is explicitly set.
