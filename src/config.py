"""Chargement typé de la configuration depuis .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AliExpressConfig:
    app_key: str
    app_secret: str
    access_token: str
    refresh_token: str
    callback_url: str
    default_language: str
    default_currency: str
    tracking_id: str


@dataclass(frozen=True)
class DropPilotRules:
    price_multiplier: float
    min_margin_pct: float
    vat_fr: float
    vat_be: float
    vat_ch: float
    vat_lu: float
    min_orders_pass: int
    min_rating_pass: float
    min_orders_watch: int
    min_rating_watch: float


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    log_level: str
    cache_max_size: int
    cache_ttl_search: int
    cache_ttl_product: int
    cache_ttl_shipping: int


@dataclass(frozen=True)
class AppConfig:
    aliexpress: AliExpressConfig
    rules: DropPilotRules
    server: ServerConfig


def _env(key: str, default: str | None = None) -> str:
    """Récupère une variable d'env, lève ValueError si absente et pas de default."""
    value = os.getenv(key, default)
    if value is None:
        raise ValueError(f"Variable d'environnement manquante : {key}")
    return value


def load_config(env_path: str | Path | None = None) -> AppConfig:
    """Charge la config depuis le fichier .env et les variables d'environnement."""
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    return AppConfig(
        aliexpress=AliExpressConfig(
            app_key=_env("AE_APP_KEY"),
            app_secret=_env("AE_APP_SECRET"),
            access_token=_env("AE_ACCESS_TOKEN", ""),
            refresh_token=_env("AE_REFRESH_TOKEN", ""),
            callback_url=_env("AE_CALLBACK_URL"),
            default_language=_env("AE_DEFAULT_LANGUAGE", "FR"),
            default_currency=_env("AE_DEFAULT_CURRENCY", "EUR"),
            tracking_id=_env("AE_TRACKING_ID", "default"),
        ),
        rules=DropPilotRules(
            price_multiplier=float(_env("DP_PRICE_MULTIPLIER", "3.0")),
            min_margin_pct=float(_env("DP_MIN_MARGIN_PCT", "40")),
            vat_fr=float(_env("DP_VAT_FR", "0.20")),
            vat_be=float(_env("DP_VAT_BE", "0.21")),
            vat_ch=float(_env("DP_VAT_CH", "0.081")),
            vat_lu=float(_env("DP_VAT_LU", "0.17")),
            min_orders_pass=int(_env("DP_MIN_ORDERS_PASS", "100")),
            min_rating_pass=float(_env("DP_MIN_RATING_PASS", "4.3")),
            min_orders_watch=int(_env("DP_MIN_ORDERS_WATCH", "50")),
            min_rating_watch=float(_env("DP_MIN_RATING_WATCH", "4.0")),
        ),
        server=ServerConfig(
            host=_env("MCP_HOST", "0.0.0.0"),
            port=int(_env("MCP_PORT", "8080")),
            log_level=_env("LOG_LEVEL", "INFO"),
            cache_max_size=int(_env("CACHE_MAX_SIZE", "500")),
            cache_ttl_search=int(_env("CACHE_TTL_SEARCH", "3600")),
            cache_ttl_product=int(_env("CACHE_TTL_PRODUCT", "21600")),
            cache_ttl_shipping=int(_env("CACHE_TTL_SHIPPING", "86400")),
        ),
    )
