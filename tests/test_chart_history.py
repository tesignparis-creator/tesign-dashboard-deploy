"""Chart provenance and owner-funding regressions; all fixtures are synthetic."""

from copy import deepcopy
from datetime import date
import json
import os
import unittest
from unittest.mock import patch

import app
from test_cache_integrity import FakeBuilder, FrozenDate


class ChartHistoryTests(unittest.TestCase):
    def setUp(self):
        self.date_patch = patch.object(app, "date", FrozenDate)
        self.date_patch.start()
        self.addCleanup(self.date_patch.stop)
        self.builder = FakeBuilder()
        self.cache = app.Cache(self.builder, 600)

    def test_charts_preserve_all_business_history_without_extra_source_calls(self):
        response = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 10))
        history = response["chart_history"]
        self.assertEqual(history["period"]["since"], "2022-01-01")
        self.assertEqual(history["period"]["until"], "2026-09-18")
        self.assertEqual(history["monthly"][0]["revenue"], 40)
        self.assertEqual(history["monthly"][1]["revenue"], 110)
        self.assertEqual(history["annual"][0]["period"], "2022")
        self.assertEqual(history["totals"]["revenue"], 150)
        self.assertEqual(len(self.builder.calls), 2)
        self.assertNotIn("daily", history)

    def test_month_and_year_never_replace_missing_day_with_zero(self):
        self.builder.rows[1]["ad_spend"] = None
        self.builder.rows[1]["estimated_result"] = None
        history = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 18))["chart_history"]
        for bucket in (history["monthly"][1], history["annual"][1], history["totals"]):
            self.assertIsNone(bucket["ad_spend"])
            self.assertIsNone(bucket["estimated_result"])
        self.assertEqual(history["monthly"][1]["ad_spend_observed"], 10)
        self.assertEqual(history["monthly"][1]["revenue"], 110)
        self.assertTrue(history["monthly"][1]["has_missing_values"])

    def test_unobserved_cost_is_unknown_even_when_revenue_is_known(self):
        history = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 18))["chart_history"]
        self.assertEqual(history["totals"]["revenue"], 150)
        self.assertIsNone(history["totals"]["variable_costs"])
        self.assertIsNone(history["totals"]["variable_costs_observed"])
        self.assertFalse(history["result_is_complete"])
        self.assertFalse(history["cost_completeness"])

    def test_period_filter_and_response_mutation_do_not_change_full_history(self):
        first = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 10))
        expected = deepcopy(first["chart_history"])
        first["chart_history"]["monthly"][0]["revenue"] = -999
        second = self.cache.get_dashboard(date(2026, 9, 18), date(2026, 9, 18))
        self.assertEqual(second["chart_history"], expected)


class CapitalHistoryTests(unittest.TestCase):
    TODAY = date(2026, 9, 18)

    def config(self, flows=None, complete=False):
        config = {"business_started_at": "2026-07-01"}
        if flows is not None:
            config["business_capital_flows"] = flows
        if complete:
            config["business_capital_coverage"] = {
                "since": "2026-07-01", "until": "2026-09-18", "is_complete": True,
            }
        return config

    def flow(self, **changes):
        return {"scope": "business", "source": "synthetic_verified_statement",
                "date": "2026-07-05", "type": "contribution", "amount": 1000,
                **changes}

    def test_no_capital_source_is_unknown_not_zero_or_inferred(self):
        config = self.config()
        config.update({"stock_valuation": 9000, "losses": 5000, "bank_balance": 120})
        result = app.build_capital_history(config, self.TODAY)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["totals"]["contributions"])
        self.assertIsNone(result["observed_totals"]["net_contributions"])
        self.assertEqual(result["monthly"], [])

    def test_documented_partial_flows_are_not_claimed_complete_totals(self):
        result = app.build_capital_history(self.config([
            self.flow(), self.flow(date="2026-09-02", type="withdrawal", amount=200),
        ]), self.TODAY)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["totals"]["net_contributions"])
        self.assertEqual(result["observed_totals"]["net_contributions"], 800)
        self.assertEqual(result["monthly"][1]["contributions"], 0)
        self.assertEqual(result["monthly"][-1]["cumulative_contributions"], 1000)
        self.assertEqual(result["monthly"][-1]["cumulative_withdrawals"], 200)
        self.assertEqual(result["monthly_basis"], "documented_flows_only")

    def test_explicit_full_coverage_can_confirm_zero_funding(self):
        result = app.build_capital_history(self.config([], complete=True), self.TODAY)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["totals"]["net_contributions"], 0)
        self.assertEqual(len(result["monthly"]), 3)

    def test_old_coverage_is_not_complete_for_today(self):
        config = self.config([self.flow()], complete=True)
        config["business_capital_coverage"]["until"] = "2026-09-17"
        result = app.build_capital_history(config, self.TODAY)
        self.assertFalse(result["is_complete"])
        self.assertEqual(result["status"], "partial")

    def test_personal_future_nonnumeric_foreign_and_undocumented_flows_rejected(self):
        invalid = [
            self.flow(scope="personal"), self.flow(date="2026-09-19"),
            self.flow(date="2026-06-30"), self.flow(amount="bad"),
            self.flow(amount=float("inf")), self.flow(amount=True),
            self.flow(amount=-30), self.flow(currency="USD"),
            self.flow(type="sale"), self.flow(source=""),
        ]
        result = app.build_capital_history(self.config(invalid, complete=True), self.TODAY)
        self.assertEqual(result["rejected_flows"], len(invalid))
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["is_complete"])
        self.assertIsNone(result["totals"]["contributions"])

    def test_public_flow_has_no_bank_reference_or_private_payload(self):
        flow = self.flow(id="private-bank-id", iban="private", label="private label")
        result = app.build_capital_history(self.config([flow, flow], complete=True), self.TODAY)
        self.assertEqual(len(result["flows"]), 1)
        self.assertEqual(result["totals"]["contributions"], 1000)
        serialized = json.dumps(result)
        self.assertNotIn("private", serialized)

    def test_environment_import_overrides_flows_without_modifying_other_config(self):
        config = self.config([self.flow(amount=1)])
        config["sentinel"] = "kept"
        capital = {"flows": [self.flow(amount=75)], "coverage": {"is_complete": False}}
        with patch.dict(os.environ, {
            "TESIGN_CONFIG_JSON": json.dumps(config),
            "TESIGN_CAPITAL_FLOWS_JSON": json.dumps(capital),
        }, clear=True), patch.object(app, "load_local_env"), patch.object(app, "normalize_config", side_effect=lambda value: value):
            loaded = app.load_config()
        self.assertEqual(loaded["sentinel"], "kept")
        self.assertEqual(loaded["business_capital_flows"][0]["amount"], 75)
        self.assertEqual(loaded["business_capital_coverage"], {"is_complete": False})


if __name__ == "__main__":
    unittest.main()
