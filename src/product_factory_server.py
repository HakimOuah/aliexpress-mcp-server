"""Product Factory MCP entrypoint.

Combines AliExpress sourcing with DataForSEO-powered Google market research.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from .candidate_economics import economics_for_candidate, rank_candidate_economics
from .dataforseo_client import (
    DataForSEOClient,
    DataForSEOError,
    DataForSEOUpstreamError,
    extract_shopping_items,
)
from .google_aliexpress_discovery import discover_from_serps, google_discovery_queries
from .market_analysis import analyze_serps
from .offer_classifier import classify_offer, summarize_classified_offers
from .opportunity_sourcing import qualify_relevant_candidates, search_relevant_candidates
from .server import _get_client, _get_config, mcp
from .sourcing_relevance import is_relevant_search_item

log = structlog.get_logger(__name__)

_dataforseo_client: DataForSEOClient | None = None


async def _get_dataforseo_client() -> DataForSEOClient:
    global _dataforseo_client
    if _dataforseo_client is None:
        cfg = _get_config()
        _dataforseo_client = DataForSEOClient(cfg.dataforseo)
        log.info("mcp.dataforseo.ready")
    return _dataforseo_client


def set_dataforseo_client_for_testing(client: DataForSEOClient) -> None:
    global _dataforseo_client
    _dataforseo_client = client


def reset_dataforseo_for_testing() -> None:
    global _dataforseo_client
    _dataforseo_client = None


@mcp.tool
async def search_google_serp(
    keyword: str,
    country_code: str = "FR",
    language_code: str | None = None,
    device: str = "desktop",
    depth: int = 20,
) -> dict[str, Any]:
    """Get a live Google SERP without browser automation or CAPTCHA."""
    try:
        client = await _get_dataforseo_client()
        return await client.google_serp_live(
            keyword, country_code=country_code, language_code=language_code,
            device=device, depth=depth,
        )
    except (DataForSEOError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


@mcp.tool
async def search_google_shopping(
    keyword: str,
    country_code: str = "FR",
    language_code: str | None = None,
    device: str = "desktop",
    depth: int = 50,
) -> dict[str, Any]:
    """Extract Google Shopping / Popular Products elements for a keyword."""
    try:
        client = await _get_dataforseo_client()
        serp = await client.google_serp_live(
            keyword, country_code=country_code, language_code=language_code,
            device=device, depth=depth,
        )
        items = extract_shopping_items(serp)
        return {
            "keyword": keyword,
            "country_code": country_code.upper(),
            "shopping_presence": bool(items),
            "result_count": len(items),
            "items": items,
            "dataforseo_cost": serp.get("cost"),
        }
    except (DataForSEOError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


async def _fetch_market_serps(
    keywords: list[str],
    *,
    country_code: str,
    language_code: str | None,
    device: str,
    depth: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    cleaned = list(dict.fromkeys(k.strip() for k in keywords if k.strip()))
    if not cleaned:
        raise RuntimeError("keywords must contain at least one non-empty query")
    if len(cleaned) > 10:
        raise RuntimeError("keywords is capped at 10 queries per analysis")
    client = await _get_dataforseo_client()
    serps = [
        await client.google_serp_live(
            keyword, country_code=country_code, language_code=language_code,
            device=device, depth=depth,
        )
        for keyword in cleaned
    ]
    return cleaned, serps


async def _discover_aliexpress_via_google(
    *,
    target_terms: list[str],
    country_code: str,
) -> tuple[list[str], list[dict[str, Any]], float, dict[str, Any]]:
    """Discover AliExpress item IDs with Google when DS text search has no recall.

    Every query is independent. After the DataForSEO client's bounded retry,
    a query that still fails is recorded and skipped instead of aborting the
    entire product-opportunity analysis.
    """
    client = await _get_dataforseo_client()
    queries = google_discovery_queries(target_terms)
    serps: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for query in queries:
        try:
            serp = await client.google_serp_live(
                query,
                country_code=country_code,
                language_code=None,
                device="desktop",
                depth=30,
            )
        except DataForSEOUpstreamError as exc:
            error = {
                "query": query,
                "status_code": exc.status_code,
                "status_message": exc.status_message,
                "error": str(exc),
                "retryable": exc.retryable,
            }
            errors.append(error)
            log.warning(
                "product_factory.google_aliexpress_discovery.query_failed",
                query=query,
                status_code=exc.status_code,
                retryable=exc.retryable,
                error=str(exc),
            )
            continue
        serps.append(serp)

    discovered = discover_from_serps(serps)
    relevant = [
        item for item in discovered
        if is_relevant_search_item(item, target_terms)
    ]
    cost = round(sum(float(s.get("cost") or 0) for s in serps), 6)
    diagnostics = {
        "queries_total": len(queries),
        "queries_succeeded": len(serps),
        "queries_failed": len(errors),
        "no_results_queries": sum(bool(s.get("no_results")) for s in serps),
        "discovered_product_ids": len(discovered),
        "relevant_product_ids": len(relevant),
        "partial": bool(errors and serps),
        "failed": bool(errors and not serps),
        "errors": errors,
    }
    return queries, relevant, cost, diagnostics


def _category_market_price(
    market: dict[str, Any],
    category: str,
) -> float | None:
    bucket = market.get("pricing_by_category", {}).get(category) or {}
    value = bucket.get("median")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _build_category_matched_economics(
    qualified: list[dict[str, Any]],
    *,
    market: dict[str, Any],
    target_terms: list[str],
    requested_category: str,
    country_code: str,
    rules: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare every supplier with the market bucket that matches its offer.

    The requested category determines the primary opportunity verdict. Suppliers
    that are bundles/professional/etc. remain visible as alternates, but cannot
    make a PRODUCT opportunity look attractive by borrowing PRODUCT pricing.
    """
    primary_rows: list[dict[str, Any]] = []
    alternate_rows: list[dict[str, Any]] = []
    unpriced_rows: list[dict[str, Any]] = []

    for candidate in qualified:
        supplier_category = classify_offer(
            str(candidate.get("title") or ""),
            str(candidate.get("store") or ""),
            target_terms,
        )
        row = dict(candidate)
        row["supplier_category"] = supplier_category
        row["requested_market_category"] = requested_category

        if supplier_category in {"IRRELEVANT", "USED"}:
            row["comparison_status"] = "NOT_COMPARABLE"
            row["comparison_reason"] = (
                "supplier offer is not comparable with the requested product family"
            )
            unpriced_rows.append(row)
            continue

        category_price = _category_market_price(market, supplier_category)
        if category_price is None:
            row["comparison_status"] = "NO_MARKET_PRICE_FOR_CATEGORY"
            row["comparison_reason"] = (
                f"no reliable market median for {supplier_category}"
            )
            unpriced_rows.append(row)
            continue

        row["comparison_status"] = "MATCHED"
        row["market_price_category"] = supplier_category
        row["comparison_scope"] = (
            "REQUESTED_CATEGORY"
            if supplier_category == requested_category
            else "ALTERNATE_CATEGORY"
        )
        economics = economics_for_candidate(
            row,
            market_price_ttc=category_price,
            country_code=country_code,
            rules=rules,
        )
        if supplier_category == requested_category:
            primary_rows.append(economics)
        else:
            alternate_rows.append(economics)

    return (
        rank_candidate_economics(primary_rows),
        rank_candidate_economics(alternate_rows),
        unpriced_rows,
    )


