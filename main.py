from fastapi import Body
from fastapi.middleware.cors import CORSMiddleware
from datetime import date as dt_date
import hashlib
import hmac
import os
from datetime import datetime, timezone
from datetime import date
from fastapi.responses import JSONResponse
from typing import Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Request, Header
from pydantic import BaseModel, Field, validator
from fastapi.responses import JSONResponse

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zoom-attendance")



def parse_iso_timestamp(value: str) -> datetime:
    # Allow common Z suffix while keeping dependencies minimal.
    cleaned = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone information")
    return dt.astimezone(timezone.utc)


class WebhookEvent(BaseModel):
    event_type: str = Field(..., pattern="^(join|leave)$")
    session_id: str
    user_id: str
    timestamp: datetime

    @validator("timestamp", pre=True)
    def validate_timestamp(cls, v: str) -> datetime:  # type: ignore[override]
        return parse_iso_timestamp(v)


DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")

app = FastAPI(title="Zoom Attendance MVP")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # later you can lock this down
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("API_KEY")

def require_api_key(x_api_key: str | None):
    # If API_KEY isn't set, don't block (useful while developing)
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


pool: Optional[asyncpg.Pool] = None


@app.on_event("startup")
async def startup() -> None:
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)


@app.on_event("shutdown")
async def shutdown() -> None:
    global pool
    if pool:
        await pool.close()
        pool = None


async def ensure_entities(conn: asyncpg.Connection, session_id: str, user_id: str) -> None:
    await conn.execute("INSERT INTO sessions(session_id) VALUES($1) ON CONFLICT DO NOTHING", session_id)
    await conn.execute("INSERT INTO users(id) VALUES($1) ON CONFLICT DO NOTHING", user_id)


@app.post("/webhooks/zoom")
async def zoom_webhook(body: dict = Body(...)):
    event = body.get("event")
    logger.info(f"Received Zoom event: {event}")

    # --- Zoom endpoint validation ---
    if event == "endpoint.url_validation":
        plain_token = body["payload"]["plainToken"]
        secret = os.environ.get("ZOOM_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(status_code=500, detail="Missing ZOOM_WEBHOOK_SECRET")

        encrypted_token = hmac.new(
            secret.encode(),
            plain_token.encode(),
            hashlib.sha256
        ).hexdigest()

        return {"plainToken": plain_token, "encryptedToken": encrypted_token}

    # ✅ Event filter: ignore everything except join/leave
    if event not in ("meeting.participant_joined", "meeting.participant_left"):
        return {"ok": True, "ignored": True}

    # ---- Parse Zoom payload into our normalized fields ----
    obj = body.get("payload", {}).get("object", {}) or {}

    session_id = obj.get("uuid") or str(obj.get("id"))
    participant = obj.get("participant", {}) or {}

    user_id = (
        participant.get("email")
        or participant.get("user_id")
        or participant.get("id")
        or "unknown"
    )

    # timestamp parsing helper (Zoom sometimes gives ms int)
    def to_dt(v) -> datetime:
        if v is None:
            return datetime.now(timezone.utc)
        if isinstance(v, (int, float)):
            # Zoom event_ts is usually milliseconds
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
        if isinstance(v, str):
            return parse_iso_timestamp(v)
        return datetime.now(timezone.utc)

    if event == "meeting.participant_joined":
        event_type = "join"
        ts = to_dt(participant.get("join_time") or body.get("event_ts"))
    else:
        event_type = "leave"
        ts = to_dt(participant.get("leave_time") or body.get("event_ts"))

    if pool is None:
        raise HTTPException(status_code=500, detail="Database not ready")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_entities(conn, session_id, user_id)

            if event_type == "join":
                await conn.execute(
                    """
                    INSERT INTO attendance_segments (session_id, user_id, join_time)
                    VALUES ($1, $2, $3)
                    """,
                    session_id, user_id, ts
                )
                return {"ok": True, "action": "segment_opened"}

            row = await conn.fetchrow(
                """
                SELECT id, join_time
                FROM attendance_segments
                WHERE session_id = $1 AND user_id = $2 AND leave_time IS NULL
                ORDER BY join_time DESC
                LIMIT 1
                """,
                session_id, user_id
            )

            if not row:
                return {"ok": True, "action": "no_open_segment"}

            duration = int((ts - row["join_time"]).total_seconds())

            await conn.execute(
                """
                UPDATE attendance_segments
                SET leave_time = $1, duration_sec = $2
                WHERE id = $3
                """,
                ts, duration, row["id"]
            )

            return {"ok": True, "action": "segment_closed", "duration_sec": duration}


@app.get("/sessions")
async def list_sessions():
    """
    Returns one row per Zoom session with total attendance stats
    """
    if pool is None:
        raise HTTPException(status_code=500, detail="Database not ready")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                session_id,
                participant_count,
                total_participant_seconds,
                first_join,
                last_seen
            FROM session_summary
            ORDER BY first_join DESC
            """
        )

    return [dict(r) for r in rows]



@app.get("/sessions")
async def list_sessions(limit: int = 20):
    if pool is None:
        raise HTTPException(status_code=500, detail="DB not ready")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT session_id
            FROM sessions
            ORDER BY session_id DESC
            LIMIT $1
            """,
            limit
        )

    return JSONResponse(content=[{"session_id": r["session_id"]} for r in rows])



