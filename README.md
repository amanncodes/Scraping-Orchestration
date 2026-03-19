# SOP — Scraping Orchestration Platform

A Django-based backend that acts as middleware between clients requesting scraped Instagram data and multiple scraper backends. SOP handles job queuing, deduplication, caching, dual-path scraping with automatic fallback, async delivery, and result storage.

---

## Architecture

```
CLIENT
    |
    | POST /api/v1/jobs
    v
+------------------------------------------+
|            SOP Django API                |  localhost:8000
|                                          |
|  1. Extract post_id from URL             |
|  2. Check Redis cache (instant return)   |
|  3. Check Postgres dedup                 |
|  4. Create Job record                    |
|  5. Push message to AWS SQS             |
|  6. Schedule Celery fallback timer (90s) |
+------------------------------------------+
    ^                    |
    |                    | SQS message
    |                    v
    |             AWS Lambda
    |             (GraphQL scraper, no cookies)
    |                    |
    | POST /api/v1/       | callback_url
    | webhook/receive    |
    +--------------------+
    |
    | -- IF Lambda does not callback within 90s --
    |
    | Celery fallback_to_login_bot task fires
    |         |
    |         v
    |   login-bot-1 (office PC, public dev tunnel)
    |   Cookie-based DOM scraper
    |         |
    | POST /api/v1/webhook/receive
    |
    v
+------------------------------------------+
|         SOP Webhook Receiver             |
|  Store raw payload in MongoDB            |
|  Update Job status to payload            |
|  Write Redis cache (24hr TTL)            |
|  Mark payload as delivered               |
+------------------------------------------+
```

### Scraper priority

| Priority | Scraper | Trigger |
|---|---|---|
| 1st | AWS Lambda via SQS | Every job, immediately |
| 2nd | login-bot-1 (office PC) | If Lambda does not respond in 90s, or if SQS push fails |

---

## Tech Stack

| Component | Purpose |
|---|---|
| Django 4.2 | REST API — job submission, webhook receiver, status polling |
| Celery 5.3 | Async task queue — fallback timer, webhook delivery with retries |
| Redis 7 | Celery broker and Instagram post cache |
| PostgreSQL 15 | Job records — status, timestamps, dedup tracking |
| MongoDB 7 | Raw scraped payloads |
| AWS SQS | Primary scrape trigger — fire-and-forget message queue to Lambda |
| AWS Lambda | Primary scraper — Instagram GraphQL API |
| login-bot-1 | Fallback scraper — cookie-based DOM scraper on office PC |
| boto3 | AWS SDK — pushes messages to SQS |
| httpx | HTTP client — SOP calling login-bot-1 |
| ngrok | Public tunnel — exposes local SOP webhook to Lambda callbacks |
| Docker Compose | Six-service local dev environment |

---

## Project Structure

```
sop/
├── sop/
│   ├── settings.py         # All config: DB, Redis, Mongo, Celery, AWS SQS
│   ├── urls.py             # Root URL routing
│   └── celery.py           # Celery app definition
├── jobs/
│   ├── models.py           # Job model
│   ├── views.py            # JobSubmitView and JobStatusView
│   ├── serializers.py      # SubmitSerializer
│   ├── urls.py             # /api/v1/jobs routes
│   └── tasks.py            # fallback_to_login_bot and deliver_webhook tasks
├── webhook/
│   └── views.py            # WebhookReceiveView
├── services/
│   ├── instagram.py        # URL parser — extract_post_id()
│   ├── cache.py            # Redis check / write / bust
│   ├── payload_store.py    # MongoDB store / fetch / mark_sent
│   └── event_logger.py     # log_event / log_error
├── mock_scraper/           # Flask mock server for local testing
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── .env
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Data Schemas

### PostgreSQL — jobs table

The jobs table is the single source of truth for every scrape request. One row per job.

```
Column          Type                  Nullable  Default
-----------     -------------------   --------  -------
id              uuid                  NOT NULL  gen_random_uuid()
url             text                  NOT NULL
post_id         varchar(64)           NULL
platform        varchar(32)           NOT NULL  'instagram'
status          varchar(16)           NOT NULL  'queued'
source          varchar(32)           NOT NULL  'live'
storage_ref     varchar(64)           NULL
error_summary   text                  NULL
created_at      timestamp             NOT NULL  now()
started_at      timestamp             NULL
completed_at    timestamp             NULL
```

Field details:

| Field | Description |
|---|---|
| id | UUID primary key, auto-generated on creation |
| url | Full Instagram URL as submitted by the client |
| post_id | Shortcode extracted from the URL (e.g. DT2LomWk2-R). Used for dedup and cache lookup. |
| platform | Always "instagram" currently. Designed to support other platforms later. |
| status | Current state of the job. See status values below. |
| source | Which scraper path was used. See source values below. |
| storage_ref | MongoDB ObjectId string pointing to the raw payload document. Null until webhook received. |
| error_summary | Human-readable error description. Only populated when status is "failed". |
| created_at | Timestamp when the job was first submitted. |
| started_at | Timestamp when SQS or login-bot-1 was successfully contacted. |
| completed_at | Timestamp when the webhook callback was received. |

Status values:

| Value | Meaning |
|---|---|
| queued | Job created, scraper has been contacted |
| processing | Scraper acknowledged the job, waiting for callback |
| payload | Scraping complete, data stored in MongoDB |
| failed | All scrapers failed or scraper returned success=false |
| cached | Returned instantly from Redis or Postgres cache, no scraper called |

Source values:

| Value | Meaning |
|---|---|
| sqs | SQS push succeeded, Lambda is scraping |
| login_bot_immediate | SQS push failed, login-bot-1 triggered immediately as first resort |
| login_bot_fallback | SQS succeeded but Lambda timed out, Celery fired login-bot-1 after 90s |
| cached | Returned from cache, no scraper involved |
| live | Legacy value from before SQS integration |

Indexes:

```sql
-- Primary key
PRIMARY KEY (id)

