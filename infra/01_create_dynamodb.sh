#!/bin/bash
# Crear tabla DynamoDB para UniFlow
set -e

AWS_REGION="us-east-1"
TABLE_NAME="uniflow_tasks"

echo "📦 Creando tabla DynamoDB: $TABLE_NAME"

aws dynamodb create-table \
  --table-name "$TABLE_NAME" \
  --attribute-definitions \
    AttributeName=task_id,AttributeType=S \
    AttributeName=status,AttributeType=S \
    AttributeName=due_date,AttributeType=S \
  --key-schema \
    AttributeName=task_id,KeyType=HASH \
  --global-secondary-indexes \
    '[
      {
        "IndexName": "status-due_date-index",
        "KeySchema": [
          {"AttributeName": "status", "KeyType": "HASH"},
          {"AttributeName": "due_date", "KeyType": "RANGE"}
        ],
        "Projection": {"ProjectionType": "ALL"}
      }
    ]' \
  --billing-mode PAY_PER_REQUEST \
  --region "$AWS_REGION"

echo "⏳ Esperando a que la tabla esté activa..."
aws dynamodb wait table-exists \
  --table-name "$TABLE_NAME" \
  --region "$AWS_REGION"

echo "✅ Tabla $TABLE_NAME creada exitosamente"
