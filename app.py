from __future__ import annotations

import argparse
import calendar
import json
import math
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

from financials import business_banking, current_cost_reference, dated_unit_model, normalize_config
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
HTML_PATH = ROOT / "dashboard.html"
ENZO_HTML_PATH = ROOT / "enzo.html"
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


def currency_code(value: Any, fallback: str = "EUR") -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("code") or fallback)
    if isinstance(value, str) and value:
        return value
    return fallback


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
        host = urllib.parse.urlparse(url).hostname or "connecteur"
        raise RuntimeError(f"Le connecteur {host} a répondu HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Un connecteur n'a pas répondu. Les données ne sont pas actualisées.") from None


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
          shop { name currencyCode ianaTimezone }
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
              id name createdAt updatedAt cancelledAt displayFinancialStatus displayFulfillmentStatus
              currentTotalPriceSet { shopMoney { amount currencyCode } }
              currentSubtotalPriceSet { shopMoney { amount currencyCode } }
              currentTotalDiscountsSet { shopMoney { amount currencyCode } }
              currentShippingPriceSet { shopMoney { amount currencyCode } }
              totalRefundedSet { shopMoney { amount currencyCode } }
              netPaymentSet { shopMoney { amount currencyCode } }
              discountCodes
              shippingLines(first: 10) { nodes { title } }
              lineItems(first: 20) {
                pageInfo { hasNextPage endCursor }
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
        # Fetch an extra day on each side. Build applies the exact shop-local
        # boundaries; an unqualified Shopify date search can otherwise lose
        # orders placed around local midnight.
        search = (
            f"created_at:>={(since - timedelta(days=1)).isoformat()} "
            f"created_at:<{(until + timedelta(days=2)).isoformat()}"
        )
        while True:
            data = self.graphql(query, {"after": after, "search": search})
            connection = data["orders"]
            orders.extend(connection["nodes"])
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
        for order in orders:
            page = order["lineItems"].get("pageInfo") or {}
            while page.get("hasNextPage"):
                result = self.graphql("""
                query MoreOrderLines($id: ID!, $after: String!) {
                  order(id: $id) {
                    lineItems(first: 100, after: $after) {
                      pageInfo { hasNextPage endCursor }
                      nodes {
                        id title quantity currentQuantity sku
                        originalUnitPriceSet { shopMoney { amount currencyCode } }
                        discountedTotalSet { shopMoney { amount currencyCode } }
                        variant { id title sku product { id title handle } }
                      }
                    }
                  }
                }
                """, {"id": order["id"], "after": page["endCursor"]})
                connection = result["order"]["lineItems"]
                order["lineItems"]["nodes"].extend(connection["nodes"])
                page = connection["pageInfo"]
                order["lineItems"]["pageInfo"] = page
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
        if not rows:
            raise RuntimeError("Shopify Analytics returned no aggregate row")
        row = rows[0]
        required = ("sessions", "sessions_with_cart_additions", "sessions_that_completed_checkout")
        if not isinstance(row, dict) or any(row.get(key) is None for key in required):
            raise RuntimeError("Shopify Analytics returned incomplete aggregate metrics")
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
            "transactions": self.list_resources("transactions", limit=20)
            if os.environ.get("BRIDGE_SHOW_TRANSACTIONS", "").lower() in {"1", "true", "yes"}
            and os.environ.get("PUBLIC_DEPLOYMENT", "").lower() not in {"1", "true", "yes"}
            else [],
        }