-- Used by dedup check (filter by post_id + platform, exclude failed)
INDEX ON jobs (post_id, platform)

-- Used by ordering
INDEX ON jobs (created_at DESC)
```

---

### MongoDB — sop database, payloads collection

One document per completed scrape. The raw payload from Lambda or login-bot-1 is stored here exactly as received, with metadata added by SOP.

```json
{
    "_id": "ObjectId (auto-generated)",
    "job_id": "string — UUID matching jobs.id in Postgres",
    "platform": "string — e.g. instagram",
    "stored_at": "ISODate — when SOP stored this document",
    "webhook_sent_at": "ISODate or null — when Celery delivered to caller",
    "data": {
        "job_id": "string",
        "success": "boolean",
        "retry_loop": "boolean",
        "comments": [
            {
                "job_id": "string",
                "data": {
                    "comment_id": "string",
                    "parent_comment_id": "string",
                    "platform": "instagram",
                    "type": "comment or reply",
                    "text": "string — comment body",
                    "media": [],
                    "profile_image": "string — URL",
                    "profile_name": "string — display name",
                    "profile_username": "string — @handle",
                    "profile_meta_data": {
                        "user_id": "string",
                        "is_verified": "boolean"
                    },
                    "comment_meta_data": {
                        "child_comment_count": "integer"
                    },
                    "reply_count": "integer",
                    "likes_count": "integer",
                    "comment_url": "string — direct link to comment",
                    "post_url": "string — original post URL",
                    "commented_at": "ISO8601 string",
                    "scrapped_at": "ISO8601 string"
                }
            }
        ],
        "message": "string",
        "error": "string or empty"
    }
}
```

The storage_ref on the Job record in Postgres is the string representation of this document's _id. SOP uses it to fetch the payload on demand without scanning the collection.

---

### Redis — cache schema

Key format: `cache:instagram:{post_id}`

Value: JSON string
```json
{
    "storage_ref": "MongoDB ObjectId string",
    "coverage": null
}
```

TTL: 86400 seconds (24 hours) by default. Configurable via CACHE_TTL_INSTAGRAM in .env.

When a job completes successfully, SOP writes this key. On subsequent requests for the same URL, SOP reads this key, fetches the MongoDB document using storage_ref, and returns the payload without contacting any scraper.

To inspect cache keys:
```bash
docker compose exec redis redis-cli KEYS "cache:*"
docker compose exec redis redis-cli GET "cache:instagram:DT2LomWk2-R"
```

To manually bust a cache entry:
```bash
docker compose exec redis redis-cli DEL "cache:instagram:DT2LomWk2-R"
```

---

## Deduplication and Caching

SOP has two layers of protection against redundant scraping. Both run before any scraper is contacted.

### Layer 1 — Redis cache (fastest)

On every job submission, SOP checks Redis for a key matching `cache:instagram:{post_id}`. If found, the job is returned immediately with status "cached" and source "redis_cache". The existing MongoDB payload is fetched and returned in the response body. No new Job record is created for the scrape itself — only a lightweight cached Job is recorded for audit purposes.

This layer covers the case where the same post was scraped recently (within 24 hours). It is the primary performance optimization.

### Layer 2 — Postgres dedup (in-flight protection)

If the Redis cache misses, SOP queries Postgres for any existing non-failed job with the same post_id and platform. This covers two sub-cases:

**Sub-case A — Job already has payload (older than cache TTL or cache was busted):**
The existing storage_ref is used to fetch the MongoDB document and return the payload immediately. Status is "cached", source is "db_cache".

**Sub-case B — Job is currently queued or processing:**
A scrape is already in progress for this post. SOP returns 202 with the existing job_id and a message saying the scrape is already running. The caller can poll that job_id for the result. No duplicate scrape job is triggered.

Failed jobs are explicitly excluded from the dedup query. This means if a scrape failed, the next request for the same URL will trigger a fresh scrape attempt.

### Dedup query (simplified)

```python
existing = Job.objects.filter(
    post_id=post_id,
    platform=platform
).exclude(
    status=JobStatus.FAILED
).order_by("-created_at").first()
```

### Full flow with both layers

```
POST /api/v1/jobs { url, platform }
    |
    v
