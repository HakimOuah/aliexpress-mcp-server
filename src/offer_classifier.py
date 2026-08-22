"""Classify Google and supplier offers into comparable market buckets."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from statistics import median
from typing import Any
from urllib.parse import urlsplit, urlunsplit


USED_HINTS = ("occasion", "used", "seconde main", "second hand", "reconditionne", "refurbished")
ACCESSORY_HINTS = (
    "tondeuse", "trimmer", "rasoir", "cadre", "frame", "fil", "yarn", "thread",
    "toile", "tissu", "cloth", "fabric", "aiguille", "needle", "ciseaux", "scissors",
    "piece", "spare", "cotton", "coton", "wool", "laine", "strand", "strands",
    "brin", "brins", "threader", "enfileur", "backing", "glue", "colle",
)
BUNDLE_HINTS = ("kit", "starter", "demarrage", "set", "ensemble", "pack", "bundle")
PRO_HINTS = ("professionnel", "professional", "industrial", "industriel", "pneumatic", "pneumatique")

_TUFTING_DEVICE_HINTS = (
    "gun", "pistolet", "machine", "touffeter", "tufter", "electric", "electrique",
    "brushless", "sans brosse", "ak-v", "ak v", "ak-i", "ak i", "ak duo",
    "cut pile", "loop pile", "cut loop",
)


def _norm(text: object) -> str:
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_tufting_family(target_terms: list[str]) -> bool:
    joined = " ".join(_norm(term) for term in target_terms)
    return "tuft" in joined or "touff" in joined


def _is_tufting_device_offer(text: str, target_terms: list[str]) -> bool:
    """Recognize translated tufting-machine titles without exact alias matching."""
    if not _is_tufting_family(target_terms):
        return False
    has_anchor = "tuft" in text or "touff" in text
    has_device = any(hint in text for hint in _TUFTING_DEVICE_HINTS)
    return has_anchor and has_device


def _manual_tufting_mismatch(text: str, target_terms: list[str]) -> bool:
    """Reject manual tufting tools when the requested opportunity is powered."""
    if not _is_tufting_family(target_terms):
        return False
    target_text = " ".join(_norm(term) for term in target_terms)
    if "manual" in target_text or "manuel" in target_text:
        return False
    if "manual" not in text and "manuel" not in text:
        return False
    strong_powered_markers = (
        "electric", "electrique", "pneumatic", "pneumatique",
        "brushless", "sans brosse", "ak-v", "ak v", "ak-i", "ak i", "ak duo",
    )
    return not any(marker in text for marker in strong_powered_markers)


def classify_offer(title: str, seller: str | None, target_terms: list[str]) -> str:
    """Return PRODUCT, BUNDLE, ACCESSORY, USED, PROFESSIONAL or IRRELEVANT."""
    text = _norm(title)
    seller_text = _norm(seller)
    target = [_norm(t) for t in target_terms if _norm(t)]

    if any(h in text for h in USED_HINTS) or "leboncoin" in seller_text:
        return "USED"
    if _manual_tufting_mismatch(text, target_terms):
        return "IRRELEVANT"
    if any(h in text for h in PRO_HINTS):
        return "PROFESSIONAL"

    tufting_device_match = _is_tufting_device_offer(text, target_terms)

    has_target = any(t in text for t in target)
    if not has_target:
        for term in target:
            words = [w for w in re.findall(r"[a-z0-9]+", term) if len(w) >= 4]
            if words and all(w in text for w in words):
                has_target = True
                break
    has_target = has_target or tufting_device_match

    has_accessory_hint = any(h in text for h in ACCESSORY_HINTS)
    has_bundle_hint = any(h in text for h in BUNDLE_HINTS)
    if has_accessory_hint and not has_bundle_hint:
        return "ACCESSORY"
    if has_target and has_bundle_hint:
        return "BUNDLE"
    if not has_target:
        return "IRRELEVANT"
    return "PRODUCT"


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return round(xs[0], 2)
    pos = (len(xs) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 2)
    value = xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
    return round(value, 2)


def _price_value(item: dict[str, Any]) -> float | None:
    raw = item.get("price")
    if isinstance(raw, dict):
        raw = raw.get("current") or raw.get("value")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return None


def _normalized_external_url(item: dict[str, Any]) -> str | None:
    """Return a stable merchant URL when one exists, excluding Google tracking URLs."""
    for key in ("product_url", "url", "link", "check_url", "shopping_url"):
        value = item.get(key)
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            continue
        try:
            parts = urlsplit(value)
        except ValueError:
            continue
        host = (parts.hostname or "").lower()
        if not host or host.endswith("google.com") or host.endswith("google.fr"):
            continue
        path = re.sub(r"/+", "/", parts.path or "/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/"), "", ""))
    return None


def _offer_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    """Build a conservative identity for de-duplicating repeated SERP observations.

    Prefer a real merchant/product URL. When DataForSEO only exposes Google
    tracking URLs, fall back to merchant + normalized title + displayed price.
    That removes the same offer seen through several keywords without collapsing
    different merchants or differently priced variants.
    """
    external_url = _normalized_external_url(item)
    if external_url:
        return ("url", external_url)

    seller = item.get("seller") or item.get("source") or item.get("domain") or ""
    title = _norm(item.get("title"))
    price = _price_value(item)
    rounded_price = round(price, 2) if price is not None else None
    return ("fallback", _norm(seller), title, rounded_price)


def dedupe_offers(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove repeated observations of the same market offer, preserving order."""
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for item in offers:
        identity = _offer_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def summarize_classified_offers(
    offers: list[dict[str, Any]], target_terms: list[str]
) -> dict[str, Any]:
    unique_offers = dedupe_offers(offers)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    merchant_counts: Counter[str] = Counter()

    for item in unique_offers:
        title = str(item.get("title") or "")
        seller = item.get("seller") or item.get("source")
        category = classify_offer(title, str(seller) if seller else None, target_terms)
        row = dict(item)
        row["offer_category"] = category
        buckets[category].append(row)
        if seller:
            merchant_counts[str(seller)] += 1

    pricing: dict[str, Any] = {}
    for category, rows in buckets.items():
        prices = [price for row in rows if (price := _price_value(row)) is not None]
        pricing[category] = {
            "count": len(rows),
            "priced_count": len(prices),
            "p25": _percentile(prices, 0.25),
            "median": round(median(prices), 2) if prices else None,
            "p75": _percentile(prices, 0.75),
            "min": round(min(prices), 2) if prices else None,
            "max": round(max(prices), 2) if prices else None,
        }

    return {
        "total_offers": len(unique_offers),
        "raw_total_offers": len(offers),
        "unique_total_offers": len(unique_offers),
        "duplicates_removed": len(offers) - len(unique_offers),
        "classification_counts": {k: len(v) for k, v in sorted(buckets.items())},
        "pricing_by_category": pricing,
        "top_merchants": [
            {"merchant": merchant, "appearances": count}
            for merchant, count in merchant_counts.most_common(20)
        ],
    }
