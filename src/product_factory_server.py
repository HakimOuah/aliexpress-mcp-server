"""Product Factory MCP entrypoint.

Extends the existing AliExpress MCP server with DataForSEO-powered Google
market research tools. Keeping registration in a separate module avoids
coupling the proven AliExpress pipeline to the SERP integration.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from .dataforseo_client import DataForSEOClient, DataForSEOError, extract_shopping_items
from .market_analysis import analyze_serps
from .server import _get_config, mcp

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
    """Get a live Google SERP without browser automation or CAPTCHA.

    Uses DataForSEO Google Organic Live Advanced. The response can contain
    organic results, paid ads, Shopping blocks, popular products and other
    Google SERP features. Use it to inspect the competitive landscape for a
    product keyword.
    """
    try:
        client = await _get_dataforseo_client()
        return await client.google_serp_live(
            keyword,
            country_code=country_code,
            language_code=language_code,
            device=device,
            depth=depth,
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
    """Extract Google Shopping/product elements visible for a keyword.

    This uses the same live Advanced SERP call and returns only shopping,
    popular-product and commercial-unit elements, with their nested product
    fields where DataForSEO exposes them.
    """
    try:
        client = await _get_dataforseo_client()
        serp = await client.google_serp_live(
            keyword,
            country_code=country_code,
            language_code=language_code,
            device=device,
            depth=depth,
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


@mcp.tool
async def analyze_google_competition(
    keywords: list[str],
    country_code: str = "FR",
    language_code: str | None = None,
    device: str = "desktop",
    depth: int = 50,
) -> dict[str, Any]:
    """Analyze competition, Ads, Shopping and pricing across Google queries.

    Pass 1-10 transactional keyword variants for the same product. Returns
    recurring organic domains, advertisers, Shopping merchants, marketplace
    presence and observed Shopping price min/median/max. This is the main
    market-validation tool for pricing/positioning and Go/No-Go analysis.
    """
    cleaned = list(dict.fromkeys(k.strip() for k in keywords if k.strip()))
    if not cleaned:
        raise RuntimeError("keywords must contain at least one non-empty query")
    if len(cleaned) > 10:
        raise RuntimeError("keywords is capped at 10 queries per analysis")

    try:
        client = await _get_dataforseo_client()
        serps: list[dict[str, Any]] = []
        query_results: list[dict[str, Any]] = []
        for keyword in cleaned:
            serp = await client.google_serp_live(
                keyword,
                country_code=country_code,
                language_code=language_code,
                device=device,
                depth=depth,
            )
            serps.append(serp)
            query_results.append(
                {
                    "keyword": keyword,
                    "item_types": serp.get("item_types", []),
                    "se_results_count": serp.get("se_results_count", 0),
                    "cost": serp.get("cost"),
                }
            )

        analysis = analyze_serps(serps)
        analysis.update(
            {
                "country_code": country_code.upper(),
                "language_code": language_code,
                "device": device,
                "keywords": cleaned,
                "query_results": query_results,
            }
        )
        return analysis
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
