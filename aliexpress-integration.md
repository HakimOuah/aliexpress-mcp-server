# Plan d'intégration AliExpress MCP Server

## 🏗️ Architecture globale

```
┌─────────────┐
│  Telegram   │  Hakim envoie une requête
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  VPS Hostinger (srv1575867.hstgr.cloud) │
│                                          │
│  ┌────────────────────────┐             │
│  │ hermes-agent-hjft      │             │
│  │ (Docker container)     │             │
│  │                        │             │
│  │  Claude Sonnet 4.6     │             │
│  │  run_agent.py          │             │
│  └──────────┬─────────────┘             │
│             │ délègue via API Anthropic │
│             ▼                            │
│  ┌────────────────────────┐             │
│  │ aliexpress-scout       │             │
│  │ (Managed Agent)        │             │
│  └──────────┬─────────────┘             │
│             │ MCP protocol              │
│             ▼                            │
│  ┌────────────────────────┐             │
│  │ aliexpress-mcp         │             │
│  │ (Docker container)     │             │
│  │                        │             │
│  │  FastMCP server        │             │
│  │  Port 8080 (localhost) │             │
│  └──────────┬─────────────┘             │
└─────────────┼────────────────────────────┘
              │ HTTPS
              ▼
      ┌───────────────┐
      │ AliExpress    │
      │ Open Platform │
      └───────────────┘
```

## 📋 Endpoints AliExpress utilisés

| Endpoint | Usage | Cache TTL | Priorité |
|---|---|---|---|
| `aliexpress.ds.text.search` | Recherche par mot-clé | 1h | P0 |
| `aliexpress.ds.product.get` | Détails d'un produit | 6h | P0 |
| `aliexpress.ds.freight.query` | Frais de port FR/BE/CH/LU | 24h | P0 |
| `aliexpress.ds.recommend.feed.get` | Produits tendance par catégorie | 2h | P1 |
| `aliexpress.ds.category.get` | Arborescence catégories | 7j | P2 |

**P0** = indispensable pour MVP  
**P1** = nice-to-have, à ajouter en v1.1  
**P2** = plus tard si besoin

## 🛠️ Tools MCP exposés

### 1. `search_aliexpress_products` (P0)

Recherche principale, utilisée à chaque demande de sourcing.

**Paramètres :**
```python
query: str                       # mot-clé ou niche, ex: "tapis yoga antiderapant"
max_results: int = 20            # 1-50
min_orders: int | None = None    # filtre volume min
min_rating: float | None = None  # filtre rating min (0-5)
max_price_eur: float | None = None
sort_by: str = 'orders'          # 'orders' | 'price_asc' | 'price_desc' | 'rating'
target_country: str = 'FR'       # pour les frais de port prioritaires
```

**Retour :** `list[Product]` trié par score décroissant

### 2. `get_aliexpress_product_details` (P0)

Détails enrichis d'un produit spécifique.

**Paramètres :**
```python
product_id: str
include_shipping_all_countries: bool = True  # calcule shipping FR/BE/CH/LU
```

**Retour :** `Product` complet

### 3. `get_shipping_cost` (P0)

Frais de port précis pour un produit vers un pays.

**Paramètres :**
```python
product_id: str
country_code: str                # 'FR' | 'BE' | 'CH' | 'LU'
quantity: int = 1
```

**Retour :** `ShippingInfo` (coût EUR, délai min/max jours, transporteur)

### 4. `search_hot_products` (P1)

Produits tendance d'une catégorie, utile pour le niche scoring proactif.

**Paramètres :**
```python
category_id: str
max_results: int = 20
```

**Retour :** `list[Product]`

## 📐 Schéma de données

### `Product` (dataclass)

```python
@dataclass
class Product:
    # Identité
    product_id: str
    title: str
    product_url: str
    image_url: str
    additional_images: list[str]
    
    # Pricing (toujours en EUR après conversion)
    price_eur: float                    # prix actuel (avec promo si active)
    original_price_eur: float | None    # prix avant promo
    discount_pct: float | None          # 0-100
    
    # Shipping par pays
    shipping_eur_fr: float | None
    shipping_eur_be: float | None
    shipping_eur_ch: float | None
    shipping_eur_lu: float | None
    shipping_days_fr: int | None        # délai médian annoncé
    
    # Qualité
    rating: float                        # 0-5
    review_count: int
    order_count: int                     # volume total commandes
    
    # Fournisseur
    seller_id: str
    seller_name: str
    seller_rating_pct: float | None     # % positif fournisseur
    seller_country: str | None          # généralement 'CN'
    
    # Scoring DropPilot (calculé)
    suggested_retail_price_eur: float   # coût × 3 par défaut
    margin_estimate_fr: float           # net après VAT FR + shipping FR
    margin_pct_fr: float                # marge en %
    verdict: str                         # 'PASS' | 'WATCH' | 'KILL'
    verdict_reason: str                  # courte explication
    
    # Méta
    fetched_at: datetime
    source: str = 'aliexpress'
```

