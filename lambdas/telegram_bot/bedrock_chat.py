"""
bedrock_chat.py
===============
Genera respuestas en lenguaje natural sobre las tareas del usuario
usando Amazon Bedrock Nova Lite.
"""

import json
import re
import boto3
from datetime import datetime, timezone, timedelta


AWS_REGION = "us-east-1"
MODEL_ID = "amazon.nova-lite-v1:0"

# Zona horaria del usuario (Bogotá, sin DST).
LOCAL_TZ = timezone(timedelta(hours=-5))

CHAT_PROMPT = """\
Eres UniFlow, un asistente académico amigable y directo. Tu trabajo es ayudar 
a un estudiante universitario a gestionar sus tareas y actividades académicas.

Hoy es: {today}

Lista de tareas pendientes del estudiante:
{tasks_summary}

Instrucciones:
- Responde en español, de forma concisa y útil
- Si preguntan por fechas, calcula cuántos días faltan desde hoy
- Usa emojis con moderación (1-2 por respuesta máximo)
- Si no hay tareas relevantes para la pregunta, dilo claramente
- Máximo 300 palabras por respuesta
- Si el estudiante dice que completó una tarea, indícale que use /completar [nombre]

Mensaje del estudiante: {user_message}"""


def _format_tasks_for_prompt(tasks: list[dict]) -> str:
    """Formatea las tareas como texto legible para el prompt."""
    if not tasks:
        return "No hay tareas pendientes registradas."

    now = datetime.now(LOCAL_TZ)
    lines = []

    for i, task in enumerate(tasks[:20], 1):  # Máximo 20 tareas en el contexto
        due_raw = task.get("due_date", "")
        days_left = "?"
        try:
            due_dt = datetime.fromisoformat(due_raw)
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=LOCAL_TZ)
            # Diferencia en días de calendario locales
            delta = (due_dt.astimezone(LOCAL_TZ).date() - now.date()).days
            days_left = f"{delta}d" if delta >= 0 else "VENCIDA"
        except Exception:
            pass

        subject = task.get("subject", "Sin título")
        course = task.get("course", "")
        task_type = task.get("type", "")
        priority = task.get("priority", "media")
        task_id = task.get("task_id", "")

        line = f"{i}. [{task_type.upper()}] {subject}"
        if course:
            line += f" ({course})"
        line += f" — vence en {days_left} | prioridad: {priority} | id: {task_id[:8]}"
        lines.append(line)

    return "\n".join(lines)


def generate_response(user_message: str, tasks: list[dict]) -> str:
    """
    Genera una respuesta en lenguaje natural sobre las tareas.

    Args:
        user_message: El mensaje del usuario en Telegram
        tasks: Lista de tareas pendientes de DynamoDB

    Returns:
        Texto de respuesta para enviar al usuario
    """
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    today = datetime.now(LOCAL_TZ).strftime("%A %d de %B de %Y")
    tasks_summary = _format_tasks_for_prompt(tasks)

    prompt = CHAT_PROMPT.format(
        today=today,
        tasks_summary=tasks_summary,
        user_message=user_message,
    )

    body_payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
        "inferenceConfig": {
            "maxTokens": 512,
            "temperature": 0.7,
        },
    }

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(body_payload),
        contentType="application/json",
        accept="application/json",
    )

    response_body = json.loads(response["body"].read())
    text = response_body["output"]["message"]["content"][0]["text"].strip()
    return text