Extract post_id from URL
    |
    v
Redis GET cache:instagram:{post_id}
    |--- HIT --> return 200 with cached payload (source: redis_cache)
    |
    v MISS
Postgres: find non-failed job with same post_id
    |--- FOUND, status=payload --> return 200 with payload (source: db_cache)
    |--- FOUND, status=queued/processing --> return 202 "already in progress"
    |
    v NOT FOUND
Create new Job, push to SQS, schedule fallback
    |
    v
return 202 queued
```

---

## API Endpoints

### POST /api/v1/jobs

Submit a new scrape job.

**Request:**
```
POST /api/v1/jobs
Content-Type: application/json

{
    "url": "https://www.instagram.com/p/DT2LomWk2-R/",
    "platform": "instagram"
}
```

The url field must be a valid Instagram URL in one of these formats:
- `https://www.instagram.com/p/{shortcode}/`
- `https://www.instagram.com/reel/{shortcode}/`
- `https://www.instagram.com/reels/{shortcode}/`
- `https://www.instagram.com/tv/{shortcode}/`

Query parameters and utm tags in the URL are ignored. Only the shortcode is extracted.

**Response — 202 Accepted (SQS path):**
```json
{
    "job_id": "36210b6a-5709-4415-a938-361649195406",
    "status": "queued",
    "source": "sqs",
    "message": "Scrape job sent to Lambda via SQS for post DT2LomWk2-R. Fallback to login-bot-1 in 90s if no response."
}
```

**Response — 202 Accepted (login-bot-1 immediate fallback):**
```json
{
    "job_id": "36210b6a-5709-4415-a938-361649195406",
    "status": "queued",
    "source": "login_bot",
    "message": "SQS unavailable. Scrape job relayed directly to login-bot-1 for post DT2LomWk2-R."
}
```

**Response — 200 OK (Redis cache hit):**
```json
{
    "job_id": "99d87c9f-63d8-4509-8917-6934b301d05c",
    "status": "cached",
    "source": "redis_cache",
    "message": "Already scraped. Returning cached payload.",
    "payload": {
        "comments": [...]
    }
}
```

**Response — 200 OK (Postgres cache hit):**
```json
{
    "job_id": "99d87c9f-63d8-4509-8917-6934b301d05c",
    "status": "cached",
    "source": "db_cache",
    "message": "Already scraped. Returning cached payload.",
    "payload": {
        "comments": [...]
    }
}
```

**Response — 202 Accepted (dedup — already in progress):**
```json
{
    "job_id": "99d87c9f-63d8-4509-8917-6934b301d05c",
    "status": "processing",
    "message": "Scrape already in progress for this post."
}
```

**Response — 422 Unprocessable Entity (bad URL):**
```json
{
    "error": "Cannot extract a valid Instagram post ID from this URL. Supported: /p/{id}/, /reel/{id}/, /tv/{id}/"
}
```

**Response — 400 Bad Request (validation error):**
```json
{
    "url": ["This field is required."],
    "platform": ["This field is required."]
}
```

**Response — 502 Bad Gateway (all scrapers unreachable):**
```json
{
    "error": "All scrapers unreachable. Job marked as failed."
}
```