### `ShippingInfo` (dataclass)

```python
@dataclass
class ShippingInfo:
    country_code: str
    cost_eur: float
    delivery_days_min: int
    delivery_days_max: int
    carrier: str                         # 'AliExpress Standard', 'Cainiao', etc.
    is_tracked: bool
```

## 🔐 Variables d'environnement

```bash
# === AliExpress Open Platform ===
AE_APP_KEY=                          # récupéré dans console AE
AE_APP_SECRET=                       # récupéré dans console AE
AE_ACCESS_TOKEN=                     # rempli après OAuth
AE_REFRESH_TOKEN=                    # rempli après OAuth
AE_CALLBACK_URL=https://srv1575867.hstgr.cloud/oauth/aliexpress/callback
AE_DEFAULT_LANGUAGE=FR
AE_DEFAULT_CURRENCY=EUR
AE_TRACKING_ID=default               # requis par le SDK même sans tracking

# === DropPilot Business Rules ===
DP_PRICE_MULTIPLIER=3.0
DP_MIN_MARGIN_PCT=40
DP_VAT_FR=0.20
DP_VAT_BE=0.21
DP_VAT_CH=0.081
DP_VAT_LU=0.17
DP_MIN_ORDERS_PASS=100
DP_MIN_RATING_PASS=4.3
DP_MIN_ORDERS_WATCH=50
DP_MIN_RATING_WATCH=4.0

# === MCP Server ===
MCP_HOST=0.0.0.0
MCP_PORT=8080
LOG_LEVEL=INFO
CACHE_MAX_SIZE=500
CACHE_TTL_SEARCH=3600                # 1h
CACHE_TTL_PRODUCT=21600              # 6h
CACHE_TTL_SHIPPING=86400             # 24h
```

## 🗺️ Roadmap d'implémentation

### Phase 1 — Setup projet (session 1, 30 min)

- Créer la structure de dossiers
- `requirements.txt` + `.env.example` + `.gitignore` + `README.md`
- Initialiser git
- Créer `src/config.py` avec chargement .env typé
- Créer `src/models.py` avec les dataclasses

**Livrable :** squelette qui compile, `python -c "from src.config import load_config; print(load_config())"` fonctionne.

### Phase 2 — OAuth one-shot (session 2, 45 min)

- Script `scripts/ae_oauth.py` avec Flask local
- Documentation `docs/oauth-setup.md` pas à pas
- Execution réelle du script par Hakim sur le VPS
- Tokens récupérés et stockés dans `.env` prod

**Livrable :** `.env` contenant `AE_ACCESS_TOKEN` et `AE_REFRESH_TOKEN` valides.

### Phase 3 — Client AE (session 3, 1h)

- `src/aliexpress_client.py` avec classe `AliExpressClient`
- Méthodes async : `search_products`, `get_product`, `get_shipping`
- Gestion erreurs + retry basique
- Tests unitaires avec mocks (`tests/test_aliexpress_client.py`)

**Livrable :** `pytest tests/test_aliexpress_client.py` passe.

### Phase 4 — Cache + Normalizer (session 4, 1h)

- `src/cache.py` avec `TTLCache` + décorateur `@cached(key_fn, ttl)`
- `src/normalizer.py` avec `normalize_product(raw) -> Product`
- `src/normalizer.py` avec `compute_scores(product, rules) -> Product`
- Tests : `tests/test_cache.py`, `tests/test_normalizer.py`

**Livrable :** pipeline raw AE → Product normalisé + scoré.

### Phase 5 — Serveur MCP (session 5, 1h30)

- `src/server.py` avec FastMCP
- 3 tools P0 exposés : `search_aliexpress_products`, `get_aliexpress_product_details`, `get_shipping_cost`
- Logs structurés JSON (structlog)
- Test manuel via curl ou inspector MCP

**Livrable :** serveur qui démarre sur port 8080, répond aux tool calls.

### Phase 6 — Dockerisation (session 6, 45 min)

- `Dockerfile` multi-stage
- `docker-compose.yml` avec réseau `hermes-network` externe
- Test local Docker sur Mac
- `docs/deploy.md` avec procédure VPS pas à pas

**Livrable :** `docker compose up` fonctionne en local.

### Phase 7 — Déploiement VPS (session 7, 30 min)

- Git push vers repo (GitHub perso de Hakim)
- SSH sur VPS, git clone
- Copie `.env` prod (jamais en git)
- `docker compose up -d`
- Vérification logs + test tool via curl depuis le VPS

**Livrable :** MCP server tourne en prod sur VPS.

### Phase 8 — Managed Agent "aliexpress-scout" (session 8, 30 min)

