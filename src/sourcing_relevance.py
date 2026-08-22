"""Query expansion and relevance filtering for AliExpress sourcing."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_GENERIC_NOISE = {
    "gun", "pistolet", "pistola", "machine", "kit", "set", "tool", "tools",
    "product", "electric", "electrique", "pour", "avec", "the", "and", "for",
}
_DEVICE_HINTS = {
    "gun", "pistolet", "pistola", "machine", "tufter", "touffeter",
}
_TUFTING_ACCESSORY_HINTS = {
    "fil", "fils", "yarn", "thread", "coton", "cotton", "laine", "wool",
    "brin", "brins", "strand", "strands", "toile", "cloth", "fabric", "tissu",
    "cadre", "frame", "rasoir", "trimmer", "tondeuse", "ciseaux", "scissors",
    "aiguille", "needle", "needles", "colle", "glue", "backing",
}
_TUFTING_QUERY_EXPANSIONS = (
    "AK-V tufting gun",
    "AK-I tufting gun",
    "AK DUO tufting gun",
    "cut loop pile tufting gun",
    "rug tufting gun",
    "carpet tufting machine",
    "electric rug tufting machine",
)


def _norm(text: object) -> str:
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _tokens(text: object) -> set[str]:
    return {t for t in _norm(text).split() if len(t) >= 3}


def _is_tufting_family(target_terms: list[str]) -> bool:
    normalized = " ".join(_norm(t) for t in target_terms)
    return "tuft" in normalized or "touff" in normalized


def build_sourcing_queries(aliexpress_query: str, target_terms: list[str]) -> list[str]:
    candidates = [aliexpress_query, *target_terms]

    if _is_tufting_family(target_terms):
        candidates.extend(_TUFTING_QUERY_EXPANSIONS)
    else:
        combined: list[str] = []
        for term in candidates:
            n = _norm(term)
            if "tuft" in n:
                combined.extend([f"{term} carpet", f"{term} rug"])
        candidates.extend(combined)

    seen: set[str] = set()
    out: list[str] = []
    for query in candidates:
        q = " ".join(str(query).split()).strip()
        key = _norm(q)
        if q and key not in seen:
            seen.add(key)
            out.append(q)
    return out[:12]


def relevance_terms(target_terms: list[str]) -> set[str]:
    tokens: set[str] = set()
    for term in target_terms:
        tokens |= _tokens(term)
    return {t for t in tokens if t not in _GENERIC_NOISE}


def _has_tufting_anchor(title_norm: str) -> bool:
    return "tuft" in title_norm or "touff" in title_norm


def _has_device_hint(title_norm: str) -> bool:
    title_tokens = set(title_norm.split())
    if title_tokens & _DEVICE_HINTS:
        return True
    return "touffeter" in title_norm or "tufter" in title_norm


def _looks_like_tufting_accessory(title_norm: str) -> bool:
    tokens = set(title_norm.split())
    accessory_hits = tokens & _TUFTING_ACCESSORY_HINTS
    if not accessory_hits:
        return False

    # A real machine can be sold as a bundle with accessories, so accessory
    # words alone must not reject titles that clearly describe an actual device.
    strong_machine_markers = (
        "tufting gun",
        "tufting machine",
        "rug tufting gun",
        "carpet tufting",
        "machine a tufting",
        "pistolet a touffeter electrique",
        "pistolet tufting electrique",
        "cut loop pile",
        "ak v",
        "ak i",
        "ak duo",
    )
    if any(marker in title_norm for marker in strong_machine_markers):
        return False
    return True


def is_relevant_search_item(item: dict[str, Any], target_terms: list[str]) -> bool:
    title = str(item.get("title") or "")
    title_norm = _norm(title)
    if not title_norm:
        return False

    # Tufting needs domain-specific evidence. Generic spray/water/massage guns
    # are rejected, and accessory-only listings are rejected even when a poor
    # machine translation happens to include "pistolet à touffeter".
    if _is_tufting_family(target_terms):
        if not (_has_tufting_anchor(title_norm) and _has_device_hint(title_norm)):
            return False
        if _looks_like_tufting_accessory(title_norm):
            return False
        return True

    for term in target_terms:
        n = _norm(term)
        if n and n in title_norm:
            return True

    core = relevance_terms(target_terms)
    title_tokens = _tokens(title)
    if not core:
        return False

    distinctive = {t for t in core if len(t) >= 5}
    if distinctive & title_tokens:
        return True

    return len(core & title_tokens) >= 2


def dedupe_and_filter(items: list[dict[str, Any]], target_terms: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        product_id = str(item.get("itemId") or "")
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        if is_relevant_search_item(item, target_terms):
            out.append(item)
    return out
