# AliExpress MCP Server — Contexte projet

## 🎯 Objectif

Serveur MCP Python qui expose la recherche de produits AliExpress via l'API Dropshipping officielle, destiné à être consommé par l'agent **Hermes** (container Docker sur VPS Hostinger) et un **Managed Agent Anthropic "aliexpress-scout"**.

## 👤 Contexte utilisateur (Hakim)

**Profil** : Digital marketing professional français, 32 ans, basé en région parisienne. 6 ans d'expérience chez Saint-Gobain comme Chef de Projet Digital, puis Directeur des Opérations chez Playse (football jeunes) et Responsable Marketing chez Looking For Soccer (LFS, plateforme sport-études football).

**Expertise technique** :
- SEO/GEO multilingue (FR/EN/ES)
- Google Ads, Meta Ads
- HubSpot CRM et automation
- WordPress / WooCommerce
- CRO
- Applied AI pour workflows marketing
- Python et TypeScript (niveau intermédiaire, apprend via Claude Code)

**Stack préférée** : Python pour scripts et agents, React pour interfaces. Utilise Claude Code quotidiennement.

**Style de travail** :
- Tutoiement
- Réponses directes et concises, pas de réexplication de contexte
- Préfère les solutions concrètes aux listes de possibilités vagues
- Priorise le MVP et l'exécution rapide
- Aime les architectures systématiques et automatisées

## 🏢 Contexte business global

Hakim exploite plusieurs activités en parallèle via sa **SASU** :