---

### GET /api/v1/jobs/{job_id}

Poll the status of an existing job.

**Request:**
```
GET /api/v1/jobs/36210b6a-5709-4415-a938-361649195406
```

**Response — 200 OK (job still running):**
```json
{
    "id": "36210b6a-5709-4415-a938-361649195406",
    "post_id": "DT2LomWk2-R",
    "url": "https://www.instagram.com/p/DT2LomWk2-R/",
    "platform": "instagram",
    "status": "processing",
    "source": "sqs",
    "error_summary": null,
    "created_at": "2026-03-19T07:29:20.379008",
    "started_at": "2026-03-19T07:29:21.378863",
    "completed_at": null,
    "message": "Scraper is running."
}
```

**Response — 200 OK (job complete, payload ready):**
```json
{
    "id": "36210b6a-5709-4415-a938-361649195406",
    "post_id": "DT2LomWk2-R",
    "url": "https://www.instagram.com/p/DT2LomWk2-R/",
    "platform": "instagram",
    "status": "payload",
    "source": "sqs",
    "error_summary": null,
    "created_at": "2026-03-19T07:29:20.379008",
    "started_at": "2026-03-19T07:29:21.378863",
    "completed_at": "2026-03-19T07:30:05.123456",
    "message": "Scrape complete. Payload ready.",
    "payload": {
        "comments": [...]
    }
}
```

**Response — 200 OK (job failed):**
```json
{
    "id": "36210b6a-5709-4415-a938-361649195406",
    "status": "failed",
    "source": "login_bot_fallback",
    "error_summary": "Lambda timed out after 90s and login-bot-1 fallback also failed: Connection refused",
    "message": "Failed. Lambda timed out after 90s and login-bot-1 fallback also failed: Connection refused"
}
```

**Response — 404 Not Found:**
```json
{
    "error": "Job not found."
}
```

---

### POST /api/v1/webhook/receive

Called by Lambda or login-bot-1 when scraping completes. This endpoint is also used to manually simulate scraper callbacks during testing.

**Request:**
```json
{
    "job_id": "36210b6a-5709-4415-a938-361649195406",
    "success": true,
    "retry_loop": false,
    "comments": [
        {
            "job_id": "36210b6a-5709-4415-a938-361649195406",
            "data": {
                "comment_id": "18012345678901234",
                "parent_comment_id": "",
                "platform": "instagram",
                "type": "comment",
                "text": "Great post!",
                "profile_username": "someuser",
                "profile_name": "Some User",
                "likes_count": 12,
                "reply_count": 2,
                "commented_at": "2026-03-15T10:23:00+00:00",
                "scrapped_at": "2026-03-19T07:30:00+00:00"
            }
        }
    ],
    "message": "",
    "error": ""
}
```

**Request :**
```json
[
    {
        "job_id": "36210b6a-5709-4415-a938-361649195406",
        "success": true,
        "comments": [...]
    }
]
```

**Request — Shape B (direct data format, mock scraper):**
```json
{
    "job_id": "36210b6a-5709-4415-a938-361649195406",
    "platform": "instagram",
    "status": "success",
    "data": {
        "comments": [...]
    }
}
```

**Request — Failure callback:**
```json
{
    "job_id": "36210b6a-5709-4415-a938-361649195406",
    "success": false,
    "error": "Rate limited by Instagram after 5 retries",
    "retry_loop": false,
    "comments": []
}
```

**Response — 200 OK (success):**
```json
{
    "message": "Payload received and stored."
}
```

**Response — 200 OK (failure recorded):**
```json
{
    "message": "Recorded as failed."
}
```

**Response — 400 Bad Request (missing job_id):**
```json
{
    "error": "Missing job_id in payload."
}
```

**Response — 404 Not Found:**
```json
{
    "error": "Job not found."
}
```

**Source detection logic in the webhook:**

The webhook inspects the payload shape to correct the source field if needed. The presence of a "retry_loop" key identifies a login-bot-1 callback. The presence of a "data" key without "retry_loop" identifies a Lambda or mock callback (Shape B). If the job source is still "sqs" but the callback came from login-bot-1, the source is updated to "login_bot_fallback" to accurately reflect which scraper delivered the result.

---

### GET /webhook/health (login-bot-1 health check)

Used to verify login-bot-1 is reachable before testing integration.

```
GET https://m6mff1b9-8000.inc1.devtunnels.ms/webhook/health/
```