- Script `scripts/create_aliexpress_scout.py` (API Anthropic)
- System prompt avec règles métier DropPilot (tutoiement, format Telegram-friendly)
- Connexion au MCP server local via `http://aliexpress-mcp:8080/mcp`
- Notation de l'`agent_id` retourné

**Livrable :** agent créé, `agent_id` noté.

### Phase 9 — Intégration Hermes (session 9, 30 min)

- Patch `run_agent.py` sur VPS : ajout du scout dans le registre
- Patch SOUL.md Hermes : ajout instruction "pour sourcing produit → délègue au scout"
- Restart container Hermes

**Livrable :** commandes Telegram de test fonctionnent.

### Phase 10 — Test end-to-end + polissage (session 10, 1h)

- Test réel depuis Telegram : "Trouve-moi 10 tapis de yoga rentables pour FR"
- Ajustement formatage de sortie pour Telegram (markdown, limite 4096 chars)
- Gestion edge cases (pas de résultats, erreur API, token expiré)
- Documentation finale dans README

**Livrable :** workflow complet opérationnel. 🎉

## 🔄 Refresh token OAuth

Les access_tokens AliExpress expirent généralement après 30 jours. À prévoir :

- Cron mensuel sur le VPS qui check la validité du token
- Si expiration < 7 jours : utiliser le refresh_token pour en obtenir un nouveau
- Alerte Telegram à Hakim si le refresh échoue (nécessitera un OAuth manuel)

**Implémentation** : à ajouter en post-MVP dans `src/token_refresher.py` avec un scheduler (APScheduler).

## 📊 Observabilité minimale

Chaque tool call loggé en JSON avec :
- `timestamp`
- `tool_name`
- `params` (sans secrets)
- `cache_hit: bool`
- `duration_ms`
- `result_count`
- `error` (si applicable)

Les logs Docker sont récupérables via :
```bash
docker logs aliexpress-mcp --tail 100 -f
```

Pour une v2 : exporter vers Grafana Loki ou similar si Hakim veut un vrai dashboard.

## 🧪 Stratégie de tests

### Tests unitaires (CI-ready)
- Mock complet de `AliExpressClient` dans tous les tests
- Fixtures pytest avec données AE réalistes (fichiers JSON dans `tests/fixtures/`)
- Couverture cible : 80% minimum sur `normalizer.py` et `server.py`

### Tests d'intégration (manuels)
- Script `scripts/integration_test.py` qui fait 5 vrais appels à AE
- Ne tourne que quand Hakim le lance explicitement (consomme quota API)
- Génère un rapport dans `tests/reports/`

## 🚨 Gestion des erreurs

| Erreur | Action |
|---|---|
| Token expiré | Log warn, tenter refresh, sinon erreur explicite + alerte |
| Rate limit AE | Backoff exponentiel, 3 retries max |
| Produit inexistant | Retourner `None` proprement, pas d'exception |
| Shipping non dispo pour pays | Retourner `None` sur le champ shipping, continuer |
| Timeout réseau | 3 retries avec backoff, puis erreur |
| Résultat vide | Retourner liste vide avec log info |

## 📋 Shape des réponses IOP (référence)

Captures live validées **2026-04-21** via `scripts/smoke_test.py`. Les trois fichiers fixture sont commités dans `tests/fixtures/real_*.json` comme référence permanente pour le normalizer Phase 4 et comme tests de régression contre toute dérive de shape.

### `aliexpress.ds.text.search`

- **Fixture** : `tests/fixtures/real_text_search_response.json`
- **Path d'extraction** : `envelope["data"]["products"]["selection_search_product"]` → `list[dict]`
- **Success marker** : `envelope["code"] == "00"` (**string**, pas `"0"` ni `0`)

Champs clés par item :

| Champ | Type | Exemple | Usage Phase 4 |
|---|---|---|---|
| `itemId` | str (16 digits) | `"1005006361450153"` | identité produit, stable |
| `title` | str | `"Tapis de litière pour chat..."` | titre FR |
| `targetSalePrice` | str (dot-decimal) | `"3.29"` | prix EUR machine-readable |
| `salePriceFormat` | str | `"3,29€"` | prix EUR localisé affichage |
| `score` | str (0-5 scale) | `"4.5"` | rating |
| `evaluateRate` | str (%) | `"89.2"` | % reviews positives |
| `orders` | str (format humain) | `"5,000+"` | volume ventes (parser avec `_parse_order_count`) |
| `itemMainPic` | str URL | `"https://ae01.alicdn.com/kf/..."` | image principale |
| `itemUrl` | str (relative) | `"//www.aliexpress.com/item/...html?skuId=..."` | URL produit |
| `originMinPrice` | str (JSON embedded) | `"{\"cent\":329,...}"` | payload formatage prix |
| `discount` | str | `"0%"` | remise affichée |

