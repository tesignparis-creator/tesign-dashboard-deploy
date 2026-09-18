"""Calendar-cost examples with exact independently calculated expectations."""

from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app


class CalendarCostsTests(unittest.TestCase):
    def test_full_calendar_month_is_exact_monthly_subscription(self):
        for start, end in ((date(2026, 2, 1), date(2026, 2, 28)),
                           (date(2024, 2, 1), date(2024, 2, 29)),
                           (date(2026, 7, 1), date(2026, 7, 31))):
            with self.subTest(start=start):
                self.assertEqual(app.prorated_monthly_cost(200, start, end), 200)

    def test_two_full_months_cost_two_subscriptions(self):
        self.assertEqual(app.prorated_monthly_cost(
            200, date(2026, 2, 1), date(2026, 3, 31)), 400)

    def test_only_active_subscription_days_are_charged(self):
        # Fifteen active days of a 30-day month: exactly half of 200 EUR.
        self.assertEqual(app.prorated_monthly_cost(
            200, date(2026, 9, 1), date(2026, 9, 30),
            starts_at="2026-09-11", ends_at="2026-09-25"), 100)
        self.assertEqual(app.prorated_monthly_cost(
            200, date(2026, 6, 1), date(2026, 6, 30), starts_at="2026-07-01"), 0)
        self.assertEqual(app.prorated_monthly_cost(
            200, date(2026, 9, 1), date(2026, 9, 30), ends_at="2026-08-31"), 0)


class FixedCostsDashboardTests(unittest.TestCase):
    """No-sales ledger: the loss must equal its two actual configured subscriptions."""

    def make_builder(self, *, duplicate_favikon=False, started_at="2026-01-01"):
        fixed = {"Store subscription": 31}
        if duplicate_favikon:
            fixed["Favikon"] = 200
        config = {
            "business_started_at": started_at,
            "stock_snapshot_at": "2026-06-16T00:00:00+00:00",
            "stock_lots": [],
            "cost_models": {},
            "monthly_fixed_costs": fixed,
            "geremy": {"commission_scope_confirmed": True,
                       "commission_basis": "meta_attributed_revenue",
                       "campaign_ids": ["fixture-campaign"], "commission_rate": 0.09},
            "affiliate": {"favikon_monthly_cost": 200, "favikon_started_at": "2026-07-01"},
        }
        builder = app.DashboardBuilder.__new__(app.DashboardBuilder)
        builder.config = config
        builder.shopify = SimpleNamespace(
            fetch_catalog=lambda: {"shop": {"name": "Fixture", "ianaTimezone": "UTC"},
                                   "products": [], "locations": []},
            fetch_orders=lambda *args, **kwargs: [],
            fetch_analytics=lambda *args, **kwargs: {
                "sessions": 0, "cart_additions": 0, "completed_checkouts": 0},
        )
        builder.meta = SimpleNamespace(fetch_daily=lambda *args, **kwargs: [])
        builder.powens = SimpleNamespace(enabled=False, domain="", client_id="", client_secret="",
                                         environment="production")
        builder.bridge = SimpleNamespace(enabled=False, environment="production")
        return builder

    def build(self, builder, since, until):
        with patch.object(app, "request_json", side_effect=AssertionError("No network allowed")), \
             patch.object(app, "meta_history_start", return_value=date(2023, 8, 19)):
            return builder.build(since, until)

    def test_favikon_is_in_company_expenses_exactly_once(self):
        for duplicate in (False, True):
            with self.subTest(duplicate_favikon=duplicate):
                result = self.build(self.make_builder(duplicate_favikon=duplicate),
                                    date(2026, 7, 1), date(2026, 7, 31))
                self.assertEqual(result["totals"]["fixed_costs_prorated"], 231)
                self.assertEqual(result["affiliate"]["totals"]["favikon_period_cost"], 200)
                self.assertEqual(result["totals"]["estimated_result"], -231)
                self.assertEqual(round(sum(row["fixed_costs"] for row in result["daily"]), 2), 231)
                self.assertEqual(round(sum(row["estimated_result"] for row in result["daily"]), 2), -231)

    def test_favikon_is_not_backfilled_before_subscription_start(self):
        result = self.build(self.make_builder(duplicate_favikon=True),
                            date(2026, 6, 1), date(2026, 6, 30))
        self.assertEqual(result["totals"]["fixed_costs_prorated"], 31)
        self.assertEqual(result["affiliate"]["totals"]["favikon_period_cost"], 0)
        self.assertEqual(result["totals"]["estimated_result"], -31)

    def test_costs_do_not_accrue_before_business_exists(self):
        result = self.build(self.make_builder(started_at="2026-09-16"),
                            date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(result["totals"]["fixed_costs_prorated"], 115.5)
        self.assertTrue(all(row["fixed_costs"] == 0 for row in result["daily"][:15]))
        self.assertEqual(result["fixed_costs_metadata"]["status"], "unverified_config")


if __name__ == "__main__":
    unittest.main()
