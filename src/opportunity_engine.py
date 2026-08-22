"""Combine classified market pricing with AliExpress landed costs."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import DropPilotProduct


def vat_rate(country_code: str, rules: Any) -> float:
    return {
        "FR": rules.vat_fr,
        "BE": rules.vat_be,
        "CH": rules.vat_ch,
        "LU": rules.vat_lu,
    }.get(country_code.upper(), rules.vat_fr)


def economics_for_product(
    product: DropPilotProduct,
    *,
    market_price_ttc: float,
    country_code: str,
    rules: Any,
) -> dict[str, Any]:
    shipping = product.shipping_fr
    shipping_cost = shipping.cost_eur if shipping else 0.0
    product_cost = product.sku_ref.offer_sale_price_eur
    landed_cost = product_cost + shipping_cost
    vat = vat_rate(country_code, rules)
    revenue_ht = market_price_ttc / (1.0 + vat)
    gross_profit = revenue_ht - landed_cost
    gross_margin_pct = (gross_profit / revenue_ht * 100.0) if revenue_ht > 0 else 0.0

    if gross_margin_pct >= rules.min_margin_pct:
        verdict = "GO"
    elif gross_margin_pct >= 25.0:
        verdict = "WATCH"
    else:
        verdict = "NO_GO"

    return {
        "product_id": product.product_id,
        "title": product.title,
        "product_url": product.product_url,
        "store": product.store.store_name,
        "rating": product.rating,
        "orders": product.order_count,
        "product_cost_eur": round(product_cost, 2),
        "shipping_cost_eur": round(shipping_cost, 2),
        "landed_cost_eur": round(landed_cost, 2),
        "market_price_ttc_eur": round(market_price_ttc, 2),
        "revenue_ht_eur": round(revenue_ht, 2),
        "gross_profit_eur": round(gross_profit, 2),
        "gross_margin_pct": round(gross_margin_pct, 1),
        "delivery_max_days": shipping.max_delivery_days if shipping else None,
        "ship_from_country": shipping.ship_from_country if shipping else None,
        "economics_verdict": verdict,
        "sku_ref": asdict(product.sku_ref),
    }


def rank_supplier_economics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"GO": 0, "WATCH": 1, "NO_GO": 2}
    return sorted(
        rows,
        key=lambda row: (
            order.get(str(row.get("economics_verdict")), 9),
            -float(row.get("gross_margin_pct") or 0),
            float(row.get("landed_cost_eur") or 999999),
        ),
    )
