-- Multi-year FIRMS archive (yearly country files), kept apart from the rolling NRT reports:
-- different region definition (country boundary vs rectangle) and product versions.
CREATE TABLE IF NOT EXISTS archive_files (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    year INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 2100),
    region TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('imported', 'unavailable')),
    sha256 TEXT,
    rows_read INTEGER NOT NULL DEFAULT 0,
    retained INTEGER NOT NULL DEFAULT 0,
    invalid INTEGER NOT NULL DEFAULT 0,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, year, region)
);

-- fire_type is FIRMS 'type': 0 presumed vegetation fire, 1 active volcano,
-- 2 other static land source, 3 offshore. All are kept and labelled.
CREATE TABLE IF NOT EXISTS archive_detections (
    file_id BIGINT NOT NULL REFERENCES archive_files(id) ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    satellite TEXT NOT NULL,
    version TEXT NOT NULL,
    quality TEXT NOT NULL CHECK (quality IN ('low', 'nominal', 'high')),
    confidence_raw TEXT NOT NULL,
    frp_mw DOUBLE PRECISION,
    daynight TEXT NOT NULL,
    fire_type SMALLINT NOT NULL CHECK (fire_type BETWEEN 0 AND 3)
);
CREATE INDEX IF NOT EXISTS archive_detections_file_idx ON archive_detections (file_id, fire_type);
