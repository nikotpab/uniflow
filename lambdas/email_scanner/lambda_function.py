"""
lambda_function.py — Email Scanner
====================================
Handler principal de la Lambda que:
1. Lee emails no leídos de nikoo.barbosa@gmail.com
2. Extrae tareas con Bedrock Nova Lite
3. Guarda en DynamoDB (sin duplicados)
4. Crea eventos en Google Calendar
5. Marca emails como leídos

Trigger: EventBridge cada 2 horas
"""

import json
import traceback
import boto3

from gmail_client import get_unread_emails_from_sender, mark_email_as_read
from bedrock_extractor import extract_tasks_from_email
from dynamo_client import save_task, update_calendar_event_id
from calendar_client import create_event_from_task


AWS_REGION = "us-east-1"


def _get_ssm(name: str) -> str:
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def handler(event, context):
    """
    Handler principal de la Lambda.

    El evento puede ser:
    - De EventBridge (ejecución automática): se procesan todos los emails nuevos
    - Manual con {"test": true, "email_id": "..."}: procesa un email específico
    """
    print(f"[EmailScanner] Inicio. Evento: {json.dumps(event)[:200]}")

    sender_email = _get_ssm("/uniflow/config/sender_email")

    stats = {
        "emails_processed": 0,
        "tasks_extracted": 0,
        "tasks_saved": 0,
        "calendar_events_created": 0,
        "errors": [],
    }

    try:
        # Obtener emails no leídos del remitente
        emails = get_unread_emails_from_sender(
            sender_email=sender_email,
            max_results=20,
        )

        if not emails:
            print("[EmailScanner] No hay emails nuevos. Fin.")
            return _response(200, {"message": "No hay emails nuevos", "stats": stats})

        print(f"[EmailScanner] Procesando {len(emails)} emails...")

        for email in emails:
            email_id = email["id"]
            print(f"\n[EmailScanner] Email: {email['subject'][:60]}")

            try:
                # Extraer tareas con Bedrock
                tasks = extract_tasks_from_email(email)
                stats["emails_processed"] += 1
                stats["tasks_extracted"] += len(tasks)

                if not tasks:
                    print(f"[EmailScanner] Sin tareas en este email, marcando como leído")
                    mark_email_as_read(email_id)
                    continue

                # Guardar tareas y crear eventos de calendario
                for task in tasks:
                    try:
                        # Guardar en DynamoDB (deduplicado)
                        task_id, created = save_task(task)

                        if not created:
                            # Ya existía (email re-procesado): no duplicar evento
                            print(f"[EmailScanner] Tarea existente, se omite calendario: {task_id}")
                            continue

                        stats["tasks_saved"] += 1

                        # Crear evento en Google Calendar
                        event_id = create_event_from_task(task)
                        if event_id:
                            update_calendar_event_id(task_id, event_id)
                            stats["calendar_events_created"] += 1

                    except Exception as task_err:
                        error_msg = f"Error en tarea '{task.get('subject', '?')}': {task_err}"
                        print(f"[EmailScanner] ❌ {error_msg}")
                        stats["errors"].append(error_msg)

                # Marcar email como leído solo si se procesó correctamente
                mark_email_as_read(email_id)

            except Exception as email_err:
                error_msg = f"Error procesando email {email_id}: {email_err}"
                print(f"[EmailScanner] ❌ {error_msg}")
                print(traceback.format_exc())
                stats["errors"].append(error_msg)

    except Exception as e:
        error_msg = f"Error general: {e}"
        print(f"[EmailScanner] ❌ {error_msg}")
        print(traceback.format_exc())
        stats["errors"].append(error_msg)
        return _response(500, {"error": error_msg, "stats": stats})

    print(f"\n[EmailScanner] ✅ Completado. Stats: {json.dumps(stats)}")
    return _response(200, {"message": "Procesamiento completado", "stats": stats})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "body": json.dumps(body, ensure_ascii=False),
        "headers": {"Content-Type": "application/json"},
    }