@mcp.tool
async def analyze_google_competition(
    keywords: list[str],
    country_code: str = "FR",
    language_code: str | None = None,
    device: str = "desktop",
    depth: int = 50,
) -> dict[str, Any]:
    """Analyze recurring competitors, Ads, Shopping merchants and pricing."""
    try:
        cleaned, serps = await _fetch_market_serps(
            keywords, country_code=country_code, language_code=language_code,
            device=device, depth=depth,
        )
        analysis = analyze_serps(serps)
        analysis.update({
            "country_code": country_code.upper(),
            "language_code": language_code,
            "device": device,
            "keywords": cleaned,
            "query_results": [
                {
                    "keyword": keyword,
                    "item_types": serp.get("item_types", []),
                    "se_results_count": serp.get("se_results_count", 0),
                    "cost": serp.get("cost"),
                }
                for keyword, serp in zip(cleaned, serps)
            ],
        })
        return analysis
    except (DataForSEOError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


@mcp.tool
async def analyze_market_pricing(
    keywords: list[str],
    target_terms: list[str],
    country_code: str = "FR",
    language_code: str | None = None,
    device: str = "desktop",
    depth: int = 50,
) -> dict[str, Any]:
    """Classify Google product offers and calculate pricing by comparable type."""
    try:
        cleaned, serps = await _fetch_market_serps(
            keywords, country_code=country_code, language_code=language_code,
            device=device, depth=depth,
        )
        offers: list[dict[str, Any]] = []
        for serp in serps:
            offers.extend(extract_shopping_items(serp))
        summary = summarize_classified_offers(offers, target_terms)
        summary.update({
            "keywords": cleaned,
            "target_terms": target_terms,
            "country_code": country_code.upper(),
            "dataforseo_cost": round(sum(float(s.get("cost") or 0) for s in serps), 6),
        })
        return summary
    except (DataForSEOError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


@mcp.tool
async def analyze_product_opportunity(
    market_keywords: list[str],
    target_terms: list[str],
    aliexpress_query: str,
    country_code: str = "FR",
    market_category: str = "PRODUCT",
    max_aliexpress_results: int = 30,
    depth: int = 50,
) -> dict[str, Any]:
    """End-to-end market pricing + category-matched AliExpress economics.

    Supplier offers are classified with the same market taxonomy as Google
    offers. The requested category drives the final verdict; alternate supplier
    categories are returned separately with their own category-specific pricing.
    """
    category = market_category.upper()
    if category not in {"PRODUCT", "BUNDLE", "ACCESSORY", "PROFESSIONAL"}:
        raise RuntimeError("market_category must be PRODUCT, BUNDLE, ACCESSORY or PROFESSIONAL")

    cfg = _get_config()
    try:
        cleaned, serps = await _fetch_market_serps(
            market_keywords, country_code=country_code, language_code=None,
            device="desktop", depth=depth,
        )
        competition_summary = analyze_serps(serps)
        offers: list[dict[str, Any]] = []
        for serp in serps:
            offers.extend(extract_shopping_items(serp))
        market = summarize_classified_offers(offers, target_terms)
        price_bucket = market.get("pricing_by_category", {}).get(category) or {}
        market_price = price_bucket.get("median")
        if not isinstance(market_price, (int, float)) or market_price <= 0:
            return {
                "status": "INSUFFICIENT_MARKET_PRICING",
                "market_category": category,
                "market": market,
                "competition_summary": competition_summary,
                "suppliers": [],
                "supplier_economics": [],
                "alternate_category_suppliers": [],
            }

        ae_client = await _get_client()
        sourcing_queries, relevant_items, raw_pool_count = await search_relevant_candidates(
            ae_client,
            aliexpress_query=aliexpress_query,
            target_terms=target_terms,
            country_code=country_code,
            max_results=max_aliexpress_results,
        )

        discovery_mode = "aliexpress_ds_text_search"
        google_discovery_queries_used: list[str] = []
        google_discovery_cost = 0.0
        google_discovery_diagnostics: dict[str, Any] | None = None
        if not relevant_items:
            (
                google_discovery_queries_used,
                google_items,
                google_discovery_cost,
                google_discovery_diagnostics,
            ) = await _discover_aliexpress_via_google(
                target_terms=target_terms,
                country_code=country_code,
            )
            if google_items:
                discovery_mode = "google_serp_aliexpress_ids"
                relevant_items = google_items[:max_aliexpress_results]
            elif google_discovery_diagnostics.get("queries_failed", 0):
                discovery_mode = "google_serp_aliexpress_inconclusive"
            else:
                discovery_mode = "google_serp_aliexpress_no_ids"

        qualified, qualification_diagnostics = await qualify_relevant_candidates(
            ae_client,
            relevant_items,
            country_code=country_code,
            min_orders=cfg.rules.min_orders_pass,
            min_orders_watch=cfg.rules.min_orders_watch,
            min_rating=cfg.rules.min_rating_pass,
            min_rating_watch=cfg.rules.min_rating_watch,
            min_store_rating=cfg.rules.min_rating_watch,
            min_product_cost_eur=25.0,
            preferred_max_delivery_days=15,
            hard_max_delivery_days=30,
        )

        ranked, alternate_ranked, unpriced_suppliers = _build_category_matched_economics(
            qualified,
            market=market,
            target_terms=target_terms,
            requested_category=category,
            country_code=country_code,
            rules=cfg.rules,
        )
        best = ranked[0] if ranked else None
        if best and best["economics_verdict"] == "GO":
            status = "GO_CANDIDATE"
        elif best:
            status = "WATCH"
        elif discovery_mode == "google_serp_aliexpress_inconclusive":
            status = "SOURCING_INCONCLUSIVE"
        else:
            status = "NO_QUALIFYING_SUPPLIER"

        market_cost = round(sum(float(s.get("cost") or 0) for s in serps), 6)
        return {
            "status": status,
            "country_code": country_code.upper(),
            "market_keywords": cleaned,
            "target_terms": target_terms,
            "aliexpress_query": aliexpress_query,
            "discovery_mode": discovery_mode,
            "aliexpress_sourcing_queries": sourcing_queries,
            "google_aliexpress_discovery_queries": google_discovery_queries_used,
            "google_aliexpress_discovery_diagnostics": google_discovery_diagnostics,
            "market_category": category,
            "market_price_reference_eur": market_price,
            "market_pricing": price_bucket,
            "market_pricing_by_category": market.get("pricing_by_category", {}),
            "market_classification_counts": market.get("classification_counts", {}),
            "competition_summary": competition_summary,
            "aliexpress_raw_pool_count": raw_pool_count,
            "aliexpress_relevant_count": len(relevant_items),
            "aliexpress_qualified_count": len(qualified),
            "requested_category_supplier_count": len(ranked),
            "alternate_category_supplier_count": len(alternate_ranked),
            "unpriced_or_noncomparable_supplier_count": len(unpriced_suppliers),
            "qualification_diagnostics": qualification_diagnostics,
            "best_supplier": best,
            "suppliers": ranked[:10],
            "supplier_economics": ranked[:10],
            "alternate_category_suppliers": alternate_ranked[:10],
            "unpriced_or_noncomparable_suppliers": unpriced_suppliers[:20],
            "market_dataforseo_cost": market_cost,
            "google_discovery_dataforseo_cost": google_discovery_cost,
            "dataforseo_cost": round(market_cost + google_discovery_cost, 6),
        }
    except (DataForSEOError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


def main() -> None:
    cfg = _get_config()
    logging.basicConfig(level=getattr(logging, cfg.server.log_level, logging.INFO))
    log.info(
        "product_factory.mcp.server.start",
        host=cfg.server.host,
        port=cfg.server.port,
        transport="http",
        dataforseo_enabled=cfg.dataforseo.enabled,
    )
    mcp.run(transport="http", host=cfg.server.host, port=cfg.server.port)


if __name__ == "__main__":
    main()
