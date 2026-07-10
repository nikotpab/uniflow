"""
gmail_client.py
===============
Lee emails de un remitente específico usando la Gmail API con OAuth2.
Los tokens se obtienen de AWS SSM Parameter Store.
"""

import base64
import json
import urllib.parse
import urllib.request
import boto3
from datetime import datetime, timezone
from typing import Optional


# ─── Constantes ───────────────────────────────────────────────────────────────

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
AWS_REGION = "us-east-1"


# ─── SSM Helper ───────────────────────────────────────────────────────────────

_ssm_cache: dict = {}

def _get_ssm(name: str) -> str:
    if name in _ssm_cache:
        return _ssm_cache[name]
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    value = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    _ssm_cache[name] = value
    return value


# ─── OAuth2 Token Refresh ──────────────────────────────────────────────────────

def _get_access_token() -> str:
    """Obtiene un access token usando el refresh token almacenado en SSM."""
    client_id = _get_ssm("/uniflow/google/client_id")
    client_secret = _get_ssm("/uniflow/google/client_secret")
    refresh_token = _get_ssm("/uniflow/google/refresh_token")

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read().decode())

    if "access_token" not in tokens:
        raise RuntimeError(f"No se obtuvo access_token: {tokens}")

    return tokens["access_token"]


# ─── Gmail API Helpers ─────────────────────────────────────────────────────────

def _gmail_get(path: str, access_token: str, params: Optional[dict] = None) -> dict:
    url = f"{GMAIL_API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _decode_body(payload: dict) -> str:
    """Extrae y decodifica el cuerpo del email (text/plain preferido)."""
    mime = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    # Email simple (text/plain o text/html)
    if mime in ("text/plain", "text/html") and body_data:
        decoded = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        # Si es HTML, hacer strip básico de tags
        if mime == "text/html":
            import re
            decoded = re.sub(r"<[^>]+>", " ", decoded)
            decoded = re.sub(r"\s+", " ", decoded).strip()
        return decoded

    # Email multiparte — buscar text/plain primero, luego text/html
    parts = payload.get("parts", [])
    text_plain = ""
    text_html = ""

    for part in parts:
        part_mime = part.get("mimeType", "")
        part_data = part.get("body", {}).get("data", "")

        if part_mime == "text/plain" and part_data:
            text_plain = base64.urlsafe_b64decode(part_data + "==").decode("utf-8", errors="replace")
        elif part_mime == "text/html" and part_data:
            import re
            raw = base64.urlsafe_b64decode(part_data + "==").decode("utf-8", errors="replace")
            text_html = re.sub(r"<[^>]+>", " ", raw)
            text_html = re.sub(r"\s+", " ", text_html).strip()
        elif part_mime.startswith("multipart/"):
            # Recursivo para multipart anidados
            nested = _decode_body(part)
            if nested:
                text_plain = nested
                break

    return text_plain or text_html or ""


# ─── Interfaz Pública ─────────────────────────────────────────────────────────

def get_unread_emails_from_sender(
    sender_email: str,
    max_results: int = 20,
    mark_as_read: bool = False,
) -> list[dict]:
    """
    Devuelve lista de emails no leídos del remitente especificado.

    Cada email tiene:
        - id: str
        - thread_id: str
        - subject: str
        - sender: str
        - date: str (ISO 8601)
        - body: str (texto plano)
        - labels: list[str]
    """
    access_token = _get_access_token()
    user_email = _get_ssm("/uniflow/config/user_email")

    # Buscar emails no leídos del remitente
    query = f"from:{sender_email} is:unread"
    search = _gmail_get(
        f"/users/{user_email}/messages",
        access_token,
        params={"q": query, "maxResults": max_results},
    )

    messages = search.get("messages", [])
    if not messages:
        return []

    emails = []
    for msg_ref in messages:
        msg_id = msg_ref["id"]
        msg = _gmail_get(f"/users/{user_email}/messages/{msg_id}", access_token)

        # Extraer headers
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("Subject", "(sin asunto)")
        sender = headers.get("From", sender_email)
        date_str = headers.get("Date", "")

        # Parsear fecha
        try:
            from email.utils import parsedate_to_datetime
            date_dt = parsedate_to_datetime(date_str)
            date_iso = date_dt.astimezone(timezone.utc).isoformat()
        except Exception:
            date_iso = datetime.now(timezone.utc).isoformat()

        # Extraer cuerpo
        body = _decode_body(msg.get("payload", {}))

        emails.append({
            "id": msg_id,
            "thread_id": msg.get("threadId", ""),
            "subject": subject,
            "sender": sender,
            "date": date_iso,
            "body": body[:8000],  # Limitar para Bedrock
            "labels": msg.get("labelIds", []),
        })

    print(f"[GmailClient] Encontrados {len(emails)} emails de {sender_email}")
    return emails


def mark_email_as_read(email_id: str) -> None:
    """Marca un email como leído removiendo el label UNREAD."""
    access_token = _get_access_token()
    user_email = _get_ssm("/uniflow/config/user_email")

    data = json.dumps({"removeLabelIds": ["UNREAD"]}).encode()
    url = f"{GMAIL_API_BASE}/users/{user_email}/messages/{email_id}/modify"

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req):
        pass
