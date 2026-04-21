# Déploiement VPS — AliExpress MCP Server

Procédure pour déployer / mettre à jour le container `aliexpress-mcp` sur le VPS Hostinger (`srv1575867.hstgr.cloud`, `148.230.118.152`), à côté du container Hermès.

---

## 0. Prérequis (à vérifier une seule fois)

Depuis le terminal Hostinger, vérifier que :

```bash
# Docker actif
docker version
docker compose version

# Le réseau Docker de Hermès existe
docker network ls | grep hermes-agent-hjft_default

# Le container Hermès tourne (source du réseau)
docker ps --filter name=hermes-agent-hjft

# Le repo est cloné et la deploy key marche
cd /opt/aliexpress-mcp-server
git remote -v
ls .env .env.example docker-compose.yml Dockerfile
```

Si `hermes-agent-hjft_default` n'existe pas, c'est que Hermès n'est pas démarré — lancer Hermès d'abord.

Si `/opt/aliexpress-mcp-server/.env` n'existe pas, le copier depuis ta machine ou recréer les variables depuis `.env.example` (App Key, App Secret, AE_ACCESS_TOKEN, AE_REFRESH_TOKEN obligatoires).

---

## 1. Mettre à jour le code depuis GitHub

```bash
cd /opt/aliexpress-mcp-server
git fetch origin
git status                    # vérifier qu'il n'y a pas de changements locaux
git log --oneline -5          # état avant pull (noter le SHA pour rollback éventuel)
git pull origin main
git log --oneline -5          # confirmer le nouveau HEAD
```

Si `git status` montre des modifications locales sur le VPS, les inspecter avant de pull — probablement un `.env` édité à la main ou un dump de smoke test ancien.

---

## 2. Build et lancement du container

```bash
cd /opt/aliexpress-mcp-server

# Build l'image (utilise le Dockerfile)
docker compose build

# Démarre le container en arrière-plan
docker compose up -d

# Suit les logs au démarrage — CTRL-C pour quitter le suivi
docker compose logs -f aliexpress-mcp
```

Signes d'un démarrage propre dans les logs :

- Pas de traceback Python
- Une ligne `mcp.server.start host=0.0.0.0 port=8080 transport=http`
- Pas de répétition / crash loop

Si ça ne démarre pas, voir la section *Troubleshooting*.

---

## 3. Vérifier l'état du container

```bash
docker compose ps                            # doit afficher "healthy" après ~30 s
docker inspect aliexpress-mcp --format='{{.State.Health.Status}}'
docker stats aliexpress-mcp --no-stream      # mémoire / CPU
docker network inspect hermes-agent-hjft_default \
  | grep -A2 aliexpress-mcp                  # confirme l'attachement au réseau
```

Le healthcheck hit `http://127.0.0.1:8080/mcp` toutes les 30 s depuis l'intérieur du container. Tant qu'il n'a pas encore run (premiers 10 s : `start_period`), l'état est `starting`.

---

## 4. Smoke test fonctionnel en live

Le smoke test est embarqué dans l'image **`aliexpress-mcp`** (et uniquement celle-là — pas dans l'image Hermès). Il se connecte au serveur MCP et exerce les 4 tools avec un vrai appel AE (nécessite un `AE_ACCESS_TOKEN` valide dans le `.env`).

### Option A — depuis le container `aliexpress-mcp` lui-même (le plus simple)

```bash
docker exec aliexpress-mcp python /app/scripts/mcp_live_smoke_test.py
```

Le script tape `http://127.0.0.1:8080/mcp` (défaut de `MCP_URL`) — c'est localhost à l'intérieur du container, donc pas de dépendance au DNS Docker.

Sortie attendue : des `✅` sur chaque étape, puis une liste de produits normalisés pour la requête `cave à vin`. Si aucun produit ne passe les filtres high-ticket (exit 3), changer la requête :

