"""
dynamo_client.py — telegram_bot
================================
CRUD de tareas en DynamoDB para la Lambda del bot de Telegram.
(Misma lógica que email_scanner/dynamo_client.py, incluida por separado
para que cada Lambda sea un paquete de despliegue independiente.)
"""

import re
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


def get_pending_tasks() -> list[dict]:
    """Devuelve todas las tareas con status=pending, ordenadas por due_date."""
    table = _table()
    response = table.query(
        IndexName="status-due_date-index",
        KeyConditionExpression=Key("status").eq("pending"),
    )
    tasks = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.query(
            IndexName="status-due_date-index",
            KeyConditionExpression=Key("status").eq("pending"),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        tasks.extend(response.get("Items", []))
    return sorted(tasks, key=lambda t: t.get("due_date", "9999"))


def get_tasks_due_today() -> list[dict]:
    """Tareas pendientes que vencen hoy (hora local de Bogotá)."""
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    return [t for t in get_pending_tasks() if t.get("due_date", "").startswith(today)]


def get_tasks_due_this_week() -> list[dict]:
    """Tareas pendientes que vencen en los próximos 7 días (hora local)."""
    now = datetime.now(LOCAL_TZ)
    week_later = (now + timedelta(days=7)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    return [
        t for t in get_pending_tasks()
        if today <= t.get("due_date", "")[:10] <= week_later
    ]


def search_tasks(query: str) -> list[dict]:
    query_lower = query.lower()
    return [
        t for t in get_pending_tasks()
        if query_lower in t.get("subject", "").lower()
        or query_lower in t.get("course", "").lower()
        or query_lower in t.get("description", "").lower()
    ]


def mark_task_completed(task_id: str) -> bool:
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


def find_task_by_partial_name(name: str) -> dict | None:
    """
    Busca la primera tarea pendiente cuyo subject contenga el nombre dado.
    También acepta el ID corto (prefijo hex del task_id) que muestra el bot.
    Si el ID no coincide con nada, se cae al buscador por nombre.
    """
    query = name.strip().lower()

    # ¿Parece un ID corto? (prefijo de un uuid: solo hex y guiones, ≥6 chars)
    if re.fullmatch(r"[0-9a-f][0-9a-f-]{5,}", query):
        for task in get_pending_tasks():
            if task.get("task_id", "").lower().startswith(query):
                return task

    results = search_tasks(name)
    return results[0] if results else None