Response:
```json
{
    "status": "healthy",
    "service": "cookie-provider-webhook"
}
```

---

### GET /mock-scraper/health (mock scraper health check)

Used to verify the local mock scraper container is running.

```
GET http://localhost:9000/health
```

Response:
```json
{
    "status": "ok",
    "service": "mock-scraper"
}
```

---

## Celery Tasks

### fallback_to_login_bot

Scheduled automatically 90 seconds after every SQS push. Checks if Lambda already delivered the payload. If yes, exits silently. If no, triggers login-bot-1 as fallback.

- Queue: sop.scrape
- Max retries: 0
- Countdown: SQS_FALLBACK_TIMEOUT seconds (default 90)

Logic:
```
1. Load job from Postgres
2. If status is payload, failed, or cached: log "Lambda succeeded" and exit
3. If status is still processing: Lambda timed out
4. POST to login-bot-1 /webhook/trigger-job/
5. If login-bot-1 accepts: update job.source to "login_bot_fallback"
6. If login-bot-1 also fails: mark job as failed with full error summary
```

### deliver_webhook

Delivers the scraped payload to the caller's configured webhook URL.

- Queue: sop.webhook
- Max retries: 3
- Retry schedule: 30s, 2 minutes, 10 minutes (30 x 4^n)

---

## Environment Variables

```env
DEBUG=True
SECRET_KEY=your-secret-key

# Databases
DATABASE_URL=postgres://sop:sop@postgres:5432/sop
REDIS_URL=redis://redis:6379/0
MONGO_URL=mongodb://mongo:27017
MONGO_DB=sop

# login-bot-1 public URL (office PC via Microsoft Dev Tunnel)
SCRAPER_URL=https://m6mff1b9-8000.inc1.devtunnels.ms

# SOP public webhook URL (ngrok tunnel — must be running for Lambda callbacks)
SOP_WEBHOOK_URL=https://YOUR-NGROK-URL.ngrok-free.dev/api/v1/webhook/receive

# Cache
CACHE_TTL_INSTAGRAM=86400

# AWS SQS
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-south-1
SQS_QUEUE_URL=https://sqs.ap-south-1.amazonaws.com/214118273758/insta-comments-queue

# Seconds to wait for Lambda before falling back to login-bot-1
# Set to 10 or 15 when testing the fallback path
SQS_FALLBACK_TIMEOUT=90
```

---

## Running with Docker Compose

Start ngrok before anything else. Lambda needs a public URL to POST callbacks to:

```bash
ngrok http 8000
```

Copy the https URL from ngrok output and set it as SOP_WEBHOOK_URL in .env.

Start all six services:

```bash
docker compose down
docker compose up --build
```

Verify the worker is connected:

```bash
docker compose logs worker --tail=20
# Look for: celery@... ready.
```

---

## Running with Plain Python

Use this approach during active development. Faster restarts, no rebuild needed.

```bash
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # Mac/Linux

pip install -r requirements.txt
```

Update .env to use localhost instead of Docker service names:

```env
DATABASE_URL=postgres://sop:sop@localhost:5432/sop
REDIS_URL=redis://localhost:6379/0
MONGO_URL=mongodb://localhost:27017
```

Run migrations:

```bash
python manage.py migrate
```

Terminal 1 — Django server:

```bash
python manage.py runserver
```

Terminal 2 — Celery worker:

```bash
celery -A sop worker -Q sop.scrape,sop.webhook -c 4 --loglevel=info
```

---

## Testing

### Test 1 — Full pipeline (SQS to Lambda to callback)

```bash
POST http://localhost:8000/api/v1/jobs
{ "url": "https://www.instagram.com/p/DT2LomWk2-R/", "platform": "instagram" }
```

Expected 202 with source "sqs". Poll until status becomes "payload":

```bash
GET http://localhost:8000/api/v1/jobs/{job_id}
```

Monitor ngrok dashboard at http://127.0.0.1:4040 to see the incoming Lambda callback.

### Test 2 — Fallback timer (Lambda slow or unreachable)

Set SQS_FALLBACK_TIMEOUT=15 in .env. Stop ngrok so Lambda cannot callback. Restart and submit a job. After 15 seconds the worker triggers login-bot-1:

```bash
docker compose logs worker -f
# After 15s:
# fallback_to_login_bot: job xxx still 'processing' after 15s — triggering login-bot-1 fallback.
# fallback_to_login_bot: login-bot-1 accepted job xxx as fallback scraper.
```

