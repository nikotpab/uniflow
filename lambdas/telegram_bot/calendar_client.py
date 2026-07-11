"""
calendar_client.py — telegram_bot
==================================
Elimina eventos de Google Calendar cuando el usuario completa una tarea.
(Mismo mecanismo OAuth2 que email_scanner/calendar_client.py, incluido por
separado para que cada Lambda sea un paquete de despliegue independiente.)
"""

import json
import urllib.error
import urllib.parse
import urllib.request
import boto3


AWS_REGION = "us-east-1"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"


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
    data = urllib.parse.urlencode({
        "client_id": _get_ssm("/uniflow/google/client_id"),
        "client_secret": _get_ssm("/uniflow/google/client_secret"),
        "refresh_token": _get_ssm("/uniflow/google/refresh_token"),
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read().decode())

    if "access_token" not in tokens:
        raise RuntimeError(f"No se obtuvo access_token: {tokens}")
    return tokens["access_token"]


# ─── Interfaz Pública ─────────────────────────────────────────────────────────

def delete_event(event_id: str) -> None:
    """
    Elimina un evento de Google Calendar.
    404/410 (el evento ya no existe) no se consideran error.
    """
    user_email = _get_ssm("/uniflow/config/user_email")
    access_token = _get_access_token()
    url = (
        f"{CALENDAR_API_BASE}/calendars/{urllib.parse.quote(user_email)}"
        f"/events/{urllib.parse.quote(event_id)}"
    )

    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urllib.request.urlopen(req):
            pass
        print(f"[Calendar] Evento eliminado: {event_id}")
    except urllib.error.HTTPError as e:
        # Google devuelve 410 Gone para eventos borrados previamente
        if e.code in (404, 410):
            print(f"[Calendar] Evento no encontrado (ya eliminado): {event_id}")
        else:
            raise
