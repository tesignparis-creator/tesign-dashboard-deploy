"""Explicit cost assumptions and TESIGN-only banking presentation.

No credentials, bank authorisation or payment operations belong in this module.
"""
from copy import deepcopy
from datetime import date
import re


CURRENT_COST_REFERENCE = {
    "label": "T-shirt à 45 € — coûts communiqués par Enzo",
    "reported_at": "2026-09-18",
    "effective_from": "2026-09-18",
    "reference_price": 45.0,
    "unit_components": {"urssaf": 7.0, "production": 13.0, "fees": 2.0, "packaging": 1.5},
    "shipping_cost": 4.5,
    "shipping_status": "confirmed",
    "reference_shipping": "mondial_relay",
    "shipping_options": {
        "mondial_relay": {"cost": 4.5, "customer_charge": 0, "status": "confirmed"},
        "home": {"cost": 5.5, "customer_charge": 4.9, "status": "confirmed"},
    },
    "historical_costs_verified": False,
}


def current_cost_reference(config):
    ref = deepcopy(CURRENT_COST_REFERENCE)
    ref.update(config.get("current_cost_reference", {}))
    ref["unit_components"] = {
        **CURRENT_COST_REFERENCE["unit_components"],
        **config.get("current_cost_reference", {}).get("unit_components", {}),
    }
    ref["known_unit_cost"] = round(sum(ref["unit_components"].values()), 2)
    ref["contribution_before_shipping"] = round(ref["reference_price"] - ref["known_unit_cost"], 2)
    margin = ref["contribution_before_shipping"]
    ref["break_even_roas_before_shipping"] = round(ref["reference_price"] / margin, 4) if margin > 0 else None
    ref["contribution_after_shipping"] = round(margin - ref["shipping_cost"], 2) if ref["shipping_cost"] is not None else None
    ref["break_even_roas"] = round(ref["reference_price"] / ref["contribution_after_shipping"], 4) if ref["contribution_after_shipping"] and ref["contribution_after_shipping"] > 0 else None
    ref["notes"] = [
        "Coûts unitaires déclarés, pas un taux fiscal certifié.",
        "Les coûts historiques restent ceux de l'ancienne configuration, non revérifiés.",
        "Le transport reste à confirmer : la marge avant transport est un plafond, pas un bénéfice net.",
        "Les charges fixes et abonnements sont des estimations de configuration, à rapprocher des factures.",
    ]
    if ref["shipping_status"] == "confirmed":
        ref["notes"][2] = "Mondial Relay offert : coût de 4,50 € par commande. Domicile facturé 4,90 € : coût transporteur de 5,50 € par commande."
    elif ref["shipping_status"] == "included":
        ref["notes"][2] = "Le transport est inclus dans les frais déclarés : il n'est pas déduit une seconde fois."
    return ref


def normalize_config(config):
    config = deepcopy(config)
    ref = current_cost_reference(config)
    config["current_cost_reference"] = ref
    # Current targets must use the user's current unit economics, not an old 48% default.
    shipping = ref["shipping_cost"] if ref["shipping_status"] == "confirmed" else 0
    config["current_margin_rate"] = max(0, (ref["contribution_before_shipping"] - (shipping or 0)) / ref["reference_price"])
    return config


def dated_unit_model(config, model_name, created_at, unit_price):
    ref = current_cost_reference(config)
    applicable = (
        model_name.startswith("tshirt")
        and created_at.date().isoformat() >= ref["effective_from"]
        and abs(unit_price - ref["reference_price"]) < 0.01
    )
    if applicable:
        return deepcopy(ref["unit_components"]), "current_reported"
    return deepcopy(config["cost_models"][model_name]), "historical_estimate"


def business_banking(config, accounts, transactions, *, today=None):
    today = today or date.today()
    allow_ids = {str(x) for x in config.get("business_bank_account_ids", [])}
    safe_accounts, allowed_transactions = [], []
    selected_ids = set()
    for account in accounts:
        account_id = str(account.get("account_id") or "")
        label = str(account.get("label") or "")
        usage = str(account.get("usage") or account.get("type") or "").upper()
        if str(account.get("scope") or "").lower() in ("personal", "perso") or usage in ("PERSONAL", "PRIV", "PRIVATE"):
            continue
        explicit_business = (
            account_id in allow_ids or account.get("scope") == "business"
            or usage in ("ORGA", "BUSINESS", "PROFESSIONAL")
            or bool(re.search(r"\btesign\b", label, re.I))
        )
        if not explicit_business:
            continue
        source = str(account.get("source") or "manual")
        recorded_at = str(account.get("recorded_at") or "")[:10] or None
        try:
            age = (today - date.fromisoformat(recorded_at)).days
        except (ValueError, TypeError):
            age = None
        is_demo = "sandbox" in source.lower() or "demo" in source.lower()
        balance = account.get("balance")
        safe_accounts.append({
            "label": "Compte professionnel TESIGN",
            "balance": None if is_demo else balance,
            "currency_code": account.get("currency_code") or "EUR",
            "recorded_at": recorded_at,
            "source": source,
            "scope": "business",
            "stale": age is None or age < 0 or age > 2,
            "age_days": age,
            "is_demo": is_demo,
        })
        if account_id and not is_demo:
            selected_ids.add(account_id)
    for transaction in transactions:
        if str(transaction.get("account_id") or "") in selected_ids:
            allowed_transactions.append({key: transaction.get(key) for key in (
                "date", "description", "amount", "currency_code", "operation_type"
            )})
    if not safe_accounts:
        safe_accounts = [{"label": "Compte professionnel TESIGN", "balance": None,
                          "recorded_at": None, "source": "non_connecte", "scope": "business",
                          "stale": True, "age_days": None, "is_demo": False, "currency_code": "EUR"}]
    real = [a for a in safe_accounts if a["balance"] is not None and not a["is_demo"]]
    fresh = [a for a in real if not a["stale"] and a["source"] != "manual" and not a["source"].endswith("_snapshot")]
    snapshots = real and all(a["source"].endswith("_snapshot") for a in real)
    status = "connected" if fresh else "snapshot" if snapshots else "manual" if real and all(a["source"] == "manual" for a in real) else "stale" if real else "unavailable"
    messages = {
        "connected": "Solde transmis par le connecteur bancaire. Vérifier la date de relevé.",
        "manual": "Dernier solde saisi manuellement ; aucune synchronisation bancaire en direct n'est confirmée.",
        "stale": "Dernier solde connu ancien : ne pas le prendre pour le solde disponible aujourd'hui.",
        "unavailable": "Aucun solde professionnel vérifié. Une connexion bancaire réelle est nécessaire ; les données de démonstration sont exclues.",
        "snapshot": "Relevé du compte professionnel vérifié via Enable Banking en lecture seule, à la date indiquée. Import ponctuel ; pas de synchronisation bancaire continue sur ce site.",
    }
    dates = [a["recorded_at"] for a in real if a["recorded_at"]]
    banking = {"status": status, "source": ", ".join(sorted({a["source"] for a in real})) or "non_connecte",
               "updated_at": min(dates) if dates else None, "stale": any(a["stale"] for a in safe_accounts),
               "message": messages[status]}
    return safe_accounts, allowed_transactions, banking
