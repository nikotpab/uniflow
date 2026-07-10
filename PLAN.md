# UniFlow — Plan de Ejecución

## Visión General

UniFlow es un asistente académico que:
1. Lee correos de `nikoo.barbosa@gmail.com` en la bandeja de `nicolasbarbosagualteros@gmail.com`
2. Usa Amazon Bedrock (Nova Lite) para extraer tareas, fechas y materias
3. Crea eventos automáticamente en Google Calendar
4. Permite consultar y gestionar tareas via bot de Telegram

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                         FUENTES DE ENTRADA                       │
│                                                                   │
│   Gmail (nikoo.barbosa@gmail.com → nicolasbarbosagualteros)      │
│   Telegram (mensajes del usuario)                                │
└───────────────────┬──────────────────────┬───────────────────────┘
                    │                      │
                    ▼                      ▼
┌───────────────────────────┐  ┌─────────────────────────────────┐
│  EventBridge (cada 2h)    │  │  API Gateway (webhook Telegram) │
│  trigger automático       │  │                                 │
└───────────────┬───────────┘  └────────────────┬────────────────┘
                │                               │
                ▼                               ▼
┌───────────────────────────────────────────────────────────────┐
│                        AWS Lambda                              │
│                                                               │
│   lambda_email_scanner.py    lambda_telegram_bot.py           │
│   - Lee Gmail via API        - Recibe mensajes Telegram       │
│   - Llama a Bedrock          - Consulta DynamoDB              │
│   - Guarda en DynamoDB       - Responde con tareas            │
│   - Crea eventos Calendar    - Llama a Bedrock (chat)         │
└──────────┬────────────────────────────┬────────────────────────┘
           │                            │
           ▼                            ▼
┌──────────────────┐        ┌───────────────────────┐
│  Amazon Bedrock  │        │      DynamoDB          │
│  Nova Lite       │        │  Tabla: uniflow_tasks  │
│  - Extrae tareas │        │  - task_id (PK)        │
│  - Chat Q&A      │        │  - subject             │
└──────────────────┘        │  - due_date            │
                            │  - course              │
                            │  - description         │
                            │  - status              │
                            │  - calendar_event_id   │
                            │  - created_at          │
                            └───────────┬────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │  Google Calendar API        │
                          │  nicolasbarbosagualteros@   │
                          └────────────────────────────┘
```

---

## Stack Tecnológico

| Componente | Servicio | Costo estimado |
|---|---|---|
| IA / LLM | Amazon Bedrock Nova Lite | ~$0 (Free Tier / muy barato) |
| Compute | AWS Lambda (Python 3.12) | Gratis (1M req/mes) |
| Base de datos | DynamoDB | Gratis (25GB Free Tier) |
| Scheduler | EventBridge Rules | Gratis (1M eventos/mes) |
| Secrets | AWS SSM Parameter Store | Gratis |
| Email | Gmail API (OAuth2) | Gratis |
| Calendario | Google Calendar API (OAuth2) | Gratis |
| Bot | Telegram Bot API | Gratis |

**Costo total estimado: $0/mes** (dentro de Free Tier)

---

## Estructura de Archivos

```
uniflow/
├── PLAN.md                          # Este archivo
├── README.md                        # Documentación del proyecto
├── infra/
│   ├── deploy.sh                    # Script de deploy completo
│   ├── create_dynamodb.sh           # Crear tabla DynamoDB
│   └── create_eventbridge.sh        # Crear regla EventBridge
├── lambdas/
│   ├── email_scanner/
│   │   ├── lambda_function.py       # Handler principal
│   │   ├── gmail_client.py          # Leer emails de Gmail
│   │   ├── bedrock_extractor.py     # Extraer tareas con IA
│   │   ├── calendar_client.py       # Crear eventos en Calendar
│   │   ├── dynamo_client.py         # CRUD en DynamoDB
│   │   └── requirements.txt
│   └── telegram_bot/
│       ├── lambda_function.py       # Handler webhook Telegram
│       ├── telegram_handler.py      # Procesar comandos
│       ├── bedrock_chat.py          # Chat con IA sobre tareas
│       ├── dynamo_client.py         # CRUD en DynamoDB
│       └── requirements.txt
├── setup/
│   ├── google_oauth_setup.py        # Script para obtener tokens OAuth
│   └── telegram_setup.md            # Instrucciones para crear el bot
└── tests/
    ├── test_extractor.py
    └── sample_email.txt