Final job: status "payload", source "login_bot_fallback".

### Test 3 — Immediate fallback (SQS unavailable)

Remove AWS keys from .env and restart. Response shows source "login_bot_immediate".

### Test 4 — Cache

Submit any URL and wait for payload. Submit the same URL again. Second response is 200 with source "redis_cache", returned instantly.

### Test 5 — Dedup (same URL submitted twice while scraping)

Submit a URL. Immediately submit the same URL again before the first completes. Second response is 202 with message "Scrape already in progress for this post."

### Test 6 — Failure handling

Submit a job to get a job_id, then manually POST a failure callback:

```bash
POST http://localhost:8000/api/v1/webhook/receive
{
    "job_id": "your-job-id",
    "success": false,
    "retry_loop": false,
    "comments": [],
    "error": "Rate limited by Instagram"
}
```

Job status becomes "failed". Resubmitting the same URL creates a fresh job.

---

## Inspect Databases

```bash
# All jobs ordered by most recent
docker compose exec postgres psql -U sop -d sop -c \
  "SELECT id, post_id, status, source, created_at FROM jobs ORDER BY created_at DESC LIMIT 10;"

# Jobs by status
docker compose exec postgres psql -U sop -d sop -c \
  "SELECT status, source, COUNT(*) FROM jobs GROUP BY status, source ORDER BY status;"

# Payloads in MongoDB (metadata only)
docker compose exec mongo mongosh sop --eval \
  "db.payloads.find({}, {job_id:1, platform:1, stored_at:1, webhook_sent_at:1}).pretty()"

# Full payload for a specific job
docker compose exec mongo mongosh sop --eval \
  "db.payloads.findOne({job_id: 'your-job-id'})"

# Cache keys in Redis
docker compose exec redis redis-cli KEYS "cache:*"

# Inspect a specific cache entry
docker compose exec redis redis-cli GET "cache:instagram:DT2LomWk2-R"

# Bust a cache entry manually
docker compose exec redis redis-cli DEL "cache:instagram:DT2LomWk2-R"

# Live worker logs
docker compose logs worker -f

# Live API logs
docker compose logs api -f
```

---

## Docker Services

| Service | Description |
|---|---|
| sop-api | Django server on port 8000 |
| sop-worker | Celery worker, 4 processes, queues: sop.scrape and sop.webhook |
| sop-postgres | PostgreSQL 15 |
| sop-redis | Redis 7 |
| sop-mongo | MongoDB 7 |
| sop-mock-scraper | Flask mock scraper on port 9000, used when login-bot-1 is unreachable |

If you have local PostgreSQL, Redis, or MongoDB already running on Windows, change the host-side port bindings in docker-compose.yml to avoid conflicts:

```yaml
postgres:
  ports:
    - "5433:5432"

redis:
  ports:
    - "6380:6379"

mongo:
  ports:
    - "27018:27017"
```

---

## External Services

### AWS SQS + Lambda

- Queue: https://sqs.ap-south-1.amazonaws.com/214118273758/insta-comments-queue
- Region: ap-south-1 (Mumbai)
- SQS message format: { job_id, post_url, callback_url }
- Lambda scrapes using Instagram GraphQL API
- Lambda self-retries up to 5 times if comment count is below 80% of expected
- Falls back to Hiker API internally if GraphQL is blocked
- Callback payload matches Shape A (see webhook endpoint docs above)

### login-bot-1

- Public URL: https://m6mff1b9-8000.inc1.devtunnels.ms
- Trigger: POST /webhook/trigger-job/
- Request fields: { job_id, platform, post_url, callback_url }
- Cookie strategy: LRU (Least Recently Used), fair distribution across accounts
- Auto-bans cookies after 5 consecutive failures
- Callback payload matches Shape A (see webhook endpoint docs above)

### ngrok

Required for Lambda to POST callbacks to local SOP. The free plan assigns a new URL on every restart. Update SOP_WEBHOOK_URL in .env and restart Docker each time ngrok restarts.

---

## Production Notes

Before deploying to a server:

- Set DEBUG=False and a strong SECRET_KEY
- Restrict ALLOWED_HOSTS to your actual domain
- Move AWS credentials to IAM roles rather than hardcoded keys
- Enable authentication on MongoDB
- Use SSL/TLS for all services
- Replace ngrok with a real public domain and nginx reverse proxy
- Scale Celery workers by increasing the worker service replica count in docker-compose.yml
