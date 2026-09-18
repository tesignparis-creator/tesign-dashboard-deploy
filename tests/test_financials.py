"""Economic and privacy regression examples using synthetic accounts/orders only."""

from copy import deepcopy
from datetime import date, datetime
from io import BytesIO
import traceback
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import app
from financials import business_banking, current_cost_reference, dated_unit_model, normalize_config


TODAY = date(2026, 9, 18)


def cost_config():
    return {
        "stock_lots": [],
        "stock_snapshot_at": "2026-06-16T00:00:00+00:00",
        "cost_models": {
            "tshirt_france": {"production": 10, "urssaf": 5, "packaging": 1},
            "sweatshirt_france": {"production": 30},
        },
        "shipping_costs": {"default_tshirt": 8, "default_sweatshirt": 10, "mondial": 8},
    }


def make_order(*, day="2026-09-18", quantity=1, shipping="Mondial Relay", customer_shipping=0):
    amount = lambda value: {"shopMoney": {"amount": str(value), "currencyCode": "EUR"}}
    return {
        "createdAt": day + "T12:00:00+00:00",
        "currentTotalPriceSet": amount(45 * quantity + customer_shipping),
        "currentShippingPriceSet": amount(customer_shipping),
        "shippingLines": {"nodes": [{"title": shipping}]},
        "lineItems": {"nodes": [{
            "title": "Fixture T-shirt", "quantity": quantity, "currentQuantity": quantity,
            "originalUnitPriceSet": amount(45),
            "variant": {"title": "M", "product": {"handle": "fixture-tee"}},
        }]},
    }


class CurrentCostsTests(unittest.TestCase):
    def test_45_euro_tee_has_17_euro_contribution_after_mondial_relay(self):
        ref = current_cost_reference({})
        self.assertEqual(ref["known_unit_cost"], 23.5)
        self.assertEqual(ref["contribution_before_shipping"], 21.5)
        self.assertEqual(ref["contribution_after_shipping"], 17)
        self.assertAlmostEqual(ref["break_even_roas"], 45 / 17, places=4)
        costs = app.CostEngine(cost_config()).order_cost(make_order())
        self.assertEqual(costs["total"], 28)
        self.assertEqual(costs["shipping"], 4.5)
        self.assertEqual(costs["shipping_status"], "confirmed")

    def test_two_items_pay_shipping_only_once_per_order(self):
        costs = app.CostEngine(cost_config()).order_cost(make_order(quantity=2))
        self.assertEqual(costs["items"], 47)
        self.assertEqual(costs["shipping"], 4.5)
        self.assertEqual(costs["total"], 51.5)
        self.assertEqual(costs["units"], 2)

    def test_home_shipping_charge_is_revenue_and_carrier_cost_is_separate(self):
        order = make_order(shipping="Livraison à domicile", customer_shipping=4.9)
        costs = app.CostEngine(cost_config()).order_cost(order)
        self.assertEqual(float(order["currentTotalPriceSet"]["shopMoney"]["amount"]), 49.9)
        self.assertEqual(costs["shipping_status"], "confirmed")
        self.assertEqual(costs["shipping"], 5.5)
        self.assertEqual(costs["total"], 29)
        self.assertAlmostEqual(49.9 - costs["total"], 20.9)
        self.assertEqual(current_cost_reference({})["shipping_options"]["home"]["cost"], 5.5)
        self.assertEqual(current_cost_reference({})["shipping_options"]["home"]["customer_charge"], 4.9)

    def test_current_cost_model_never_rewrites_historical_orders(self):
        config = cost_config()
        historical = app.CostEngine(config).order_cost(make_order(day="2026-09-17"))
        current = app.CostEngine(config).order_cost(make_order(day="2026-09-18"))
        self.assertEqual(historical["items"], 16)
        self.assertEqual(historical["shipping"], 8)
        self.assertEqual(historical["cost_bases"], ["historical_estimate"])
        self.assertEqual(current["items"], 23.5)
        self.assertEqual(config["cost_models"]["tshirt_france"]["production"], 10)

    def test_reference_does_not_overwrite_other_prices_or_sweatshirt_models(self):
        when = datetime.fromisoformat("2026-09-18T12:00:00+00:00")
        historical, basis = dated_unit_model(cost_config(), "tshirt_france", when, 35)
        self.assertEqual(sum(historical.values()), 16)
        self.assertEqual(basis, "historical_estimate")
        sweatshirt, basis = dated_unit_model(cost_config(), "sweatshirt_france", when, 80)
        self.assertEqual(sweatshirt, {"production": 30})
        self.assertEqual(basis, "historical_estimate")

    def test_config_normalization_updates_current_margin_without_mutating_input(self):
        source = cost_config()
        before = deepcopy(source)
        normalized = normalize_config(source)
        self.assertAlmostEqual(normalized["current_margin_rate"], 17 / 45)
        self.assertEqual(source, before)