```bash
docker exec -e MCP_QUERY="aspirateur robot" aliexpress-mcp \
  python /app/scripts/mcp_live_smoke_test.py
```

Exit codes du script : `0` OK, `1` tools manquants, `2` AE renvoie 0 item, `3` filtres coupent tout, `4` exception inattendue.

### Option B — valider la connectivité depuis Hermès (DNS inter-container)

Le script Python n'est pas copié dans l'image Hermès, et le dupliquer juste pour ce test ajoute une dette inutile. Pour valider que Hermès atteint bien `aliexpress-mcp:8080` via le DNS Docker, on fait un simple `curl` — suffisant pour prouver que la Phase 8 pourra joindre le serveur. Voir la section suivante.

---

## 4b. Vérification DNS inter-container

**Étape critique** : en Phase 8, le scout agent (qui tourne dans l'espace de Hermès) appellera `http://aliexpress-mcp:8080/mcp`. Si le DNS Docker ne résout pas ou si le port n'est pas joignable, on le découvrira sur la chaîne scout complète — bien plus pénible à debugger. Mieux vaut le valider ici.

```bash
# 1. Les 2 containers partagent-ils le même réseau ?
docker network inspect hermes-agent-hjft_default \
  --format '{{range .Containers}}{{.Name}} {{end}}'
# Attendu : doit lister à la fois aliexpress-mcp ET hermes-agent-hjft-hermes-agent-1

# 2. Le nom aliexpress-mcp est-il résolvable depuis Hermès ?
docker exec hermes-agent-hjft-hermes-agent-1 getent hosts aliexpress-mcp
# Attendu : "<172.x.x.x>  aliexpress-mcp" (IP interne du réseau hermes-agent-hjft_default)

# 3. Le port 8080 répond-il depuis Hermès ?
docker exec hermes-agent-hjft-hermes-agent-1 \
  curl -s -o /dev/null -w "HTTP %{http_code}\n" http://aliexpress-mcp:8080/mcp
# Attendu : un code HTTP (200, 400, 405 — peu importe, tant que ce n'est PAS "Connection refused")
```

Ce qui peut foirer :

| Symptôme | Cause probable | Fix |
|---|---|---|
| `getent hosts` ne renvoie rien | `aliexpress-mcp` pas attaché au réseau | `docker network connect hermes-agent-hjft_default aliexpress-mcp` |
| `Connection refused` sur le port 8080 | Container démarré mais serveur pas encore ready | attendre 10 s puis retry (le `start_period` du healthcheck couvre ça) |
| `Could not resolve host` | DNS Docker cassé | redémarrer le daemon : `sudo systemctl restart docker` (affecte aussi Hermès — à faire hors prod) |
| `curl: command not found` dans Hermès | `curl` pas installé dans l'image Hermès | remplacer par `wget -qO- http://aliexpress-mcp:8080/mcp` ou `python3 -c "import urllib.request; print(urllib.request.urlopen('http://aliexpress-mcp:8080/mcp').status)"` |

Une fois ces 3 étapes `ok`, la Phase 8 a le feu vert réseau.

---

## 5. Monitoring en continu

```bash
# Logs en temps réel (CTRL-C pour quitter)
docker compose logs -f aliexpress-mcp

# Logs des 30 dernières minutes uniquement
docker compose logs --since 30m aliexpress-mcp

# Usage ressources instantané
docker stats aliexpress-mcp --no-stream

# Redémarrer sans rebuild (en cas de reset de config)
docker compose restart aliexpress-mcp
```

Les logs sont en JSON structuré (structlog). Chaque tool call émet un événement `mcp.tool.call` avec `tool`, `status` (start / success / error), `duration_ms`, `result_count` ou `error`. Rotation configurée : max 10 Mo par fichier, 3 fichiers max (= 30 Mo plafond disque).

---

## 6. Rollback

Si le nouveau code casse quelque chose et qu'il faut revenir en arrière :

```bash
cd /opt/aliexpress-mcp-server

# Stopper le container
docker compose down

# Revenir au SHA qui marchait (noté à l'étape 1)
git log --oneline -10
git reset --hard <SHA_OK>

# Rebuilder et relancer sur l'ancien code
docker compose build
docker compose up -d
docker compose logs -f aliexpress-mcp
```

Alternative plus soft (si tu veux juste stopper sans toucher au code) :

```bash
docker compose down          # stoppe et supprime le container (image gardée)
# ... ou ...
docker compose stop          # stoppe sans supprimer
```

---

## 7. Troubleshooting

### 7.1 Le container ne démarre pas / crash loop

```bash
docker compose logs --tail=50 aliexpress-mcp
```

Erreurs fréquentes :

| Symptôme dans les logs | Cause | Fix |
|---|---|---|
| `ValueError: Variable d'environnement manquante : AE_APP_KEY` | `.env` absent ou vide | Recréer `/opt/aliexpress-mcp-server/.env` depuis `.env.example` et remplir les valeurs |
| `fastmcp.exceptions.ClientError` / `connection refused` au healthcheck | Serveur pas encore ready lors du 1er healthcheck | Attendre 30 s, Docker retry automatique |
| `OSError: [Errno 98] Address already in use` | Port 8080 déjà pris sur le host | `sudo lsof -i :8080` → tuer le squatter, ou changer le port dans `docker-compose.yml` |
| `ModuleNotFoundError: No module named 'fastmcp'` | Image pas rebuild après update requirements | `docker compose build --no-cache` puis `up -d` |

### 7.2 Token OAuth expiré

Symptôme : `IOPAuthError | Auth error: ... | ae_code=27` dans les logs quand un tool est appelé.

Fix : régénérer un access_token via `scripts/ae_oauth.py` (voir `docs/oauth-setup.md`), mettre à jour `AE_ACCESS_TOKEN` et `AE_REFRESH_TOKEN` dans le `.env` VPS, puis `docker compose restart aliexpress-mcp`. Le token est lu au démarrage du container.

### 7.3 Le healthcheck échoue en permanence

```bash
docker inspect aliexpress-mcp --format='{{json .State.Health}}' | jq
```

Regarder `Log[].Output` pour voir ce que retourne le check. Si le serveur tourne mais renvoie 500+ systématiquement, inspecter les logs applicatifs — probablement un bug logique à fixer et redéployer.

### 7.4 Hermès ne joint pas `aliexpress-mcp:8080`

```bash
# Depuis Hermès, tenter le ping DNS interne
docker exec hermes-agent-hjft-hermes-agent-1 getent hosts aliexpress-mcp
# Doit renvoyer une IP 172.x.x.x du réseau Docker
```

Si pas de résolution : vérifier que les deux containers sont bien sur le même réseau (`docker network inspect hermes-agent-hjft_default`). Sinon, `docker network connect hermes-agent-hjft_default aliexpress-mcp`.

### 7.5 Mémoire saturée

Limite fixée à 512 Mo dans `docker-compose.yml`. Si le container OOM-kill :

```bash
docker events --filter container=aliexpress-mcp --filter event=oom
```

Augmenter `mem_limit` à `1g` si besoin — typique si des appels IOP retournent des payloads géants (rare).

---

## 8. Commandes de référence

| Action | Commande |
|---|---|
| Voir le statut | `docker compose ps` |
| Démarrer | `docker compose up -d` |
| Stopper | `docker compose stop` |
| Redémarrer | `docker compose restart aliexpress-mcp` |
| Supprimer (sans l'image) | `docker compose down` |
| Rebuild complet | `docker compose build --no-cache` |
| Logs live | `docker compose logs -f aliexpress-mcp` |
| Smoke test live | `docker exec aliexpress-mcp python /app/scripts/mcp_live_smoke_test.py` |
| Shell dans le container | `docker exec -it aliexpress-mcp bash` |
