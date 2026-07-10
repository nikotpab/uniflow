"""
lambda_function.py — Telegram Bot
====================================
Handler principal de la Lambda que recibe los webhooks de Telegram
y delega el procesamiento a telegram_handler.py.

Trigger: API Gateway (POST /webhook)
"""

import hmac
import json
import traceback

from telegram_handler import process_message, _get_ssm_optional


def _webhook_secret_ok(event: dict) -> bool:
    """
    Valida el header X-Telegram-Bot-Api-Secret-Token contra el secreto en SSM.
    Si el parámetro no está configurado, se permite todo (retrocompatible).
    """
    expected = _get_ssm_optional("/uniflow/telegram/webhook_secret")
    if not expected:
        return True

    headers = event.get("headers") or {}
    received = ""
    for key, value in headers.items():
        if key.lower() == "x-telegram-bot-api-secret-token":
            received = value or ""
            break

    return hmac.compare_digest(received, expected)


def handler(event, context):
    """
    Handler principal del webhook de Telegram.

    Telegram envía un POST con el update en el body.
    Siempre devolvemos 200 para evitar que Telegram reintente.
    """
    if not _webhook_secret_ok(event):
        print("[TelegramBot] Webhook rechazado: secret token inválido")
        return {
            "statusCode": 403,
            "body": json.dumps({"ok": False, "error": "forbidden"}),
            "headers": {"Content-Type": "application/json"},
        }

    try:
        # Parsear el body del evento de API Gateway
        body_raw = event.get("body", "{}")
        if isinstance(body_raw, str):
            body = json.loads(body_raw)
        else:
            body = body_raw or {}

        print(f"[TelegramBot] Update recibido: {json.dumps(body)[:300]}")

        # Solo procesamos mensajes de texto (no edits, callbacks, etc.)
        message = body.get("message")
        if not message:
            # Puede ser edited_message, channel_post, etc. — ignorar silenciosamente
            return _ok()

        process_message(message)

    except json.JSONDecodeError as e:
        print(f"[TelegramBot] Error parseando body: {e}")
        # Devolvemos 200 igual para que Telegram no reintente
    except Exception as e:
        print(f"[TelegramBot] Error inesperado: {e}")
        print(traceback.format_exc())
        # Devolvemos 200 igual para evitar bucles de reintentos de Telegram

    return _ok()


def _ok() -> dict:
    """Respuesta 200 OK para Telegram."""
    return {
        "statusCode": 200,
        "body": json.dumps({"ok": True}),
        "headers": {"Content-Type": "application/json"},
    }