⚠️ **Piège** : `targetSalePrice` reflète le **SKU le moins cher** du produit. Pour le vrai prix facturé sur un SKU donné, passer par `product.get`.

### `aliexpress.ds.product.get`

- **Fixture** : `tests/fixtures/real_product_get_response.json`
- **Path d'extraction** : `envelope["result"]` → `dict` structuré
- **Success marker** : `envelope["rsp_code"] == 200` (**int**, différent de text.search)

Sous-sections :

| Section | Type | Contenu |
|---|---|---|
| `ae_item_base_info_dto` | dict | `product_id`, `subject`, `avg_evaluation_rating`, `evaluation_count`, `sales_count`, `product_status_type` (`"onSelling"`), `category_id`, `currency_code` (**CNY** au niveau base, EUR sur les SKUs) |
| `ae_item_sku_info_dtos.ae_item_sku_info_d_t_o` | list[dict] | N variantes avec `sku_id`, `offer_sale_price` (EUR dropshipper), `sku_price` (EUR retail), `sku_available_stock`, `price_include_tax: true`, `ae_sku_property_dtos` |
| `ae_store_info` | dict | `store_id`, `store_name`, `shipping_speed_rating`, `communication_rating`, `item_as_described_rating`, `store_country_code` |
| `logistics_info_dto` | dict | `delivery_time` (int jours), `ship_to_country` |
| `package_info_dto` | dict | `package_width/height/length` (cm, int), `gross_weight` (str kg) |
| `ae_multimedia_info_dto` | dict | `image_urls` (str, URLs séparées par `;`) |
| `ae_item_properties.ae_item_property` | list[dict] | attributs produit ; inclut flag `{"attr_name": "Choice", "attr_value": "yes"}` ← indicateur AliExpress Choice (logistique premium) |
| `manufacturer_info` | dict | adresse + email + téléphone fabricant CN |

⚠️ **Piège SKU** — chaque SKU a **trois champs similaires**, non interchangeables :

```
"id":       "14:29#Bear;183:200007741"   ← alias de sku_attr, NE PAS utiliser
"sku_attr": "14:29#Bear;183:200007741"   ← combinaison d'attributs pour affichage
"sku_id":   "12000044126059467"          ← identifiant numérique AE, POUR freight.query
```

Utiliser `id` à la place de `sku_id` déclenche silencieusement `code: 501, msg: "DELIVERY_INFO_EMPTY"` côté freight. Test de régression : `tests/test_aliexpress_client_helpers.py::test_first_sku_id_is_numeric_not_sku_attr`.

### `aliexpress.ds.freight.query`

- **Fixture (cas erreur)** : `tests/fixtures/real_freight_query_response.json`
- **Path d'extraction** : `envelope["result"]` → `dict` à inspecter via `result["success"]`

**Cas succès** (observé live sur produit livrable FR) :

```json
{
  "result": {
    "success": true,
    "aeop_freight_calculate_result_for_buyer_dtolist": [
      {
        "service_name": "Cainiao Fulfillment",
        "estimated_delivery_time": "7-9",
        "tracking_available": "true",
        "freight": {"amount": "1.99", "cent": 199, "currency_code": "EUR"}
      }
    ]
  }
}
```

**Cas erreur métier** (HTTP 200 + `result.success: false`) :

```json
{
  "result": {
    "success": false,
    "code": 501,
    "msg": "DELIVERY_INFO_EMPTY"
  }
}
```

⚠️ AE renvoie HTTP **200** même en cas d'erreur métier (SKU invalide, produit non livrable sur le pays, etc.). Le normalizer Phase 4 **doit** inspecter `result["success"]` avant d'accéder à la liste de méthodes de shipping.

---

## 📝 Journal d'avancement

### 2026-04-18 — Kick-off
- App AliExpress créée (type Drop Shipping), approuvée immédiatement
- App Key + App Secret récupérés et stockés
- IP VPS identifiée : 148.230.118.152
- Callback URL définie : `https://srv1575867.hstgr.cloud/oauth/aliexpress/callback`
- Plan d'intégration rédigé
- Décision : interface Telegram uniquement, pas de dashboard web
- Décision : pas de Redis pour le MVP, cache mémoire suffit

### 2026-04-18 — Phase 1 + Phase 2

**Phase 1 — Setup projet :**
- Structure de dossiers créée (`src/`, `scripts/`, `tests/`, `docs/`)
- `requirements.txt` avec 8 dépendances (fastmcp, python-aliexpress-api, cachetools, dotenv, flask, structlog, pytest, pytest-asyncio)
- `.env.example` avec 30 variables (AE + DropPilot rules + MCP server)
- `.gitignore` Python standard
- `src/config.py` : 3 dataclasses frozen + `load_config()` typé
- `src/models.py` : enum `Verdict` (PASS/WATCH/KILL) + dataclasses `ShippingInfo` et `Product`
- Squelette compilé et validé

