# SMS + LLM Chat

A backend service that receives incoming SMS messages, answers them with an LLM, stores
every conversation, and sends the answer back to the sender. Users can rate an answer by
replying `👍` / `👎`, and an admin can read the full conversation log of any phone number.

It runs end to end with **no external accounts**: the default configuration uses a mock
SMS provider and a mock LLM. Swapping in Twilio or Groq/OpenAI is a change in `.env`, not
in code.

---

## Contents

- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Testing the webhook locally](#testing-the-webhook-locally)
- [Using a real LLM (Groq / OpenAI)](#using-a-real-llm-groq--openai)
- [Using real Twilio](#using-real-twilio)
- [Admin endpoint](#admin-endpoint)
- [API reference](#api-reference)
- [How it works](#how-it-works)
- [Design decisions](#design-decisions)
- [Testing](#testing)
- [Assumptions and tradeoffs](#assumptions-and-tradeoffs)
- [Future improvements](#future-improvements)

---

## Quick start

Requires Python 3.11+ (developed on 3.14).

```bash
python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate

pip install -e ".[dev]"          # dependencies live in pyproject.toml
cp .env.example .env             # optional: the defaults already work

uvicorn app.main:app --reload --port 3000
```

Interactive API docs: <http://localhost:3000/docs>

```bash
curl localhost:3000/health
# {"status":"ok","smsProvider":"mock","llmProvider":"mock","replyMode":"api"}
```

Run the tests:

```bash
pytest          # 157 tests, no network access required
```

---

## Configuration

Everything is environment-driven; see [`.env.example`](.env.example) for a copy-ready
file. With no `.env` at all the service still starts on the mock providers.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `3000` | Port for uvicorn (pass it with `--port`). |
| `LOG_LEVEL` | `INFO` | Root log level. |
| `DEBUG_ENDPOINTS_ENABLED` | `true` | Mounts `GET /debug/outbox`. Turn off outside local dev. |
| `SMS_PROVIDER` | `mock` | `mock` or `twilio`. |
| `SMS_REPLY_MODE` | `api` | `api` (async, reply sent over the REST API) or `twiml` (synchronous XML reply). |
| `TWILIO_ACCOUNT_SID` | — | Required when `SMS_PROVIDER=twilio`. |
| `TWILIO_AUTH_TOKEN` | — | Required for sending **and** for webhook signature validation. |
| `TWILIO_PHONE_NUMBER` | — | The number replies are sent from. |
| `TWILIO_VALIDATE_SIGNATURE` | `true` | Validation also switches itself off when no auth token is set. |
| `LLM_PROVIDER` | `mock` | `mock`, `groq` or `openai`. |
| `LLM_MODEL` | — | Empty means the provider default (`openai/gpt-oss-20b` / `gpt-4o-mini`). |
| `LLM_TIMEOUT_SECONDS` | `20` | Deadline for one generation. |
| `LLM_MAX_RETRIES` | `2` | Retries with exponential backoff (performed by the SDK). |
| `LLM_MAX_OUTPUT_TOKENS` | `300` | Output cap requested from the model. |
| `MAX_REPLY_LENGTH` | `480` | Hard character cap on the SMS text (~3 segments). |
| `GROQ_API_KEY` / `OPENAI_API_KEY` | — | Whichever provider is selected. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/app.db` | Any SQLAlchemy async URL. |
| `CONVERSATION_HISTORY_LIMIT` | `10` | Previous turns of the same number sent as context. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `password` | Credentials for the admin endpoint. |

---

## Testing the webhook locally

With the server running on the default (mock) configuration:

**1. Send a message**

```bash
curl -X POST localhost:3000/webhooks/sms \
  -H "Content-Type: application/json" \
  -d '{
    "from": "+36123456789",
    "body": "How do I reset my password?",
    "messageId": "SM123456789",
    "timestamp": "2026-07-27T12:00:00Z"
  }'
```

```json
{ "status": "accepted", "conversationId": "conv_ac0ef7592098", "reply": null }
```

**2. See the reply the mock provider "sent"**

```bash
curl localhost:3000/debug/outbox
```

```json
[
  {
    "to": "+36123456789",
    "body": "You can reset your password by clicking 'Forgot password' on the login page.",
    "messageId": "MOCK00000001",
    "sentAt": "2026-07-27T12:00:00.214987Z"
  }
]
```

**3. Rate the answer**

```bash
curl -X POST localhost:3000/webhooks/sms \
  -H "Content-Type: application/json" \
  -d '{"from": "+36123456789", "body": "1", "messageId": "SM123456790"}'
```

```json
{
  "status": "feedback",
  "conversationId": "conv_ac0ef7592098",
  "reply": "Thanks for the feedback!"
}
```

`1` and `👍` are equivalent, as are `0` and `👎`. In a shell, prefer the JSON
escape — `"body": "\ud83d\udc4d"` for 👍 and `"\ud83d\udc4e"` for 👎 — because some
Windows terminals mangle emoji on the command line before curl ever sees them. The rating
attaches to the most recent conversation of that number and never reaches the LLM.

**4. Read the conversation log**

```bash
curl -u admin:password \
  "localhost:3000/admin/conversations?phoneNumber=%2B36123456789"
```

The Twilio-format endpoint works locally too — no account and no signature needed while
`TWILIO_AUTH_TOKEN` is unset:

```bash
curl -X POST localhost:3000/webhooks/sms/twilio \
  -d "From=%2B36123456789" -d "Body=How do I reset my password?" -d "MessageSid=SM1"
```

To see the answer come back as XML instead, set `SMS_REPLY_MODE=twiml` and repeat:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response><Message>You can reset your password by clicking 'Forgot password' on the login page.</Message></Response>
```

---

## Using a real LLM (Groq / OpenAI)

Groq and OpenAI expose the same chat-completions protocol, so both are served by one
client with a different base url and default model.

```dotenv
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
# LLM_MODEL=openai/gpt-oss-20b   # optional override
```

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
# LLM_MODEL=gpt-4o-mini
```

Restart the server; nothing else changes. If the key is missing the service fails at
startup with a clear `configuration_error` rather than at the first message.

---

## Using real Twilio

1. Create a [free trial account](https://help.twilio.com/articles/360036052753-Twilio-Free-Trial-Limitations)
   and verify your own phone number (trial accounts can only message verified numbers,
   up to 100 messages/day).
2. Buy or claim a trial phone number with SMS capability.
3. Put the credentials in `.env`:

   ```dotenv
   SMS_PROVIDER=twilio
   TWILIO_ACCOUNT_SID=ACxxxxxxxx
   TWILIO_AUTH_TOKEN=xxxxxxxx
   TWILIO_PHONE_NUMBER=+15550001111
   ```

4. Expose your local server:

   ```bash
   ngrok http 3000
   ```

5. In the Twilio console open **Phone Numbers → Manage → Active numbers → your number**,
   and under *Messaging → A message comes in* set:

   ```
   POST https://<your-ngrok-subdomain>.ngrok-free.app/webhooks/sms/twilio
   ```

6. Text your Twilio number. The answer arrives as a separate SMS.

Signature validation is on as soon as `TWILIO_AUTH_TOKEN` is set: requests without a
valid `X-Twilio-Signature` are rejected with `403`. Because the signature covers the full
URL, the webhook URL configured in Twilio must match exactly what the app sees — behind a
proxy that terminates TLS, forward `X-Forwarded-Proto`/`X-Forwarded-Host` (uvicorn:
`--proxy-headers`), or the check will fail.

**Which reply mode?** `SMS_REPLY_MODE=api` (default) returns `202` immediately and sends
the answer over the REST API — the LLM call is not racing Twilio's ~15 s webhook timeout.
`SMS_REPLY_MODE=twiml` answers inside the webhook response with Messaging Response XML,
which is simpler and needs no outbound credentials, but ties the user's wait to the
model's latency.

---

## Admin endpoint

```bash
curl -u admin:password \
  "localhost:3000/admin/conversations?phoneNumber=%2B36123456789&limit=50&offset=0"
```

```json
{
  "phoneNumber": "+36123456789",
  "count": 1,
  "conversations": [
    {
      "id": "conv_ac0ef7592098",
      "phoneNumber": "+36123456789",
      "incomingMessage": "How do I reset my password?",
      "llmResponse": "You can reset your password by clicking 'Forgot password' on the login page.",
      "providerMessageId": "SM123456789",
      "providerResponseId": "MOCK00000001",
      "status": "completed",
      "deliveryStatus": "sent",
      "feedback": "positive",
      "createdAt": "2026-07-27T12:00:00Z",
      "updatedAt": "2026-07-27T12:00:01Z"
    }
  ]
}
```

The endpoint requires HTTP Basic credentials (`401` without them) **and** the `admin`
role (`403` otherwise). The phone number is normalised, so `0036 123-456-789` and
`+36123456789` return the same conversations.

---

## API reference

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/webhooks/sms` | — | Provider-neutral JSON: `{from, body, messageId, timestamp?}`. |
| `POST` | `/webhooks/sms/twilio` | signature | Twilio form-encoded payload; answers with TwiML. |
| `GET` | `/admin/conversations?phoneNumber=…` | Basic (admin) | Conversation log, newest first. |
| `GET` | `/debug/outbox` | — | What the mock provider "sent" (local dev only). |
| `GET` | `/health` | — | Liveness and the active providers. |

Errors share one shape:

```json
{ "error": { "code": "invalid_phone_number", "message": "…", "requestId": "a1b2c3d4" } }
```

`requestId` also comes back in the `X-Request-ID` header and appears on every log line
produced while handling that request.

---

## How it works

```
POST /webhooks/sms                    ← carrier
        │
        ├─ parse + normalise (SMS provider)      → InboundMessage
        ├─ is it a duplicate delivery?           → 202 "duplicate", stop
        ├─ is it a 👍 / 👎 rating?                → record on the latest conversation, ack, stop
        └─ store the message (status: received)  → 202 "accepted" + conversationId
                │
                └── background task ──────────────────────────────────────
                        ├─ load the last N turns of this number
                        ├─ generate the answer   (LLM provider, timeout + retries)
                        ├─ store it              (status: completed / failed)
                        └─ send it back          (SMS provider, delivery status stored)
```

```
app/
  main.py                    application factory, lifespan, provider wiring
  config.py                  typed settings from the environment
  api/                       routers + dependencies (webhooks, admin, health, debug)
  core/                      errors, JSON logging, phone normalisation, schema base
  domain/                    enums and provider-agnostic models
  services/                  conversation orchestration, feedback classification
  providers/llm/             LLMProvider protocol + mock + OpenAI-compatible (groq/openai)
  providers/sms/             SmsProvider protocol + mock + Twilio
  db/                        ORM model, engine, ConversationRepository (SQL + in-memory)
tests/                       157 tests, all offline
```

### Stored conversation

| Field | Notes |
| --- | --- |
| `id` | `conv_<12 hex>` |
| `phone_number` | E.164, the conversation key |
| `incoming_message` / `llm_response` | question and answer |
| `provider_message_id` | id of the inbound SMS, **unique** — the idempotency key |
| `provider_response_id` | id of the outbound SMS |
| `status` | `received` → `processing` → `completed` \| `failed` |
| `delivery_status` | `queued` \| `sent` \| `delivered` \| `failed` \| `unknown` |
| `feedback` | `none` \| `positive` \| `negative` |
| `feedback_message_id` | id of the SMS that carried the rating, unique — stops a retried rating |
| `error_message` | why a generation or delivery failed |
| `created_at` / `updated_at` | UTC |

---

## Design decisions

**Providers behind `Protocol`s, chosen by a factory.** `LLMProvider` and `SmsProvider`
are structural interfaces; `build_llm_provider()` / `build_sms_provider()` are the only
places that know which vendors exist. The service layer never imports Twilio or the
OpenAI SDK, so a new provider is one file plus one factory line. Vendor exceptions are
translated at the boundary into `LLMError` / `SmsProviderError`.

**Groq and OpenAI share one implementation.** They speak the same wire protocol, so the
difference is a `base_url` and a default model held in a `PRESETS` table. Adding another
OpenAI-compatible vendor (Together, Fireworks, a local vLLM) is a dictionary entry.

**Storage behind a repository.** The service depends on `ConversationRepository`, not on
SQLAlchemy. Two implementations ship: SQLAlchemy (any async dialect — moving to
PostgreSQL is a `DATABASE_URL` change) and in-memory, used by unit tests.

**Store first, answer the carrier, generate afterwards.** The webhook persists the
message and returns `202` with the conversation id, then does the slow work in a
background task. The carrier is never held for the model's latency, and this is exactly
the seam where a real queue (Celery/RQ/SQS) replaces `BackgroundTasks` without touching
the service.

**Idempotency is enforced by the database.** `provider_message_id` is unique, so a
retried webhook cannot create a second conversation or a second LLM call even under a
race — the lookup is the fast path, the constraint is the guarantee. Ratings are not
conversations, so they carry their own unique `feedback_message_id`; without it a
retried `👍` would be acknowledged twice.

**Failures degrade, they don't disappear.** If the LLM fails or times out, the
conversation is stored as `failed` with the reason and the user still receives a fallback
SMS. If sending fails, the generated answer is kept with `delivery_status=failed` so it
can be inspected or retried. Every error the API returns has one shape and a request id.

**Feedback only counts when it is the whole message.** `1` is a rating; `1 more question`
is a question. Skin-tone modifiers are stripped, so `👍🏽` works.

**Authentication and authorization are separate.** `get_current_user` verifies Basic
credentials with `secrets.compare_digest`; `require_admin` checks the role. The role is
constant today, but the endpoint's contract will not change when a user table and JWT
replace the environment variables.

**camelCase on the wire, snake_case in Python.** All responses share a `CamelModel` base,
matching the payload shapes in the assignment.

---

## Testing

```bash
pytest                       # 157 tests
pytest tests/test_webhook_flow.py -v
ruff check app tests
```

No test touches the network. HTTP tests run the **real application** (same factory, same
lifespan, same routers) over ASGI against a temporary SQLite database, with the mock
providers — so the wiring under test is the wiring that ships.

| File | Covers |
| --- | --- |
| `test_webhook_flow.py` | Happy path, stored record, dialogue context, reply truncation, both reply modes |
| `test_twilio_webhook.py` | Twilio form payload, signature verification, TwiML output and escaping |
| `test_feedback.py` | Classification rules, rating the right conversation, no LLM call |
| `test_idempotency.py` | Duplicate and concurrent deliveries, retried ratings |
| `test_error_handling.py` | LLM failure/timeout, SMS failure, storage failure, bad payloads, forged signature |
| `test_admin.py` | 401/403/200, filtering, pagination, end-to-end visibility |
| `test_llm_providers.py` / `test_sms_providers.py` | Provider contracts, factories, error mapping |
| `test_ops.py` | Health, debug outbox on/off, OpenAPI completeness |

---

## Assumptions and tradeoffs

- **Scope was capped at ~4 hours**, as the assignment asks. The list below is what I
  would add next, not what I could not do.
- **Twilio was implemented but not exercised against a live account.** The provider,
  signature validation, status mapping and TwiML rendering are covered by tests that
  compute real Twilio signatures, but no trial number was registered — the mock provider
  is the default so a reviewer can run everything immediately.
- **A real LLM key was not used during development**; the OpenAI-compatible provider is
  covered by tests with an injected client. Point `LLM_PROVIDER` at `groq` with a key to
  use it for real.
- **SQLite by default.** Convenient and zero-setup; the repository abstraction and an
  async SQLAlchemy URL make PostgreSQL a configuration change. SQLite's single writer
  would be the first thing to hit under real concurrency.
- **`BackgroundTasks`, not a queue.** In-process background work is lost if the process
  dies between the `202` and the reply. Acceptable here, wrong for production.
- **Schema is created at startup**, not migrated. Fine for a fresh database, unsafe for
  an evolving one.
- **One admin account from the environment.** No user table, no password hashing, no
  token expiry.
- **Feedback keywords are English-only** and deliberately narrow to avoid swallowing real
  questions.
- **No rate limiting**, so a single number can drive unbounded LLM spend.

## Future improvements

1. **Durable queue** (Celery/RQ/SQS) instead of `BackgroundTasks`, with retries and a
   dead-letter queue for messages whose generation or delivery failed.
2. **Alembic migrations** and a `docker-compose` with PostgreSQL.
3. **Delivery status callbacks** — accept Twilio's `StatusCallback` webhook and move
   conversations from `sent` to `delivered`/`undelivered`.
4. **Retry of failed deliveries**, driven by the stored `delivery_status`.
5. **Rate limiting and spend caps** per phone number.
6. **Real auth**: user table, hashed passwords, JWT/OAuth2 and roles beyond `admin`.
7. **Observability**: OpenTelemetry traces across webhook → LLM → send, plus metrics for
   latency, failure rate and the positive/negative feedback ratio.
8. **Feedback that closes the loop** — feed the negative ratings into prompt evaluation
   instead of only storing them.
9. **Grounded answers** (RAG over a real help centre) rather than a bare system prompt.
10. **CI**: run `pytest` and `ruff` on every push.