@app.get("/sessions/{session_id}")
async def session_details(session_id: str):
    """
    Returns per-user attendance for a single session
    """
    if pool is None:
        raise HTTPException(status_code=500, detail="Database not ready")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                user_id,
                segments,
                total_seconds,
                first_join,
                last_seen
            FROM session_user_summary
            WHERE session_id = $1
            ORDER BY total_seconds DESC
            """,
            session_id
        )

    return {
        "session_id": session_id,
        "participants": [dict(r) for r in rows]
    }

from fastapi.responses import PlainTextResponse

@app.get("/sessions/{session_id}/csv", response_class=PlainTextResponse)
async def session_details_csv(session_id: str):
    if pool is None:
        raise HTTPException(status_code=500, detail="Database not ready")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                user_id,
                segments,
                total_seconds,
                first_join,
                last_seen
            FROM session_user_summary
            WHERE session_id = $1
            ORDER BY total_seconds DESC
            """,
            session_id
        )

    lines = ["user_id,segments,total_seconds,first_join,last_seen"]
    for r in rows:
        lines.append(
            f"{r['user_id']},{r['segments']},{r['total_seconds']},{r['first_join']},{r['last_seen']}"
        )
    return "\n".join(lines)

@app.get("/health")
async def health():
    if pool is None:
        raise HTTPException(status_code=500, detail="DB not ready")
    return {"status": "ok"}

@app.get("/daily/{day}/summary")
async def daily_summary(day: str, x_api_key: str | None = Header(default=None)):
    require_api_key(x_api_key)
    """
    day format: YYYY-MM-DD
    Example: /daily/2025-12-29/summary
    """
    if pool is None:
        raise HTTPException(status_code=500, detail="DB not ready")

    # Parse the day safely
    try:
        d = date.fromisoformat(day)  # expects YYYY-MM-DD
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date. Use YYYY-MM-DD")

    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    end = start.replace(hour=23, minute=59, second=59)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                user_id,
                COUNT(*) AS segments,
                SUM(duration_sec) AS total_seconds,
                MIN(join_time) AS first_join,
                MAX(COALESCE(leave_time, NOW())) AS last_seen
            FROM attendance_segments
            WHERE join_time >= $1 AND join_time <= $2
              AND leave_time IS NOT NULL
            GROUP BY user_id
            ORDER BY total_seconds DESC
            """,
            start,
            end
        )
        if not rows:
            return []

    data = []
    for r in rows:
        data.append({
            "user_id": r["user_id"],
            "segments": int(r["segments"] or 0),
            "total_seconds": int(r["total_seconds"] or 0),
            "first_join": r["first_join"].isoformat() if r["first_join"] else None,
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        })

    return JSONResponse(content=data)





@app.get("/sessions/{session_id}/summary")
async def session_summary(session_id: str, x_api_key: str | None = Header(default=None)):
    require_api_key(x_api_key)
    if pool is None:
        raise HTTPException(status_code=500, detail="DB not ready")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                user_id,
                segments,
                total_seconds,
                first_join,
                last_seen
            FROM session_user_summary
            WHERE session_id = $1
            ORDER BY total_seconds DESC
            """,
            session_id,
        )

    # convert asyncpg Records into normal JSON-safe dicts
    data = []
    for r in rows:
        data.append(
            {
                "user_id": r["user_id"],
                "segments": r["segments"],
                "total_seconds": r["total_seconds"],
                "first_join": r["first_join"].isoformat() if r["first_join"] else None,
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            }
        )

    return JSONResponse(content=data)
