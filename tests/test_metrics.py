import copy
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app


TODAY = date.today()
DAY = TODAY.isoformat()


def amount(value):
    return {"shopMoney": {"amount": str(value), "currencyCode": "EUR"}}


def order(number=1, *, created_at=None, revenue=45, status="PAID", refunded=0):
    return {
        "id": f"gid://shopify/Order/{number}", "name": f"#{number}",
        "createdAt": created_at or f"{DAY}T12:00:00Z", "cancelledAt": None,
        "displayFinancialStatus": status, "displayFulfillmentStatus": "FULFILLED",
        "currentTotalPriceSet": amount(revenue), "currentTotalDiscountsSet": amount(0),
        "totalRefundedSet": amount(refunded), "netPaymentSet": amount(revenue),
        "discountCodes": [], "shippingLines": {"nodes": []},
        "lineItems": {"nodes": [{
            "title": "T-shirt", "quantity": 1, "currentQuantity": 1,
            "originalUnitPriceSet": amount(45), "variant": {"title": "M", "product": {"handle": "tee"}},
        }]},
    }


def meta_row(*, spend=186.21, purchases=0, value=0, campaign_id=None, name="Campagne"):
    return {
        "date_start": DAY, "spend": str(spend), "impressions": "1000", "clicks": "100",
        "actions": [{"action_type": "purchase", "value": str(purchases)}],
        "action_values": [{"action_type": "purchase", "value": str(value)}],
        "campaign_id": campaign_id, "campaign_name": name,
    }


def builder(orders=None, account_rows=None, campaign_rows=None, config_patch=None):
    config = {
        "business_started_at": DAY, "stock_snapshot_at": f"{DAY}T00:00:00+00:00",
        "monthly_fixed_costs": {}, "stock_lots": [], "current_margin_rate": 0.5,
        "cost_models": {"tshirt_france": {"fabrication": 13, "urssaf": 7}, "sweatshirt_france": {"fabrication": 20}},
        "shipping_costs": {"default_tshirt": 4, "default_sweatshirt": 5},
        "geremy": {"commission_rate": 0.09, "mission_started_at": DAY, "campaign_names": ["Geremy"], "track_all_campaigns_after_start": True},
        "affiliate": {"favikon_monthly_cost": 0}, "margin_profiles": [],
    }
    config.update(config_patch or {})
    result = app.DashboardBuilder.__new__(app.DashboardBuilder)
    result.config = config
    result.shopify = Mock()
    result.shopify.fetch_catalog.return_value = {"shop": {"name": "Test", "currencyCode": "EUR", "ianaTimezone": "Europe/Paris"}, "products": []}
    result.shopify.fetch_orders.return_value = copy.deepcopy(orders or [])
    result.shopify.fetch_analytics.return_value = {"sessions": 100, "cart_additions": 10, "completed_checkouts": 9}
    result.meta = Mock()
    result.meta.fetch_daily.side_effect = lambda since, until, *, level="account": copy.deepcopy(account_rows if level == "account" else campaign_rows) or []
    result.powens = SimpleNamespace(enabled=False, domain="", client_id="", client_secret="", environment="production", show_transactions=False)
    result.bridge = SimpleNamespace(enabled=False, environment="production")
    return result


