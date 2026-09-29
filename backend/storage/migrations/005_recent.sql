-- Rolling store of recent NRT detections (FIRMS area API) for 7/30-day map windows.
-- The API's NRT files carry no fire 'type', so static heat sources cannot be separated here.
CREATE TABLE IF NOT EXISTS recent_detections (
    source TEXT NOT NULL,
    satellite TEXT NOT NULL,
    version TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    quality TEXT NOT NULL CHECK (quality IN ('low', 'nominal', 'high')),
    confidence_raw TEXT NOT NULL,
    frp_mw DOUBLE PRECISION,
    daynight TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, satellite, observed_at, latitude, longitude)
);
CREATE INDEX IF NOT EXISTS recent_detections_time_idx ON recent_detections (observed_at);

-- Which UTC days were fetched per source; 'complete' once NASA has finished that day.
CREATE TABLE IF NOT EXISTS recent_coverage (
    source TEXT NOT NULL,
    day DATE NOT NULL,
    complete BOOLEAN NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, day)
);
