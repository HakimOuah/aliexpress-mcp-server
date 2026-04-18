#!/usr/bin/env python3
"""
AliExpress OAuth one-shot — Flow complet pour obtenir access_token + refresh_token.

Usage :
    1. Copier .env.example vers .env et remplir AE_APP_KEY + AE_APP_SECRET
    2. python3 scripts/ae_oauth.py
    3. Autoriser dans le navigateur
    4. Les tokens sont écrits dans .env automatiquement (si confirmé)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
from flask import Flask, request

# ── Constantes AliExpress OP API ──────────────────────────────────────────────

AE_AUTH_URL = "https://api-sg.aliexpress.com/oauth/authorize"
AE_REST_URL = "https://api-sg.aliexpress.com/rest"
TOKEN_CREATE_PATH = "/auth/token/create"
SIGN_METHOD = "sha256"

# ── Chargement config ─────────────────────────────────────────────────────────

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"

load_dotenv(ENV_PATH)

APP_KEY = os.getenv("AE_APP_KEY", "")
APP_SECRET = os.getenv("AE_APP_SECRET", "")
CALLBACK_URL = os.getenv("AE_CALLBACK_URL", "")
FLASK_PORT = 3000

if not APP_KEY or not APP_SECRET:
    print("ERREUR : AE_APP_KEY et AE_APP_SECRET doivent être définis dans .env")
    sys.exit(1)

if not CALLBACK_URL:
    print("ERREUR : AE_CALLBACK_URL doit être défini dans .env")
    sys.exit(1)

# ── Signature HMAC-SHA256 (OP API) ───────────────────────────────────────────

def sign_request(app_secret: str, api_path: str, params: dict[str, str]) -> str:
    """Signe une requête OP API avec HMAC-SHA256.

    Base string = api_path + clés/valeurs triées alphabétiquement.
    Clé HMAC = app_secret.
    Résultat = hex uppercase.
    """
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    base_string = api_path + "".join(f"{k}{v}" for k, v in sorted_params)

    signature = hmac.new(
        app_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()

    return signature


# ── Échange code → tokens ────────────────────────────────────────────────────

def exchange_code_for_tokens(code: str) -> dict:
    """Échange le code d'autorisation contre access_token + refresh_token."""
    import requests

    timestamp = str(int(time.time() * 1000))

    # Paramètres à signer (système + application, sans 'method' ni 'sign')
    sign_params = {
        "app_key": APP_KEY,
        "code": code,
        "sign_method": SIGN_METHOD,
        "simplify": "true",
        "timestamp": timestamp,
    }

    signature = sign_request(APP_SECRET, TOKEN_CREATE_PATH, sign_params)

    # Query string = paramètres système + signature
    query = {
        "app_key": APP_KEY,
        "sign": signature,
        "sign_method": SIGN_METHOD,
        "simplify": "true",
        "timestamp": timestamp,
    }

    url = f"{AE_REST_URL}{TOKEN_CREATE_PATH}?{urlencode(query)}"

    # POST body = paramètres application
    response = requests.post(
        url,
        data={"code": code},
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} : {response.text}")

    data = response.json()

    if "access_token" not in data:
        error_msg = data.get("error_response", {}).get("msg", str(data))
        raise RuntimeError(f"Erreur AliExpress : {error_msg}")

    return data


# ── Écriture des tokens dans .env ────────────────────────────────────────────

def update_env_file(access_token: str, refresh_token: str) -> None:
    """Met à jour AE_ACCESS_TOKEN et AE_REFRESH_TOKEN dans le fichier .env."""
    if not ENV_PATH.exists():
        print(f"  Fichier .env introuvable ({ENV_PATH}), écriture impossible.")
        return

    content = ENV_PATH.read_text()

    def replace_or_append(text: str, key: str, value: str) -> str:
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, text, re.MULTILINE):
            return re.sub(pattern, f"{key}={value}", text, flags=re.MULTILINE)
        return text.rstrip("\n") + f"\n{key}={value}\n"

    content = replace_or_append(content, "AE_ACCESS_TOKEN", access_token)
    content = replace_or_append(content, "AE_REFRESH_TOKEN", refresh_token)

    ENV_PATH.write_text(content)
    print(f"  .env mis à jour : {ENV_PATH}")


# ── Serveur Flask (callback OAuth) ───────────────────────────────────────────

app = Flask(__name__)

# Extraire le path du callback URL pour matcher la route Flask
callback_path = urlparse(CALLBACK_URL).path or "/oauth/callback"


@app.route(callback_path)
def oauth_callback():
    """Reçoit le code d'autorisation d'AliExpress et échange contre des tokens."""
    code = request.args.get("code")

    if not code:
        error = request.args.get("error", "Pas de code reçu")
        return f"<h2>Erreur OAuth</h2><p>{error}</p>", 400

    print(f"\n{'='*60}")
    print(f"  Code d'autorisation reçu : {code[:20]}...")
    print(f"{'='*60}\n")

    try:
        token_data = exchange_code_for_tokens(code)
    except RuntimeError as e:
        print(f"  ERREUR lors de l'échange : {e}")
        return f"<h2>Erreur</h2><p>{e}</p>", 500

    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_in = token_data.get("expires_in", "?")
    user_nick = token_data.get("user_nick", "?")

    print("  Tokens obtenus avec succès !")
    print(f"  User         : {user_nick}")
    print(f"  Access Token : {access_token[:30]}...")
    print(f"  Refresh Token: {refresh_token[:30]}...")
    print(f"  Expire dans  : {expires_in} secondes")
    print()

    # Écriture automatique dans .env
    update_env_file(access_token, refresh_token)

    # Shutdown Flask après succès
    shutdown = request.environ.get("werkzeug.server.shutdown")
    if shutdown:
        shutdown()

    return (
        "<h2>OAuth AliExpress OK</h2>"
        f"<p>User: {user_nick}</p>"
        f"<p>Access token obtenu (expire dans {expires_in}s)</p>"
        "<p>Tokens écrits dans .env. Tu peux fermer cet onglet.</p>"
    )


# ── Point d'entrée ───────────────────────────────────────────────────────────

def main() -> None:
    auth_url = (
        f"{AE_AUTH_URL}?"
        + urlencode({
            "response_type": "code",
            "force_auth": "true",
            "redirect_uri": CALLBACK_URL,
            "client_id": APP_KEY,
            "view": "web",
        })
    )

    print()
    print("=" * 60)
    print("  AliExpress OAuth — DropPilot")
    print("=" * 60)
    print()
    print(f"  App Key      : {APP_KEY}")
    print(f"  Callback URL : {CALLBACK_URL}")
    print(f"  Flask route  : {callback_path}")
    print(f"  Flask port   : {FLASK_PORT}")
    print()
    print("  URL d'autorisation :")
    print(f"  {auth_url}")
    print()
    print("  Ouverture du navigateur...")
    print("  (Si rien ne s'ouvre, copie-colle l'URL ci-dessus)")
    print()

    webbrowser.open(auth_url)

    print(f"  En attente du callback sur le port {FLASK_PORT}...")
    print()

    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)


if __name__ == "__main__":
    main()
