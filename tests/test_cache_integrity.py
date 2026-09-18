"""Regression tests for history, inventory and cache boundaries; no live accounts."""

from copy import deepcopy
from datetime import date
import unittest
from unittest.mock import patch

import app


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 18)


class FakeBuilder:
    def __init__(self):
        self.config = {
            "business_started_at": "2022-01-01",
            "historical_shopify_orders_complete": True,
            "stock_snapshot_at": "2026-06-16T00:00:00+00:00",
        }
        self.calls = []
        self.rows = [
            {"date": "2022-01-02", "revenue": 40.0, "contribution_margin": 15.0,
             "ad_spend": 5.0, "estimated_result": 10.0},
            {"date": "2026-09-10", "revenue": 50.0, "contribution_margin": 20.0,
             "ad_spend": 10.0, "estimated_result": 10.0},
            {"date": "2026-09-18", "revenue": 60.0, "contribution_margin": 25.0,
             "ad_spend": 10.0, "estimated_result": 15.0},
        ]

    def build(self, since, until, *, include_analytics=True):
        self.calls.append((since, until, include_analytics))
        rows = deepcopy([
            row for row in self.rows
            if since.isoformat() <= row["date"] <= until.isoformat()
        ])
        return {
            "generated_at": "2026-09-18T08:42:00+00:00",
            "period": {"since": since.isoformat(), "until": until.isoformat(),
                       "days": (until - since).days + 1},
            "totals": {"revenue": sum(row["revenue"] for row in rows)},
            "daily": rows,
            # Intentionally period-sensitive fixture: the public dashboard must
            # select its current cumulative stock, regardless of this sales filter.
            "stock": [{"id": "fixture-lot", "remaining": 10 - len(rows)}],
            "stock_totals": {"available": 10 - len(rows), "incoming": 0},
        }


class CacheIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.date_patch = patch.object(app, "date", FrozenDate)
        self.date_patch.start()
        self.addCleanup(self.date_patch.stop)
        self.builder = FakeBuilder()
        self.cache = app.Cache(self.builder, 600)

    def test_cumulative_preserves_shopify_revenue_before_meta_retention(self):
        data = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 18))
        self.assertEqual(data["cumulative"]["period"]["since"], "2022-01-01")
        self.assertEqual(data["cumulative"]["totals"]["revenue"], 150)
        self.assertTrue(data["cumulative"]["meta_history_truncated"])
        self.assertFalse(data["cumulative"]["is_complete"])
        self.assertIn("37 mois", data["cumulative"]["missing_data"])

    def test_filters_change_period_revenue_but_not_cumulative_or_current_stock(self):
        first = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 10))
        second = self.cache.get_dashboard(date(2026, 9, 18), date(2026, 9, 18))
        self.assertEqual(first["totals"]["revenue"], 50)
        self.assertEqual(second["totals"]["revenue"], 60)
        self.assertEqual(first["cumulative"]["totals"], second["cumulative"]["totals"])
        self.assertEqual(first["stock_totals"], second["stock_totals"])
        self.assertEqual(first["stock"][0]["remaining"], 7)
        self.assertEqual(first["stock_as_of"], "2026-09-18")
        self.assertEqual(first["data_freshness"]["physical_stock_snapshot_at"],
                         "2026-06-16T00:00:00+00:00")

    def test_response_mutations_cannot_poison_cached_totals_or_trend(self):
        first = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 18))
        first["cumulative"]["totals"]["revenue"] = -999
        first["trend_history"]["daily"][0]["revenue"] = -999
        first["stock"][0]["remaining"] = -999
        second = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 18))
        self.assertEqual(second["cumulative"]["totals"]["revenue"], 150)
        self.assertEqual(second["trend_history"]["daily"][0]["revenue"], 50)
        self.assertEqual(second["stock"][0]["remaining"], 7)
        self.assertEqual(len(self.builder.calls), 2)

    def test_missing_meta_days_remain_unknown_in_trend_total(self):
        self.builder.rows[1]["ad_spend"] = None
        self.builder.rows[1]["estimated_result"] = None
        result = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 18))
        totals = result["trend_history"]["totals"]
        self.assertIsNone(totals["ad_spend"])
        self.assertIsNone(totals["estimated_result"])
        self.assertEqual(totals["ad_spend_observed"], 10)
        self.assertEqual(totals["estimated_result_observed"], 15)
        self.assertEqual(totals["revenue"], 110)

    def test_forced_refresh_and_analytics_variants_have_separate_cache_entries(self):
        since, until = date(2026, 9, 1), date(2026, 9, 18)
        self.cache.get(since, until)
        self.cache.get(since, until)
        self.assertEqual(len(self.builder.calls), 1)
        self.cache.get(since, until, include_analytics=False)
        self.assertEqual(len(self.builder.calls), 2)
        self.cache.get(since, until, force=True)
        self.assertEqual(len(self.builder.calls), 3)

    def test_recent_history_is_not_complete_when_a_source_or_costs_are_unknown(self):
        self.builder.config["business_started_at"] = "2026-09-01"
        original_build = self.builder.build

        def unavailable_build(*args, **kwargs):
            data = original_build(*args, **kwargs)
            data["data_status"] = {"meta_account": {"status": "unavailable"}}
            data["totals"]["cost_completeness"] = False
            return data

        with patch.object(self.builder, "build", side_effect=unavailable_build):
            result = self.cache.get_dashboard(date(2026, 9, 1), date(2026, 9, 18))
        self.assertFalse(result["cumulative"]["meta_history_truncated"])
        self.assertFalse(result["cumulative"]["is_complete"])
        self.assertIn("indisponible", result["cumulative"]["missing_data"])
        self.assertFalse(result["cumulative"]["cost_completeness"])


class PeriodTests(unittest.TestCase):
    @patch.object(app, "date", FrozenDate)
    def test_period_before_meta_retention_is_still_valid_for_shopify(self):
        since, until = app.parse_period({"since": ["2022-01-01"], "until": ["2022-02-01"]})
        self.assertEqual(since, date(2022, 1, 1))
        self.assertEqual(until, date(2022, 2, 1))

    @patch.object(app, "date", FrozenDate)
    def test_reversed_future_and_excessively_long_periods_are_rejected(self):
        for query in (
            {"since": ["2026-09-18"], "until": ["2026-09-17"]},
            {"since": ["2026-09-18"], "until": ["2026-09-19"]},
            {"since": ["2010-01-01"], "until": ["2026-09-18"]},
        ):
            with self.subTest(query=query), self.assertRaises(ValueError):
                app.parse_period(query)

    def test_meta_boundary_handles_shorter_february(self):
        self.assertEqual(app.meta_history_start(date(2024, 3, 31)), date(2021, 3, 1))
        self.assertEqual(app.meta_history_start(date(2026, 9, 18)), date(2023, 8, 19))


if __name__ == "__main__":
    unittest.main()
