"""
telegram_handler.py
===================
Procesa mensajes y comandos del bot de Telegram.
Maneja los comandos estructurados y delega a Bedrock para texto libre.
"""

import html
import json
import urllib.error
import urllib.request
import urllib.parse
import boto3
from datetime import datetime, timezone, timedelta

from dynamo_client import (
    get_pending_tasks,
    get_tasks_due_today,
    get_tasks_due_this_week,
    search_tasks,
    mark_task_completed,
    find_task_by_partial_name,
)
from bedrock_chat import generate_response
from calendar_client import delete_event


AWS_REGION = "us-east-1"
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

# Zona horaria del usuario (Bogotá, sin DST).
# Los due_date sin timezone se interpretan como hora local.
LOCAL_TZ = timezone(timedelta(hours=-5))


# ─── SSM Helper ───────────────────────────────────────────────────────────────

_ssm_cache: dict = {}

def _get_ssm(name: str) -> str:
    if name in _ssm_cache:
        return _ssm_cache[name]
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    value = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    _ssm_cache[name] = value
    return value


def _get_ssm_optional(name: str) -> str | None:
    """
    Como _get_ssm pero devuelve None si el parámetro no existe.
    La ausencia NO se cachea: el parámetro puede crearse después de que el
    contenedor de la Lambda ya esté caliente (p. ej. activar el allowlist).
    """
    if _ssm_cache.get(name) is not None:
        return _ssm_cache[name]
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    try:
        value = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None
    _ssm_cache[name] = value
    return value


# ─── Telegram API ─────────────────────────────────────────────────────────────

def send_message(chat_id: int, text: str, parse_mode: str = "HTML") -> None:
    """Envía un mensaje a un chat de Telegram."""
    token = _get_ssm("/uniflow/telegram/bot_token")
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Limitar a 4096 chars (límite de Telegram)
    if len(text) > 4096:
        text = text[:4090] + "\n..."

    body = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    payload = json.dumps(body).encode()

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            pass
    except urllib.error.HTTPError as e:
        # Si falla con HTML, reintentar sin parse_mode
        if parse_mode == "HTML":
            send_message(chat_id, text, parse_mode="")
        else:
            raise


# ─── Formateadores ────────────────────────────────────────────────────────────

def _format_task(task: dict, index: int = None) -> str:
    """Formatea una tarea para mostrar en Telegram."""
    now = datetime.now(LOCAL_TZ)

    due_raw = task.get("due_date", "")
    due_display = "Sin fecha"
    days_left_str = ""

    try:
        due_dt = datetime.fromisoformat(due_raw)
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=LOCAL_TZ)
        due_display = due_dt.astimezone(LOCAL_TZ).strftime("%d %b %Y %H:%M")
        # Diferencia en días de calendario (no en bloques de 24h)
        delta = (due_dt.astimezone(LOCAL_TZ).date() - now.date()).days
        if delta < 0:
            days_left_str = " ⚠️ <b>VENCIDA</b>"
        elif delta == 0:
            days_left_str = " 🔴 <b>HOY</b>"
        elif delta == 1:
            days_left_str = " 🟠 mañana"
        elif delta <= 3:
            days_left_str = f" 🟡 en {delta} días"
        else:
            days_left_str = f" 🟢 en {delta} días"
    except Exception:
        pass

    type_emoji = {
        "parcial": "📝", "proyecto": "🚀", "tarea": "📚",
        "quiz": "❓", "laboratorio": "🔬", "exposicion": "🎤", "otro": "📌",
    }.get(task.get("type", "otro"), "📌")

    subject = html.escape(task.get("subject", "Sin título"))
    course = html.escape(task.get("course", ""))
    task_id_short = task.get("task_id", "")[:8]

    prefix = f"{index}. " if index else ""
    line = f"{prefix}{type_emoji} <b>{subject}</b>"
    if course and course != "General":
        line += f"\n   📖 {course}"
    line += f"\n   📅 {due_display}{days_left_str}"
    line += f"\n   <code>{task_id_short}</code>"

    return line


def _format_tasks_list(tasks: list[dict], title: str) -> str:
    """Formatea una lista de tareas para Telegram."""
    if not tasks:
        return f"{title}\n\n✅ No hay tareas en esta categoría."

    lines = [f"<b>{title}</b>\n"]
    for i, task in enumerate(tasks, 1):
        lines.append(_format_task(task, i))
        lines.append("")  # Línea en blanco entre tareas

    lines.append(f"\n<i>Total: {len(tasks)} tarea(s)</i>")
    return "\n".join(lines)


# ─── Handlers de comandos ─────────────────────────────────────────────────────

def _cmd_hoy(chat_id: int) -> None:
    tasks = get_tasks_due_today()
    send_message(chat_id, _format_tasks_list(tasks, "📅 Tareas para hoy"))


def _cmd_semana(chat_id: int) -> None:
    tasks = get_tasks_due_this_week()
    send_message(chat_id, _format_tasks_list(tasks, "📆 Tareas esta semana"))


