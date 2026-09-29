-- Auth v2: profile/audit fields, rotating refresh sessions, saved areas and an audit log.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS display_name TEXT CHECK (char_length(display_name) BETWEEN 1 AND 80),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failed_logins INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ;

-- v1 opaque sessions were development-only; users sign in again once.
DROP TABLE IF EXISTS sessions;

-- Only a SHA-256 of each refresh token is stored. A family is one sign-in; reuse of a
-- rotated token revokes the whole family.
CREATE TABLE IF NOT EXISTS refresh_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    family_id UUID NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_reason TEXT CHECK (revoked_reason IN ('logout', 'rotated', 'reuse', 'password_change', 'logout_all')),
    replaced_by UUID REFERENCES refresh_sessions(id),
    user_agent TEXT CHECK (char_length(user_agent) <= 200)
);
CREATE INDEX IF NOT EXISTS refresh_sessions_user_idx ON refresh_sessions (user_id);
CREATE INDEX IF NOT EXISTS refresh_sessions_family_idx ON refresh_sessions (family_id);

CREATE TABLE IF NOT EXISTS saved_areas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    west DOUBLE PRECISION NOT NULL,
    south DOUBLE PRECISION NOT NULL,
    east DOUBLE PRECISION NOT NULL,
    north DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (-180 <= west AND west < east AND east <= 180 AND -90 <= south AND south < north AND north <= 90),
    UNIQUE (user_id, name)
);

-- Security audit trail. Never stores passwords, hashes, tokens or attempted emails.
CREATE TABLE IF NOT EXISTS auth_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    event TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS auth_events_user_idx ON auth_events (user_id, created_at DESC);
