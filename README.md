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
- Expects standard Zoom Webhook JSON structure.

## Curl examples

### 1. Participant Joined
```bash
curl -X POST http://127.0.0.1:8000/webhooks/zoom \
  -H "Content-Type: application/json" \
  -d '{
    "event": "meeting.participant_joined",
    "payload": {
      "object": {
        "uuid": "test-session-12345",
        "participant": {
          "email": "alice@example.com",
          "join_time": "2024-01-01T10:00:00Z"
        }
      }
    }
  }'
```

### 2. Participant Left
```bash
curl -X POST http://127.0.0.1:8000/webhooks/zoom \
  -H "Content-Type: application/json" \
  -d '{
    "event": "meeting.participant_left",
    "payload": {
      "object": {
        "uuid": "test-session-12345",
        "participant": {
          "email": "alice@example.com",
          "leave_time": "2024-01-01T11:15:30Z"
        }
      }
    }
  }'
```

### 3. URL Validation
```bash
curl -X POST http://127.0.0.1:8000/webhooks/zoom \
  -H "Content-Type: application/json" \
  -d '{
    "event": "endpoint.url_validation",
    "payload": {
      "plainToken": "some-random-token"
    }
  }'
```


No auth or extra features are included; this is intentionally minimal.

