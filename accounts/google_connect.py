"""Connecting a user's Google account: the OAuth web flow and stored credentials."""

import os

import requests
from cryptography.fernet import Fernet
from django.conf import settings
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

import google_auth
import gmail
import run_context

# Google may return the scopes in another order or add earlier grants; we
# check the granted scopes ourselves instead of letting oauthlib raise.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

TOKEN_URI = "https://oauth2.googleapis.com/token"


def setup_problem() -> str | None:
    """Why Google sign-in can't start yet, or None if it's configured."""
    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET):
        return ("Google sign-in isn't set up yet: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET "
                "in .env, from a Web application OAuth client.")
    return None


def _flow(redirect_uri: str, **kwargs) -> Flow:
    client_config = {"web": {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": TOKEN_URI,
    }}
    return Flow.from_client_config(
        client_config, scopes=google_auth.SCOPES, redirect_uri=redirect_uri, **kwargs
    )


def authorization_url(redirect_uri: str) -> tuple[str, str, str]:
    """Returns (url, state, code_verifier); keep the last two for the callback."""
    flow = _flow(redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return url, state, flow.code_verifier


def exchange_code(redirect_uri: str, code: str, code_verifier: str) -> Credentials:
    flow = _flow(redirect_uri, code_verifier=code_verifier)
    flow.fetch_token(code=code)
    return flow.credentials


def has_all_scopes(creds: Credentials) -> bool:
    granted = creds.granted_scopes or []
    if isinstance(granted, str):
        granted = granted.split()
    return set(google_auth.SCOPES) <= set(granted)


def account_email(creds: Credentials) -> str:
    with run_context.use(run_context.RunContext(creds)):
        return gmail.my_address()


def encrypt(token: str) -> str:
    return Fernet(settings.TOKEN_KEY).encrypt(token.encode()).decode()


def decrypt(token: str) -> str:
    return Fernet(settings.TOKEN_KEY).decrypt(token.encode()).decode()


def credentials(connection) -> Credentials:
    """Credentials for a stored connection; refreshed on first use."""
    return Credentials(
        None,
        refresh_token=decrypt(connection.refresh_token),
        token_uri=TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=google_auth.SCOPES,
    )


def revoke(connection) -> None:
    """Best effort: also remove the app's access in the user's Google account."""
    try:
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": decrypt(connection.refresh_token)}, timeout=10,
        )
    except requests.RequestException:
        pass