class MetricTests(unittest.TestCase):
    def test_zero_meta_purchases_is_not_nine_shopify_purchases(self):
        result = builder([order(i) for i in range(9)], [meta_row()]).build(TODAY, TODAY)
        totals = result["totals"]
        self.assertEqual(totals["orders"], 9)
        self.assertEqual(totals["meta_purchases"], 0)
        self.assertIsNone(totals["meta_cpa"])
        self.assertEqual(totals["blended_cpa"], 20.69)
        self.assertEqual(totals["meta_roas"], 0)
        self.assertEqual(totals["blended_roas"], 2.17)

    def test_meta_ratios_use_meta_value_and_purchases(self):
        result = builder([order()], [meta_row(spend=90, purchases=2, value=180)]).build(TODAY, TODAY)
        self.assertEqual(result["totals"]["meta_cpa"], 45)
        self.assertEqual(result["totals"]["meta_roas"], 2)
        self.assertEqual(result["totals"]["blended_cpa"], 90)

    def test_campaigns_include_personal_and_geremy_with_same_name(self):
        campaigns = [meta_row(campaign_id="personal", name="Same", spend=100), meta_row(campaign_id="geremy", name="Same", spend=50)]
        result = builder([order()], [meta_row(spend=150)], campaigns, {"personal_campaign_ids": ["personal"]}).build(TODAY, TODAY)
        self.assertEqual(len(result["campaign_performance"]), 2)
        self.assertEqual({row["campaign_id"] for row in result["campaign_performance"]}, {"personal", "geremy"})
        self.assertEqual(result["campaign_performance"][0]["scope"], "personal_confirmed")
        self.assertIsNone(result["totals"]["geremy_commission"])
        self.assertIsNone(result["totals"]["geremy_revenue"])
        self.assertEqual(result["totals"]["geremy_commission_deducted"], 0)

    def test_confirmed_commission_uses_id_attributed_revenue_only(self):
        campaigns = [meta_row(campaign_id="p", value=900, spend=100), meta_row(campaign_id="g", value=45, spend=50)]
        config = {"geremy": {"commission_rate": 0.09, "mission_started_at": DAY, "commission_scope_confirmed": True, "commission_basis": "meta_attributed_revenue", "campaign_ids": ["g"]}}
        result = builder([order(i) for i in range(9)], [meta_row(spend=150, value=945)], campaigns, config).build(TODAY, TODAY)
        self.assertEqual(result["totals"]["geremy_revenue"], 45)
        self.assertEqual(result["totals"]["geremy_commission"], 4.05)
        self.assertEqual(result["totals"]["geremy_commission_deducted"], 4.05)

    def test_source_failure_is_unknown_not_zero(self):
        subject = builder([order()])
        subject.meta.fetch_daily.side_effect = RuntimeError("API unavailable")
        subject.shopify.fetch_analytics.side_effect = RuntimeError("API unavailable")
        result = subject.build(TODAY, TODAY)
        for field in ("ad_spend", "meta_purchases", "meta_purchase_value", "meta_cpa", "meta_roas", "estimated_result", "site_visits", "conversion_rate"):
            self.assertIsNone(result["totals"][field], field)
        self.assertEqual(result["totals"]["revenue"], 45)
        self.assertEqual(result["data_status"]["meta_account"]["status"], "unavailable")

    def test_successful_zero_and_skipped_analytics_are_distinct(self):
        subject = builder()
        subject.shopify.fetch_analytics.return_value = {"sessions": 0, "cart_additions": 0, "completed_checkouts": 0}
        live = subject.build(TODAY, TODAY)
        skipped = subject.build(TODAY, TODAY, include_analytics=False)
        self.assertEqual(live["totals"]["site_visits"], 0)
        self.assertIsNone(skipped["totals"]["site_visits"])
        self.assertEqual(skipped["data_status"]["shopify_analytics"]["status"], "not_requested")
        self.assertEqual(live["totals"]["ad_spend"], 0)

    def test_shop_local_period_midnight_boundaries(self):
        start = date(2026, 9, 17)
        orders = [order(1, created_at="2026-09-16T22:30:00Z"), order(2, created_at="2026-09-17T22:30:00Z")]
        result = builder(orders).build(start, start)
        self.assertEqual(result["totals"]["orders"], 1)
        self.assertEqual(result["orders"][0]["name"], "#1")
        self.assertEqual(result["orders"][0]["date"], "2026-09-17")

    def test_refund_is_retained_without_double_subtracting(self):
        partial = order(1, revenue=25, refunded=20, status="PARTIALLY_REFUNDED")
        full = order(2, revenue=0, refunded=45, status="REFUNDED")
        result = builder([partial, full]).build(TODAY, TODAY)
        self.assertEqual(result["totals"]["orders"], 2)
        self.assertEqual(result["totals"]["revenue"], 25)
        self.assertEqual(result["totals"]["refunds_amount"], 65)
        self.assertEqual(result["totals"]["net_payments"], 25)
        self.assertFalse(result["totals"]["cost_completeness"])

    def test_old_period_keeps_shopify_and_clips_meta(self):
        boundary = TODAY - timedelta(days=10)
        since = boundary - timedelta(days=1)
        subject = builder([order(created_at=f"{since.isoformat()}T12:00:00Z")], [meta_row()])
        with patch("app.meta_history_start", return_value=boundary):
            result = subject.build(since, TODAY, include_analytics=False)
        self.assertEqual(result["totals"]["orders"], 1)
        self.assertEqual(subject.meta.fetch_daily.call_args_list[0].args[0], boundary)
        self.assertEqual(result["data_status"]["meta_account"]["status"], "partial")
        self.assertIsNone(result["totals"]["ad_spend"])
        self.assertEqual(result["totals"]["ad_spend_observed"], 186.21)
        self.assertIsNone(result["daily"][0]["estimated_result"])

    def test_empty_analytics_response_is_not_invented_zero(self):
        subject = app.ShopifyClient.__new__(app.ShopifyClient)
        subject.graphql = Mock(return_value={"shopifyqlQuery": {"tableData": {"rows": []}}})
        with self.assertRaises(RuntimeError):
            subject.fetch_analytics(TODAY, TODAY)

    def test_order_lines_are_paginated(self):
        subject = app.ShopifyClient.__new__(app.ShopifyClient)
        first = order()
        first["lineItems"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
        subject.graphql = Mock(side_effect=[
            {"orders": {"nodes": [first], "pageInfo": {"hasNextPage": False}}},
            {"order": {"lineItems": {"nodes": [{"id": "extra"}], "pageInfo": {"hasNextPage": False}}}},
        ])
        result = subject.fetch_orders(TODAY, TODAY)
        self.assertEqual(len(result[0]["lineItems"]["nodes"]), 2)
        self.assertFalse(result[0]["lineItems"]["pageInfo"]["hasNextPage"])

    def test_same_order_cost_does_not_change_with_date_filter(self):
        first_day = TODAY - timedelta(days=1)
        orders = [order(1, created_at=f"{first_day.isoformat()}T12:00:00Z"), order(2)]
        lots = [{
            "id": "old-lot", "product_label": "T-shirt", "product_handle": "tee", "variant_match": "M",
            "quantity": 1, "status": "available", "origin": "France", "cost_model": "tshirt_france",
        }, {
            "id": "next-lot", "product_label": "T-shirt", "product_handle": "tee", "variant_match": "M",
            "quantity": 1, "status": "available", "origin": "Turkey", "cost_model": "tshirt_turkey",
        }]
        config = {
            "stock_snapshot_at": f"{first_day.isoformat()}T00:00:00+00:00", "stock_lots": lots,
            "cost_models": {"tshirt_france": {"fabrication": 20}, "tshirt_turkey": {"fabrication": 10}},
        }
        both = builder(orders, config_patch=config).build(first_day, TODAY)
        selected = builder(orders, config_patch=config).build(TODAY, TODAY)
        full_order = next(row for row in both["orders"] if row["name"] == "#2")
        selected_order = selected["orders"][0]
        self.assertEqual(selected_order["name"], "#2")
        self.assertEqual(selected_order["variable_costs"], full_order["variable_costs"])
        self.assertEqual(selected_order["allocations"][0]["lot_id"], "next-lot")

    def test_meta_malformed_success_does_not_become_zero(self):
        subject = app.MetaClient.__new__(app.MetaClient)
        subject.token = "test-token"
        subject.api_version = "test"
        subject.account_id = "test"
        with patch("app.request_json", return_value={"error": {"message": "unavailable"}}):
            with self.assertRaises(RuntimeError):
                subject.fetch_daily(TODAY, TODAY)


if __name__ == "__main__":
    unittest.main()
