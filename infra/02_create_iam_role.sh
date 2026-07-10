#!/bin/bash
# Crear IAM role para las Lambdas de UniFlow
set -e

AWS_REGION="us-east-1"
ROLE_NAME="uniflow-lambda-role"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "🔐 Creando IAM role: $ROLE_NAME"

# Trust policy para Lambda
cat > /tmp/uniflow-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Crear el role
aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document file:///tmp/uniflow-trust-policy.json \
  --description "Role para las Lambdas de UniFlow"

# Política de permisos
cat > /tmp/uniflow-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan"
      ],
      "Resource": [
        "arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/uniflow_tasks",
        "arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/uniflow_tasks/index/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel"
      ],
      "Resource": "arn:aws:bedrock:${AWS_REGION}::foundation-model/amazon.nova-lite-v1:0"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters"
      ],
      "Resource": "arn:aws:ssm:${AWS_REGION}:${ACCOUNT_ID}:parameter/uniflow/*"
    }
  ]
}
EOF

# Crear y attachar la política inline
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "uniflow-lambda-policy" \
  --policy-document file:///tmp/uniflow-policy.json

echo "✅ IAM role $ROLE_NAME creado con permisos: Logs, DynamoDB, Bedrock, SSM"
echo "📋 ARN del role: arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# Limpiar archivos temporales
rm /tmp/uniflow-trust-policy.json /tmp/uniflow-policy.json
