CREATE TABLE IF NOT EXISTS reports (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    digest TEXT UNIQUE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_generated_idx ON reports (generated_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS observations (
    report_id BIGINT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    source TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    payload JSONB NOT NULL,
    PRIMARY KEY (report_id, ordinal)
);
CREATE INDEX IF NOT EXISTS observations_filter_idx ON observations (report_id, source, observed_at);
