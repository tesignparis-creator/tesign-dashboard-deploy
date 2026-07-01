from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
HTML_PATH = ROOT / "dashboard.html"
STATIC_PATH = ROOT / "static"
DEFAULT_AFFILIATE_CONFIG = {
    "favikon_monthly_cost": 200.0,
    "favikon_started_at": "2026-07-01",
    "default_commission_rate": 0.15,
    "default_discount_rate": 0.10,
    "default_product_seed_cost": 18.0,
    "influencers": [],
    "manual_sales": [],
}


def load_local_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def iso_date(value: Any) -> str:
    return str(value or "")[:10]


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = None
    final_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        final_headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=body, headers=final_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail[:800]}") from exc


class ShopifyClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.shop = os.environ.get("SHOPIFY_SHOP", "").strip()
        self.client_id = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
        self.api_version = config["shopify_api_version"]
        if not all((self.shop, self.client_id, self.client_secret)):
            raise RuntimeError(
                "SHOPIFY_SHOP, SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET must be defined."
            )
        self._token = ""
        self._expires_at = 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires_at - 120:
            return self._token
        result = request_json(
            f"https://{self.shop}.myshopify.com/admin/oauth/access_token",
            method="POST",
            form={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        self._token = result["access_token"]
        self._expires_at = time.time() + int(result.get("expires_in", 86400))
        return self._token

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        result = request_json(
            f"https://{self.shop}.myshopify.com/admin/api/{self.api_version}/graphql.json",
            method="POST",
            headers={"X-Shopify-Access-Token": self.token()},
            payload={"query": query, "variables": variables},
        )
        if result.get("errors"):
            raise RuntimeError(f"Shopify GraphQL error: {result['errors']}")
        return result["data"]

    def fetch_catalog(self) -> dict[str, Any]:
        query = """
        query Catalog($after: String) {
          shop { name currencyCode }
          locations(first: 20) { nodes { id name } }
          products(first: 20, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id title handle description status totalInventory
              variants(first: 20) {
                nodes { id title sku price inventoryQuantity }
              }
            }
          }
        }
        """
        products: list[dict[str, Any]] = []
        after = None
        shop: dict[str, Any] = {}
        locations: list[dict[str, Any]] = []
        while True:
            data = self.graphql(query, {"after": after})
            shop = data["shop"]
            locations = data["locations"]["nodes"]
            products.extend(data["products"]["nodes"])
            page = data["products"]["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
        return {"shop": shop, "locations": locations, "products": products}

    def fetch_orders(self, since: date, until: date) -> list[dict[str, Any]]:
        query = """
        query Orders($after: String, $search: String!) {
          orders(first: 20, after: $after, query: $search, sortKey: CREATED_AT) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id name createdAt updatedAt cancelledAt displayFinancialStatus
              currentTotalPriceSet { shopMoney { amount currencyCode } }
              currentSubtotalPriceSet { shopMoney { amount currencyCode } }
              currentTotalDiscountsSet { shopMoney { amount currencyCode } }
              currentShippingPriceSet { shopMoney { amount currencyCode } }
              totalRefundedSet { shopMoney { amount currencyCode } }
              discountCodes
              shippingLines(first: 10) { nodes { title } }
              lineItems(first: 20) {
                nodes {
                  id title quantity currentQuantity sku
                  originalUnitPriceSet { shopMoney { amount currencyCode } }
                  discountedTotalSet { shopMoney { amount currencyCode } }
                  variant { id title sku product { id title handle } }
                }
              }
            }
          }
        }
        """
        after = None
        orders: list[dict[str, Any]] = []
        search = (
            f"created_at:>={since.isoformat()} "
            f"created_at:<{(until + timedelta(days=1)).isoformat()}"
        )
        while True:
            data = self.graphql(query, {"after": after, "search": search})
            connection = data["orders"]
            orders.extend(connection["nodes"])
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
        return orders

    def fetch_analytics(self, since: date, until: date) -> dict[str, int]:
        query = """
        query Analytics($query: String!) {
          shopifyqlQuery(query: $query) {
            tableData { rows }
            parseErrors
          }
        }
        """
        shopifyql = (
            "FROM sessions SHOW sessions, sessions_with_cart_additions, "
            "sessions_that_completed_checkout "
            f"SINCE {since.isoformat()} UNTIL {until.isoformat()}"
        )
        data = self.graphql(query, {"query": shopifyql})["shopifyqlQuery"]
        if data.get("parseErrors"):
            raise RuntimeError(f"ShopifyQL error: {data['parseErrors']}")
        rows = (data.get("tableData") or {}).get("rows") or []
        row = rows[0] if rows else {}
        return {
            "sessions": int(row.get("sessions") or 0),
            "cart_additions": int(row.get("sessions_with_cart_additions") or 0),
            "completed_checkouts": int(
                row.get("sessions_that_completed_checkout") or 0
            ),
        }


class BridgeClient:
    def __init__(self) -> None:
        self.client_id = os.environ.get("BRIDGE_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("BRIDGE_CLIENT_SECRET", "").strip()
        self.environment = os.environ.get("BRIDGE_ENV", "sandbox").strip() or "sandbox"
        self.external_user_id = (
            os.environ.get("BRIDGE_EXTERNAL_USER_ID", "tesign-owner").strip()
            or "tesign-owner"
        )
        self.base_url = "https://api.bridgeapi.io/v3/aggregation"
        self._token = ""
        self._expires_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def headers(self, *, authenticated: bool = False) -> dict[str, str]:
        headers = {
            "Bridge-Version": "2025-01-15",
            "Client-Id": self.client_id,
            "Client-Secret": self.client_secret,
            "accept": "application/json",
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self.token()}"
        return headers

    def ensure_user(self) -> None:
        request_json(
            f"{self.base_url}/users",
            method="POST",
            headers=self.headers(),
            payload={"external_user_id": self.external_user_id},
        )

    def token(self) -> str:
        if self._token and time.time() < self._expires_at - 120:
            return self._token
        try:
            result = request_json(
                f"{self.base_url}/authorization/token",
                method="POST",
                headers=self.headers(),
                payload={"external_user_id": self.external_user_id},
            )
        except RuntimeError:
            self.ensure_user()
            result = request_json(
                f"{self.base_url}/authorization/token",
                method="POST",
                headers=self.headers(),
                payload={"external_user_id": self.external_user_id},
            )
        self._token = result["access_token"]
        expires_at = result.get("expires_at")
        if expires_at:
            try:
                parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                self._expires_at = parsed.timestamp()
            except ValueError:
                self._expires_at = time.time() + 7200
        else:
            self._expires_at = time.time() + 7200
        return self._token

    def list_resources(self, path: str, *, limit: int = 100) -> list[dict[str, Any]]:
        result = request_json(
            f"{self.base_url}/{path}?limit={limit}",
            headers=self.headers(authenticated=True),
        )
        return list(result.get("resources") or [])

    def snapshot(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "accounts": [], "transactions": []}
        return {
            "enabled": True,
            "environment": self.environment,
            "external_user_id": self.external_user_id,
            "accounts": self.list_resources("accounts", limit=100),
            "transactions": self.list_resources("transactions", limit=20),
        }


class MetaClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.token = os.environ.get("META_ACCESS_TOKEN", "").strip()
        self.api_version = config["meta_api_version"]
        self.account_id = config["meta_ad_account_id"]
        if not self.token:
            raise RuntimeError("META_ACCESS_TOKEN must be defined.")

    def fetch_daily(
        self,
        since: date,
        until: date,
        *,
        level: str = "account",
        campaign_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        field_names = [
                "date_start",
                "date_stop",
                "spend",
                "impressions",
                "reach",
                "clicks",
                "actions",
                "action_values",
            ]
        if level == "campaign":
            field_names.append("campaign_name")
        params = {
                "fields": ",".join(field_names),
                "level": level,
                "time_increment": "1",
                "time_range": json.dumps(
                    {"since": since.isoformat(), "until": until.isoformat()},
                    separators=(",", ":"),
                ),
                "limit": "500",
            }
        query = urllib.parse.urlencode(params)
        url: str | None = (
            f"https://graph.facebook.com/{self.api_version}/{self.account_id}/insights?{query}"
        )
        rows: list[dict[str, Any]] = []
        headers = {"Authorization": f"Bearer {self.token}"}
        while url:
            result = request_json(url, headers=headers)
            rows.extend(result.get("data", []))
            url = result.get("paging", {}).get("next")
        if campaign_names:
            allowed = set(campaign_names)
            rows = [row for row in rows if row.get("campaign_name") in allowed]
        return rows


def action_value(actions: list[dict[str, Any]] | None) -> float:
    values = {item.get("action_type"): money(item.get("value")) for item in actions or []}
    for key in ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"):
        if key in values:
            return values[key]
    return 0.0


def metric_action_value(
    actions: list[dict[str, Any]] | None, action_types: tuple[str, ...]
) -> float:
    values = {item.get("action_type"): money(item.get("value")) for item in actions or []}
    for action_type in action_types:
        if action_type in values:
            return values[action_type]
    return 0.0


def affiliate_code_index(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for influencer in config.get("affiliate", {}).get("influencers", []):
        codes = list(influencer.get("codes") or [])
        if influencer.get("code"):
            codes.append(influencer["code"])
        for code in codes:
            normalized = str(code or "").strip().lower()
            if normalized:
                index[normalized] = influencer
    return index


def prorated_monthly_cost(
    amount: float,
    since: date,
    until: date,
    *,
    starts_at: str | None = None,
    ends_at: str | None = None,
) -> float:
    effective_since = max(since, date.fromisoformat(starts_at)) if starts_at else since
    effective_until = min(until, date.fromisoformat(ends_at)) if ends_at else until
    if effective_until < effective_since:
        return 0.0
    period_days = (effective_until - effective_since).days + 1
    return round(amount * period_days / 30.4375, 2)


@dataclass
class Allocation:
    model_name: str
    origin: str
    lot_id: str | None


class CostEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.lots = deepcopy(config["stock_lots"])
        for lot in self.lots:
            lot["remaining"] = int(lot["quantity"])
        self.snapshot = datetime.fromisoformat(config["stock_snapshot_at"])
        self.warnings: list[str] = []

    def resolve_catalog_mappings(self, catalog: dict[str, Any]) -> None:
        for lot in self.lots:
            if lot.get("product_handle") or "therm" not in lot["product_label"].lower():
                continue
            matches = []
            for product in catalog["products"]:
                description = (product.get("description") or "").lower()
                variant_titles = {
                    variant["title"] for variant in product.get("variants", {}).get("nodes", [])
                }
                if "vision thermique" in description and lot["variant_match"] in variant_titles:
                    matches.append(product["handle"])
            if len(matches) == 1:
                lot["product_handle"] = matches[0]

    @staticmethod
    def _line_key(line: dict[str, Any]) -> tuple[str, str]:
        variant = line.get("variant") or {}
        product = variant.get("product") or {}
        return product.get("handle") or "", variant.get("title") or ""

    def allocate(self, line: dict[str, Any], created_at: datetime) -> Allocation:
        unit_price = money(
            (line.get("originalUnitPriceSet") or {}).get("shopMoney", {}).get("amount")
        )
        if unit_price >= 70:
            return Allocation("sweatshirt_france", "France", None)

        handle, variant_title = self._line_key(line)
        if created_at >= self.snapshot:
            candidates = [
                lot
                for lot in self.lots
                if lot["status"] == "available"
                and lot.get("product_handle") == handle
                and lot.get("variant_match") == variant_title
                and lot["remaining"] > 0
            ]
            candidates.sort(key=lambda lot: 0 if lot["origin"] == "France" else 1)
            if candidates:
                lot = candidates[0]
                lot["remaining"] -= 1
                return Allocation(lot["cost_model"], lot["origin"], lot["id"])
            self.warnings.append(
                f"No available lot matched {handle or line.get('title')} / {variant_title}."
            )
        return Allocation("tshirt_france", "France (estimated)", None)

    def shipping_cost(self, order: dict[str, Any], has_sweatshirt: bool) -> float:
        title = " ".join(
            line.get("title", "") for line in order.get("shippingLines", {}).get("nodes", [])
        ).lower()
        for keyword, cost in self.config["shipping_costs"].items():
            if keyword.startswith("default_"):
                continue
            if keyword in title:
                return money(cost)
        fallback = "default_sweatshirt" if has_sweatshirt else "default_tshirt"
        return money(self.config["shipping_costs"][fallback])

    def order_cost(self, order: dict[str, Any]) -> dict[str, Any]:
        created_at = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
        item_cost = 0.0
        breakdown: dict[str, float] = defaultdict(float)
        units = 0
        allocations: list[dict[str, Any]] = []
        has_sweatshirt = False
        for line in order.get("lineItems", {}).get("nodes", []):
            quantity = int(line.get("currentQuantity") or 0)
            unit_price = money(
                (line.get("originalUnitPriceSet") or {}).get("shopMoney", {}).get("amount")
            )
            # Shopify line items also contain free sweets and stickers. They are not garments.
            if unit_price < 20:
                continue
            units += quantity
            for _ in range(quantity):
                allocation = self.allocate(line, created_at)
                model = self.config["cost_models"][allocation.model_name]
                item_cost += sum(money(value) for value in model.values())
                for component, value in model.items():
                    breakdown[component] += money(value)
                has_sweatshirt = has_sweatshirt or allocation.model_name.startswith("sweatshirt")
                allocations.append(
                    {
                        "line": line.get("title"),
                        "variant": (line.get("variant") or {}).get("title"),
                        "origin": allocation.origin,
                        "lot_id": allocation.lot_id,
                        "cost_model": allocation.model_name,
                    }
                )
        shipping = self.shipping_cost(order, has_sweatshirt) if units else 0.0
        breakdown["shipping"] += shipping
        return {
            "items": round(item_cost, 2),
            "shipping": shipping,
            "total": round(item_cost + shipping, 2),
            "units": units,
            "allocations": allocations,
            "breakdown": {key: round(value, 2) for key, value in breakdown.items()},
        }

    def stock_summary(self, catalog: dict[str, Any]) -> list[dict[str, Any]]:
        shopify_index: dict[tuple[str, str], int] = {}
        for product in catalog["products"]:
            for variant in product["variants"]["nodes"]:
                shopify_index[(product["handle"], variant["title"])] = int(
                    variant.get("inventoryQuantity") or 0
                )
        return [
            {
                "id": lot["id"],
                "product": lot["product_label"],
                "variant": lot["variant_match"],
                "origin": lot["origin"],
                "status": lot["status"],
                "initial": lot["quantity"],
                "remaining": lot["remaining"],
                "shopify_quantity": shopify_index.get(
                    (lot.get("product_handle"), lot.get("variant_match"))
                )
                if lot.get("product_handle")
                else None,
                "shopify_linked": bool(lot.get("product_handle")),
            }
            for lot in self.lots
        ]


class DashboardBuilder:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.shopify = ShopifyClient(config)
        self.meta = MetaClient(config)
        self.bridge = BridgeClient()

    def build(
        self, since: date, until: date, *, include_analytics: bool = True
    ) -> dict[str, Any]:
        catalog = self.shopify.fetch_catalog()
        orders = self.shopify.fetch_orders(since, until)
        shopify_analytics = (
            self.shopify.fetch_analytics(since, until)
            if include_analytics
            else {"sessions": 0, "cart_additions": 0, "completed_checkouts": 0}
        )
        meta_rows = self.meta.fetch_daily(since, until)
        geremy_config = self.config.get("geremy", {})
        geremy_campaigns = geremy_config.get("campaign_names", [])
        track_all_campaigns = bool(geremy_config.get("track_all_campaigns_after_start"))
        geremy_rows = self.meta.fetch_daily(
            since,
            until,
            level="campaign",
            campaign_names=None if track_all_campaigns else geremy_campaigns,
        )
        engine = CostEngine(self.config)
        engine.resolve_catalog_mappings(catalog)

        daily: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "revenue": 0.0,
                "orders": 0,
                "units": 0,
                "variable_costs": 0.0,
                "contribution_margin": 0.0,
                "ad_spend": 0.0,
                "meta_purchases": 0.0,
                "meta_purchase_value": 0.0,
                "impressions": 0,
                "clicks": 0,
                "urssaf_estimated": 0.0,
                "geremy_revenue": 0.0,
                "geremy_commission": 0.0,
                "business_expenses": 0.0,
                "site_visits": 0.0,
                "add_to_carts": 0.0,
            }
        )
        order_details: list[dict[str, Any]] = []
        sold_model_units: dict[str, int] = defaultdict(int)
        affiliate_config = self.config.get("affiliate", {})
        affiliate_index = affiliate_code_index(self.config)
        affiliate_rows: dict[str, dict[str, Any]] = {}
        for influencer in affiliate_config.get("influencers", []):
            name = influencer.get("name") or influencer.get("handle") or "Influenceur"
            key = name.lower()
            codes = list(influencer.get("codes") or [])
            if influencer.get("code"):
                codes.append(influencer["code"])
            seeded_units = int(influencer.get("seeded_units") or 0)
            seed_cost = money(
                influencer.get(
                    "product_seed_cost",
                    affiliate_config.get("default_product_seed_cost", 0),
                )
            )
            affiliate_rows[key] = {
                "name": name,
                "handle": influencer.get("handle"),
                "codes": sorted({str(code).strip() for code in codes if str(code).strip()}),
                "commission_rate": float(
                    influencer.get(
                        "commission_rate",
                        affiliate_config.get("default_commission_rate", 0.0),
                    )
                ),
                "discount_rate": float(
                    influencer.get(
                        "discount_rate",
                        affiliate_config.get("default_discount_rate", 0.0),
                    )
                ),
                "seeded_units": seeded_units,
                "seed_cost": seed_cost,
                "seed_total_cost": round(seeded_units * seed_cost, 2),
                "orders": 0,
                "units": 0,
                "revenue": 0.0,
                "discounts": 0.0,
                "contribution_margin": 0.0,
                "commission_due": 0.0,
            }

        included_statuses = {"PAID", "PARTIALLY_PAID", "PARTIALLY_REFUNDED"}
        valid_orders = [
            order
            for order in orders
            if not order.get("cancelledAt")
            and order.get("displayFinancialStatus") in included_statuses
        ]
        valid_orders.sort(key=lambda order: order["createdAt"])
        for order in valid_orders:
            day = iso_date(order["createdAt"])
            revenue = money(order["currentTotalPriceSet"]["shopMoney"]["amount"])
            discounts = money(order["currentTotalDiscountsSet"]["shopMoney"]["amount"])
            costs = engine.order_cost(order)
            units = costs["units"]
            discount_codes = [
                str(code).strip()
                for code in order.get("discountCodes", [])
                if str(code).strip()
            ]
            matched_affiliate = None
            matched_code = None
            for code in discount_codes:
                influencer = affiliate_index.get(code.lower())
                if influencer:
                    matched_affiliate = influencer
                    matched_code = code
                    break
            daily[day]["revenue"] += revenue
            daily[day]["orders"] += 1
            daily[day]["units"] += units
            daily[day]["variable_costs"] += costs["total"]
            daily[day]["contribution_margin"] += revenue - costs["total"]
            daily[day]["urssaf_estimated"] += costs["breakdown"].get("urssaf", 0.0)
            if matched_affiliate:
                name = (
                    matched_affiliate.get("name")
                    or matched_affiliate.get("handle")
                    or matched_code
                )
                key = name.lower()
                if key not in affiliate_rows:
                    affiliate_rows[key] = {
                        "name": name,
                        "handle": matched_affiliate.get("handle"),
                        "codes": [matched_code],
                        "commission_rate": float(
                            matched_affiliate.get(
                                "commission_rate",
                                affiliate_config.get("default_commission_rate", 0.0),
                            )
                        ),
                        "discount_rate": float(
                            matched_affiliate.get(
                                "discount_rate",
                                affiliate_config.get("default_discount_rate", 0.0),
                            )
                        ),
                        "seeded_units": int(matched_affiliate.get("seeded_units") or 0),
                        "seed_cost": money(
                            matched_affiliate.get(
                                "product_seed_cost",
                                affiliate_config.get("default_product_seed_cost", 0),
                            )
                        ),
                        "seed_total_cost": 0.0,
                        "orders": 0,
                        "units": 0,
                        "revenue": 0.0,
                        "discounts": 0.0,
                        "contribution_margin": 0.0,
                        "commission_due": 0.0,
                    }
                    affiliate_rows[key]["seed_total_cost"] = round(
                        affiliate_rows[key]["seeded_units"] * affiliate_rows[key]["seed_cost"],
                        2,
                    )
                row = affiliate_rows[key]
                if matched_code not in row["codes"]:
                    row["codes"].append(matched_code)
                row["orders"] += 1
                row["units"] += units
                row["revenue"] += revenue
                row["discounts"] += discounts
                row["contribution_margin"] += revenue - costs["total"]
                row["commission_due"] += round(revenue * row["commission_rate"], 2)
            for allocation in costs["allocations"]:
                sold_model_units[allocation["cost_model"]] += 1
            order_details.append(
                {
                    "name": order["name"],
                    "date": day,
                    "financial_status": order.get("displayFinancialStatus"),
                    "revenue": revenue,
                    "variable_costs": costs["total"],
                    "discount_codes": discount_codes,
                    "margin": round(revenue - costs["total"], 2),
                    "units": units,
                    "allocations": costs["allocations"],
                    "cost_breakdown": costs["breakdown"],
                }
            )

        for row in meta_rows:
            day = row["date_start"]
            daily[day]["ad_spend"] += money(row.get("spend"))
            daily[day]["meta_purchases"] += action_value(row.get("actions"))
            daily[day]["meta_purchase_value"] += action_value(row.get("action_values"))
            daily[day]["impressions"] += int(row.get("impressions") or 0)
            daily[day]["clicks"] += int(row.get("clicks") or 0)
            daily[day]["site_visits"] += metric_action_value(
                row.get("actions"), ("landing_page_view",)
            )
            daily[day]["add_to_carts"] += metric_action_value(
                row.get("actions"),
                (
                    "omni_add_to_cart",
                    "add_to_cart",
                    "offsite_conversion.fb_pixel_add_to_cart",
                ),
            )

        campaign_performance_index: dict[str, dict[str, float]] = defaultdict(
            lambda: {"spend": 0.0, "purchases": 0.0, "purchase_value": 0.0}
        )
        for row in geremy_rows:
            campaign = row.get("campaign_name") or "Campagne sans nom"
            campaign_performance_index[campaign]["spend"] += money(row.get("spend"))
            campaign_performance_index[campaign]["purchases"] += action_value(
                row.get("actions")
            )
            campaign_performance_index[campaign]["purchase_value"] += action_value(
                row.get("action_values")
            )
        campaign_performance = []
        for campaign, values in campaign_performance_index.items():
            spend = round(values["spend"], 2)
            purchases = round(values["purchases"], 2)
            purchase_value = round(values["purchase_value"], 2)
            campaign_performance.append(
                {
                    "campaign": campaign,
                    "spend": spend,
                    "purchases": purchases,
                    "purchase_value": purchase_value,
                    "roas": round(purchase_value / spend, 2) if spend else None,
                    "cpa": round(spend / purchases, 2) if purchases else None,
                }
            )
        campaign_performance.sort(key=lambda row: row["spend"], reverse=True)

        managed_campaigns = (
            sorted({row.get("campaign_name") for row in geremy_rows if row.get("campaign_name")})
            if track_all_campaigns
            else geremy_campaigns
        )
        campaign_ranges = []
        for campaign_name in managed_campaigns:
            active_days = sorted(
                row["date_start"]
                for row in geremy_rows
                if row.get("campaign_name") == campaign_name and money(row.get("spend")) > 0
            )
            if active_days:
                campaign_ranges.append(
                    {"campaign": campaign_name, "since": active_days[0], "until": active_days[-1]}
                )
        commission_rate = float(geremy_config.get("commission_rate", 0.0))
        mission_started_at = geremy_config.get("mission_started_at", "9999-12-31")
        for day, row in daily.items():
            managed = day >= mission_started_at and any(
                period["since"] <= day <= period["until"] for period in campaign_ranges
            )
            if managed:
                row["geremy_revenue"] = row["revenue"]
                row["geremy_commission"] = round(row["revenue"] * commission_rate, 2)
        manual_expenses = [
            expense
            for expense in self.config.get("business_expenses", [])
            if since.isoformat() <= expense.get("date", "") <= until.isoformat()
        ]
        for expense in manual_expenses:
            daily[expense["date"]]["business_expenses"] += money(expense.get("amount"))

        total_fixed_monthly = round(sum(self.config["monthly_fixed_costs"].values()), 2)
        business_started_at = date.fromisoformat(self.config["business_started_at"])
        period_days = (until - since).days + 1
        fixed_period_start = max(since, business_started_at)
        fixed_period_days = max(0, (until - fixed_period_start).days + 1)
        fixed_prorated = round(total_fixed_monthly * fixed_period_days / 30.4375, 2)
        fixed_daily = total_fixed_monthly / 30.4375
        daily_rows = []
        cursor = since
        while cursor <= until:
            key = cursor.isoformat()
            row = daily[key]
            spend = round(row["ad_spend"], 2)
            revenue = round(row["revenue"], 2)
            contribution = round(row["contribution_margin"], 2)
            daily_fixed_cost = fixed_daily if cursor >= business_started_at else 0.0
            daily_rows.append(
                {
                    "date": key,
                    **{k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()},
                    "blended_roas": round(revenue / spend, 2) if spend else None,
                    "meta_roas": round(row["meta_purchase_value"] / spend, 2) if spend else None,
                    "estimated_result": round(
                        contribution
                        - spend
                        - daily_fixed_cost
                        - row["geremy_commission"]
                        - row["business_expenses"],
                        2,
                    ),
                }
            )
            cursor += timedelta(days=1)

        totals = {
            "revenue": round(sum(row["revenue"] for row in daily_rows), 2),
            "orders": sum(row["orders"] for row in daily_rows),
            "units": sum(row["units"] for row in daily_rows),
            "variable_costs": round(sum(row["variable_costs"] for row in daily_rows), 2),
            "contribution_margin": round(
                sum(row["contribution_margin"] for row in daily_rows), 2
            ),
            "ad_spend": round(sum(row["ad_spend"] for row in daily_rows), 2),
            "fixed_costs_prorated": fixed_prorated,
            "fixed_cost_days": fixed_period_days,
            "meta_purchases": round(sum(row["meta_purchases"] for row in daily_rows), 2),
            "meta_purchase_value": round(
                sum(row["meta_purchase_value"] for row in daily_rows), 2
            ),
            "urssaf_estimated": round(
                sum(row["urssaf_estimated"] for row in daily_rows), 2
            ),
            "geremy_revenue": round(sum(row["geremy_revenue"] for row in daily_rows), 2),
            "geremy_commission": round(
                sum(row["geremy_commission"] for row in daily_rows), 2
            ),
            "site_visits": shopify_analytics["sessions"],
            "add_to_carts": shopify_analytics["cart_additions"],
            "completed_checkouts": shopify_analytics["completed_checkouts"],
        }
        geremy_paid = round(
            sum(
                money(payment.get("amount"))
                for payment in geremy_config.get("payments", [])
                if since.isoformat() <= payment.get("date", "") <= until.isoformat()
            ),
            2,
        )
        urssaf_paid = round(
            sum(
                money(payment.get("amount"))
                for payment in self.config.get("urssaf_payments", [])
                if since.isoformat() <= payment.get("date", "") <= until.isoformat()
            ),
            2,
        )
        totals["geremy_paid"] = geremy_paid
        totals["geremy_balance_due"] = round(totals["geremy_commission"] - geremy_paid, 2)
        totals["urssaf_paid"] = urssaf_paid
        totals["urssaf_balance_due"] = round(totals["urssaf_estimated"] - urssaf_paid, 2)
        totals["business_expenses"] = round(
            sum(money(expense.get("amount")) for expense in manual_expenses), 2
        )
        totals["blended_roas"] = (
            round(totals["revenue"] / totals["ad_spend"], 2) if totals["ad_spend"] else None
        )
        totals["meta_roas"] = (
            round(totals["meta_purchase_value"] / totals["ad_spend"], 2)
            if totals["ad_spend"]
            else None
        )
        totals["estimated_result"] = round(
            totals["contribution_margin"]
            - totals["ad_spend"]
            - totals["fixed_costs_prorated"]
            - totals["geremy_commission"]
            - totals["business_expenses"],
            2,
        )
        totals["cpa"] = (
            round(totals["ad_spend"] / totals["orders"], 2) if totals["orders"] else None
        )
        targets = self.config.get("kpi_targets", {})
        current_margin_rate = float(self.config.get("current_margin_rate", 0.0))
        margin_after_geremy = current_margin_rate - commission_rate
        target_roas = (
            round(1 / margin_after_geremy, 2) if margin_after_geremy > 0 else None
        )
        gross_break_even_roas = (
            round(1 / current_margin_rate, 2) if current_margin_rate > 0 else None
        )
        configured_target_cpa = money(targets.get("cpa"))
        totals["target_roas"] = target_roas
        totals["gross_break_even_roas"] = gross_break_even_roas
        totals["current_margin_rate"] = current_margin_rate
        totals["margin_after_geremy"] = round(margin_after_geremy, 4)
        totals["target_cpa"] = (
            configured_target_cpa
            or (
                round((totals["revenue"] / totals["orders"]) / target_roas, 2)
                if totals["orders"] and target_roas
                else None
            )
        )
        totals["average_order_value"] = (
            round(totals["revenue"] / totals["orders"], 2) if totals["orders"] else None
        )
        totals["conversion_rate"] = (
            round(totals["completed_checkouts"] / totals["site_visits"] * 100, 2)
            if totals["site_visits"]
            else None
        )
        tshirt_units = sum(
            units for model, units in sold_model_units.items() if model.startswith("tshirt")
        )
        sweatshirt_units = sum(
            units for model, units in sold_model_units.items() if model.startswith("sweatshirt")
        )
        mix_units = tshirt_units + sweatshirt_units
        totals["sales_mix"] = {
            "tshirt_units": tshirt_units,
            "sweatshirt_units": sweatshirt_units,
            "tshirt_share": round(tshirt_units / mix_units * 100, 2) if mix_units else None,
            "sweatshirt_share": (
                round(sweatshirt_units / mix_units * 100, 2) if mix_units else None
            ),
        }

        stock = engine.stock_summary(catalog)
        available = sum(row["remaining"] for row in stock if row["status"] == "available")
        incoming = sum(row["remaining"] for row in stock if row["status"] == "incoming")
        warnings = sorted(set(engine.warnings))
        if any(not row["shopify_linked"] for row in stock if row["status"] == "incoming"):
            warnings.append("Turkey incoming lots still need their Shopify product mapping.")
        mismatches = [
            row
            for row in stock
            if row["status"] == "available"
            and row["shopify_quantity"] is not None
            and row["shopify_quantity"] != row["remaining"]
        ]
        if mismatches:
            mismatch_units = sum(row["remaining"] for row in mismatches)
            warnings.append(
                f"{mismatch_units} available physical units do not match Shopify inventory."
            )
        if sum(product.get("totalInventory") or 0 for product in catalog["products"]) == 0:
            warnings.append("Shopify currently reports zero inventory for the whole catalog.")
        if not self.config.get("business_expenses"):
            warnings.append(
                "No sample or other manual business expense has been recorded yet."
            )
        if totals["geremy_balance_due"] < 0:
            warnings.append(
                f"Geremy payment exceeds the 9% calculated commission by "
                f"{abs(totals['geremy_balance_due']):.2f} EUR."
            )

        margin_profiles = []
        for configured_profile in self.config.get("margin_profiles", []):
            profile = deepcopy(configured_profile)
            margin_rate = float(profile.get("margin_rate", 0.0))
            projected_contribution = round(totals["revenue"] * margin_rate, 2)
            profile["projected_contribution"] = projected_contribution
            profile["projected_result"] = round(
                projected_contribution
                - totals["ad_spend"]
                - totals["geremy_commission"]
                - totals["fixed_costs_prorated"]
                - totals["business_expenses"],
                2,
            )
            margin_profiles.append(profile)

        for sale in affiliate_config.get("manual_sales", []):
            if not since.isoformat() <= sale.get("date", "") <= until.isoformat():
                continue
            name = sale.get("influencer") or sale.get("name") or sale.get("code") or "Influenceur"
            key = str(name).lower()
            if key not in affiliate_rows:
                commission_rate = float(
                    sale.get(
                        "commission_rate",
                        affiliate_config.get("default_commission_rate", 0.0),
                    )
                )
                affiliate_rows[key] = {
                    "name": name,
                    "handle": sale.get("handle"),
                    "codes": [sale.get("code")] if sale.get("code") else [],
                    "commission_rate": commission_rate,
                    "discount_rate": float(
                        sale.get(
                            "discount_rate",
                            affiliate_config.get("default_discount_rate", 0.0),
                        )
                    ),
                    "seeded_units": int(sale.get("seeded_units") or 0),
                    "seed_cost": money(
                        sale.get(
                            "product_seed_cost",
                            affiliate_config.get("default_product_seed_cost", 0),
                        )
                    ),
                    "seed_total_cost": 0.0,
                    "orders": 0,
                    "units": 0,
                    "revenue": 0.0,
                    "discounts": 0.0,
                    "contribution_margin": 0.0,
                    "commission_due": 0.0,
                }
                affiliate_rows[key]["seed_total_cost"] = round(
                    affiliate_rows[key]["seeded_units"] * affiliate_rows[key]["seed_cost"],
                    2,
                )
            row = affiliate_rows[key]
            revenue = money(sale.get("revenue"))
            units = int(sale.get("units") or sale.get("orders") or 0)
            contribution = (
                money(sale.get("contribution_margin"))
                if sale.get("contribution_margin") is not None
                else round(revenue * current_margin_rate, 2)
            )
            row["orders"] += int(sale.get("orders") or 1)
            row["units"] += units
            row["revenue"] += revenue
            row["discounts"] += money(sale.get("discounts"))
            row["contribution_margin"] += contribution
            row["commission_due"] += round(revenue * row["commission_rate"], 2)
            if sale.get("code") and sale["code"] not in row["codes"]:
                row["codes"].append(sale["code"])

        favikon_monthly_cost = money(affiliate_config.get("favikon_monthly_cost"))
        favikon_period_cost = prorated_monthly_cost(
            favikon_monthly_cost,
            since,
            until,
            starts_at=affiliate_config.get("favikon_started_at"),
            ends_at=affiliate_config.get("favikon_ended_at"),
        )
        affiliate_influencers = []
        affiliate_count_for_allocation = max(1, len(affiliate_rows))
        favikon_share = (
            round(favikon_period_cost / affiliate_count_for_allocation, 2)
            if affiliate_rows
            else 0.0
        )
        for row in affiliate_rows.values():
            row["codes"] = sorted({str(code).strip() for code in row["codes"] if str(code).strip()})
            row["revenue"] = round(row["revenue"], 2)
            row["discounts"] = round(row["discounts"], 2)
            row["contribution_margin"] = round(row["contribution_margin"], 2)
            row["commission_due"] = round(row["commission_due"], 2)
            row["direct_result"] = round(
                row["contribution_margin"]
                - row["commission_due"]
                - row["seed_total_cost"],
                2,
            )
            row["favikon_share"] = favikon_share
            row["net_result"] = round(row["direct_result"] - favikon_share, 2)
            row["break_even_sales"] = (
                int(-(-max(0.0, row["seed_total_cost"] + favikon_share)
                      // max(0.01, row["contribution_margin"] / max(1, row["units"])
                             - (row["revenue"] / max(1, row["units"])) * row["commission_rate"])))
                if row["units"]
                else None
            )
            row["status"] = "rentable" if row["net_result"] >= 0 else "perte"
            affiliate_influencers.append(row)
        affiliate_influencers.sort(key=lambda row: row["net_result"], reverse=True)
        affiliate_totals = {
            "favikon_monthly_cost": favikon_monthly_cost,
            "favikon_period_cost": favikon_period_cost,
            "orders": sum(row["orders"] for row in affiliate_influencers),
            "units": sum(row["units"] for row in affiliate_influencers),
            "revenue": round(sum(row["revenue"] for row in affiliate_influencers), 2),
            "discounts": round(sum(row["discounts"] for row in affiliate_influencers), 2),
            "contribution_margin": round(
                sum(row["contribution_margin"] for row in affiliate_influencers), 2
            ),
            "commission_due": round(
                sum(row["commission_due"] for row in affiliate_influencers), 2
            ),
            "seed_total_cost": round(
                sum(row["seed_total_cost"] for row in affiliate_influencers), 2
            ),
            "direct_result": round(
                sum(row["direct_result"] for row in affiliate_influencers), 2
            ),
            "net_result": round(
                sum(row["direct_result"] for row in affiliate_influencers)
                - favikon_period_cost,
                2,
            ),
            "profitable_count": sum(
                1 for row in affiliate_influencers if row["net_result"] >= 0
            ),
            "loss_count": sum(1 for row in affiliate_influencers if row["net_result"] < 0),
        }

        legacy_bank_account = self.config.get("bank_account", {})
        bank_accounts = deepcopy(self.config.get("bank_accounts", []))
        bank_transactions: list[dict[str, Any]] = []
        bridge_status: dict[str, Any] = {
            "enabled": self.bridge.enabled,
            "environment": self.bridge.environment,
            "connected": False,
        }
        public_deployment = os.environ.get("PUBLIC_DEPLOYMENT", "").lower() in {
            "1",
            "true",
            "yes",
        }
        show_bridge_transactions = (
            os.environ.get("BRIDGE_SHOW_TRANSACTIONS", "").lower()
            in {"1", "true", "yes"}
        ) or not public_deployment
        if self.bridge.enabled:
            try:
                bridge_snapshot = self.bridge.snapshot()
                bridge_accounts = bridge_snapshot["accounts"]
                if bridge_accounts:
                    bank_accounts = [
                        {
                            "label": account.get("name") or f"Compte {account.get('id')}",
                            "balance": account.get("balance"),
                            "currency_code": account.get("currency_code", "EUR"),
                            "recorded_at": iso_date(account.get("updated_at")),
                            "source": (
                                "bridge_sandbox"
                                if self.bridge.environment == "sandbox"
                                else "bridge"
                            ),
                            "account_id": account.get("id"),
                            "type": account.get("type"),
                            "status": account.get("last_refresh_status"),
                        }
                        for account in bridge_accounts
                    ]
                if show_bridge_transactions:
                    bank_transactions = [
                        {
                            "id": transaction.get("id"),
                            "date": iso_date(transaction.get("date")),
                            "description": transaction.get("clean_description")
                            or transaction.get("provider_description")
                            or "Transaction",
                            "amount": transaction.get("amount"),
                            "currency_code": transaction.get("currency_code", "EUR"),
                            "account_id": transaction.get("account_id"),
                            "operation_type": transaction.get("operation_type"),
                        }
                        for transaction in bridge_snapshot["transactions"]
                        if not transaction.get("deleted")
                    ]
                bridge_status.update(
                    {
                        "connected": bool(bridge_accounts),
                        "account_count": len(bridge_accounts),
                        "transaction_count": len(bridge_snapshot["transactions"]),
                        "transactions_visible": show_bridge_transactions,
                    }
                )
            except Exception as exc:
                bridge_status["error"] = str(exc)[:240]
                warnings.append(f"Bridge banking sync failed: {bridge_status['error']}")
        if not bank_accounts:
            bank_accounts = [
                {
                    "label": "Compte perso",
                    "balance": None,
                    "recorded_at": None,
                    "source": "non_connecte",
                },
                {
                    "label": "Compte pro Tesign",
                    "balance": legacy_bank_account.get("balance"),
                    "recorded_at": legacy_bank_account.get("recorded_at"),
                    "source": legacy_bank_account.get("source", "manual"),
                },
                {
                    "label": "Compte loisir",
                    "balance": None,
                    "recorded_at": None,
                    "source": "non_connecte",
                },
                {
                    "label": "Compte economie",
                    "balance": None,
                    "recorded_at": None,
                    "source": "non_connecte",
                },
            ]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {"since": since.isoformat(), "until": until.isoformat(), "days": period_days},
            "shop": catalog["shop"],
            "totals": totals,
            "stock_totals": {"available": available, "incoming": incoming},
            "fixed_costs_monthly": self.config["monthly_fixed_costs"],
            "financial": {
                "geremy": {
                    "rate": commission_rate,
                    "campaigns": managed_campaigns,
                    "active_ranges": campaign_ranges,
                },
                "bank_account": self.config.get("bank_account", {}),
                "bank_accounts": bank_accounts,
                "bank_transactions": bank_transactions,
                "bridge": bridge_status,
                "manual_expenses": manual_expenses,
            },
            "campaign_performance": campaign_performance,
            "affiliate": {
                "totals": affiliate_totals,
                "influencers": affiliate_influencers,
                "settings": {
                    "favikon_started_at": affiliate_config.get("favikon_started_at"),
                    "favikon_ended_at": affiliate_config.get("favikon_ended_at"),
                    "default_commission_rate": float(
                        affiliate_config.get("default_commission_rate", 0.0)
                    ),
                    "default_discount_rate": float(
                        affiliate_config.get("default_discount_rate", 0.0)
                    ),
                    "default_product_seed_cost": money(
                        affiliate_config.get("default_product_seed_cost")
                    ),
                },
            },
            "traffic_source": "Shopify Analytics sessions",
            "margin_profiles": margin_profiles,
            "stock": stock,
            "daily": daily_rows,
            "orders": list(reversed(order_details)),
            "warnings": warnings,
            "read_only": True,
        }


class Cache:
    def __init__(self, builder: DashboardBuilder, refresh_seconds: int) -> None:
        self.builder = builder
        self.refresh_seconds = refresh_seconds
        self.lock = threading.Lock()
        self.values: dict[tuple[str, str, bool], tuple[float, dict[str, Any]]] = {}

    def get(
        self,
        since: date,
        until: date,
        force: bool = False,
        *,
        include_analytics: bool = True,
    ) -> dict[str, Any]:
        key = (since.isoformat(), until.isoformat(), include_analytics)
        with self.lock:
            cached = self.values.get(key)
            if cached and not force and time.time() - cached[0] < self.refresh_seconds:
                return cached[1]
            value = self.builder.build(
                since, until, include_analytics=include_analytics
            )
            self.values[key] = (time.time(), value)
            return value

    def get_dashboard(self, since: date, until: date, force: bool = False) -> dict[str, Any]:
        period_data = self.get(since, until, force=force)
        cumulative_since = date.fromisoformat(self.builder.config["business_started_at"])
        cumulative_data = self.get(
            cumulative_since,
            date.today(),
            force=force,
            include_analytics=False,
        )
        trend_since = max(cumulative_since, until - timedelta(days=730))
        trend_until = min(until, date.today())
        trend_daily = [
            row
            for row in cumulative_data["daily"]
            if trend_since.isoformat() <= row["date"] <= trend_until.isoformat()
        ]
        response = deepcopy(period_data)
        response["cumulative"] = {
            "period": cumulative_data["period"],
            "totals": cumulative_data["totals"],
            "is_estimate": True,
            "is_complete": bool(
                self.builder.config.get("historical_shopify_orders_complete")
            ),
            "basis": "all known revenues minus all known expenses",
            "missing_data": (
                None
                if self.builder.config.get("historical_shopify_orders_complete")
                else "Historique des commandes Shopify au-dela de 60 jours"
            ),
        }
        response["trend_history"] = {
            "period": {
                "since": trend_since.isoformat(),
                "until": trend_until.isoformat(),
                "days": len(trend_daily),
            },
            "daily": trend_daily,
            "totals": {
                "revenue": round(sum(row["revenue"] for row in trend_daily), 2),
                "contribution_margin": round(
                    sum(row["contribution_margin"] for row in trend_daily), 2
                ),
                "ad_spend": round(sum(row["ad_spend"] for row in trend_daily), 2),
                "estimated_result": round(
                    sum(row["estimated_result"] for row in trend_daily), 2
                ),
            },
            "grain": "daily_source_monthly_display",
            "label": "24 derniers mois",
        }
        return response


def write_snapshot(data: dict[str, Any]) -> None:
    (ROOT / "latest-dashboard.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def background_sync(cache: Cache, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        today = date.today()
        try:
            data = cache.get_dashboard(today - timedelta(days=90), today, force=True)
            write_snapshot(data)
            print(
                f"Background sync: {data['totals']['orders']} orders, "
                f"{data['totals']['revenue']:.2f} EUR revenue."
            )
        except Exception as exc:
            print(f"Background sync failed: {exc}")
        stop_event.wait(cache.refresh_seconds)


def parse_period(query: dict[str, list[str]]) -> tuple[date, date]:
    today = date.today()
    since = date.fromisoformat(query.get("since", [(today - timedelta(days=30)).isoformat()])[0])
    until = date.fromisoformat(query.get("until", [today.isoformat()])[0])
    if until < since:
        raise ValueError("until must be on or after since")
    if (until - since).days > 3650:
        raise ValueError("period cannot exceed 10 years")
    return since, until


def make_handler(cache: Cache):
    public_deployment = os.environ.get("PUBLIC_DEPLOYMENT", "").lower() in {
        "1",
        "true",
        "yes",
    }

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path == "/":
                    self.send_bytes(HTML_PATH.read_bytes(), "text/html; charset=utf-8")
                    return
                if parsed.path == "/static/favicon.png":
                    self.send_bytes(
                        (STATIC_PATH / "favicon.png").read_bytes(),
                        "image/png",
                    )
                    return
                if parsed.path in ("/api/dashboard", "/api/refresh"):
                    since, until = parse_period(urllib.parse.parse_qs(parsed.query))
                    data = cache.get_dashboard(
                        since,
                        until,
                        force=parsed.path == "/api/refresh" and not public_deployment,
                    )
                    self.send_bytes(
                        json.dumps(data, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8",
                    )
                    return
                self.send_bytes(b'{"error":"not found"}', "application/json", 404)
            except Exception as exc:  # The UI needs a structured connector error.
                self.send_bytes(
                    json.dumps({"error": str(exc)}).encode("utf-8"),
                    "application/json; charset=utf-8",
                    500,
                )

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[{self.log_date_time_string()}] {fmt % args}")

    return Handler


def load_config() -> dict[str, Any]:
    load_local_env()
    environment_config = os.getenv("TESIGN_CONFIG_JSON")
    if environment_config:
        config = json.loads(environment_config)
    else:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    affiliate_config = deepcopy(DEFAULT_AFFILIATE_CONFIG)
    affiliate_config.update(config.get("affiliate", {}))
    config["affiliate"] = affiliate_config
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Tesign stock and advertising dashboard")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--refresh-minutes", type=int, default=10)
    parser.add_argument("--once", action="store_true", help="Generate JSON and exit")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--output", default=str(ROOT / "latest-dashboard.json"))
    args = parser.parse_args()

    config = load_config()
    builder = DashboardBuilder(config)
    if args.once:
        today = date.today()
        since = date.fromisoformat(args.since) if args.since else today - timedelta(days=30)
        until = date.fromisoformat(args.until) if args.until else today
        data = builder.build(since, until)
        Path(args.output).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"Dashboard written to {args.output}: "
            f"{data['totals']['orders']} orders, {data['totals']['revenue']:.2f} EUR revenue."
        )
        return

    cache = Cache(builder, max(args.refresh_minutes, 1) * 60)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(cache))
    stop_event = threading.Event()
    sync_thread = threading.Thread(
        target=background_sync, args=(cache, stop_event), name="tesign-sync", daemon=True
    )
    sync_thread.start()
    print(f"Tesign dashboard: http://{args.host}:{args.port}")
    print("Read-only mode. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