class PowensClient:
    def __init__(self) -> None:
        raw_domain = os.environ.get("POWENS_DOMAIN", "").strip()
        self.domain = raw_domain.removeprefix("https://").removeprefix("http://")
        self.domain = self.domain.removesuffix("/").removesuffix(".biapi.pro")
        self.client_id = os.environ.get("POWENS_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("POWENS_CLIENT_SECRET", "").strip()
        self.user_id = os.environ.get("POWENS_USER_ID", "").strip()
        self.access_token = (
            os.environ.get("POWENS_ACCESS_TOKEN", "").strip()
            or os.environ.get("POWENS_USER_TOKEN", "").strip()
        )
        self.show_transactions = os.environ.get("POWENS_SHOW_TRANSACTIONS", "").lower() in {
            "1",
            "true",
            "yes",
        } and os.environ.get("PUBLIC_DEPLOYMENT", "").lower() not in {"1", "true", "yes"}
        self._token = ""

    @property
    def enabled(self) -> bool:
        return bool(
            self.domain
            and (self.access_token or (self.client_id and self.client_secret and self.user_id))
        )

    @property
    def environment(self) -> str:
        return "sandbox" if self.domain.endswith("-sandbox") else "production"

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}.biapi.pro/2.0"

    def token(self) -> str:
        if self._token:
            return self._token
        if self.access_token:
            self._token = self.access_token
            return self._token
        if not self.user_id:
            raise RuntimeError("POWENS_USER_ID or POWENS_ACCESS_TOKEN must be defined.")
        result = request_json(
            f"{self.base_url}/auth/renew",
            method="POST",
            payload={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "id_user": self.user_id,
            },
        )
        self._token = result.get("access_token") or result.get("auth_token") or ""
        if not self._token:
            raise RuntimeError("Powens did not return an access token.")
        return self._token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}", "accept": "application/json"}

    def list_resource(self, path: str, *, limit: int = 100) -> list[dict[str, Any]]:
        separator = "&" if "?" in path else "?"
        result = request_json(
            f"{self.base_url}/{path}{separator}limit={limit}",
            headers=self.headers(),
        )
        if isinstance(result, list):
            return list(result)
        for key in ("accounts", "transactions", "resources", "items"):
            if isinstance(result.get(key), list):
                return list(result[key])
        return []

    def snapshot(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "accounts": [], "transactions": []}
        transactions = (
            self.list_resource("users/me/transactions", limit=20)
            if self.show_transactions
            else []
        )
        return {
            "enabled": True,
            "environment": self.environment,
            "domain": self.domain,
            "user_id": self.user_id or "me",
            "accounts": self.list_resource("users/me/accounts?all", limit=100),
            "transactions": transactions,
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
            field_names.extend(("campaign_id", "campaign_name"))
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
            if result.get("error") or not isinstance(result.get("data"), list):
                raise RuntimeError("Meta returned an invalid insights response")
            rows.extend(result["data"])
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
    total = 0.0
    cursor = effective_since
    while cursor <= effective_until:
        days_in_month = calendar.monthrange(cursor.year, cursor.month)[1]
        segment_until = min(effective_until, cursor.replace(day=days_in_month))
        total += amount * ((segment_until - cursor).days + 1) / days_in_month
        cursor = segment_until + timedelta(days=1)
    return round(total, 2)


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
        cost_bases: set[str] = set()
        current_reference_used = False
        has_adjusted_quantities = False
        for line in order.get("lineItems", {}).get("nodes", []):
            quantity = int(line.get("currentQuantity") or 0)
            original_quantity = int(line.get("quantity") or quantity)
            if original_quantity > quantity:
                has_adjusted_quantities = True
                quantity = original_quantity
            unit_price = money(
                (line.get("originalUnitPriceSet") or {}).get("shopMoney", {}).get("amount")
            )
            # Shopify line items also contain free sweets and stickers. They are not garments.
            if unit_price < 20:
                continue
            units += quantity
            for _ in range(quantity):
                allocation = self.allocate(line, created_at)
                model, cost_basis = dated_unit_model(self.config, allocation.model_name, created_at, unit_price)
                cost_bases.add(cost_basis)
                current_reference_used = current_reference_used or cost_basis == "current_reported"
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
                        "cost_basis": cost_basis,
                    }
                )
        shipping = self.shipping_cost(order, has_sweatshirt) if units else 0.0
        shipping_status = "historical_estimate"
        if current_reference_used:
            ref = current_cost_reference(self.config)
            shipping_title = " ".join(line.get("title", "") for line in order.get("shippingLines", {}).get("nodes", [])).lower()
            delivery = "mondial_relay" if ("mondial" in shipping_title or "relay" in shipping_title or "relais" in shipping_title) else "home" if ("domicile" in shipping_title or "colissimo" in shipping_title) else None
            option = ref["shipping_options"].get(delivery, {})
            shipping_status = option.get("status", "unconfirmed")
            shipping = money(option.get("cost")) if shipping_status == "confirmed" else 0.0
            if shipping_status != "confirmed":
                self.warnings.append("Transport d'une commande au coût actuel non confirmé : la marge affichée exclut ce coût inconnu.")
        if has_adjusted_quantities:
            self.warnings.append("Retour/remboursement : coûts initiaux conservés par prudence ; récupération des produits et frais à rapprocher des justificatifs.")
        breakdown["shipping"] += shipping
        return {
            "items": round(item_cost, 2),
            "shipping": shipping,
            "total": round(item_cost + shipping, 2),
            "units": units,
            "allocations": allocations,
            "breakdown": {key: round(value, 2) for key, value in breakdown.items()},
            "cost_bases": sorted(cost_bases),
            "shipping_status": shipping_status,
            "refund_costs_unverified": has_adjusted_quantities,
            "is_estimate": True,
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
        self.powens = PowensClient()

    def build(
        self, since: date, until: date, *, include_analytics: bool = True
    ) -> dict[str, Any]:
        catalog = self.shopify.fetch_catalog()
        orders = self.shopify.fetch_orders(since, until)
        source_warnings: list[str] = []
        data_status: dict[str, Any] = {}
        shop_timezone_name = catalog["shop"].get("ianaTimezone") or self.config.get("shop_timezone") or "UTC"
        try:
            shop_timezone = ZoneInfo(shop_timezone_name)
        except ZoneInfoNotFoundError:
            shop_timezone_name = "UTC"
            shop_timezone = timezone.utc
            source_warnings.append("Fuseau boutique indisponible : dates de commande calculees en UTC.")

        def order_day(order: dict[str, Any]) -> str:
            created = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
            return created.astimezone(shop_timezone).date().isoformat()

        orders = [order for order in orders if since.isoformat() <= order_day(order) <= until.isoformat()]
        data_status["shopify_orders"] = {
            "status": "available", "checked_at": datetime.now(timezone.utc).isoformat(),
            "timezone": shop_timezone_name,
            "basis": "order_created_date_current_value_after_returns",
            "refund_timing": "restated_on_original_order_date_not_refund_date",
        }
        shopify_analytics = {"sessions": None, "cart_additions": None, "completed_checkouts": None}
        data_status["shopify_analytics"] = {"status": "not_requested", "checked_at": None}
        if include_analytics:
            try:
                shopify_analytics = self.shopify.fetch_analytics(since, until)
                data_status["shopify_analytics"] = {"status": "available", "checked_at": datetime.now(timezone.utc).isoformat()}
            except Exception:
                data_status["shopify_analytics"] = {"status": "unavailable", "checked_at": datetime.now(timezone.utc).isoformat()}
                source_warnings.append("Shopify Analytics indisponible : visites et conversion inconnues, pas nulles.")

        meta_available_since = meta_history_start()
        meta_since = max(since, meta_available_since)
        def fetch_meta(level: str) -> list[dict[str, Any]]:
            key = "meta_account" if level == "account" else "meta_campaigns"
            status = {
                "status": "available" if since >= meta_available_since else "partial",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "available_since": meta_available_since.isoformat(),
                "requested_since": since.isoformat(),
                "effective_since": meta_since.isoformat() if until >= meta_since else None,
                "date_basis": "Meta ad-account timezone",
            }
            if until < meta_since:
                status["status"] = "unavailable"
                data_status[key] = status
                return []
            try:
                rows = self.meta.fetch_daily(meta_since, until, level=level)
            except Exception:
                status["status"] = "unavailable"
                rows = []
                source_warnings.append(f"{key} indisponible : les metriques publicitaires sont inconnues.")
            data_status[key] = status
            return rows

        meta_rows = fetch_meta("account")
        geremy_config = self.config.get("geremy", {})
        campaign_rows = fetch_meta("campaign")
        engine = CostEngine(self.config)
        engine.resolve_catalog_mappings(catalog)
        # Reconstruct the lot state before the selected period. Otherwise changing
        # a date filter silently changes the cost assigned to the same order.
        snapshot_day = datetime.fromisoformat(self.config["stock_snapshot_at"].replace("Z", "+00:00")).astimezone(shop_timezone).date()
        if snapshot_day < since:
            prior_orders = self.shopify.fetch_orders(snapshot_day, since - timedelta(days=1))
            for prior in sorted(prior_orders, key=lambda order: order["createdAt"]):
                if (snapshot_day.isoformat() <= order_day(prior) < since.isoformat()
                    and prior.get("displayFinancialStatus") in {"PAID", "PARTIALLY_PAID", "PARTIALLY_REFUNDED", "REFUNDED"}):
                    engine.order_cost(prior)

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
                "refunds_amount": 0.0,
                "net_payments": 0.0,
                "business_expenses": 0.0,
                "site_visits": 0.0,
                "add_to_carts": 0.0,
            }
        )
        order_details: list[dict[str, Any]] = []
        missing_payment_days: set[str] = set()
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

        included_statuses = {"PAID", "PARTIALLY_PAID", "PARTIALLY_REFUNDED", "REFUNDED"}
        valid_orders = [
            order
            for order in orders
            if order.get("displayFinancialStatus") in included_statuses
        ]
        valid_orders.sort(key=lambda order: order["createdAt"])
        for order in valid_orders:
            day = order_day(order)
            if order.get("lineItems", {}).get("pageInfo", {}).get("hasNextPage"):
                raise RuntimeError("Lignes de commande incompletes : calcul interrompu.")
            # currentTotalPriceSet already reflects returns/refunds/removals;
            # subtracting totalRefundedSet again would count refunds twice.
            revenue = money(order["currentTotalPriceSet"]["shopMoney"]["amount"])
            refunded = money(order.get("totalRefundedSet", {}).get("shopMoney", {}).get("amount"))
            net_payment_value = order.get("netPaymentSet", {}).get("shopMoney", {}).get("amount")
            net_payment = money(net_payment_value) if net_payment_value is not None else None
            if net_payment is None:
                missing_payment_days.add(day)
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
            daily[day]["refunds_amount"] += refunded
            if net_payment is not None:
                daily[day]["net_payments"] += net_payment
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
                    "fulfillment_status": order.get("displayFulfillmentStatus"),
                    "cancelled_at": order.get("cancelledAt"),
                    "refunded_amount": refunded,
                    "net_payment": net_payment,
                    "revenue_basis": "current_order_value_after_returns",
                    "costs_estimated": True,
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

        managed_ids = {str(value) for value in geremy_config.get("campaign_ids", [])}
        personal_ids = {str(value) for value in self.config.get("personal_campaign_ids", [])}
        campaign_performance_index: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"spend": 0.0, "purchases": 0.0, "purchase_value": 0.0, "campaign_id": None, "campaign": "Campagne sans nom"}
        )
        for index, row in enumerate(campaign_rows):
            campaign = row.get("campaign_name") or "Campagne sans nom"
            campaign_id = str(row.get("campaign_id") or "")
            # Do not merge unrelated campaigns merely because they share a name.
            campaign_key = campaign_id or f"missing-id-{index}"
            values = campaign_performance_index[campaign_key]
            values["campaign_id"] = campaign_id or None
            values["campaign"] = campaign
            values["spend"] += money(row.get("spend"))
            values["purchases"] += action_value(
                row.get("actions")
            )
            values["purchase_value"] += action_value(
                row.get("action_values")
            )
        campaign_performance = []
        for values in campaign_performance_index.values():
            spend = round(values["spend"], 2)
            purchases = round(values["purchases"], 2)
            purchase_value = round(values["purchase_value"], 2)
            campaign_performance.append(
                {
                    "campaign_id": values["campaign_id"],
                    "campaign": values["campaign"],
                    "scope": "geremy_confirmed" if values["campaign_id"] in managed_ids else "personal_confirmed" if values["campaign_id"] in personal_ids else "unconfirmed",
                    "spend": spend,
                    "purchases": purchases,
                    "purchase_value": purchase_value,
                    "roas": round(purchase_value / spend, 2) if spend else None,
                    "cpa": round(spend / purchases, 2) if purchases else None,
                }
            )
        campaign_performance.sort(key=lambda row: row["spend"], reverse=True)

        managed_campaigns = sorted({row["campaign"] for row in campaign_performance if row["campaign_id"] in managed_ids})
        campaign_ranges = []
        for campaign_id in sorted(managed_ids):
            active_days = sorted(
                row["date_start"]
                for row in campaign_rows
                if str(row.get("campaign_id")) == campaign_id and money(row.get("spend")) > 0
            )
            if active_days:
                campaign_ranges.append(
                    {"campaign_id": campaign_id, "since": active_days[0], "until": active_days[-1]}
                )
        geremy_commission_rate = float(geremy_config.get("commission_rate", 0.0))
        commission_rate = geremy_commission_rate
        mission_started_at = geremy_config.get("mission_started_at", "9999-12-31")
        mission_ended_at = geremy_config.get("mission_ended_at") or "9999-12-31"
        commission_confirmed = bool(
            geremy_config.get("commission_scope_confirmed") is True
            and geremy_config.get("commission_basis") == "meta_attributed_revenue"
            and managed_ids
            and data_status["meta_campaigns"]["status"] == "available"
        )
        if commission_confirmed:
            for campaign_row in campaign_rows:
                day = campaign_row["date_start"]
                if (str(campaign_row.get("campaign_id")) in managed_ids
                    and mission_started_at <= day <= mission_ended_at):
                    attributed_revenue = action_value(campaign_row.get("action_values"))
                    daily[day]["geremy_revenue"] += attributed_revenue
                    daily[day]["geremy_commission"] += round(attributed_revenue * geremy_commission_rate, 2)
        manual_expenses = [
            expense
            for expense in self.config.get("business_expenses", [])
            if since.isoformat() <= expense.get("date", "") <= until.isoformat()
        ]
        for expense in manual_expenses:
            daily[expense["date"]]["business_expenses"] += money(expense.get("amount"))

        configured_fixed_costs = self.config["monthly_fixed_costs"]
        # Favikon also appears in affiliate profitability. That is an allocation of
        # the same expense, not a second company expense or an all-history charge.
        other_fixed_monthly = round(sum(
            money(amount)
            for name, amount in configured_fixed_costs.items()
            if "favikon" not in name.casefold()
        ), 2)
        legacy_favikon_monthly = sum(
            money(amount)
            for name, amount in configured_fixed_costs.items()
            if "favikon" in name.casefold()
        )
        fixed_favikon_monthly = money(
            affiliate_config.get("favikon_monthly_cost", legacy_favikon_monthly)
        )
        business_started_at = date.fromisoformat(self.config["business_started_at"])
        period_days = (until - since).days + 1
        fixed_period_start = max(since, business_started_at)
        fixed_period_days = max(0, (until - fixed_period_start).days + 1)
        fixed_favikon_start = max(
            business_started_at.isoformat(),
            affiliate_config.get("favikon_started_at") or business_started_at.isoformat(),
        )

        def accrued_fixed_costs(through: date) -> float:
            return round(
                prorated_monthly_cost(
                    other_fixed_monthly, since, through,
                    starts_at=business_started_at.isoformat(),
                )
                + prorated_monthly_cost(
                    fixed_favikon_monthly, since, through,
                    starts_at=fixed_favikon_start,
                    ends_at=affiliate_config.get("favikon_ended_at"),
                ), 2,
            )

        fixed_prorated = accrued_fixed_costs(until)
        previous_fixed_accrual = 0.0
        daily_rows = []
        cursor = since
        while cursor <= until:
            key = cursor.isoformat()
            row = daily[key]
            spend = round(row["ad_spend"], 2)
            revenue = round(row["revenue"], 2)
            contribution = round(row["contribution_margin"], 2)
            fixed_accrual = accrued_fixed_costs(cursor)
            daily_fixed_cost = round(fixed_accrual - previous_fixed_accrual, 2)
            previous_fixed_accrual = fixed_accrual
            daily_rows.append(
                {
                    "date": key,
                    "fixed_costs": daily_fixed_cost,
                    **{k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()},
                    "blended_roas": round(revenue / spend, 2) if spend else None,
                    "meta_roas": round(row["meta_purchase_value"] / spend, 2) if spend else None,
                    "meta_cpa": round(spend / row["meta_purchases"], 2) if row["meta_purchases"] else None,
                    "blended_cpa": round(spend / row["orders"], 2) if row["orders"] else None,
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
            "refunds_amount": round(sum(row["refunds_amount"] for row in daily_rows), 2),
            "net_payments": round(sum(row["net_payments"] for row in daily_rows), 2) if all(row["net_payment"] is not None for row in order_details) else None,
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
        totals["geremy_commission_status"] = "calculated_from_confirmed_scope" if commission_confirmed else "unconfirmed"
        totals["geremy_commission_deducted"] = totals["geremy_commission"]
        totals["geremy_balance_due"] = round(totals["geremy_commission"] - geremy_paid, 2) if commission_confirmed else None
        totals["geremy_commission_hypothetical_storewide"] = round(totals["revenue"] * geremy_commission_rate, 2)
        totals["geremy_commission_hypothetical_rate"] = geremy_commission_rate
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
            - totals["geremy_commission_deducted"]
            - totals["business_expenses"],
            2,
        )
        totals["blended_cpa"] = (
            round(totals["ad_spend"] / totals["orders"], 2) if totals["orders"] else None
        )
        totals["cpa"] = totals["blended_cpa"]  # Legacy alias; never label this Meta CPA.
        totals["meta_cpa"] = (
            round(totals["ad_spend"] / totals["meta_purchases"], 2)
            if totals["meta_purchases"] else None
        )
        targets = self.config.get("kpi_targets", {})
        current_margin_rate = float(self.config.get("current_margin_rate", 0.0))
        margin_after_geremy = current_margin_rate - (geremy_commission_rate if commission_confirmed else 0.0)
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
            if totals["site_visits"] and totals["completed_checkouts"] is not None
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
        warnings = sorted(set(engine.warnings + source_warnings))
        if totals["refunds_amount"]:
            warnings.append("Remboursements rattaches a la date de commande ; couts de retours et recuperation de stock non rapproches.")
        if not commission_confirmed:
            warnings.append("Commission Geremy non confirmee : montant du et solde inconnus ; aucune commission hypothetique deduite du resultat partiel.")
        if data_status["meta_account"]["status"] != "available":
            warnings.append("Historique Meta incomplet ou indisponible : depense totale et resultat de la periode inconnus.")
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
        if totals["geremy_balance_due"] is not None and totals["geremy_balance_due"] < 0:
            warnings.append(
                f"Geremy payment exceeds the calculated commission by "
                f"{abs(totals['geremy_balance_due']):.2f} EUR."
            )

        margin_profiles = []
        profit_commission_rate = 0.20
        for configured_profile in self.config.get("margin_profiles", []):
            profile = deepcopy(configured_profile)
            margin_rate = float(profile.get("margin_rate", 0.0))
            projected_contribution = round(totals["revenue"] * margin_rate, 2)
            projected_meta_profit = round(totals["meta_purchase_value"] * margin_rate - totals["ad_spend"], 2)
            geremy_profit_commission = round(
                max(0.0, projected_meta_profit) * profit_commission_rate,
                2,
            )
            profile["projected_contribution"] = projected_contribution
            profile["projected_result"] = round(
                projected_contribution
                - totals["ad_spend"]
                - totals["geremy_commission"]
                - totals["fixed_costs_prorated"]
                - totals["business_expenses"],
                2,
            )
            profile["geremy_profit_deal"] = {
                "status": "hypothetical",
                "rate": profit_commission_rate,
                "basis": "hypothetical_commission_on_positive_meta_attributed_profit",
                "projected_meta_profit": projected_meta_profit,
                "commission": geremy_profit_commission,
                "projected_result": round(
                    projected_contribution
                    - totals["ad_spend"]
                    - geremy_profit_commission
                    - totals["fixed_costs_prorated"]
                    - totals["business_expenses"],
                    2,
                ),
            }
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

        # Financial unknowns remain null in the public contract. Arithmetic above
        # uses the confirmed deduction only; a proposed rate is never a debt.
        totals["cost_completeness"] = False
        totals["result_status"] = "partial"
        totals["estimated_result_basis"] = "configured_historical_cost_estimates"
        totals["estimated_result_excludes_unconfirmed_commission"] = not commission_confirmed
        if not commission_confirmed:
            totals["geremy_revenue"] = None
            totals["geremy_commission"] = None
            for row in daily_rows:
                row["geremy_revenue"] = None
                row["geremy_commission"] = None
        if data_status["meta_account"]["status"] != "available":
            for key in ("ad_spend", "meta_purchases", "meta_purchase_value"):
                totals[f"{key}_observed"] = totals[key]
                totals[key] = None
            for key in ("meta_cpa", "meta_roas", "blended_cpa", "cpa", "blended_roas", "estimated_result"):
                totals[key] = None
            totals["result_status"] = "unavailable"
            for profile in margin_profiles:
                profile["projected_result"] = None
                profile["geremy_profit_deal"]["projected_meta_profit"] = None
                profile["geremy_profit_deal"]["commission"] = None
                profile["geremy_profit_deal"]["projected_result"] = None
        for row in daily_rows:
            if row["date"] in missing_payment_days:
                row["net_payments"] = None
            row["meta_landing_page_views"] = row["site_visits"]
            row["meta_add_to_carts"] = row["add_to_carts"]
            if (data_status["meta_account"]["status"] == "unavailable"
                or row["date"] < meta_available_since.isoformat()):
                for key in ("ad_spend", "meta_purchases", "meta_purchase_value", "impressions", "clicks", "site_visits", "add_to_carts", "meta_landing_page_views", "meta_add_to_carts", "meta_cpa", "meta_roas", "blended_cpa", "blended_roas", "estimated_result"):
                    row[key] = None

        legacy_bank_account = self.config.get("bank_account", {})
        bank_accounts = deepcopy(self.config.get("bank_accounts", []))
        bank_transactions: list[dict[str, Any]] = []
        powens_status: dict[str, Any] = {
            "enabled": self.powens.enabled,
            "client_configured": bool(
                self.powens.domain and self.powens.client_id and self.powens.client_secret
            ),
            "environment": self.powens.environment,
            "domain": self.powens.domain,
            "connected": False,
        }
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
        if self.powens.enabled and self.powens.environment != "sandbox":
            try:
                powens_snapshot = self.powens.snapshot()
                powens_accounts = powens_snapshot["accounts"]
                if powens_accounts:
                    bank_accounts = [
                        {
                            "label": (
                                account.get("name")
                                or account.get("display_name")
                                or account.get("original_name")
                                or f"Compte {account.get('id')}"
                            ),
                            "balance": next(
                                (
                                    value
                                    for value in (
                                        account.get("balance"),
                                        account.get("coming_balance"),
                                        account.get("booked_balance"),
                                    )
                                    if value is not None
                                ),
                                None,
                            ),
                            "currency_code": currency_code(
                                account.get("currency") or account.get("currency_code")
                            ),
                            "recorded_at": iso_date(
                                account.get("last_update")
                                or account.get("last_refresh")
                                or account.get("updated_at")
                            ),
                            "source": (
                                "powens_sandbox"
                                if self.powens.environment == "sandbox"
                                else "powens"
                            ),
                            "account_id": account.get("id"),
                            "type": account.get("type") or account.get("usage"),
                            "status": account.get("state") or account.get("disabled"),
                        }
                        for account in powens_accounts
                    ]
                if self.powens.show_transactions:
                    bank_transactions = [
                        {
                            "id": transaction.get("id"),
                            "date": iso_date(
                                transaction.get("date")
                                or transaction.get("rdate")
                                or transaction.get("datetime")
                            ),
                            "description": transaction.get("wording")
                            or transaction.get("simplified_wording")
                            or transaction.get("original_wording")
                            or transaction.get("label")
                            or "Transaction",
                            "amount": next(
                                (
                                    value
                                    for value in (
                                        transaction.get("value"),
                                        transaction.get("amount"),
                                    )
                                    if value is not None
                                ),
                                None,
                            ),
                            "currency_code": currency_code(
                                transaction.get("currency") or transaction.get("currency_code")
                            ),
                            "account_id": transaction.get("id_account")
                            or transaction.get("account_id"),
                            "operation_type": transaction.get("type")
                            or transaction.get("category"),
                        }
                        for transaction in powens_snapshot["transactions"]
                        if not transaction.get("deleted")
                    ]
                powens_status.update(
                    {
                        "connected": bool(powens_accounts),
                        "account_count": len(powens_accounts),
                        "transaction_count": len(powens_snapshot["transactions"]),
                        "transactions_visible": self.powens.show_transactions,
                    }
                )
            except Exception as exc:
                powens_status["error"] = str(exc)[:240]
                warnings.append(f"Powens banking sync failed: {powens_status['error']}")
        if not powens_status.get("connected") and self.bridge.enabled and self.bridge.environment != "sandbox":
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

        bank_accounts, bank_transactions, banking = business_banking(
            self.config, bank_accounts, bank_transactions
        )
        if public_deployment:
            bank_transactions = []
        for status in (powens_status, bridge_status):
            status.pop("domain", None)
            status["transactions_visible"] = bool(bank_transactions)
            if status.get("error"):
                status["error"] = "Connexion bancaire indisponible. Vérifier l'autorisation du connecteur."

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cost_assumptions": current_cost_reference(self.config),
            "period": {"since": since.isoformat(), "until": until.isoformat(), "days": period_days},
            "shop": catalog["shop"],
            "totals": totals,
            "stock_totals": {"available": available, "incoming": incoming},
            "fixed_costs_monthly": self.config["monthly_fixed_costs"],
            "fixed_costs_metadata": {
                "status": "unverified_config",
                "basis": "calendar_month_proration",
                "description": "Charges configurees estimees; montants et dates a confirmer.",
                "favikon_included_once": True,
                "other_monthly_amount": other_fixed_monthly,
                "favikon_monthly_amount": fixed_favikon_monthly,
                "favikon_period_amount": prorated_monthly_cost(
                    fixed_favikon_monthly, since, until,
                    starts_at=fixed_favikon_start,
                    ends_at=affiliate_config.get("favikon_ended_at"),
                ),
            },
            "financial": {
                "geremy": {
                    "rate": geremy_commission_rate,
                    "status": totals["geremy_commission_status"],
                    "commission_basis": geremy_config.get("commission_basis") if commission_confirmed else None,
                    "campaign_ids": sorted(managed_ids),
                    "campaigns": managed_campaigns,
                    "active_ranges": campaign_ranges,
                },
                "bank_account": bank_accounts[0],
                "bank_accounts": bank_accounts,
                "bank_transactions": bank_transactions,
                "banking": banking,
                "powens": powens_status,
                "bridge": bridge_status,
                "manual_expenses": manual_expenses,
            },
            "campaign_performance": campaign_performance,
            "data_status": data_status,
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


def meta_history_start(today: date | None = None) -> date:
    today = today or date.today()
    year, month_index = divmod(today.year * 12 + today.month - 1 - 37, 12)
    month = month_index + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    # Keep one day inside Meta's rolling window to allow for timezone boundaries.
    return date(year, month, day) + timedelta(days=1)


CHART_METRICS = (
    "revenue", "orders", "units", "variable_costs", "contribution_margin",
    "ad_spend", "fixed_costs", "business_expenses", "geremy_commission",
    "estimated_result",
)


def aggregate_chart_rows(rows: list[dict[str, Any]], grain: str) -> list[dict[str, Any]]:
    """Aggregate cached source days without turning an unavailable day into zero."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = row["date"][:4] if grain == "annual" else row["date"][:7]
        groups[key].append(row)
    result = []
    for key, days in sorted(groups.items()):
        bucket: dict[str, Any] = {
            "period": key,
            "date": key + ("-01-01" if grain == "annual" else "-01"),
            "days": len(days),
            "has_missing_values": False,
        }
        for metric in CHART_METRICS:
            known = [row[metric] for row in days if row.get(metric) is not None]
            observed = round(sum(known), 2) if known else None
            complete = len(known) == len(days)
            bucket[metric] = observed if complete else None
            bucket[f"{metric}_observed"] = observed
            bucket["has_missing_values"] |= not complete
        result.append(bucket)
    return result


def build_chart_history(
    cumulative_data: dict[str, Any], cumulative_metadata: dict[str, Any]
) -> dict[str, Any]:
    rows = cumulative_data["daily"]
    totals: dict[str, Any] = {}
    for metric in CHART_METRICS:
        known = [row[metric] for row in rows if row.get(metric) is not None]
        observed = round(sum(known), 2) if known else None
        totals[metric] = observed if len(known) == len(rows) else None
        totals[f"{metric}_observed"] = observed
    return {
        "period": deepcopy(cumulative_data["period"]),
        "monthly": aggregate_chart_rows(rows, "monthly"),
        "annual": aggregate_chart_rows(rows, "annual"),
        "totals": totals,
        "generated_at": cumulative_data.get("generated_at"),
        "source_status": deepcopy(cumulative_metadata["source_status"]),
        "shopify_history_complete": cumulative_metadata["shopify_history_complete"],
        "cost_completeness": cumulative_metadata["cost_completeness"],
        "is_complete": cumulative_metadata["is_complete"],
        "result_is_complete": cumulative_metadata["is_complete"],
        "is_estimate": True,
        "estimated_result_excludes_unconfirmed_commission": cumulative_data["totals"].get(
            "estimated_result_excludes_unconfirmed_commission", False
        ),
        "missing_data": cumulative_metadata["missing_data"],
        "basis": "Historique Shopify et couts documentes ou estimes; un montant inconnu reste null.",
        "result_basis": "CA moins couts variables, publicite, charges fixes, frais manuels et commission confirmee uniquement.",
        "business_expenses_basis": "Frais manuels distincts des couts variables, publicite, charges fixes et commission.",
        "revenue_basis": "Valeur actuelle des commandes Shopify rattachee a leur date de creation; ne represente pas les encaissements bancaires.",
    }


def business_plan(config: dict[str, Any]) -> dict[str, Any]:
    """Expose only explicitly configured planning fields; never invent a target."""
    raw = config.get("business_plan")
    if not isinstance(raw, dict):
        raw = {}
    income_target = None
    try:
        value = float(raw.get("personal_monthly_income_target"))
        if not isinstance(raw.get("personal_monthly_income_target"), bool) and math.isfinite(value) and value >= 0:
            income_target = round(value, 2)
    except (TypeError, ValueError, OverflowError):
        pass
    target_date = None
    try:
        target_date = date.fromisoformat(raw.get("target_date")).isoformat()
    except (TypeError, ValueError):
        pass
    context = raw.get("target_context")
    target_context = context.strip()[:120] or None if isinstance(context, str) else None
    return {
        "personal_monthly_income_target": income_target,
        "target_date": target_date,
        "target_context": target_context,
    }


def build_capital_history(config: dict[str, Any], today: date) -> dict[str, Any]:
    """Documented owner funding only, never inferred from revenue, losses or stock."""
    since = date.fromisoformat(config["business_started_at"])
    raw_flows = config.get("business_capital_flows", [])
    coverage = config.get("business_capital_coverage") or {}
    flows: list[dict[str, Any]] = []
    rejected = 0
    if not isinstance(raw_flows, list):
        raw_flows = []
        rejected += 1
    seen_ids: set[str] = set()
    for raw in raw_flows:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        try:
            flow_date = date.fromisoformat(raw.get("date", ""))
            amount = float(raw.get("amount"))
            source = str(raw.get("source") or "").strip()
            if (
                raw.get("scope") != "business"
                or raw.get("type") not in {"contribution", "withdrawal"}
                or raw.get("currency", "EUR") != "EUR"
                or isinstance(raw.get("amount"), bool)
                or not math.isfinite(amount) or amount <= 0
                or not source or len(source) > 100
                or not since <= flow_date <= today
            ):
                raise ValueError("Invalid capital flow")
        except (TypeError, ValueError):
            rejected += 1
            continue
        # A reference can prevent duplicate imports without exposing bank IDs.
        flow_id = str(raw.get("id") or "")
        if flow_id and flow_id in seen_ids:
            continue
        if flow_id:
            seen_ids.add(flow_id)
        flows.append({
            "date": flow_date.isoformat(), "type": raw["type"],
            "amount": round(amount, 2), "currency": "EUR", "source": source,
        })
    flows.sort(key=lambda row: (row["date"], row["type"]))
    complete = False
    if isinstance(coverage, dict) and coverage.get("is_complete") is True and not rejected:
        try:
            complete = (
                date.fromisoformat(coverage.get("since", "")) <= since
                and date.fromisoformat(coverage.get("until", "")) >= today
            )
        except (TypeError, ValueError):
            pass
    contributions = round(sum(row["amount"] for row in flows if row["type"] == "contribution"), 2)
    withdrawals = round(sum(row["amount"] for row in flows if row["type"] == "withdrawal"), 2)
    observed_totals = {
        "contributions": contributions if flows or complete else None,
        "withdrawals": withdrawals if flows or complete else None,
        "net_contributions": round(contributions - withdrawals, 2) if flows or complete else None,
    }
    monthly: list[dict[str, Any]] = []
    if flows or complete:
        grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"contributions": 0.0, "withdrawals": 0.0})
        for flow in flows:
            field = "contributions" if flow["type"] == "contribution" else "withdrawals"
            grouped[flow["date"][:7]][field] += flow["amount"]
        cursor = since.replace(day=1)
        cumulative_contributions = cumulative_withdrawals = 0.0
        while cursor <= today:
            period = cursor.isoformat()[:7]
            values = grouped[period]
            cumulative_contributions += values["contributions"]
            cumulative_withdrawals += values["withdrawals"]
            monthly.append({
                "date": cursor.isoformat(), "period": period,
                "contributions": round(values["contributions"], 2),
                "withdrawals": round(values["withdrawals"], 2),
                "net_contributions": round(values["contributions"] - values["withdrawals"], 2),
                "cumulative_contributions": round(cumulative_contributions, 2),
                "cumulative_withdrawals": round(cumulative_withdrawals, 2),
                "cumulative_net_contributions": round(cumulative_contributions - cumulative_withdrawals, 2),
            })
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return {
        "period": {"since": since.isoformat(), "until": today.isoformat()},
        "status": "confirmed" if complete else "partial" if flows else "unknown",
        "is_complete": complete,
        "totals": observed_totals.copy() if complete else {key: None for key in observed_totals},
        "observed_totals": observed_totals,
        "monthly": monthly, "flows": flows,
        "rejected_flows": rejected,
        "currency": "EUR",
        "basis": "Apports personnels et retraits documentes uniquement; aucune estimation a partir des pertes, du stock ou des depenses.",
        "monthly_basis": "complete_history" if complete else "documented_flows_only",
        "missing_data": None if complete else "Historique des apports et retraits non rapproche integralement depuis le lancement.",
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
        today = date.today()
        requested_since = date.fromisoformat(self.builder.config["business_started_at"])
        meta_available_since = meta_history_start(today)
        # Meta's retention limit must never remove Shopify revenue or business costs.
        # Each connector is responsible for its own supported history window.
        cumulative_since = requested_since
        meta_history_truncated = meta_available_since > requested_since
        shopify_history_complete = bool(
            self.builder.config.get("historical_shopify_orders_complete")
        )
        missing_data = []
        if not shopify_history_complete:
            missing_data.append("Historique des commandes Shopify au-dela de 60 jours")
        if meta_history_truncated:
            missing_data.append(
                f"Historique Meta avant le {meta_available_since.isoformat()} "
                "indisponible (limite de 37 mois)"
            )
        cumulative_data = deepcopy(self.get(
            cumulative_since,
            today,
            force=force,
            include_analytics=False,
        ))
        cumulative_sources = cumulative_data.get("data_status", {})
        source_history_complete = all(
            cumulative_sources.get(source, {}).get("status", "available") == "available"
            for source in ("shopify_orders", "meta_account", "meta_campaigns")
        )
        if not source_history_complete and not meta_history_truncated:
            missing_data.append("Une source necessaire au cumul est partielle ou indisponible")
        costs_complete = cumulative_data["totals"].get("cost_completeness") is True
        if not costs_complete:
            missing_data.append("Couts historiques configures et charges non rapproches integralement")
        if cumulative_data["totals"].get("estimated_result_excludes_unconfirmed_commission"):
            missing_data.append("Commission non confirmee exclue du resultat partiel")
        trend_since = max(cumulative_since, until - timedelta(days=730))
        trend_until = min(until, today)
        trend_daily = [
            row
            for row in cumulative_data["daily"]
            if trend_since.isoformat() <= row["date"] <= trend_until.isoformat()
        ]
        response = deepcopy(period_data)
        # Inventory is a current operational snapshot, independent of the sales filter.
        for key in ("stock", "stock_totals"):
            if key in cumulative_data:
                response[key] = deepcopy(cumulative_data[key])
        response["stock_as_of"] = today.isoformat()
        response["stock_snapshot_at"] = self.builder.config.get("stock_snapshot_at")
        response["data_freshness"] = {
            **response.get("data_freshness", {}),
            "selected_period_generated_at": period_data.get("generated_at"),
            "cumulative_generated_at": cumulative_data.get("generated_at"),
            "stock_generated_at": cumulative_data.get("generated_at"),
            "stock_as_of": today.isoformat(),
            "physical_stock_snapshot_at": self.builder.config.get("stock_snapshot_at"),
        }
        response["cumulative"] = {
            "period": cumulative_data["period"],
            "requested_since": requested_since.isoformat(),
            "effective_since": cumulative_since.isoformat(),
            "meta_history_available_since": meta_available_since.isoformat(),
            "meta_history_truncated": meta_history_truncated,
            "shopify_history_complete": shopify_history_complete,
            "cost_completeness": costs_complete,
            "source_status": cumulative_sources,
            "generated_at": cumulative_data.get("generated_at"),
            "totals": cumulative_data["totals"],
            "is_estimate": True,
            "is_complete": (
                shopify_history_complete and not meta_history_truncated
                and source_history_complete and costs_complete
            ),
            "basis": "known Shopify revenues minus recorded or estimated costs; missing source history is not zero",
            "missing_data": "; ".join(missing_data) or None,
        }
        trend_totals = {}
        for metric in ("revenue", "contribution_margin", "ad_spend", "estimated_result"):
            observed = round(sum(
                row[metric] for row in trend_daily if row.get(metric) is not None
            ), 2)
            trend_totals[metric] = (
                observed if all(row.get(metric) is not None for row in trend_daily) else None
            )
            trend_totals[f"{metric}_observed"] = observed
        response["trend_history"] = {
            "period": {
                "since": trend_since.isoformat(),
                "until": trend_until.isoformat(),
                "days": len(trend_daily),
            },
            "daily": trend_daily,
            "totals": trend_totals,
            "grain": "daily_source_monthly_display",
            "label": "24 derniers mois",
        }
        # Reuse the already fetched full business history. Charts are independent
        # of the current sales filter and add no connector calls or daily payload.
        response["chart_history"] = build_chart_history(cumulative_data, response["cumulative"])
        response["capital_history"] = build_capital_history(self.builder.config, today)
        response["business_plan"] = business_plan(self.builder.config)
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
    if until > today:
        raise ValueError("until cannot be in the future")
    if (until - since).days > 3650:
        raise ValueError("period cannot exceed 10 years")
    return since, until


def make_handler(cache: Cache):
    public_deployment = os.environ.get("PUBLIC_DEPLOYMENT", "").lower() in {
        "1",
        "true",
        "yes",
    }
    # Serve only named dashboard assets; never resolve an arbitrary request path.
    static_assets = {
        "/static/favicon.png": ("favicon.png", "image/png"),
        "/static/pilotage.css": ("pilotage.css", "text/css; charset=utf-8"),
        "/static/vision.js": ("vision.js", "application/javascript; charset=utf-8"),
        "/static/loading.js": ("loading.js", "application/javascript; charset=utf-8"),
        "/static/trajectory.js": ("trajectory.js", "application/javascript; charset=utf-8"),
        "/static/annual-summary.js": ("annual-summary.js", "application/javascript; charset=utf-8"),
        "/static/annual-forecast.js": ("annual-forecast.js", "application/javascript; charset=utf-8"),
        "/static/annual-view.js": ("annual-view.js", "application/javascript; charset=utf-8"),
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
                if parsed.path == "/enzo":
                    self.send_bytes(ENZO_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
                    return
                if parsed.path in static_assets:
                    filename, content_type = static_assets[parsed.path]
                    self.send_bytes(
                        (STATIC_PATH / filename).read_bytes(),
                        content_type,
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
            except Exception as exc:  # Never expose connector URLs, tokens or response bodies.
                self.send_bytes(
                    json.dumps({"error": "Période invalide." if isinstance(exc, ValueError) else "Données temporairement indisponibles : un connecteur n'a pas pu être actualisé."}).encode("utf-8"),
                    "application/json; charset=utf-8",
                    400 if isinstance(exc, ValueError) else 503,
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
    # A dated business-only statement can be imported without transferring bank
    # credentials or personal accounts to this deployment.
    bank_snapshot_json = os.environ.get("TESIGN_BANK_SNAPSHOT_JSON")
    if bank_snapshot_json:
        snapshot = json.loads(bank_snapshot_json)
        if snapshot.get("scope") != "business" or snapshot.get("source") != "enable_banking_snapshot":
            raise ValueError("Le relevé doit concerner uniquement le compte professionnel TESIGN.")
        date.fromisoformat(snapshot["recorded_at"][:10])
        config["bank_accounts"] = [{
            "label": "Compte professionnel TESIGN", "scope": "business",
            "balance": float(snapshot["balance"]), "currency_code": snapshot.get("currency_code", "EUR"),
            "recorded_at": snapshot["recorded_at"], "source": "enable_banking_snapshot",
        }]
    capital_flows_json = os.environ.get("TESIGN_CAPITAL_FLOWS_JSON")
    if capital_flows_json:
        capital = json.loads(capital_flows_json)
        if isinstance(capital, list):
            config["business_capital_flows"] = capital
        elif isinstance(capital, dict):
            config["business_capital_flows"] = capital.get("flows", [])
            config["business_capital_coverage"] = capital.get("coverage", {})
        else:
            raise ValueError("Historique des apports invalide.")
    business_plan_json = os.environ.get("TESIGN_BUSINESS_PLAN_JSON")
    if business_plan_json:
        try:
            config["business_plan"] = json.loads(business_plan_json)
        except (TypeError, ValueError):
            config["business_plan"] = {}
    return normalize_config(config)


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
