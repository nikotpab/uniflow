"""
dynamo_client.py
================
CRUD de tareas en DynamoDB para UniFlow.
"""

import uuid
import boto3
from datetime import datetime, timezone, timedelta
from boto3.dynamodb.conditions import Key, Attr


AWS_REGION = "us-east-1"
TABLE_NAME = "uniflow_tasks"

# Zona horaria del usuario. Colombia no tiene DST, así que un offset fijo es seguro.
# Convención: los due_date sin timezone se interpretan como hora local de Bogotá.
LOCAL_TZ = timezone(timedelta(hours=-5))


def _table():
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return dynamodb.Table(TABLE_NAME)


# ─── Escritura ─────────────────────────────────────────────────────────────────

def save_task(task: dict) -> tuple[str, bool]:
    """
    Guarda una tarea en DynamoDB. Evita duplicados por email_id + subject.
    Devuelve (task_id, created): created=False si la tarea ya existía.
    """
    table = _table()

    # Verificar si ya existe una tarea del mismo email con el mismo asunto
    existing = find_task_by_email_and_subject(
        task.get("email_id", ""),
        task.get("subject", ""),
    )
    if existing:
        print(f"[DynamoDB] Tarea ya existe: {task['subject'][:50]} (id: {existing['task_id']})")
        return existing["task_id"], False

    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "task_id": task_id,
        "email_id": task.get("email_id", ""),
        "email_subject": task.get("email_subject", ""),
        "subject": task.get("subject", "Sin título"),
        "course": task.get("course") or "General",
        "due_date": task.get("due_date") or "9999-12-31T23:59:00",
        "description": task.get("description", ""),
        "type": task.get("type", "otro"),
        "priority": task.get("priority", "media"),
        "status": "pending",
        "calendar_event_id": "",
        "created_at": now,
        "updated_at": now,
    }

    table.put_item(Item=item)
    print(f"[DynamoDB] Tarea guardada: {item['subject'][:50]} (id: {task_id})")
    return task_id, True


def update_calendar_event_id(task_id: str, event_id: str) -> None:
    """Actualiza el calendar_event_id de una tarea."""
    table = _table()
    table.update_item(
        Key={"task_id": task_id},
        UpdateExpression="SET calendar_event_id = :eid, updated_at = :now",
        ExpressionAttributeValues={
            ":eid": event_id,
            ":now": datetime.now(timezone.utc).isoformat(),
        },
    )


def mark_task_completed(task_id: str) -> bool:
    """Marca una tarea como completada. Devuelve True si existía."""
    table = _table()
    try:
        table.update_item(
            Key={"task_id": task_id},
            UpdateExpression="SET #s = :completed, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":completed": "completed",
                ":now": datetime.now(timezone.utc).isoformat(),
            },
            ConditionExpression=Attr("task_id").exists(),
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


# ─── Consulta ──────────────────────────────────────────────────────────────────

def get_pending_tasks() -> list[dict]:
    """Devuelve todas las tareas con status=pending, ordenadas por due_date."""
    table = _table()
    response = table.query(
        IndexName="status-due_date-index",
        KeyConditionExpression=Key("status").eq("pending"),
    )
    tasks = response.get("Items", [])
    # Continuar si hay más páginas
    while "LastEvaluatedKey" in response:
        response = table.query(
            IndexName="status-due_date-index",
            KeyConditionExpression=Key("status").eq("pending"),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        tasks.extend(response.get("Items", []))

    return sorted(tasks, key=lambda t: t.get("due_date", "9999"))


def get_tasks_due_today() -> list[dict]:
    """Devuelve tareas pendientes que vencen hoy (hora local de Bogotá)."""
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    all_pending = get_pending_tasks()
    return [t for t in all_pending if t.get("due_date", "").startswith(today)]


def get_tasks_due_this_week() -> list[dict]:
    """Devuelve tareas pendientes que vencen en los próximos 7 días (hora local)."""
    now = datetime.now(LOCAL_TZ)
    week_later = (now + timedelta(days=7)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    all_pending = get_pending_tasks()
    return [
        t for t in all_pending
        if today <= t.get("due_date", "")[:10] <= week_later
    ]


def search_tasks(query: str) -> list[dict]:
    """Busca tareas pendientes cuyo subject o course contenga el query."""
    query_lower = query.lower()
    all_pending = get_pending_tasks()
    return [
        t for t in all_pending
        if query_lower in t.get("subject", "").lower()
        or query_lower in t.get("course", "").lower()
        or query_lower in t.get("description", "").lower()
    ]


def find_task_by_email_and_subject(email_id: str, subject: str) -> dict | None:
    """Busca si ya existe una tarea del mismo email con el mismo asunto."""
    if not email_id:
        return None
    table = _table()
    # Nota: no usar Limit aquí — en DynamoDB, Limit se aplica ANTES del
    # FilterExpression, por lo que podría devolver vacío aunque exista el item.
    kwargs = {
        "FilterExpression": Attr("email_id").eq(email_id) & Attr("subject").eq(subject),
    }
    response = table.scan(**kwargs)
    items = response.get("Items", [])
    while not items and "LastEvaluatedKey" in response:
        response = table.scan(**kwargs, ExclusiveStartKey=response["LastEvaluatedKey"])
        items = response.get("Items", [])
    return items[0] if items else None


def get_task_by_id(task_id: str) -> dict | None:
    """Obtiene una tarea por su ID."""
    table = _table()
    response = table.get_item(Key={"task_id": task_id})
    return response.get("Item")


# ─── Marcadores de emails procesados ──────────────────────────────────────────
# El scope de Gmail es solo-lectura, así que no podemos marcar emails como
# leídos. En su lugar, cada email procesado deja un marcador en la tabla.
# Los marcadores usan status="processed_email", por lo que nunca aparecen en
# el GSI de tareas pendientes (status="pending").

def is_email_processed(email_id: str) -> bool:
    """True si este email ya fue procesado (tiene marcador o tareas guardadas)."""
    if not email_id:
        return False
    table = _table()
    kwargs = {"FilterExpression": Attr("email_id").eq(email_id)}
    response = table.scan(**kwargs)
    while not response.get("Items") and "LastEvaluatedKey" in response:
        response = table.scan(**kwargs, ExclusiveStartKey=response["LastEvaluatedKey"])
    return bool(response.get("Items"))


def save_processed_email_marker(email_id: str, email_subject: str = "") -> None:
    """Registra que un email ya fue procesado (aunque no tuviera tareas)."""
    table = _table()
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(Item={
        "task_id": f"email-marker-{email_id}",
        "email_id": email_id,
        "email_subject": email_subject,
        "status": "processed_email",
        "due_date": "0000-01-01T00:00:00",
        "created_at": now,
        "updated_at": now,
    })
