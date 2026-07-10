#!/bin/bash
# Crear parámetros SSM para UniFlow
# Uso: ./03_create_ssm_params.sh
# Los valores sensibles se piden interactivamente
set -e

AWS_REGION="us-east-1"

echo "🔑 Configurando parámetros SSM para UniFlow"
echo "================================================"
echo "Necesitarás tener listos:"
echo "  - Google OAuth2 Client ID y Client Secret (de Google Cloud Console)"
echo "  - Google Refresh Token (se genera con setup/google_oauth_setup.py)"
echo "  - Token del bot de Telegram (de @BotFather)"
echo ""

# Función para crear parámetro SecureString
put_secure() {
  local name=$1
  local value=$2
  aws ssm put-parameter \
    --name "$name" \
    --value "$value" \
    --type "SecureString" \
    --overwrite \
    --region "$AWS_REGION" \
    --query "Version" \
    --output text > /dev/null
  echo "  ✅ $name"
}

# Función para crear parámetro String normal
put_string() {
  local name=$1
  local value=$2
  aws ssm put-parameter \
    --name "$name" \
    --value "$value" \
    --type "String" \
    --overwrite \
    --region "$AWS_REGION" \
    --query "Version" \
    --output text > /dev/null
  echo "  ✅ $name"
}

# Parámetros de configuración (no sensibles)
echo "📝 Guardando configuración básica..."
put_string "/uniflow/config/sender_email" "nikoo.barbosa@gmail.com"
put_string "/uniflow/config/user_email" "nicolasbarbosagualteros@gmail.com"
put_string "/uniflow/config/aws_region" "$AWS_REGION"

# Parámetros sensibles — pedir al usuario
echo ""
echo "🔐 Ahora ingresa los secretos (no se mostrarán en pantalla):"
echo ""

read -p "Google OAuth2 Client ID: " GOOGLE_CLIENT_ID
put_secure "/uniflow/google/client_id" "$GOOGLE_CLIENT_ID"

read -s -p "Google OAuth2 Client Secret: " GOOGLE_CLIENT_SECRET
echo ""
put_secure "/uniflow/google/client_secret" "$GOOGLE_CLIENT_SECRET"

echo ""
echo "⚠️  El Refresh Token de Google se genera DESPUÉS de correr:"
echo "   python3 setup/google_oauth_setup.py"
echo "   Puedes dejarlo vacío ahora y actualizarlo después."
read -s -p "Google Refresh Token (Enter para omitir): " GOOGLE_REFRESH_TOKEN
echo ""

if [ -n "$GOOGLE_REFRESH_TOKEN" ]; then
  put_secure "/uniflow/google/refresh_token" "$GOOGLE_REFRESH_TOKEN"
else
  # Guardar placeholder
  put_secure "/uniflow/google/refresh_token" "PENDING_SETUP"
  echo "  ⚠️  Refresh token pendiente — corre google_oauth_setup.py después"
fi

read -s -p "Telegram Bot Token (de @BotFather): " TELEGRAM_TOKEN
echo ""
put_secure "/uniflow/telegram/bot_token" "$TELEGRAM_TOKEN"

echo ""
echo "✅ Todos los parámetros SSM configurados en $AWS_REGION"
echo ""
echo "Próximo paso: python3 setup/google_oauth_setup.py"