class BusinessBankingTests(unittest.TestCase):
    def test_explicit_personal_scope_wins_over_tesign_name(self):
        selected, movements, status = business_banking({}, [{"label": "TESIGN perso", "scope": "personal", "balance": 999}], [], today=TODAY)
        self.assertIsNone(selected[0]["balance"])
        self.assertEqual(status["status"], "unavailable")

    def test_only_business_account_and_its_transactions_are_exposed(self):
        accounts = [
            {"account_id": "business-fixture", "label": "TESIGN", "balance": 123,
             "source": "powens", "recorded_at": "2026-09-17"},
            {"account_id": "personal-fixture", "label": "Compte perso", "balance": 9999,
             "source": "powens", "recorded_at": "2026-09-18"},
        ]
        transactions = [
            {"account_id": "business-fixture", "description": "Fixture supplier", "amount": -10},
            {"account_id": "personal-fixture", "description": "Private fixture", "amount": -99},
        ]
        selected, movements, status = business_banking({}, accounts, transactions, today=TODAY)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["balance"], 123)
        self.assertEqual([row["description"] for row in movements], ["Fixture supplier"])
        self.assertNotIn("account_id", selected[0])
        self.assertNotIn("account_id", movements[0])
        self.assertEqual(status["updated_at"], "2026-09-17")
        self.assertEqual(status["status"], "connected")

    def test_demo_balances_and_demo_transactions_never_become_real_cash(self):
        accounts = [{"account_id": "demo-fixture", "label": "TESIGN", "balance": 50000,
                     "source": "powens_sandbox", "recorded_at": "2026-09-18"}]
        transactions = [{"account_id": "demo-fixture", "description": "Demo", "amount": 99}]
        selected, movements, status = business_banking({}, accounts, transactions, today=TODAY)
        self.assertIsNone(selected[0]["balance"])
        self.assertTrue(selected[0]["is_demo"])
        self.assertEqual(movements, [])
        self.assertEqual(status["status"], "unavailable")
        self.assertIsNone(status["updated_at"])

    def test_balance_date_is_source_date_and_old_balance_is_stale(self):
        accounts = [{"label": "TESIGN", "balance": 100,
                     "source": "powens", "recorded_at": "2026-08-01T12:30:00Z"}]
        selected, _, status = business_banking({}, accounts, [], today=TODAY)
        self.assertEqual(selected[0]["recorded_at"], "2026-08-01")
        self.assertEqual(status["updated_at"], "2026-08-01")
        self.assertEqual(status["status"], "stale")
        self.assertTrue(status["stale"])

    def test_missing_and_future_dates_never_look_fresh(self):
        for recorded_at in (None, "", "invalid", "2026-09-19"):
            with self.subTest(recorded_at=recorded_at):
                selected, _, status = business_banking({}, [{
                    "label": "TESIGN", "balance": 100, "source": "powens",
                    "recorded_at": recorded_at,
                }], [], today=TODAY)
                self.assertTrue(selected[0]["stale"])
                self.assertNotEqual(status["status"], "connected")

    def test_manual_balance_remains_manual_even_if_dated_today(self):
        selected, _, status = business_banking({}, [{
            "label": "TESIGN", "balance": 100, "source": "manual", "recorded_at": "2026-09-18",
        }], [], today=TODAY)
        self.assertEqual(status["status"], "manual")
        self.assertEqual(selected[0]["source"], "manual")

    def test_explicit_account_allowlist_can_select_unlabelled_business_account(self):
        selected, _, status = business_banking({"business_bank_account_ids": [42]}, [{
            "account_id": 42, "label": "Current account", "balance": 77,
            "source": "bridge", "recorded_at": "2026-09-18",
        }], [], today=TODAY)
        self.assertEqual(selected[0]["balance"], 77)
        self.assertEqual(status["status"], "connected")


class ConnectorErrorPrivacyTests(unittest.TestCase):
    def test_http_error_never_exposes_query_token_or_remote_error_body(self):
        token = "SYNTHETIC_DO_NOT_EXPOSE_TOKEN"
        private_body = "SYNTHETIC_PRIVATE_REMOTE_BODY"
        url = "https://example.invalid/private/path?access_token=" + token
        error = HTTPError(url, 401, "Unauthorized " + token, {},
                          BytesIO(private_body.encode()))
        with patch.object(app.urllib.request, "urlopen", side_effect=error):
            try:
                app.request_json(url)
            except RuntimeError as exc:
                rendered = "".join(traceback.format_exception(exc))
                self.assertIn("HTTP 401", str(exc))
                self.assertIn("example.invalid", str(exc))
                self.assertNotIn(token, rendered)
                self.assertNotIn(private_body, rendered)
                self.assertNotIn("/private/path", str(exc))
            else:
                self.fail("Expected a sanitized connector failure")


if __name__ == "__main__":
    unittest.main()
