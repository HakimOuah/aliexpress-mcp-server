# Product Factory MCP Server

Serveur MCP Python pour DropPilot, combinant :

- **AliExpress Drop Shipping API** pour le sourcing produit, les variantes et la livraison ;
- **DataForSEO Google SERP API** pour l'étude de concurrence, Google Ads/Shopping visibles et le pricing.

Le serveur tourne sur un seul endpoint MCP (`/mcp`) et expose les outils AliExpress existants ainsi que les outils de market research.

## Outils Google / DataForSEO

- `search_google_serp(keyword, ...)` — SERP Google Live Advanced structurée, sans navigateur ni CAPTCHA ;
- `search_google_shopping(keyword, ...)` — extrait les blocs Shopping / Popular Products / Commercial Units ;
- `analyze_google_competition(keywords, ...)` — agrège plusieurs requêtes : domaines organiques récurrents, annonceurs, marchands Shopping, marketplaces et fourchette de prix observée.

L'endpoint utilisé est `POST /v3/serp/google/organic/live/advanced` chez DataForSEO. Une analyse peut contenir jusqu'à 10 variantes de mots-clés.

## Variables d'environnement

Les secrets restent dans le fichier `.env` du VPS (jamais commité) :

```env
DATAFORSEO_LOGIN=...
DATAFORSEO_PASSWORD=...
```

Les credentials AliExpress restent inchangés. Voir `.env.example` pour la liste complète des variables.

## Lancement

```bash
docker compose up -d --build
```

Le Dockerfile lance `python -m src.product_factory_server`, qui réutilise le serveur FastMCP AliExpress existant puis ajoute les outils DataForSEO.

## Documentation

- [CLAUDE.md](./CLAUDE.md) — contexte projet complet, conventions, stack
- [Plan d'intégration](./aliexpress-integration.md) — architecture, roadmap, schémas de données
- [Déploiement](./docs/deploy.md) — procédure VPS
