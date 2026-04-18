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
