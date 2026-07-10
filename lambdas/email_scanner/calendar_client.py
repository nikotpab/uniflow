"""
calendar_client.py
==================
Crea y gestiona eventos en Google Calendar usando OAuth2.
Los tokens se obtienen de AWS SSM Parameter Store.
"""

import json
import urllib.parse
import urllib.request
import boto3
from datetime import datetime, timezone, timedelta
from typing import Optional


AWS_REGION = "us-east-1"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Zona horaria del usuario (Bogotá, sin DST). Los due_date que extrae Bedrock
# vienen sin timezone y representan hora local del estudiante, no UTC.
LOCAL_TZ = timezone(timedelta(hours=-5))

# Colores de Google Calendar por tipo de tarea
TYPE_COLORS = {
    "parcial":     "11",  # Tomate (rojo)
    "proyecto":    "6",   # Mandarina (naranja)
    "tarea":       "2",   # Salvia (verde)
    "quiz":        "5",   # Banana (amarillo)
    "laboratorio": "7",   # Pavo real (azul)
    "exposicion":  "3",   # Uva (morado)
    "otro":        "1",   # Lavanda (azul claro)
}


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


# ─── Calendar API Helper ───────────────────────────────────────────────────────

def _calendar_request(method: str, path: str, body: Optional[dict] = None) -> dict:
    access_token = _get_access_token()
    user_email = _get_ssm("/uniflow/config/user_email")
    url = f"{CALENDAR_API_BASE}{path.format(calendar_id=user_email)}"

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


# ─── Interfaz Pública ─────────────────────────────────────────────────────────

def create_event_from_task(task: dict) -> str:
    """
    Crea un evento en Google Calendar a partir de una tarea.
    Devuelve el ID del evento creado.

    Args:
        task: dict con keys: subject, course, due_date, description, type, priority

    Returns:
        event_id: str
    """
    subject = task.get("subject", "Tarea sin título")
    course = task.get("course", "")
    due_date_str = task.get("due_date", "")
    description = task.get("description", "")
    task_type = task.get("type", "otro")
    priority = task.get("priority", "media")

    # Construir título del evento
    type_emoji = {
        "parcial": "📝",
        "proyecto": "🚀",
        "tarea": "📚",
        "quiz": "❓",
        "laboratorio": "🔬",
        "exposicion": "🎤",
        "otro": "📌",
    }.get(task_type, "📌")

    title = f"{type_emoji} {subject}"
    if course:
        title += f" — {course}"

    # Parsear fecha de vencimiento (naive = hora local de Bogotá)
    try:
        due_dt = datetime.fromisoformat(due_date_str)
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=LOCAL_TZ)
    except (ValueError, TypeError):
        # Fallback: mañana a las 23:59 hora local
        due_dt = datetime.now(LOCAL_TZ) + timedelta(days=1)
        due_dt = due_dt.replace(hour=23, minute=59, second=0, microsecond=0)

    # El evento dura 1 hora antes del deadline
    start_dt = due_dt - timedelta(hours=1)

    # Descripción enriquecida
    full_description = f"📋 {description}\n\n" if description else ""
    full_description += f"🎯 Tipo: {task_type.capitalize()}\n"
    full_description += f"⚡ Prioridad: {priority.capitalize()}\n"
    if course:
        full_description += f"📖 Materia: {course}\n"
    full_description += f"\n🤖 Creado automáticamente por UniFlow"

    # Recordatorios según prioridad
    reminder_minutes = {
        "alta": [1440, 60],   # 1 día antes + 1 hora antes
        "media": [1440],       # 1 día antes
        "baja": [60],          # 1 hora antes
    }.get(priority, [1440])

    event_body = {
        "summary": title,
        "description": full_description,
        "colorId": TYPE_COLORS.get(task_type, "1"),
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "America/Bogota",
        },
        "end": {
            "dateTime": due_dt.isoformat(),
            "timeZone": "America/Bogota",
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": m}
                for m in reminder_minutes
            ],
        },
    }

    response = _calendar_request(
        "POST",
        "/calendars/{calendar_id}/events",
        body=event_body,
    )

    event_id = response.get("id", "")
    event_link = response.get("htmlLink", "")
    print(f"[Calendar] Evento creado: {title[:60]} → {event_link}")
    return event_id


def delete_event(event_id: str) -> None:
    """Elimina un evento de Google Calendar."""
    user_email = _get_ssm("/uniflow/config/user_email")
    access_token = _get_access_token()
    url = f"{CALENDAR_API_BASE}/calendars/{user_email}/events/{event_id}"

    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urllib.request.urlopen(req):
            pass
        print(f"[Calendar] Evento eliminado: {event_id}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[Calendar] Evento no encontrado (ya fue eliminado): {event_id}")
        else:
            raise
