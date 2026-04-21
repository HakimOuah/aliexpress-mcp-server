"""Modèles de données du projet.

Le normalizer Phase 4 prend les payloads bruts IOP (dict) et renvoie
des `DropPilotProduct` immuables, qualitativement filtrés. Le scoring
marge et la recherche concurrentielle ne sont pas faits ici — ils
appartiennent au scout agent (Phase 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Verdict(Enum):
    PASS = "PASS"
    WATCH = "WATCH"
    KILL = "KILL"


@dataclass(frozen=True)
class SkuRef:
    """SKU de référence utilisé pour le scoring.

    Sélectionné par le normalizer comme la variante la moins chère
    (`offer_sale_price_eur`) parmi celles ayant `available_stock >= 1`.
    """

    sku_id: str
    """Identifiant numérique AE — à utiliser pour `aliexpress.ds.freight.query`.
    Ne PAS confondre avec `sku_attr` (combinaison d'attributs d'affichage)."""

    sku_attr: str
    """Chaîne de propriétés combinées, ex ``"14:29#Bear;183:200007741"``."""

    offer_sale_price_eur: float
    """Prix dropshipper (celui qu'on paye AE), TTC EUR."""

    sku_price_eur: float
    """Prix retail AE affiché (référence publique), TTC EUR."""

    currency_code: str

    available_stock: int

    sku_properties: dict[str, str]
    """Map nom-propriété → valeur, ex ``{"Couleur": "Gris clair", "Spécification": "400MMx600MM"}``."""

    sku_image_url: str | None


@dataclass(frozen=True)
class StoreInfo:
    store_id: int
    store_name: str
    store_country_code: str
    shipping_speed_rating: float
    communication_rating: float
    item_as_described_rating: float


@dataclass(frozen=True)
class ShippingInfo:
    country_code: str
    cost_eur: float
    cost_format: str
    currency: str
    min_delivery_days: int
    max_delivery_days: int
    delivery_date_desc: str
    ship_from_country: str
    is_eu_warehouse: bool
    tracking: bool
    company: str
    shipping_code: str
    free_shipping: bool


@dataclass(frozen=True)
class PackageInfo:
    weight_kg: float
    length_cm: int
    width_cm: int
    height_cm: int


@dataclass(frozen=True)
class DropPilotProduct:
    # Identifiants
    product_id: str
    source: str

    # Base info
    title: str
    subject: str
    category_id: int | None
    product_url: str
    main_image_url: str
    image_urls: list[str]

    # Qualité
    rating: float
    evaluate_rate_pct: float
    order_count: int
    evaluation_count: int
    is_aliexpress_choice: bool

    # Références SKU
    sku_ref: SkuRef
    all_skus: list[SkuRef]
    sku_ref_is_cheapest_absolute: bool
    """True quand `sku_ref` est la variante la moins chère dans l'absolu
    (toutes SKUs confondues, en stock ou non). False quand on a fallback
    sur une variante plus chère parce que la vraie moins chère est OOS —
    signal utile au scout : la marge réelle est potentiellement moins
    attrayante que ce que suggère le prix d'affichage de text.search."""

    # Vendeur
    store: StoreInfo

    # Logistique — None par défensivité, mais tout produit retourné par
    # le normalizer a un `shipping_fr` rempli (filtre passe-1 obligatoire).
    shipping_fr: ShippingInfo | None

    # Package — None toléré si AE n'a pas renseigné les dimensions.
    package: PackageInfo | None

    # Filtres passés (ordre d'application), pour debug / audit.
    passed_filters: list[str]

    # Méta
    fetched_at: datetime


@dataclass(frozen=True)
class ItemDiagnostic:
    """Result of running one text.search item through the passe-1
    evaluator. Used by `search_and_diagnose` to calibrate filter
    thresholds against the real AE catalog.

    * `verdict` is either ``"PASS"`` (every applicable filter passed —
      `product` is populated) or ``"KILL"`` (at least one filter failed
      or evaluation couldn't complete — `product` is ``None``).
    * `passed_filters` / `failed_filters` are disjoint; a filter only
      appears in one of them, and is absent from both when it couldn't
      be evaluated (e.g. downstream of a hard failure like "no SKU in
      stock" or "product.get raised").
    """

    product_id: str
    title: str
    verdict: str
    passed_filters: list[str]
    failed_filters: list[str]

    # Scanned metadata — None when the pipeline couldn't gather the value.
    offer_sale_price_eur: float | None
    rating: float | None
    order_count: int | None
    store_ratings: dict[str, float] | None

    # Full product, populated only when verdict == "PASS".
    product: DropPilotProduct | None
