"""Modèles de données du projet."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Verdict(Enum):
    PASS = "PASS"
    WATCH = "WATCH"
    KILL = "KILL"


@dataclass
class ShippingInfo:
    country_code: str
    cost_eur: float
    delivery_days_min: int
    delivery_days_max: int
    carrier: str
    is_tracked: bool


@dataclass
class Product:
    # Identité
    product_id: str
    title: str
    product_url: str
    image_url: str
    additional_images: list[str] = field(default_factory=list)

    # Pricing (toujours en EUR après conversion)
    price_eur: float = 0.0
    original_price_eur: float | None = None
    discount_pct: float | None = None

    # Shipping par pays
    shipping_eur_fr: float | None = None
    shipping_eur_be: float | None = None
    shipping_eur_ch: float | None = None
    shipping_eur_lu: float | None = None
    shipping_days_fr: int | None = None

    # Qualité
    rating: float = 0.0
    review_count: int = 0
    order_count: int = 0

    # Fournisseur
    seller_id: str = ""
    seller_name: str = ""
    seller_rating_pct: float | None = None
    seller_country: str | None = None

    # Scoring DropPilot (calculé)
    suggested_retail_price_eur: float = 0.0
    margin_estimate_fr: float = 0.0
    margin_pct_fr: float = 0.0
    verdict: Verdict = Verdict.KILL
    verdict_reason: str = ""

    # Méta
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    source: str = "aliexpress"
