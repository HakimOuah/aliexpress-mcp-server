"""Product Factory MCP entrypoint.

Combines AliExpress sourcing with DataForSEO-powered Google market research.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from .dataforseo_client import DataForSEOClient, DataForSEOError, extract_shopping_items
from .market_analysis import analyze_serps
from .normalizer import diagnose_search_results, normalize_search_results
from .offer_classifier import summarize_classified_offers
from .opportunity_diagnostics import summarize_filter_failures
from .opportunity_engine import economics_for_product, rank_supplier_economics
from .server import _get_client, _get_config, mcp

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
    """Classify Google product offers and calculate pricing by comparable type.

    Categories are PRODUCT, BUNDLE, ACCESSORY, USED, PROFESSIONAL and
    IRRELEVANT. `target_terms` are aliases for the core product.
    """
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
    """End-to-end market pricing + AliExpress landed-cost/margin analysis.

    When no AliExpress result qualifies, the response automatically includes
    a full filter diagnostic so the agent can distinguish a bad niche from
    thresholds that are simply too strict for the current catalogue.
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
            }

        ae_client = await _get_client()
        raw_items = await ae_client.search_products(
            query=aliexpress_query,
            max_results=max_aliexpress_results,
            target_country=country_code,
            sort_by="orders",
        )
        products = await normalize_search_results(
            client=ae_client,
            raw_items=raw_items,
            target_country=country_code,
        )
        economics = [
            economics_for_product(
                product,
                market_price_ttc=float(market_price),
                country_code=country_code,
                rules=cfg.rules,
            )
            for product in products
        ]
        ranked = rank_supplier_economics(economics)
        best = ranked[0] if ranked else None
        status = "GO_CANDIDATE" if best and best["economics_verdict"] == "GO" else (
            "WATCH" if best else "NO_QUALIFYING_SUPPLIER"
        )

        qualification_diagnostics: dict[str, Any] | None = None
        if not products and raw_items:
            diagnostics = await diagnose_search_results(
                client=ae_client,
                raw_items=raw_items,
                target_country=country_code,
            )
            diagnostic_rows = [
                {
                    "product_id": d.product_id,
                    "title": d.title,
                    "failed_filters": d.failed_filters,
                    "passed_filters": d.passed_filters,
                    "offer_sale_price_eur": d.offer_sale_price_eur,
                    "rating": d.rating,
                    "order_count": d.order_count,
                }
                for d in diagnostics
            ]
            qualification_diagnostics = summarize_filter_failures(diagnostic_rows)
            qualification_diagnostics["candidates"] = diagnostic_rows

        return {
            "status": status,
            "country_code": country_code.upper(),
            "market_keywords": cleaned,
            "target_terms": target_terms,
            "aliexpress_query": aliexpress_query,
            "market_category": category,
            "market_price_reference_eur": market_price,
            "market_pricing": price_bucket,
            "market_classification_counts": market.get("classification_counts", {}),
            "competition_summary": competition_summary,
            "aliexpress_raw_count": len(raw_items),
            "aliexpress_qualified_count": len(products),
            "qualification_diagnostics": qualification_diagnostics,
            "best_supplier": best,
            "suppliers": ranked[:10],
            "supplier_economics": ranked[:10],
            "dataforseo_cost": round(sum(float(s.get("cost") or 0) for s in serps), 6),
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
