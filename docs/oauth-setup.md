# Guide OAuth AliExpress — pas à pas

## Prérequis

1. **App AliExpress créée** sur [openservice.aliexpress.com](https://openservice.aliexpress.com)
   - Type : Drop Shipping
   - Statut : Active
2. **App Key + App Secret** récupérés depuis la console AE
3. **IP VPS whitelistée** dans la console AE : `148.230.118.152`
4. **Python 3.11+** installé sur le VPS avec `flask`, `requests`, `python-dotenv`
5. **Accès SSH** au VPS Hostinger

## Installation des dépendances (sur le VPS)

```bash
cd ~/aliexpress-mcp-server
pip install flask requests python-dotenv
```

## Étape 1 — Préparer le .env

```bash
cp .env.example .env
```

Remplir dans `.env` :
```
AE_APP_KEY=ton_app_key
AE_APP_SECRET=ton_app_secret
AE_CALLBACK_URL=http://148.230.118.152:3000/oauth/aliexpress/callback
```

> **Important** : on utilise `http://` + IP directe + port 3000 pour le one-shot OAuth. Pas de Traefik.

## Étape 2 — Changer le callback URL dans la console AE

1. Aller sur [openservice.aliexpress.com](https://openservice.aliexpress.com) → ton app "Hermes DropPilot"
2. App Settings → **Callback URL**
3. Remplacer par : `http://148.230.118.152:3000/oauth/aliexpress/callback`
4. Sauvegarder

## Étape 3 — Ouvrir le port 3000 sur le VPS

SSH sur le VPS, puis :

**Si le VPS utilise `ufw` :**
```bash
sudo ufw allow 3000/tcp
```

**Si le VPS utilise `iptables` directement :**
```bash
sudo iptables -I INPUT -p tcp --dport 3000 -j ACCEPT
```

Vérifier que le port est ouvert :
```bash
# ufw
sudo ufw status | grep 3000

# iptables
sudo iptables -L INPUT -n | grep 3000
```

## Étape 4 — Lancer le script OAuth

```bash
cd ~/aliexpress-mcp-server
python3 scripts/ae_oauth.py
```

Le terminal affiche :
```
============================================================
  AliExpress OAuth — DropPilot
============================================================

  App Key      : 123456
  Callback URL : http://148.230.118.152:3000/oauth/aliexpress/callback
  Flask route  : /oauth/aliexpress/callback
  Flask port   : 3000

  URL d'autorisation :
  https://api-sg.aliexpress.com/oauth/authorize?response_type=code&...

  Ouverture du navigateur...
  (Si rien ne s'ouvre, copie-colle l'URL ci-dessus)

  En attente du callback sur le port 3000...
```

Le navigateur ne s'ouvrira pas sur un VPS headless. **Copier l'URL d'autorisation** et l'ouvrir dans le navigateur sur ton Mac.

## Étape 5 — Autoriser l'app

1. Ouvrir l'URL d'autorisation dans Chrome/Safari sur ton Mac
2. Se connecter avec le compte AliExpress seller
3. Cliquer sur **Authorize**
4. AliExpress redirige vers `http://148.230.118.152:3000/oauth/aliexpress/callback?code=xxx`
5. Flask capture le code et échange automatiquement contre les tokens

## Étape 6 — Vérifier les tokens

Si tout se passe bien, le terminal VPS affiche :
```
============================================================
  Code d'autorisation reçu : abc123def456...
============================================================

  Tokens obtenus avec succès !
  User         : ton_pseudo_ae
  Access Token : 50000901e28e8gkrle...
  Refresh Token: 50001901e28e8gkrle...
  Expire dans  : 2592000 secondes

  .env mis à jour : /root/aliexpress-mcp-server/.env
```

Vérifier :
```bash
grep "AE_ACCESS_TOKEN\|AE_REFRESH_TOKEN" .env
```

## Étape 7 — Sécuriser après l'OAuth

**Fermer le port 3000 immédiatement :**

```bash
# ufw
sudo ufw delete allow 3000/tcp

# iptables
sudo iptables -D INPUT -p tcp --dport 3000 -j ACCEPT
```

**Remettre le callback HTTPS dans la console AE :**

1. Console AE → App Settings → Callback URL
2. Remettre : `https://srv1575867.hstgr.cloud/oauth/aliexpress/callback`
3. Sauvegarder

**Mettre à jour le .env** pour la prod :
```bash
sed -i 's|AE_CALLBACK_URL=http://148.230.118.152:3000/oauth/aliexpress/callback|AE_CALLBACK_URL=https://srv1575867.hstgr.cloud/oauth/aliexpress/callback|' .env
```

## Durée de vie des tokens

| Token | App Test | App Online |
|---|---|---|
| Access Token | 7 jours | 30 jours |
| Refresh Token | 30 jours | 180 jours |

Le refresh automatique viendra en post-MVP. En attendant, relancer ce script quand les tokens expirent.

---

## Troubleshooting

### Le navigateur affiche "Invalid redirect_uri"

Le callback URL dans `.env` ne correspond pas à celui enregistré dans la console AE. Les deux doivent être **exactement identiques** (même protocole HTTP/HTTPS, même IP, même port, même path).

### Erreur de signature ("Sign check failed")

- `AE_APP_SECRET` incorrect → copier-coller depuis la console AE
- Horloge VPS décalée → vérifier avec `date` (doit être UTC ± 5 min)

### Le callback n'arrive jamais

- Port 3000 pas ouvert → vérifier avec `curl http://148.230.118.152:3000/` depuis ton Mac
- Firewall Hostinger bloque le port → vérifier dans le panel Hostinger (Firewall section)

### "Remote service error" lors de l'échange du code

- IP VPS pas dans la whitelist AE → vérifier dans la console AE → App Settings
- Pas de connectivité vers AE : `curl -I https://api-sg.aliexpress.com` depuis le VPS

### Token expiré

Relancer `python3 scripts/ae_oauth.py` (refaire les étapes 2-7).