### DropPilot — activité principale actuelle
- E-commerce dropshipping via Shopify
- Acquisition 100% Google Ads (pas de Meta, pas d'organique)
- Marchés : **France, Belgique, Suisse, Luxembourg**
- Format : boutique mono-niche multi-produits
- Objectif CA : premier palier mi-2026
- Orchestration via un écosystème d'agents IA (Hermes + Managed Agents Anthropic)

### Écosystème d'agents DropPilot
Hermes = chef d'orchestre. Managed Agents Anthropic spécialisés :
- `product-writer` — fiches produit
- `ads-architect` — campagnes Google Ads
- `cro-optimizer` — optimisation conversion
- `ux-auditor` — audit UX boutique
- `gmc-checker` — conformité Google Merchant Center
- `image-maker` — visuels produit (Gemini / Nano Banana)
- + 2 autres agents bonus
- **(nouveau) `aliexpress-scout`** — sourcing produits AliExpress ← objet de ce projet

### Autre activité : LFS (Looking For Soccer)
Continue en freelance/consulting partiel. Pas concerné par ce projet mais peut influencer la disponibilité de Hakim.

## 🖥️ Infrastructure existante

### VPS Hostinger
- Hostname : `srv1575867.hstgr.cloud`
- IPv4 publique : `148.230.118.152`
- Gestionnaire Docker Hostinger utilisé pour orchestration
- Containers actifs :
  - `hermes-agent-hjft` (Hermes Agent, Claude Sonnet 4.6, Telegram, Camofox, mémoire persistante)
  - `traefik` (reverse proxy SSL)
- Réseau Docker partagé : `hermes-network`

### Stack Hermes existant
- Script orchestrateur : `run_agent.py` (charge le registre d'agents)
- Registre : fichier Python avec `agent_id` et `version` de chaque Managed Agent
- SOUL.md DropPilot défini (personnalité de Hermes)
- Interface utilisateur : **Telegram uniquement** (pas de dashboard web, décision finale prise le 2026-04-18)
- Clé API Anthropic (à régénérer périodiquement)
- Clé Tavily pour recherche web

### AliExpress Open Platform (nouveau)
- App créée : "Hermes DropPilot"
- Type : **Drop Shipping** (Affiliate API refusée par AE, conditions non remplies)
- Statut : **Active** (approuvée immédiatement, pas d'audit requis)
- `App Key` + `App Secret` récupérés (stockés en .env)
- Callback URL : `https://srv1575867.hstgr.cloud/oauth/aliexpress/callback`
- IP Whitelist : `148.230.118.152`

## 🎯 Use case concret

Depuis Telegram, l'utilisateur (Hakim) envoie à Hermes un message type :

> "Trouve-moi 10 tapis de yoga antidérapants rentables pour le marché FR"

Workflow attendu :
1. Hermes comprend la demande de sourcing produit
2. Hermes délègue à `aliexpress-scout` via `run_agent.py`
3. Le scout appelle le MCP server AliExpress
4. Le MCP server interroge l'API AE Dropshipping (search + freight)
5. Le MCP normalise et score les résultats selon les règles DropPilot
6. Le scout renvoie une shortlist à Hermes
7. Hermes formate pour Telegram et envoie à Hakim

## 📦 Stack technique de ce projet

- **Python 3.11+ requis** (FastMCP v3 exige ≥ 3.10 ; le Dockerfile utilise `python:3.11-slim`). Créer le venv local avec `python3.11 -m venv .venv-311` (ou `uv venv --python 3.11 .venv-311`).
- **FastMCP ≥ 3.0** (SDK MCP officiel) — https://github.com/jlowin/fastmcp
- **httpx ≥ 0.27** — appels HTTP directs à la gateway IOP AE (le SDK `python-aliexpress-api` n'expose que l'API Affiliate, incompatible avec notre app Drop Shipping — cf. Phase 3bis)
- **cachetools** — cache mémoire TTL (pas besoin de Redis pour ce scope)
- **python-dotenv** — env management
- **Flask** — uniquement pour le script OAuth one-shot
- **pytest + pytest-asyncio** — tests avec mocks

## 🔌 Serveur MCP — endpoints

**URL interne Docker (réseau `hermes-network`)** : `http://aliexpress-mcp:8080/mcp`

**URL locale (dev sur Mac)** : `http://127.0.0.1:8080/mcp`

⚠️ **Important pour la Phase 8 (scout agent)** : le path MCP est `/mcp`, **pas `/`**. FastMCP v3 sert le Streamable HTTP sur ce chemin par défaut. Taper `http://aliexpress-mcp:8080/` renvoie 404.

**Tools exposés** (fichier `src/server.py`) :

| Tool | Signature courte | Usage |
|---|---|---|
| `search_and_normalize` | `(query, max_results=20, target_country="FR") -> list[dict]` | **Tool principal.** Pipeline complet : text.search → product.get → freight.query → filtres passe-1 (high-ticket). Renvoie des `DropPilotProduct` sérialisés. |
| `search_products_raw` | `(query, max_results=20, target_country="FR", sort_by="orders") -> list[dict]` | Passthrough brut `aliexpress.ds.text.search`. Debug / investigation. |
| `get_product_detail` | `(product_id) -> dict` | Passthrough brut `aliexpress.ds.product.get`. Deep-dive produit unique. |
| `get_shipping_cost` | `(product_id, sku_id, country_code="FR", quantity=1) -> dict` | Passthrough brut `aliexpress.ds.freight.query`. **`sku_id` doit être le champ numérique** (pas `id` ni `sku_attr`). |

**Lancement local (dev)** : `.venv-311/bin/python -m src.server`  
**Lancement prod (VPS)** : `docker compose up -d` (cf. `docker-compose.yml`, port 8080 bindé sur `127.0.0.1` uniquement, réseau `hermes-network` externe partagé avec `hermes-agent-hjft`).

## 🚀 Déploiement cible

- Container Docker sur le VPS Hostinger
- Réseau `hermes-network` (partagé avec `hermes-agent-hjft`)
- Port 8080 bindé sur **127.0.0.1:8080 uniquement** (jamais exposé internet)
- Communication inter-containers via nom DNS Docker (`aliexpress-mcp:8080`)
- Logs JSON structurés vers stdout (Docker gère la rotation)

## 📐 Conventions de code

- Python async/await partout où pertinent
- Typage strict : `mypy --strict` doit passer, pas de `Any` sauf justifié en commentaire
- Validation des schémas avec `@dataclass` (pas de Pydantic pour rester léger)
- Logs structurés JSON via `structlog` pour chaque tool call
- Tests systématiques avec mocks pour les appels AE (jamais de vrais appels en CI)
- Commits conventionnels : `feat:`, `fix:`, `docs:`, `refactor:`, `test:`
- Branches : `main` (prod), `dev` (intégration), `feature/xxx` (travail en cours)

## 💼 Règles métier DropPilot

### Marchés et fiscalité
- **Marchés cibles** : France, Belgique, Suisse, Luxembourg
- **VAT appliquée** :
  - FR : 20%
  - BE : 21%
  - CH : 8.1%
  - LU : 17%

### Pricing
- **Multiplicateur par défaut** : prix vente = coût AE × 3
- **Marge minimale cible** : 40% net après VAT et shipping
- **Devise de référence** : EUR (conversion automatique depuis CNY/USD via API AE)

### Scoring des produits
| Verdict | Conditions |
|---|---|
| **PASS** | Marge ≥ 40% + Rating ≥ 4.3 + Orders ≥ 100 |
| **WATCH** | Un critère entre 25-40% / 4.0-4.3 / 50-100 |
| **KILL** | En dessous de tous ces seuils |

### Critères qualité fournisseur (bonus)
- Rating fournisseur ≥ 90% positif (si dispo)
- Délai de livraison annoncé ≤ 15 jours
- Existence de shipping AliExpress Standard ou Cainiao (pas que ePacket)

## 📂 Structure du projet

```
aliexpress-mcp-server/
├── CLAUDE.md                       # ce fichier
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── src/
│   ├── __init__.py
│   ├── server.py                   # serveur MCP FastMCP (4 tools, cf. section "Serveur MCP — endpoints")
│   ├── aliexpress_client.py        # client httpx async sur la gateway IOP (Drop Shipping)
│   ├── iop_signature.py            # signature HMAC-SHA256 partagée (business + system endpoints)
│   ├── serializers.py              # dataclass → dict JSON-ready pour MCP transport
│   ├── normalizer.py               # AE raw → Product + scoring
│   ├── cache.py                    # TTLCache avec décorateur
│   ├── models.py                   # dataclasses Product, Verdict, ShippingInfo
│   └── config.py                   # chargement .env typé
├── scripts/
│   ├── ae_oauth.py                 # flow OAuth one-shot (Flask local)
│   └── create_aliexpress_scout.py  # création du Managed Agent via API Anthropic
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_normalizer.py
│   ├── test_cache.py
│   └── test_server.py
└── docs/
    ├── aliexpress-integration.md   # plan détaillé
    ├── deploy.md                   # procédure déploiement VPS
    └── oauth-setup.md              # guide OAuth pas à pas
```

## ✅ Statut d'avancement

- [x] App AliExpress créée et approuvée (Drop Shipping permission active)
- [x] App Key + App Secret récupérés et stockés en sécurité
- [x] IP VPS ajoutée à la whitelist AE (148.230.118.152)
- [x] Callback URL définie : `https://srv1575867.hstgr.cloud/oauth/aliexpress/callback`
- [x] Structure projet créée
- [x] Flow OAuth réalisé → access_token + refresh_token obtenus
- [ ] MCP server développé (client ✅, cache / normalizer / server en Phase 4-5)
- [x] Tests unitaires (119 tests, client + signature + helpers)
- [x] Client Drop Shipping validé en live (3 endpoints : text.search, product.get, freight.query)
- [ ] Dockerisation
- [ ] Déploiement VPS
- [ ] Managed Agent `aliexpress-scout` créé via API Anthropic
- [ ] `run_agent.py` mis à jour avec le scout
- [ ] SOUL.md Hermes mis à jour pour déléguer au scout
- [ ] Test end-to-end depuis Telegram

## 🔒 À ne jamais commit

- `.env` (App Secret, tokens OAuth, clés API Anthropic)
- Données produits scrapées
- Logs contenant des creds
- `docker-compose.override.yml` avec creds locales

## 🎯 Philosophie de développement

- **MVP d'abord** : faire tourner un flow end-to-end simple avant d'optimiser
- **Une étape = un commit** : chaque ajout significatif déclenche un commit isolé
- **Pas d'optimisation prématurée** : cache simple en mémoire, pas de Redis tant que pas nécessaire
- **Tests dès le début** : au moins un test par module quand la logique est non triviale
- **Documentation vivante** : mettre à jour le journal dans `docs/aliexpress-integration.md` après chaque session

## 📞 Pilotage de Claude Code

Hakim va lancer Claude Code session par session, pas en one-shot :
- Une session = une ou deux étapes du plan
- Demander systématiquement le diff avant commit
- Valider chaque module avec tests avant de passer au suivant
- Ne jamais deviner un endpoint ou paramètre AliExpress : consulter la doc officielle ou demander

## 🔗 Ressources clés

- Console AliExpress : https://openservice.aliexpress.com
- Doc AE Dropshipping : accessible depuis la console AE, onglet Documentation
- FastMCP docs : https://gofastmcp.com (v3)
- Ref shape IOP : `tests/fixtures/real_*.json` (captures live commitées)
- API Anthropic (Managed Agents) : https://docs.claude.com
