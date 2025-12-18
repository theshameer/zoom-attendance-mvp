# Zoom Attendance MVP (FastAPI)

Minimal FastAPI backend to track time spent in Zoom sessions using PostgreSQL.

## Setup
- Create venv: `python3 -m venv .venv && source .venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- Set database URL (example for local Postgres on macOS):  
  `export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/zoom_attendance"`

## Database schema
- Apply schema: `psql "$DATABASE_URL" -f schema.sql`

## Run the API
- Start server: `uvicorn main:app --reload`
- Default listens on `http://127.0.0.1:8000`

## Webhook endpoint
- `POST /webhooks/zoom`
- Body: `{"event_type": "join"|"leave", "session_id": "string", "user_id": "string", "timestamp": "ISO8601"}`  
  Timestamp must include timezone; `Z` is accepted for UTC.

## Curl examples
- Join:
  `curl -X POST http://127.0.0.1:8000/webhooks/zoom -H "Content-Type: application/json" -d '{"event_type":"join","session_id":"abc123","user_id":"alice","timestamp":"2024-01-01T10:00:00Z"}'`
- Leave:
  `curl -X POST http://127.0.0.1:8000/webhooks/zoom -H "Content-Type: application/json" -d '{"event_type":"leave","session_id":"abc123","user_id":"alice","timestamp":"2024-01-01T11:15:30Z"}'`

No auth or extra features are included; this is intentionally minimal.