**Phase 2 — OAuth one-shot :**
- `scripts/ae_oauth.py` : flow OAuth complet avec Flask (port 3000)
  - Signature HMAC-SHA256 conforme à l'OP API AliExpress (vérifié contre ae_sdk, ae-api, doc Lazada/IOP)
  - `sign_method = "sha256"` confirmé correct pour la plateforme IOP
  - `api-sg.aliexpress.com` confirmé correct pour les comptes EU (pas d'endpoint régional EU)
  - Écriture automatique des tokens dans `.env`
- `docs/oauth-setup.md` : guide 7 étapes (callback HTTP temporaire sur VPS, ouverture/fermeture port 3000, remise HTTPS après OAuth)

### 2026-04-18 — Phase 3 : Client AliExpress

- `src/aliexpress_client.py` : façade async sur `python-aliexpress-api`
  - 3 méthodes async (wrapping `asyncio.to_thread` car le SDK est sync) :
    - `search_products(query, max_results, min_orders, min_rating, max_price_eur, sort_by, target_country)` → `get_products()` du SDK
    - `get_product_details(product_id, include_shipping)` → `get_products_details()` du SDK
    - `get_shipping_cost(product_id, country_code, quantity)` → **stub `NotImplementedError`** (le SDK Affiliate ne couvre pas le freight)
  - `SORT_MAP` : mapping `"orders"|"price_asc"|"price_desc"` → `models.SortBy`
  - `_filter_products` : filtres post-fetch pour `min_orders` / `min_rating` (l'API AE ne les supporte pas en paramètres)
  - Conversion `max_price_eur` → cents (lowest currency denomination)
  - Logs structurés `structlog` à chaque étape (start / done / error / not_implemented)
  - Hiérarchie d'erreurs : `AliExpressClientError` > `ProductsNotFound`, `UpstreamError`
  - SDK injectable via le constructeur (`AliExpressClient(config, sdk=...)`) → testable sans réseau

- **Décision freight (`get_shipping_cost`) reportée à la Phase 4 :**
  - `python-aliexpress-api` n'expose que les endpoints Affiliate, pas Drop Shipping
  - L'endpoint `aliexpress.ds.freight.query` nécessite un appel HTTP signé brut sur la gateway IOP
  - Implémentation cible : `httpx` + signature **HMAC-SHA256** (`sign_method=sha256`) avec l'OAuth `access_token` en system parameter — même schéma que Phase 2 OAuth (déjà validé)
  - Le docstring du stub mentionne explicitement HMAC-SHA256 pour éviter la confusion avec MD5

- **Décision over-fetch :** comportement simple, un seul appel API, peut renvoyer < `max_results` après filtrage `min_orders` / `min_rating`. TODO commenté dans le code pour pagination en Phase 4.

- `tests/conftest.py` : fixtures pytest (`ae_config`, `mock_sdk`, `client`, `fake_search_products`, `fake_product_detail`)
- `tests/fixtures/products_search.json` : 4 produits yoga avec ratings et volumes variés (1284 / 47 / 312 / 802 ventes ; 92.5% / 88% / 78.4% / 95.2%)
- `tests/fixtures/product_details.json` : 1 produit détaillé (yoga TPE 6mm)
- `tests/test_aliexpress_client.py` : **17 tests** couvrant :
  - Cas nominal `search_products` (params, sort mapping paramétré, page_size cap à 50, conversion cents)
  - Filtres `min_orders` / `min_rating` post-fetch
  - Cas erreur `search_products` (`ProductsNotFoudException` → liste vide, `ApiRequestException` → `UpstreamError`)
  - Cas nominal et erreurs `get_product_details` (not found → `ProductsNotFound`, upstream → `UpstreamError`)
  - Stub `get_shipping_cost` → `NotImplementedError`
- **17/17 tests verts en 0.04s, aucun appel réseau réel à AE**

### 2026-04-20 → 2026-04-21 — Phase 3bis : refactor HTTP direct (Drop Shipping API)

**Contexte** : le premier smoke test live de la Phase 3 a révélé une incompatibilité majeure. Le SDK `python-aliexpress-api` (de `sergioteula`) n'expose que les endpoints **Affiliate** (`aliexpress.affiliate.product.query`), alors que notre app AE est de type **Drop Shipping** (permission Affiliate refusée à l'enregistrement). Résultat : `InsufficientPermission` sur chaque appel.

**Décision structurante** : abandon complet du SDK tiers, réécriture du client avec des appels HTTP directs à la gateway IOP (`https://api-sg.aliexpress.com/sync`), en réutilisant la logique de signature HMAC-SHA256 déjà validée en Phase 2 pour OAuth.

**Travail réalisé (6 commits) :**

1. **`src/iop_signature.py`** : module partagé avec 3 fonctions pures (`sign_business_request`, `sign_system_request`, `build_business_system_params`). 20 tests unitaires avec vecteurs golden HMAC-SHA256 + test de parité contre l'ancienne implémentation inline de `ae_oauth.py`.
2. **Refactor `scripts/ae_oauth.py`** pour consommer `iop_signature`. Parité bit-à-bit vérifiée sur payload OAuth réaliste, zéro changement de comportement observable.
3. **Réécriture complète `src/aliexpress_client.py`** : façade async sur `httpx.AsyncClient` avec `_call_iop(method, business_params)` centralisé (sign + POST form-urlencoded + parse JSON + classification erreur). Hiérarchie d'exceptions typées (`IOPAuthError`, `IOPRateLimitError`, `IOPPermissionError`, `IOPUpstreamError`, `IOPNetworkError`) avec wrap du `request_id` AE pour debug console. 3 endpoints exposés : `search_products` (text.search), `get_product_details` (product.get), `get_shipping_cost` (freight.query).
4. **Smoke test instrumenté** (`scripts/smoke_test.py`) : `TeeingAsyncClient` qui wrap `httpx.AsyncClient` pour dumper la réponse brute **avant** parsing/classification → on ne perd jamais la payload même en cas d'exception en aval. Chain des 3 endpoints avec gestion d'erreur fine par étape.
5. **Découverte de la shape réelle** au 1er run live réussi :
   - text.search : items sous `data.products.selection_search_product` (pas de clé `result`), success code `"00"` (pas `"0"` ni `200`), items exposent `itemId` / `targetSalePrice` / `score` / `orders` (format `"5,000+"`).
   - product.get : 6 SKUs par produit, 3 champs similaires `id` / `sku_attr` / `sku_id` dont seul `sku_id` (numérique) est valide pour freight.
   - freight.query : retourne HTTP 200 avec `result.success: false` en cas d'erreur métier → pas une exception, un cas à normaliser.
6. **3 fixtures réelles commitées** dans `tests/fixtures/real_*.json` comme référence permanente pour le normalizer Phase 4 et guardrails de régression contre une éventuelle dérive de shape.

**Bugs corrigés en cascade** (chacun avec son test de régression) :
- Extraction : path `data.products.selection_search_product` remplace la stratégie "guessing" initiale → `test_search_products_extracts_from_real_fixture`
- Success codes : `_SUCCESS_CODES = {"0", "00", "200"}` + comparaison sur `str(value)` accepte int `0` et `200`
- SKU id : le smoke test extrayait `id` (= `sku_attr`) au lieu de `sku_id`, déclenchant `code: 501, msg: "DELIVERY_INFO_EMPTY"` silencieux → `test_first_sku_id_is_numeric_not_sku_attr`

**État final Phase 3bis** :
- **119 tests** passent (unit + régression sur vraies données) en 0.10 s
- **Smoke test live 3/3 endpoints validés** : text.search (3 items FR/EUR) → product.get (6 SKUs, rating 4.6, store_info, logistics FR, flag "Choice") → freight.query (Cainiao Fulfillment, 1.99€, 7-9 jours, tracké)
- Classification d'erreurs tolérante aux variantes de formulation AE (`InvalidSession`, `SessionExpired`, `APP_CALL_LIMITED`, `Forbidden`, etc.) via markers case-insensitive + codes numériques stables
- Documentation embarquée : docstring `get_shipping_cost` contient le workflow pseudo-code complet + warning sur le piège `id` vs `sku_id`

**Décisions reportées en Phase 4** :
- Pagination pour garantir `max_results` après filtrage client-side (TODO commenté dans `search_products`)
- `ship_to_country` paramétrable (actuellement hardcodé `"FR"` dans `get_product_details`)
- Mapping `sort_by` : `"orders,desc"` validé en live, alternatives `"orders_desc"` / `"LAST_VOLUME,desc"` documentées comme fallback manuel si AE change
- Normalizer : conversion `dict[str, Any]` brut → `Product` dataclass avec scoring DropPilot (PASS / WATCH / KILL)

### 2026-04-21 — Phase 4 : Normalizer DropPilot + filtres high-ticket

**Objectif** : transformer les payloads bruts IOP (3 endpoints) en objets `DropPilotProduct` standardisés, immuables, qualitativement filtrés. Le scout agent (Phase 8) récupère ensuite les produits "passe 1" et applique le scoring marge + concurrence.

**Décisions structurantes** :

1. **Client "dumb transport" / normalizer "smart"** : la Phase 3bis reste un wrapper HTTP pur (pas de sémantique métier). La Phase 4 fait TOUT le travail : extraction, enrichissement, filtrage. Ce découpage permet de faire évoluer les règles DropPilot sans toucher au client.

2. **Filtres "passe 1" éliminatoires** : si un filtre échoue → KILL silencieux (log DEBUG + product non retourné). Pas de verdict `WATCH` ni de score à ce niveau — c'est binaire, pour garder le signal clean vers le scout. 11 filtres :

| Filtre | Seuil | Motivation |
|---|---|---|
| `rating_min` | ≥ 4.5 | Qualité produit perçue |
| `orders_min` | ≥ 300 | Validation marché |
| `store_shipping_rating_min` | ≥ 4.5 | Store OK pour livraison |
| `store_communication_rating_min` | ≥ 4.5 | Store OK pour SAV éventuel |
| `store_as_described_rating_min` | ≥ 4.5 | Store fiable sur les fiches |
| `min_stock_ref_sku` | ≥ 1 | SKU de réf doit exister |
| **`offer_sale_price_min_eur`** | **≥ 25.0 €** | **High-ticket : coût dropshipper < 25 € ne peut pas multiplier crédiblement vers 200-300 €** |
| `max_weight_kg` | ≤ 3.0 | Shipping abordable |
| `max_length_cm` | ≤ 60 | Plus grande dim raisonnable |
| `shipping_fr_available` | True | AE peut livrer en France |
| `max_delivery_days` | ≤ 15 | Délai acceptable pour conversion |

3. **Sélection SKU de référence** : le moins cher avec `sku_available_stock ≥ 1`. Flag `sku_ref_is_cheapest_absolute` ajouté pour signaler quand on a fallback sur un SKU plus cher parce que le moins cher absolu est OOS — signal utile au scout : le "prix d'appel" affiché par text.search est alors trompeur.

4. **Concurrency** : `asyncio.Semaphore(5)` autour du pipeline per-item. `product.get` et `freight.query` restent séquentiels par produit (le second a besoin du `sku_id` du premier), mais 5 produits en parallèle. Prudent pour éviter rate limit AE, tuning à revoir en charge réelle.

5. **Fixture freight success** : la capture live `real_freight_query_response.json` est un cas d'**erreur** (`DELIVERY_INFO_EMPTY` issue du premier smoke test buggé). Le chain corrigé a bien renvoyé un Cainiao 1.99€ en live, mais ce dump n'est pas committé. Les tests happy path utilisent des fixtures synthétiques (`FREIGHT_SUCCESS_CN`, `FREIGHT_SUCCESS_ES`, `FREIGHT_SUCCESS_SLOW`) avec shape inférée — à remplacer par une vraie capture si smoke test ultérieur.

**Travail réalisé** :
- `src/models.py` : 6 dataclasses `frozen=True` (`SkuRef`, `StoreInfo`, `ShippingInfo`, `PackageInfo`, `DropPilotProduct`, + `Verdict` conservé pour future use). Remplace l'ancien `Product` minimaliste de Phase 1.
- `src/normalizer.py` (420 lignes, nouveau) : pipeline async complet, 3 nouveaux parsers (`_parse_weight_kg`, `_parse_int_safe`, `_normalize_url`, `_split_images`, `_parse_delivery_range`), sélection SKU, parser freight défensif avec fallbacks de noms de champs (FIXME pinner post-live-capture), builders par sous-section, filtres groupés en helpers `_apply_*_filters` qui raise `FilterRejection`.
- `tests/test_normalizer.py` (17 tests async) : happy path sur fixture réelle bumpée high-price + 10 tests KILL (1 par filtre) + flag cheapest absolute + concurrency sanity.
- `tests/test_normalizer_helpers.py` (45 tests sync) : 6 parsers paramétrés × edge cases, sélection SKU edge cases, freight parser fallbacks, `_is_cheapest_absolute` logique (min, ties, zero-priced, OOS fallback).

**Validation fonctionnelle** :
- **Scenario A** — tapis de sol absorbant (`product_id=1005008177221739`, SKU le moins cher 5.09 €) → **KILL** sur `offer_sale_price 5.09€ < 25.0€ (high-ticket floor)`. Conforme à la stratégie.
- **Scenario B** — cave à vin thermoélectrique 12 bouteilles synthétique (SKU le moins cher 38.50 €, 2.8 kg, 45×35×55 cm, Choice yes, rating 4.7, store 4.8/4.7/4.6, shipping Cainiao 12.50 € 8-12j) → **PASS** sur les 11 filtres. Coût total 51 € → vente cible 255 €, marge brute ~80%.

**Tests** : **191/191 verts** en 0.19 s (119 précédents + 10 nouveaux tests price filter/flag + 45 helpers + 17 async).

**Différé (non Phase 4)** :
- Cache TTL `cachetools` : retiré du scope (YAGNI — on ajoutera si le smoke test révèle un besoin de cache en amont).
- Tuning `CONCURRENCY_LIMIT` : à ajuster quand on verra le comportement sous charge réelle.

### 2026-04-21 — Phase 5 : Serveur MCP FastMCP

**Objectif** : exposer le client + normalizer via un serveur MCP que le scout agent (Phase 8) appellera en HTTP.

**Décisions structurantes** :

1. **FastMCP v3.2.4** choisi (dernière release stable au 2026-04-14). API : décorateur `@mcp.tool` (sans parens, changement vs v2), `mcp.run(transport="http", host=..., port=...)`. Serveur Streamable HTTP sur `/mcp` par défaut — **pas `/`**, point de vigilance critique pour la Phase 8.

2. **4 tools exposés** :
   - **`search_and_normalize`** (primary, ~95% des appels scout) : pipeline complet text.search → product.get → freight.query → filtres passe-1, retourne des `DropPilotProduct` sérialisés en dict JSON-ready (17 clés top-level, dataclasses imbriquées recursivement).
   - **`search_products_raw`** / **`get_product_detail`** / **`get_shipping_cost`** : passthroughs bruts sur les 3 endpoints IOP, pour debug et cas edge.
   - Chaque tool wrappe `IOPError` → `RuntimeError` formaté `"IOPPermissionError | msg | request_id=..."` pour que le scout lise un message actionnable.

3. **Client singleton lazy** : `AliExpressClient` créé au 1er tool call, fermé à l'arrêt serveur. Un seul pool de connexions httpx = moins de TLS handshakes. Test hooks `set_client_for_testing` / `reset_for_testing` pour injection mock sans toucher au `.env`.

4. **Sérialisation** (`src/serializers.py`) : `dataclasses.asdict` recursif + conversion `datetime` → ISO 8601 string. Pas de sérialiseur custom sophistiqué — volontairement minimaliste, les noms de champs sont préservés 1:1 pour que le prompt scout matche le schema.

5. **Logs tool-call** structurés (structlog) : `mcp.tool.call` avec `tool`, `status` (start / success / error), `duration_ms`, `result_count` ou `error`. Pas de paramètres sensibles loggés (pas d'access_token ni d'app_secret).

**Tests (9 nouveaux, 200 total verts en 0.66 s)** :
- Discovery : `list_tools()` retourne exactement les 4 tools attendus
- Happy path `search_and_normalize` : toutes les clés du `DropPilotProduct` présentes, `sku_id` numérique, `fetched_at` ISO string
- High-ticket propagation : un produit à 5.09€ → tool retourne `[]`
- Erreurs IOP : `IOPAuthError` / `IOPPermissionError` remontent comme erreur MCP propre (pas de crash serveur)
- Passthroughs bruts : items/details/freight transmis tels quels
- Business error freight (`code: 501, DELIVERY_INFO_EMPTY`) passé tel quel — le tool n'interprète pas, c'est le scout qui décide skip/retry

**Dockerisation** :
- `Dockerfile` : `python:3.11-slim` (FastMCP requiert ≥ 3.10)
- `docker-compose.yml` : port `127.0.0.1:8080:8080` (jamais exposé internet), réseau `hermes-network` externe partagé avec `hermes-agent-hjft_default`
- `.env` injecté via `env_file` (pas copié dans l'image)
- Pas de healthcheck encore — à pinner Phase 6 si besoin

**Ménage venv** : suppression de l'ancien `.venv` Python 3.9 (héritage des phases 1-4 avant que FastMCP ne force le saut ≥ 3.10). Seul `.venv-311` reste. `.gitignore` étendu en glob `.venv*/` pour couvrir les deux.

**Validation** : commandes de test documentées :
- Auto : `.venv-311/bin/python -m pytest tests/`
- Serveur local : `.venv-311/bin/python -m src.server` → Streamable HTTP `0.0.0.0:8080/mcp`
- Client MCP : `fastmcp.Client("http://127.0.0.1:8080/mcp")` + `call_tool("search_and_normalize", {...})`
- Inspector UI : `npx @modelcontextprotocol/inspector http://127.0.0.1:8080/mcp`

**Différé** :
- Auth MCP (réseau Docker interne uniquement pour l'instant — si plus tard public, ajouter middleware Bearer token)
- Healthcheck Docker (attendre de pinner l'endpoint santé MCP standard)
- Cache TTL `cachetools` (toujours YAGNI — on ajoute si smoke test révèle latence)

### [À compléter au fur et à mesure des sessions Claude Code]

---

## 💡 Conseils pour le pilotage Claude Code

1. **Une session = une phase** (du roadmap ci-dessus). Ne pas essayer de tout faire d'un coup.
2. **Toujours demander le diff** avant commit, valider visuellement.
3. **Lancer les tests** après chaque modification non triviale.
4. **Mettre à jour le journal** à la fin de chaque session.
5. **Si Claude Code hallucine un endpoint AE** → le rediriger vers la doc officielle ou couper court.
6. **Ne jamais laisser Claude Code exécuter l'OAuth seul** — c'est une action manuelle par design.
7. **Utiliser une branche `feature/xxx` par phase**, merger sur `dev` après validation, merger sur `main` uniquement pour les releases stables.