def _cmd_tareas(chat_id: int) -> None:
    tasks = get_pending_tasks()
    send_message(chat_id, _format_tasks_list(tasks, "📋 Todas las tareas pendientes"))


def _cmd_completar(chat_id: int, args: str) -> None:
    if not args.strip():
        send_message(
            chat_id,
            "⚠️ Usa el formato: <code>/completar nombre de la tarea</code>\n"
            "O el ID corto: <code>/completar abc12345</code>",
        )
        return

    query = args.strip()
    task = find_task_by_partial_name(query)

    if not task:
        send_message(chat_id, f"❌ No encontré ninguna tarea pendiente con: <i>{html.escape(query)}</i>")
        return

    success = mark_task_completed(task["task_id"])
    if not success:
        send_message(chat_id, "❌ No se pudo completar la tarea. Intenta de nuevo.")
        return

    # La tarea completada ya no debe estorbar en el calendario
    calendar_note = ""
    event_id = task.get("calendar_event_id", "")
    if event_id:
        try:
            delete_event(event_id)
            calendar_note = "\n🗑 Evento eliminado de Google Calendar"
        except Exception as e:
            # No bloquear la completación por un fallo del calendario
            print(f"[TelegramHandler] No se pudo eliminar el evento {event_id}: {e}")
            calendar_note = "\n⚠️ No se pudo eliminar el evento del calendario"

    send_message(
        chat_id,
        f"✅ <b>¡Tarea completada!</b>\n\n"
        f"📚 {html.escape(task.get('subject', ''))}\n"
        f"📖 {html.escape(task.get('course', ''))}"
        f"{calendar_note}",
    )


def _cmd_buscar(chat_id: int, args: str) -> None:
    if not args.strip():
        send_message(chat_id, "⚠️ Usa: <code>/buscar [término]</code>")
        return

    results = search_tasks(args.strip())
    send_message(chat_id, _format_tasks_list(results, f'🔍 Resultados para "{html.escape(args.strip())}"'))


def _cmd_start(chat_id: int) -> None:
    send_message(
        chat_id,
        "👋 <b>Hola! Soy UniFlow</b>\n\n"
        "Tu asistente académico personal. Analizo tus correos universitarios "
        "y te ayudo a gestionar tus tareas.\n\n"
        "<b>Comandos disponibles:</b>\n"
        "📅 /hoy — Tareas que vencen hoy\n"
        "📆 /semana — Tareas de los próximos 7 días\n"
        "📋 /tareas — Todas las tareas pendientes\n"
        "✅ /completar [nombre] — Marcar como completada\n"
        "🔍 /buscar [texto] — Buscar una tarea\n\n"
        "También puedes escribirme en <b>lenguaje natural</b>:\n"
        "<i>\"¿Cuándo es el parcial de cálculo?\"</i>\n"
        "<i>\"¿Qué tengo más urgente esta semana?\"</i>",
    )


def _cmd_help(chat_id: int) -> None:
    _cmd_start(chat_id)


# ─── Dispatcher principal ─────────────────────────────────────────────────────

def process_message(message: dict) -> None:
    """
    Procesa un mensaje de Telegram y genera la respuesta adecuada.

    Args:
        message: dict con la estructura del mensaje de Telegram
    """
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return

    # Allowlist opcional: si /uniflow/telegram/allowed_chat_id existe,
    # solo ese chat puede usar el bot (es un asistente personal).
    allowed_chat = _get_ssm_optional("/uniflow/telegram/allowed_chat_id")
    if allowed_chat and allowed_chat.strip() not in ("", "0") and str(chat_id) != allowed_chat.strip():
        print(f"[TelegramHandler] Chat no autorizado: {chat_id}")
        send_message(chat_id, "🔒 Este bot es privado.")
        return

    print(f"[TelegramHandler] Chat {chat_id}: {text[:100]}")

    # Parsear comando y argumentos
    if text.startswith("/"):
        parts = text.split(" ", 1)
        command = parts[0].lower().split("@")[0]  # /cmd@botname → /cmd
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/start": lambda: _cmd_start(chat_id),
            "/help": lambda: _cmd_help(chat_id),
            "/hoy": lambda: _cmd_hoy(chat_id),
            "/semana": lambda: _cmd_semana(chat_id),
            "/tareas": lambda: _cmd_tareas(chat_id),
            "/completar": lambda: _cmd_completar(chat_id, args),
            "/buscar": lambda: _cmd_buscar(chat_id, args),
        }

        handler = handlers.get(command)
        if handler:
            handler()
        else:
            send_message(
                chat_id,
                f"❓ Comando no reconocido: <code>{html.escape(command)}</code>\n"
                "Usa /help para ver los comandos disponibles.",
            )
    else:
        # Texto libre → chat con Bedrock
        try:
            tasks = get_pending_tasks()
            response_text = generate_response(text, tasks)
            send_message(chat_id, response_text)
        except Exception as e:
            print(f"[TelegramHandler] Error en chat Bedrock: {e}")
            send_message(
                chat_id,
                "⚠️ Hubo un error procesando tu mensaje. "
                "Intenta con un comando como /tareas o /hoy.",
            )
