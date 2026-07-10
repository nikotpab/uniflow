"""
bedrock_extractor.py
====================
Usa Amazon Bedrock (Nova Lite) para extraer tareas académicas del cuerpo
de un email universitario.
"""

import json
import re
import boto3
from datetime import datetime, timezone, timedelta


AWS_REGION = "us-east-1"
MODEL_ID = "amazon.nova-lite-v1:0"

# Zona horaria del usuario (Bogotá, sin DST). Las fechas relativas de los
# emails ("el próximo viernes") se calculan respecto a esta zona.
LOCAL_TZ = timezone(timedelta(hours=-5))

EXTRACTION_PROMPT = """\
Eres un asistente académico especializado en leer correos universitarios.
Analiza el siguiente email y extrae TODAS las tareas, actividades, evaluaciones 
o entregables académicos mencionados.

Para cada tarea encontrada, devuelve un objeto JSON con exactamente estos campos:
- "subject": nombre descriptivo y conciso de la tarea (string)
- "course": materia o asignatura (string, o null si no se menciona)
- "due_date": fecha límite en formato ISO 8601 YYYY-MM-DDTHH:MM:SS (string, o null si no hay fecha)
- "description": descripción breve de qué hay que hacer (string, máximo 200 caracteres)
- "type": uno de: "tarea" | "parcial" | "proyecto" | "quiz" | "laboratorio" | "exposicion" | "otro"
- "priority": uno de: "alta" | "media" | "baja" (basado en la urgencia detectada)

REGLAS IMPORTANTES:
- Si el email no contiene ninguna tarea académica, devuelve exactamente: []
- Si hay fecha pero sin hora, usa T23:59:00 como hora
- Si la fecha es relativa (ej: "próximo lunes"), calcúlala desde hoy: {today}
- Responde ÚNICAMENTE con el array JSON válido, sin texto adicional, sin markdown

Email a analizar:
---
Asunto: {subject}
Remitente: {sender}
Fecha del email: {email_date}

{body}
---"""


def _parse_json_array(raw_text: str) -> list | None:
    """
    Parsea la respuesta del modelo como array JSON.
    Tolera fences de markdown y texto extra alrededor del array.
    Devuelve None si no se pudo obtener una lista.
    """
    # Limpiar markdown si el modelo lo devuelve envuelto
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        pass

    # Fallback: extraer el primer array JSON embebido en texto
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    print(f"[BedrockExtractor] No se pudo parsear JSON. Respuesta raw: {raw_text[:500]}")
    return None


def extract_tasks_from_email(email: dict) -> list[dict]:
    """
    Analiza un email con Bedrock y devuelve lista de tareas extraídas.

    Args:
        email: dict con keys: id, subject, sender, date, body

    Returns:
        Lista de dicts con las tareas extraídas y enriquecidas.
    """
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d (%A)")

    prompt = EXTRACTION_PROMPT.format(
        today=today,
        subject=email.get("subject", ""),
        sender=email.get("sender", ""),
        email_date=email.get("date", ""),
        body=email.get("body", ""),
    )

    body_payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
        "inferenceConfig": {
            "maxTokens": 2048,
            "temperature": 0.1,  # Baja temperatura para respuestas deterministas
        },
    }

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(body_payload),
        contentType="application/json",
        accept="application/json",
    )

    response_body = json.loads(response["body"].read())
    raw_text = response_body["output"]["message"]["content"][0]["text"].strip()

    tasks = _parse_json_array(raw_text)
    if tasks is None:
        return []

    # Enriquecer cada tarea con metadatos del email
    enriched = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if not task.get("subject"):
            continue

        task["email_id"] = email["id"]
        task["email_subject"] = email["subject"]
        task["email_date"] = email["date"]
        enriched.append(task)

    print(f"[BedrockExtractor] Extraídas {len(enriched)} tareas del email: {email['subject'][:60]}")
    return enriched
