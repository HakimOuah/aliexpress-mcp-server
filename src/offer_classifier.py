"""Classify Google and supplier offers into comparable market buckets."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from statistics import median
from typing import Any


USED_HINTS = ("occasion", "used", "seconde main", "second hand", "reconditionné", "refurbished")
ACCESSORY_HINTS = (
    "tondeuse", "trimmer", "rasoir", "cadre", "frame", "fil", "yarn", "thread",
    "toile", "tissu", "cloth", "fabric", "aiguille", "needle", "ciseaux", "scissors",
    "pièce", "spare", "cotton", "coton", "wool", "laine", "strand", "strands",
    "brin", "brins", "threader", "enfileur", "backing", "glue", "colle",
)
BUNDLE_HINTS = ("kit", "starter", "démarrage", "set", "ensemble", "pack", "bundle")
PRO_HINTS = ("professionnel", "professional", "industrial", "industriel", "pneumatic", "pneumatique")


def _norm(text: object) -> str:
    s = str(text or "").lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _is_tufting_family(target_terms: list[str]) -> bool:
    joined = " ".join(_norm(term) for term in target_terms)
    return "tuft" in joined or "touff" in joined


def _manual_tufting_mismatch(text: str, target_terms: list[str]) -> bool:
    """Reject manual tufting tools when the requested opportunity is a gun/machine.

    Manual punch-style tufting tools can be valid products in their own right, but
    they are not price-comparable with electric/pneumatic tufting machines. If the
    caller explicitly includes ``manual`` in the target aliases, they remain valid.
    """
    if not _is_tufting_family(target_terms):
        return False
    target_text = " ".join(_norm(term) for term in target_terms)
    if "manual" in target_text or "manuel" in target_text:
        return False
    if "manual" not in text and "manuel" not in text:
        return False
    strong_powered_markers = (
        "electric", "électrique", "electrique", "pneumatic", "pneumatique",
        "brushless", "sans brosse", "ak-v", "ak v", "ak-i", "ak i", "ak duo",
    )
    return not any(marker in text for marker in strong_powered_markers)


def classify_offer(title: str, seller: str | None, target_terms: list[str]) -> str:
    """Return PRODUCT, BUNDLE, ACCESSORY, USED, PROFESSIONAL or IRRELEVANT.

    The classifier is deliberately deterministic and auditable. ``target_terms``
    contains aliases for the core product being compared. The same classifier is
    used for Google market offers and AliExpress supplier listings so economics
    are calculated against like-for-like market buckets.
    """
    text = _norm(title)
    seller_text = _norm(seller)
    target = [_norm(t) for t in target_terms if _norm(t)]

    if any(h in text for h in USED_HINTS) or "leboncoin" in seller_text:
        return "USED"
    if _manual_tufting_mismatch(text, target_terms):
        return "IRRELEVANT"
    if any(h in text for h in PRO_HINTS):
        return "PROFESSIONAL"

    has_target = any(t in text for t in target)
    if not has_target:
        for term in target:
            words = [w for w in re.findall(r"[a-zà-ÿ0-9]+", term) if len(w) >= 4]
            if words and all(w in text for w in words):
                has_target = True
                break

    # Accessory-heavy titles often mention the parent product for compatibility
    # or SEO (e.g. "tufting gun ... cotton 400g"). Keep those in ACCESSORY so
    # they remain discoverable in the tufting universe without contaminating
    # machine pricing/economics.
    if any(h in text for h in ACCESSORY_HINTS) and not any(h in text for h in BUNDLE_HINTS):
        return "ACCESSORY"
    if not has_target:
        return "IRRELEVANT"
    if any(h in text for h in BUNDLE_HINTS):
        return "BUNDLE"
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


def summarize_classified_offers(
    offers: list[dict[str, Any]], target_terms: list[str]
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    merchant_counts: Counter[str] = Counter()

    for item in offers:
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
        prices: list[float] = []
        for row in rows:
            raw = row.get("price")
            if isinstance(raw, dict):
                raw = raw.get("current") or raw.get("value")
            if isinstance(raw, (int, float)) and raw > 0:
                prices.append(float(raw))
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
        "total_offers": len(offers),
        "classification_counts": {k: len(v) for k, v in sorted(buckets.items())},
        "pricing_by_category": pricing,
        "top_merchants": [
            {"merchant": merchant, "appearances": count}
            for merchant, count in merchant_counts.most_common(20)
        ],
    }