```

---

## Fases de Construcción

### Fase 1 — Infraestructura AWS (30 min)
- [ ] Crear tabla DynamoDB `uniflow_tasks`
- [ ] Crear IAM role para Lambda con permisos Bedrock + DynamoDB
- [ ] Configurar SSM Parameter Store para secretos
- [ ] Crear regla EventBridge (trigger cada 2 horas)

### Fase 2 — Autenticación Google (45 min)
- [ ] Crear proyecto en Google Cloud Console
- [ ] Habilitar Gmail API y Google Calendar API
- [ ] Configurar OAuth2 credentials
- [ ] Ejecutar script de autorización → guardar refresh_token en SSM
- [ ] Verificar acceso a Gmail y Calendar

### Fase 3 — Lambda Email Scanner (60 min)
- [ ] `gmail_client.py` — leer emails del remitente específico
- [ ] `bedrock_extractor.py` — prompt para extraer tareas
- [ ] `dynamo_client.py` — guardar tareas (deduplicación por email_id)
- [ ] `calendar_client.py` — crear eventos con recordatorio
- [ ] `lambda_function.py` — orquestador principal
- [ ] Deploy y test manual

### Fase 4 — Bot de Telegram (60 min)
- [ ] Crear bot en @BotFather → guardar token en SSM
- [ ] `telegram_handler.py` — comandos: /tareas, /semana, /hoy, /completar
- [ ] `bedrock_chat.py` — respuestas en lenguaje natural
- [ ] `lambda_function.py` — webhook handler
- [ ] Configurar API Gateway + webhook URL en Telegram
- [ ] Deploy y test

### Fase 5 — Pruebas end-to-end (30 min)
- [ ] Enviar email de prueba desde nikoo.barbosa@gmail.com
- [ ] Verificar extracción en DynamoDB
- [ ] Verificar evento en Google Calendar
- [ ] Consultar via Telegram

---

## Diseño del Prompt (Bedrock)

### Para extracción de tareas:
```
Eres un asistente académico. Analiza el siguiente email universitario y extrae 
todas las tareas, actividades o evaluaciones mencionadas.

Para cada tarea encontrada, devuelve un JSON con:
- "subject": nombre descriptivo de la tarea
- "course": materia o asignatura (si se menciona)
- "due_date": fecha límite en formato ISO 8601 (YYYY-MM-DDTHH:MM:SS)
- "description": descripción breve de qué hay que hacer
- "type": "tarea" | "parcial" | "proyecto" | "quiz" | "laboratorio" | "otro"

Si no hay fecha explícita, infiere una razonable o usa null.
Si no hay tareas académicas en el email, devuelve [].

Responde SOLO con el array JSON, sin texto adicional.

Email:
---
{email_content}
---
```

### Para el chat de Telegram:
```
Eres UniFlow, un asistente académico amigable. Tienes acceso a la lista de 
tareas pendientes del estudiante.

Tareas actuales:
{tasks_json}

Responde la pregunta del usuario de forma concisa y útil.
Si pregunta por fechas, calcula los días restantes desde hoy ({today}).
Usa emojis moderadamente. Responde en español.

Pregunta: {user_message}
```

---

## Esquema DynamoDB

**Tabla:** `uniflow_tasks`

| Atributo | Tipo | Descripción |
|---|---|---|
| `task_id` | String (PK) | UUID generado al crear |
| `email_id` | String | ID del email origen (para deduplicar) |
| `subject` | String | Nombre de la tarea |
| `course` | String | Materia |
| `due_date` | String | ISO 8601 |
| `description` | String | Descripción |
| `type` | String | tarea/parcial/proyecto/etc |
| `status` | String | pending/completed |
| `calendar_event_id` | String | ID del evento en Google Calendar |
| `created_at` | String | ISO 8601 timestamp |

---

## Comandos del Bot Telegram

| Comando | Descripción |
|---|---|
| `/hoy` | Tareas que vencen hoy |
| `/semana` | Tareas de los próximos 7 días |
| `/tareas` | Todas las tareas pendientes |
| `/completar [nombre]` | Marcar tarea como completada |
| `/buscar [texto]` | Buscar tarea por nombre |
| Texto libre | Chat con IA sobre tus tareas |

---

## Secretos en SSM Parameter Store

| Parámetro | Contenido |
|---|---|
| `/uniflow/google/client_id` | OAuth2 Client ID |
| `/uniflow/google/client_secret` | OAuth2 Client Secret |
| `/uniflow/google/refresh_token` | Refresh token de OAuth2 |
| `/uniflow/telegram/bot_token` | Token del bot de Telegram |
| `/uniflow/config/sender_email` | nikoo.barbosa@gmail.com |
| `/uniflow/config/user_email` | nicolasbarbosagualteros@gmail.com |

---

## Requisitos Previos (acciones manuales)

1. **Google Cloud Console:**
   - Crear proyecto "UniFlow"
   - Habilitar Gmail API + Google Calendar API
   - Crear credenciales OAuth2 (tipo "Desktop App")
   - Agregar `nicolasbarbosagualteros@gmail.com` como usuario de prueba

2. **Telegram:**
   - Hablar con @BotFather → `/newbot` → guardar el token

3. **AWS:**
   - La cuenta ya está configurada y limpia

---

## Checklist Final

- [ ] Email de nikoo.barbosa@gmail.com → detectado y procesado
- [ ] Tarea extraída correctamente por Bedrock
- [ ] Evento creado en Google Calendar
- [ ] Tarea visible en DynamoDB
- [ ] Bot Telegram responde a /hoy, /semana, /tareas
- [ ] Bot Telegram responde preguntas en lenguaje natural
- [ ] EventBridge ejecuta el scanner cada 2 horas automáticamente
