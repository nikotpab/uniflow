<!--
  BORRADOR para AWS Builder Center.
  ⚠️ La versión YA publicada en Builder Center (~340 palabras) NO pasa el
  Completeness Gate de los términos: faltan las secciones "How You Built It"
  y "What You Learned" y no llega a 500 palabras. EDITA el artículo publicado
  y reemplaza el cuerpo completo con este archivo (ya supera las 900 palabras).
  Al publicar:
  1. Agrega el tag #productivity en Builder Center.
  2. Sube 2-3 screenshots: el bot respondiendo en Telegram, un evento creado
     en Google Calendar, y la tabla DynamoDB con tareas extraídas.
  3. Sube docs/architecture.jpg como imagen del diagrama de arquitectura
     (Builder Center no renderiza mermaid).
  Ventana de publicación: 10 de julio 9:00 AM PT — 13 de julio 1:00 PM PT.
-->

# Weekend Productivity Challenge: UniFlow — An AI Assistant That Turns University Emails Into a Calendar That Manages Itself

**Tag:** #productivity

## Vision & What the App Does

A university student's day-to-day is usually pretty hectic — moving between classes, dense lectures, commuting, exams, so many things keep us constantly busy.

At my university (and I'm sure at most others too) there's no option for assignments posted on the digital classroom to be automatically added to your calendar. The process of logging into the platform tends to be tedious: sign in, verify with the Microsoft authenticator, and go hunt for the new assignments section. It doesn't seem like a complicated task, but when you're constantly loaded down with university work, a tool that lets you automate it can give you a bit more peace of mind.

This is where UNIFLOW comes in, a tool that automates that whole process: it connects to your university email, detects messages where professors or academic coordination announce assignments, quizzes, or deadlines, and uses an AI model (Amazon Bedrock, Nova Lite) to automatically extract what the task is, which course it belongs to, and what the due date is. With that information, it creates the event directly in your Google Calendar, reminders included, without you ever having to open the digital classroom or fight with the Microsoft authenticator.

And so you don't even have to depend on checking the calendar, UNIFLOW also has a Telegram bot: you can ask it "what do I have today?", "what's left for this week?", or search for a specific task by course, and it answers instantly. You can also mark tasks as completed or just write to it in natural language, as if you were talking to a personal assistant.

All of this runs serverless on AWS (Lambda, DynamoDB, EventBridge, API Gateway), taking advantage of the free tier, so the cost of keeping it running is practically zero. I built it as a response to a problem I live with every day: I don't need another app to check, I need one that works in the background and only alerts me when it matters.

## How I Built It

I planned the build in five phases: AWS infrastructure, Google OAuth, the email-scanner Lambda, the Telegram bot Lambda, and end-to-end testing. Some key decisions along the way:

- **Zero external dependencies.** Both Lambdas use only the Python 3.12 standard library plus boto3 (already in the runtime). Gmail, Calendar and Telegram are called with plain `urllib` against their REST APIs instead of their SDKs. Deployment packages are a few KB, cold starts are fast, and there's no dependency layer to maintain.
- **Gmail via OAuth 2.0 with a read-only scope.** No IMAP, no stored passwords: the scanner mints short-lived access tokens from a refresh token and requests the narrowest possible scope (`gmail.readonly`), so the app can read the inbox but can never modify, delete or send mail. All secrets live in SSM Parameter Store as `SecureString` values — never in code or environment variables.
- **Data minimalism.** DynamoDB stores only what the assistant needs: the extracted fields (task, course, due date, a short summary) plus the source email's ID and subject for deduplication. Full email bodies are never persisted — they only exist in Lambda memory during the Bedrock call.
- **Telegram instead of a web frontend.** For a weekend project, a bot gives you a polished, mobile-ready UI for free — nothing to install, nothing to host.

The most interesting part was the bugs I found while hardening it:

- **DynamoDB's `Limit` applies *before* `FilterExpression`.** My email deduplication used `scan(FilterExpression=..., Limit=1)`, which can return empty even when a match exists — silently re-creating tasks. The fix: paginate the scan without `Limit`. A classic DynamoDB gotcha I'll never forget.
- **Timezones.** Everything ran on UTC, but I live in Bogotá (UTC-5). At 8 PM my time, "what do I have today?" answered with *tomorrow's* tasks, and calendar events landed five hours early. I standardized on one convention — naive datetimes are Bogotá local time — and wrote a regression test that freezes the clock at 8 PM to prove the boundary case.
- **Trusting an LLM with my deadlines.** A wrong date is worse than no date, so extraction is boxed in: temperature 0.1, a JSON-only prompt that receives today's date as an explicit anchor (so "next Friday" is computed, not guessed), a tolerant parser for when the model wraps the JSON in prose anyway, and deduplication by source email + task subject so re-processing never creates duplicate events. Every task also keeps the ID and subject of the email it came from, so any deadline can be traced back to its source in seconds.
- **Read-only means read-only.** The scanner originally marked emails as read after processing — which returned `403 Forbidden`, because I had deliberately requested only the read-only Gmail scope. Instead of widening the scope, processed emails now leave marker items in DynamoDB, which makes the scanner fully idempotent with zero write access to the inbox.
- **Webhook security.** Anyone who discovers the API Gateway URL could post fake Telegram updates. The deploy script generates a random secret, registers it with Telegram's `setWebhook`, and the Lambda validates the secret header on every request — plus a chat-ID allowlist, since this is a personal assistant.

I also wrote 48 unit tests that run without AWS or even boto3 installed (they inject a fake one), covering Bedrock response parsing, deduplication, timezone windows and bot command dispatch.

## AWS Services Used / Architecture Overview

| Service | Role |
|---|---|
| **Amazon Bedrock (Nova Lite)** | Task extraction from emails + natural-language chat |
| **AWS Lambda** (×2, Python 3.12) | `email-scanner` and `telegram-bot` |
| **Amazon DynamoDB** | `uniflow_tasks` table + GSI on `status`/`due_date` |
| **Amazon EventBridge** | Triggers the scanner every 2 hours |
| **Amazon API Gateway** | HTTPS webhook for Telegram |
| **AWS SSM Parameter Store** | All secrets and configuration |

The flow: every two hours, EventBridge fires the email-scanner Lambda; it pulls new university emails through the Gmail API, sends each body to Bedrock Nova Lite for extraction, saves the tasks to DynamoDB and creates the Google Calendar events. Independently, Telegram pushes each message through API Gateway to the telegram-bot Lambda, which queries DynamoDB and uses Nova Lite to phrase the answer. Everything fits comfortably in the Free Tier.

## What I Learned

- **Nova Lite is remarkably good at structured extraction.** With a low temperature and a strict "JSON array only" prompt, it reliably parses messy, informal emails — including relative dates. I still wrapped parsing in a fallback that slices the first JSON array out of the response, because models occasionally add prose.
- **DynamoDB scan semantics** (`Limit` applies before the filter) — learned the hard way, now pinned by a regression test.
- **Least-privilege IAM is cheap when you start early.** The Lambda role can invoke exactly one model, touch exactly one table, and read exactly one parameter prefix.
- **Serverless is the right shape for personal tools**: no idle cost, nothing to patch, and the whole deployment is four idempotent bash scripts.

## Link to App or Repo

Here's the repo:
**https://github.com/nikotpab/uniflow**

The app itself runs as a Telegram bot. Since it manages my real inbox and calendar, it only answers allowlisted chat IDs — the screenshots show it working live.

---

*Built solo over a weekend for the AWS Build a Productivity App Weekend Challenge.*
