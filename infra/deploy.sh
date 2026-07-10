#!/bin/bash
# deploy.sh — Deploy completo de UniFlow a AWS
# Uso: ./infra/deploy.sh
set -e

# ─── Configuración ────────────────────────────────────────────────────────────
AWS_REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/uniflow-lambda-role"
LAMBDA_SCANNER="uniflow-email-scanner"
LAMBDA_BOT="uniflow-telegram-bot"
API_NAME="uniflow-api"
EVENTBRIDGE_RULE="uniflow-email-scan-schedule"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "╔══════════════════════════════════════════════╗"
echo "║        UniFlow — Deploy a AWS                ║"
echo "╚══════════════════════════════════════════════╝"
echo "  Cuenta:  $ACCOUNT_ID"
echo "  Región:  $AWS_REGION"
echo ""

# ─── Paso 1: DynamoDB ─────────────────────────────────────────────────────────
echo "─── [1/6] DynamoDB ──────────────────────────────"
TABLE_EXISTS=$(aws dynamodb describe-table \
  --table-name uniflow_tasks \
  --region "$AWS_REGION" \
  --query "Table.TableStatus" \
  --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$TABLE_EXISTS" = "NOT_FOUND" ]; then
  bash "$SCRIPT_DIR/01_create_dynamodb.sh"
else
  echo "  ✅ Tabla uniflow_tasks ya existe ($TABLE_EXISTS)"
fi

# ─── Paso 2: IAM Role ─────────────────────────────────────────────────────────
echo ""
echo "─── [2/6] IAM Role ──────────────────────────────"
ROLE_EXISTS=$(aws iam get-role \
  --role-name uniflow-lambda-role \
  --query "Role.RoleName" \
  --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$ROLE_EXISTS" = "NOT_FOUND" ]; then
  bash "$SCRIPT_DIR/02_create_iam_role.sh"
else
  echo "  ✅ IAM role uniflow-lambda-role ya existe"
fi

# Esperar a que el role sea utilizable
echo "  ⏳ Esperando propagación del IAM role..."
sleep 10

# ─── Paso 3: Empaquetar Lambdas ───────────────────────────────────────────────
echo ""
echo "─── [3/6] Empaquetando Lambdas ──────────────────"

# Email Scanner
echo "  📦 Empaquetando email_scanner..."
cd "$PROJECT_ROOT/lambdas/email_scanner"
zip -r9q /tmp/email_scanner.zip . -x "*.pyc" -x "__pycache__/*"
echo "  ✅ email_scanner.zip ($(du -sh /tmp/email_scanner.zip | cut -f1))"

# Telegram Bot
echo "  📦 Empaquetando telegram_bot..."
cd "$PROJECT_ROOT/lambdas/telegram_bot"
zip -r9q /tmp/telegram_bot.zip . -x "*.pyc" -x "__pycache__/*"
echo "  ✅ telegram_bot.zip ($(du -sh /tmp/telegram_bot.zip | cut -f1))"

cd "$PROJECT_ROOT"

# ─── Paso 4: Deploy Lambda Email Scanner ─────────────────────────────────────
echo ""
echo "─── [4/6] Lambda Email Scanner ──────────────────"

SCANNER_EXISTS=$(aws lambda get-function \
  --function-name "$LAMBDA_SCANNER" \
  --region "$AWS_REGION" \
  --query "Configuration.FunctionName" \
  --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$SCANNER_EXISTS" = "NOT_FOUND" ]; then
  echo "  🚀 Creando Lambda $LAMBDA_SCANNER..."
  aws lambda create-function \
    --function-name "$LAMBDA_SCANNER" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler lambda_function.handler \
    --zip-file fileb:///tmp/email_scanner.zip \
    --timeout 300 \
    --memory-size 256 \
    --description "UniFlow: Escanea emails y extrae tareas con Bedrock" \
    --region "$AWS_REGION" \
    --output text \
    --query "FunctionName" > /dev/null
  echo "  ✅ Lambda $LAMBDA_SCANNER creada"
else
  echo "  🔄 Actualizando código de $LAMBDA_SCANNER..."
  aws lambda update-function-code \
    --function-name "$LAMBDA_SCANNER" \
    --zip-file fileb:///tmp/email_scanner.zip \
    --region "$AWS_REGION" \
    --output text \
    --query "FunctionName" > /dev/null
  echo "  ✅ Lambda $LAMBDA_SCANNER actualizada"
fi

# ─── Paso 5: Deploy Lambda Telegram Bot ──────────────────────────────────────
echo ""
echo "─── [5/6] Lambda Telegram Bot ───────────────────"

BOT_EXISTS=$(aws lambda get-function \
  --function-name "$LAMBDA_BOT" \
  --region "$AWS_REGION" \
  --query "Configuration.FunctionName" \
  --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$BOT_EXISTS" = "NOT_FOUND" ]; then
  echo "  🚀 Creando Lambda $LAMBDA_BOT..."
  aws lambda create-function \
    --function-name "$LAMBDA_BOT" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler lambda_function.handler \
    --zip-file fileb:///tmp/telegram_bot.zip \
    --timeout 30 \
    --memory-size 128 \
    --description "UniFlow: Bot de Telegram para consultar tareas" \
    --region "$AWS_REGION" \
    --output text \
    --query "FunctionName" > /dev/null
  echo "  ✅ Lambda $LAMBDA_BOT creada"
else
  echo "  🔄 Actualizando código de $LAMBDA_BOT..."
  aws lambda update-function-code \
    --function-name "$LAMBDA_BOT" \
    --zip-file fileb:///tmp/telegram_bot.zip \
    --region "$AWS_REGION" \
    --output text \
    --query "FunctionName" > /dev/null
  echo "  ✅ Lambda $LAMBDA_BOT actualizada"
fi

# Esperar a que las Lambdas estén activas
aws lambda wait function-updated \
  --function-name "$LAMBDA_SCANNER" \
  --region "$AWS_REGION"
aws lambda wait function-updated \
  --function-name "$LAMBDA_BOT" \
  --region "$AWS_REGION"

# ─── Paso 6: API Gateway para Telegram Webhook ───────────────────────────────
echo ""
echo "─── [6/6] API Gateway + EventBridge ────────────"

# Buscar si ya existe la API
API_ID=$(aws apigateway get-rest-apis \
  --region "$AWS_REGION" \
  --query "items[?name=='$API_NAME'].id" \
  --output text 2>/dev/null)

if [ -z "$API_ID" ]; then
  echo "  🚀 Creando API Gateway..."
  API_ID=$(aws apigateway create-rest-api \
    --name "$API_NAME" \
    --description "UniFlow Telegram Webhook" \
    --region "$AWS_REGION" \
    --query "id" \
    --output text)
  echo "  ✅ API creada: $API_ID"
else
  echo "  ✅ API Gateway ya existe: $API_ID"
fi

# Obtener root resource
ROOT_ID=$(aws apigateway get-resources \
  --rest-api-id "$API_ID" \
  --region "$AWS_REGION" \
  --query "items[?path=='/'].id" \
  --output text)

# Crear resource /webhook si no existe
WEBHOOK_ID=$(aws apigateway get-resources \
  --rest-api-id "$API_ID" \
  --region "$AWS_REGION" \
  --query "items[?path=='/webhook'].id" \
  --output text)

if [ -z "$WEBHOOK_ID" ]; then
  WEBHOOK_ID=$(aws apigateway create-resource \
    --rest-api-id "$API_ID" \
    --parent-id "$ROOT_ID" \
    --path-part webhook \
    --region "$AWS_REGION" \
    --query "id" \
    --output text)
  echo "  ✅ Resource /webhook creado"

  # Crear método POST
  aws apigateway put-method \
    --rest-api-id "$API_ID" \
    --resource-id "$WEBHOOK_ID" \
    --http-method POST \
    --authorization-type NONE \
    --region "$AWS_REGION" > /dev/null

  # Integración Lambda
  BOT_ARN="arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${LAMBDA_BOT}"
  aws apigateway put-integration \
    --rest-api-id "$API_ID" \
    --resource-id "$WEBHOOK_ID" \
    --http-method POST \
    --type AWS_PROXY \
    --integration-http-method POST \
    --uri "arn:aws:apigateway:${AWS_REGION}:lambda:path/2015-03-31/functions/${BOT_ARN}/invocations" \
    --region "$AWS_REGION" > /dev/null

  # Permiso para que API Gateway invoque la Lambda
  aws lambda add-permission \
    --function-name "$LAMBDA_BOT" \
    --statement-id apigateway-webhook \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${AWS_REGION}:${ACCOUNT_ID}:${API_ID}/*/POST/webhook" \
    --region "$AWS_REGION" > /dev/null

  # Deploy
  aws apigateway create-deployment \
    --rest-api-id "$API_ID" \
    --stage-name prod \
    --region "$AWS_REGION" > /dev/null

  echo "  ✅ API Gateway desplegado en stage 'prod'"
fi

WEBHOOK_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/prod/webhook"

# ─── EventBridge Schedule ─────────────────────────────────────────────────────
RULE_EXISTS=$(aws events describe-rule \
  --name "$EVENTBRIDGE_RULE" \
  --region "$AWS_REGION" \
  --query "Name" \
  --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$RULE_EXISTS" = "NOT_FOUND" ]; then
  echo "  🚀 Creando regla EventBridge (cada 2 horas)..."
  RULE_ARN=$(aws events put-rule \
    --name "$EVENTBRIDGE_RULE" \
    --schedule-expression "rate(2 hours)" \
    --state ENABLED \
    --description "UniFlow: Escanear emails cada 2 horas" \
    --region "$AWS_REGION" \
    --query "RuleArn" \
    --output text)

  SCANNER_ARN="arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${LAMBDA_SCANNER}"

  # Permiso para que EventBridge invoque la Lambda
  aws lambda add-permission \
    --function-name "$LAMBDA_SCANNER" \
    --statement-id eventbridge-schedule \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "$RULE_ARN" \
    --region "$AWS_REGION" > /dev/null

  # Asociar Lambda como target
  aws events put-targets \
    --rule "$EVENTBRIDGE_RULE" \
    --targets "[{\"Id\": \"1\", \"Arn\": \"${SCANNER_ARN}\"}]" \
    --region "$AWS_REGION" > /dev/null

  echo "  ✅ EventBridge configurado: cada 2 horas"
else
  echo "  ✅ EventBridge ya configurado: $EVENTBRIDGE_RULE"
fi

# ─── Configurar Webhook de Telegram ──────────────────────────────────────────
echo ""
echo "─── Configurando Webhook de Telegram ────────────"
TELEGRAM_TOKEN=$(aws ssm get-parameter \
  --name "/uniflow/telegram/bot_token" \
  --with-decryption \
  --region "$AWS_REGION" \
  --query "Parameter.Value" \
  --output text 2>/dev/null || echo "")

if [ -n "$TELEGRAM_TOKEN" ]; then
  # Secret token: Telegram lo enviará en cada webhook y la Lambda lo valida.
  # Así nadie puede inyectar updates falsos aunque descubra la URL.
  WEBHOOK_SECRET=$(aws ssm get-parameter \
    --name "/uniflow/telegram/webhook_secret" \
    --with-decryption \
    --region "$AWS_REGION" \
    --query "Parameter.Value" \
    --output text 2>/dev/null || echo "")

  if [ -z "$WEBHOOK_SECRET" ]; then
    WEBHOOK_SECRET=$(openssl rand -hex 32)
    aws ssm put-parameter \
      --name "/uniflow/telegram/webhook_secret" \
      --value "$WEBHOOK_SECRET" \
      --type "SecureString" \
      --overwrite \
      --region "$AWS_REGION" \
      --query "Version" \
      --output text > /dev/null
    echo "  🔐 Webhook secret generado y guardado en SSM"
  fi

  RESPONSE=$(curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/setWebhook" \
    -d "url=${WEBHOOK_URL}" \
    -d "secret_token=${WEBHOOK_SECRET}" \
    -d "drop_pending_updates=true")
  echo "  Respuesta: $RESPONSE"
  echo "  ✅ Webhook registrado en Telegram (con secret token)"
else
  echo "  ⚠️  Token de Telegram no encontrado en SSM."
  echo "     Registra el webhook manualmente:"
  echo "     curl -X POST https://api.telegram.org/bot<TOKEN>/setWebhook -d url=$WEBHOOK_URL"
fi

# ─── Resumen ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║           ✅ Deploy completado!                      ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Lambda Email Scanner: arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${LAMBDA_SCANNER}"
echo "  Lambda Telegram Bot:  arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${LAMBDA_BOT}"
echo "  Webhook URL:          $WEBHOOK_URL"
echo "  EventBridge:          Cada 2 horas"
echo ""
echo "  🔜 Próximo paso si no lo has hecho:"
echo "     python3 setup/google_oauth_setup.py"
echo ""

# Limpiar zips temporales
rm -f /tmp/email_scanner.zip /tmp/telegram_bot.zip
