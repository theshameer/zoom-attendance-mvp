CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS attendance_segments (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    user_id TEXT NOT NULL REFERENCES users(id),
    join_time TIMESTAMPTZ NOT NULL,
    leave_time TIMESTAMPTZ NULL,
    duration_sec INT NULL,
    CONSTRAINT leave_after_join CHECK (leave_time IS NULL OR leave_time >= join_time)
);

CREATE INDEX IF NOT EXISTS idx_attendance_session_user ON attendance_segments (session_id, user_id);
CREATE INDEX IF NOT EXISTS idx_attendance_open_segments ON attendance_segments (leave_time) WHERE leave_time IS NULL;

