<!--
  BORRADOR para AWS Builder Center.
  Antes de publicar:
  1. Agrega el tag #productivity en Builder Center.
  2. Sube 2-3 screenshots: el bot respondiendo en Telegram, un evento creado
     en Google Calendar, y la tabla DynamoDB con tareas extraídas.
  3. Builder Center puede no renderizar mermaid: exporta el diagrama del
     README como imagen (captura de pantalla desde GitHub) y súbela.
  Publica entre el 10 de julio 9:00 AM PT y el 13 de julio 1:00 PM PT.
-->

# Weekend Productivity Challenge: UniFlow — An AI Assistant That Turns University Emails Into a Calendar That Manages Itself

**Tag:** #productivity

## Vision & What the App Does

Every week my university sends emails packed with assignments: "the double-integrals worksheet is due Friday at 11:59 PM", "the physics quiz is Thursday in class", "project checkpoint presentation on Monday at 2 PM". Every deadline I've ever missed wasn't because I didn't have time — it was because a deadline lived in an email I read once, at 7 AM, half asleep, and never opened again.

UniFlow fixes that. It is a personal academic assistant that:

1. **Reads my university emails automatically.** Every two hours, a Lambda checks my Gmail inbox for unread messages from my university's sender address.
2. **Extracts assignments with AI.** The email body goes to Amazon Bedrock (Nova Lite) with a prompt that returns structured JSON: task name, course, due date, type (homework, exam, project, quiz, lab) and an inferred priority. It even resolves relative dates like "next Friday" using the email's date.
3. **Creates Google Calendar events.** Each task becomes a color-coded calendar event (red for exams, orange for projects, green for homework) with reminders scaled to its priority.
4. **Answers me on Telegram.** A bot gives me `/hoy` (due today), `/semana` (next 7 days), `/tareas`, `/buscar`, `/completar` — and free-text questions like "what's my most urgent thing this week?", answered by Nova Lite with my real task list as context.

From my perspective as a user, I do nothing. Emails arrive, my calendar fills itself, and when I'm on the bus I ask a Telegram bot what's due. That's the whole point: the productivity tool that requires zero discipline to maintain.

## How I Built It

I planned the build in five phases: AWS infrastructure, Google OAuth, the email-scanner Lambda, the Telegram bot Lambda, and end-to-end testing. Some key decisions:

- **Zero external dependencies.** Both Lambdas use only the Python 3.12 standard library plus boto3 (already in the runtime). Gmail, Calendar and Telegram are called with plain `urllib` against their REST APIs instead of their SDKs. Deployment packages are a few KB, cold starts are fast, and there's no dependency layer to maintain.
- **Secrets in SSM Parameter Store** as `SecureString` — Google OAuth credentials, the refresh token, and the Telegram bot token never touch the code or environment variables.
- **Telegram instead of a web frontend.** For a weekend project, a bot gives me a polished, mobile-ready UI for free.

The interesting challenges were the bugs I found while hardening it:

- **DynamoDB's `Limit` applies *before* `FilterExpression`.** My email deduplication used `scan(FilterExpression=..., Limit=1)`, which can return empty even when a match exists — silently re-creating tasks. The fix: paginate the scan without `Limit`. A classic DynamoDB gotcha I'll never forget.
- **Timezones.** Everything ran on UTC, but I live in Bogotá (UTC-5). At 8 PM my time, `/hoy` answered with *tomorrow's* tasks, and calendar events landed 5 hours early. I standardized on a convention — naive datetimes are Bogotá local time — and wrote a regression test that freezes the clock at 8 PM to prove the boundary case.
- **Webhook security.** Anyone who discovers the API Gateway URL could post fake Telegram updates. The deploy script now generates a random secret, registers it with Telegram's `setWebhook`, and the Lambda validates the `X-Telegram-Bot-Api-Secret-Token` header (with constant-time comparison), plus a chat-ID allowlist since this is a personal assistant. I verified it live: a forged update with a valid secret but a foreign chat ID gets "🔒 This bot is private."
- **Read-only means read-only.** The scanner originally marked emails as read after processing — which returned `403 Forbidden`, because I had deliberately requested only the `gmail.readonly` scope. Instead of widening the scope, I kept the privacy guarantee: processed emails now leave marker items in DynamoDB (`status=processed_email`, invisible to the bot's task queries), making the scanner fully idempotent without any write access to my inbox.

I also wrote 33 unit tests that run without AWS or even boto3 installed, by injecting a fake boto3 into `sys.modules` — they cover the Bedrock response parsing, deduplication, timezone windows and bot command dispatch.

## AWS Services Used / Architecture Overview

| Service | Role |
|---|---|
| **Amazon Bedrock (Nova Lite)** | Task extraction from emails + natural-language chat |
| **AWS Lambda** (×2, Python 3.12) | `email-scanner` and `telegram-bot` |
| **Amazon DynamoDB** | `uniflow_tasks` table + GSI on `status`/`due_date` |
| **Amazon EventBridge** | Triggers the scanner every 2 hours |
| **Amazon API Gateway** | HTTPS webhook for Telegram |
| **AWS SSM Parameter Store** | All secrets and configuration |

Flow: EventBridge → email-scanner Lambda → Gmail API → Bedrock Nova Lite → DynamoDB + Google Calendar. In parallel: Telegram → API Gateway → telegram-bot Lambda → DynamoDB + Bedrock → reply. Everything fits comfortably in the Free Tier; my estimated cost is $0/month.

*(Architecture diagram: see the README in the repo.)*

## What I Learned

- **Nova Lite is remarkably good at structured extraction.** With a low temperature and a strict "JSON array only" prompt, it reliably parses messy, informal Spanish emails — including relative dates. Still, I wrapped parsing in a fallback that slices the first `[`…`]` block, because models occasionally add prose.
- **DynamoDB scan semantics** (`Limit` before filter) — learned it the hard way, now verified by a test.
- **Least-privilege IAM is cheap when you start early.** The Lambda role can invoke exactly one model, touch exactly one table, and read exactly one parameter prefix.
- **Serverless is the right shape for personal tools**: no idle cost, no servers to patch, and the whole deployment is four idempotent bash scripts.

## Link to Repo

Source code, architecture diagram, deploy scripts and tests:
**https://github.com/nikotpab/uniflow**

---

*Built solo over a weekend for the AWS Build a Productivity App Weekend Challenge.*
