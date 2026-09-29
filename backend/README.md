# AgniCalendar backend — FastAPI + PostgreSQL

Python 3.10+, FastAPI/Uvicorn, psycopg 3, PostgreSQL 17. The Flutter application lives separately in `app/` and connects to these endpoints.

## Local database (normal PC terminal)

```bash
cd "$HOME/Documents/ChatGPT/nasa_space_chalange_2026"
python3 backend/scripts/dev_db.py start
```

This uses host Podman, creates `agnicalendar-postgres`, and binds PostgreSQL to **127.0.0.1:55432**. The `agnicalendar-pgdata` volume preserves data across container restarts. A random password is generated in `backend/.env` with mode 600, never printed or committed. Existing `.env` is preserved. Do not replace its password after initialization without also changing the database password. To use your own database, configure `DATABASE_URL` using `.env.example` instead of running the helper.

```bash
python3 backend/scripts/dev_db.py status
python3 backend/scripts/dev_db.py stop
```

## Python environment (inside flutter-dev)

Run from the repository root:

```bash
sudo apt-get install -y python3-venv
python3 -m venv backend/.venv-flutter
source backend/.venv-flutter/bin/activate
python -m pip install -r backend/requirements.lock.txt
python -m backend.storage.manage migrate
```

`requirements.lock.txt` pins the tested runtime and test dependency versions. `.venv-flutter` is the user-facing Ubuntu environment; `.venv` is an ignored agent verification environment and must not be used across different Python installations.

## Import existing data or fetch new NASA data

Old SQLite is only a migration input; the service and pipeline now use PostgreSQL exclusively. Import is non-destructive and repeatable; exact report duplicates are skipped.

```bash
python -m backend.storage.manage import-sqlite backend/data/output/agni.sqlite
# Or import an existing pipeline JSON:
python -m backend.storage.manage import-json backend/data/output/report.json
# Or fetch public rolling 24-hour South Asia feeds:
python -m backend.processing.pipeline --fetch
```

For historical CSV files:

```bash
python -m backend.processing.pipeline \
  --input MODIS_SP=/path/to/modis.csv \
  --input VIIRS_SNPP_SP=/path/to/viirs.csv
```

Original CSV bytes and SHA-256 hashes are retained in `backend/data/raw/`. The feed is first cut to the rectangle **88,20.5,92.7,26.7**, then only detections **inside the Bangladesh outline** are kept (`--region bangladesh`, the default; `--region none` keeps the whole rectangle). The outline is Natural Earth 1:10m Admin 0 (public domain) in `data/boundaries/bangladesh.geojson`. It matches the FIRMS country archive: none of the 260k archive detections fall outside it. Detections dropped at the border are counted as `outside_region`. Pixels straddling the border may fall on either side, and very small islands (Saint Martin's) are not in the 1:10m outline. JSON goes to `backend/data/output/report.json`, and a successful pipeline run transactionally stores the report and map observations in PostgreSQL. `--output` changes the JSON location only, not the database. Reports cover only their supplied data; rolling feeds are not cumulatively summed. A manual `--fetch` stores nothing new when NASA's files are byte-identical to the newest report's inputs.

## Multi-year archive (burning season)

```bash
python -m backend.processing.archive                    # all years, all products
python -m backend.processing.archive --years 2023-2024 --products viirs-snpp
```

This downloads NASA FIRMS **yearly country files** for Bangladesh (public, no API key). Years NASA has not published as files yet (e.g. 2025 and the current year) are fetched through the **area API** instead, using the same standard (SP) products with fire `type`, when `FIRMS_MAP_KEY` is set. SP data ends about three months before today. These years are recorded with `url = firms-api:…?through=DATE`, filtered to the same Bangladesh outline, and skipped on later runs unless NASA has published more days. Once NASA publishes the yearly file, it replaces the API copy.

Everything is stored in `archive_files` / `archive_detections`: MODIS from 2000, VIIRS S-NPP from 2012 and VIIRS NOAA-20 (`viirs-jpss1`) from 2018. Raw CSVs are kept by SHA-256 in `data/raw/`. Re-running skips unchanged files and atomically replaces changed ones. Years NASA has not published yet are recorded as `unavailable` and picked up on a later run. A file that fails validation (e.g. an HTML error page) is rejected and the previous data stays.

`GET /v1/archive/season` (login required; optional `bbox=`, `types=vegetation|all`) returns monthly counts per product and year, plus mean/median/min/max over **complete mission years only** (MODIS 2003+, when Aqua joined Terra; S-NPP 2013+; NOAA-20 2019+; never the current year). Rules:
- Only FIRMS `type` 0 (presumed vegetation fire) is counted by default. Static land sources and offshore detections are kept, labelled and reported as `other_types`.
- A year with a file but no detections in a month counts as 0 *detections*, not 0 fires.
- The region is the FIRMS **country boundary**, unlike the rectangle used for the rolling feeds. The two datasets are never mixed.
- Products stay separate. Processing versions are reported per year span (MODIS 6.2 → 2017, 6.03 2018–2022, 61.03 2023+), because averages span them.

## Recent days and map time windows (FIRMS MAP_KEY)

The map offers **24 h** (the rolling report), **7 / 30 days** and **any archive month**. The 7/30-day windows need a free NASA FIRMS key (https://firms.modaps.eosdis.nasa.gov/api/map_key/) in `backend/.env`:

```bash
FIRMS_MAP_KEY=your-key            # in backend/.env; never commit it
python -m backend.processing.recent --days 30   # first backfill (~24 requests, ~30 s)
```

After that, the server's scheduler keeps the last 30 days current alongside the 24-hour report. It refetches only today and yesterday (4 requests per run) and deletes detections older than 45 days. The FIRMS area API allows 5 days per request and 5,000 transactions per 10 minutes. The data covers MODIS, VIIRS S-NPP, NOAA-20 and NOAA-21 near-real-time, each kept as its own source, filtered to the Bangladesh outline. Raw responses are kept by SHA-256. **NRT API files have no fire `type`, so 7/30-day windows may include industrial heat sources**; the app says so. Without a key these windows report no data and everything else keeps working.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/map/hotspots?window=7d\|30d` | Recent NRT points (whole UTC days, today included), `missing_days`, `fire_type_known: false` |
| `GET /v1/map/hotspots?month=YYYY-MM` | Archive month, vegetation fires only; `excluded_other_types` counts the rest |
| `GET /v1/map/available` | Archive months with counts, recent-days range |

Responses over 2 KB are gzip-compressed. For example, April 2024 has 13,307 points, about 3 MB of JSON sent as 160 KB.

## Likely industrial heat sources (machine learning)

Recent NRT data has no fire `type`, so a model flags hotspots that are likely **non-vegetation heat sources** (industry, brick kilns, gas flares) in the 7/30-day map windows.

```bash
python -m backend.ml.static_sources train    # ~5 s; restart the server afterwards
```

- **Labels:** the archive's NASA `type` (≠ 0 vs 0).
- **Features:** only where and when hotspots occur: how many of the previous 5 years, how many detections, and how many months (incl. monsoon months) had hotspots in the ~550 m cell and ~1.6 km neighbourhood, plus day/night, FRP, month and sensor. History comes **strictly from earlier years**, and the label is never a feature.
- **Split:** train on 2018–2023, choose the threshold on 2024, report once on **2025**, which the model never saw. Features were also chosen on 2024. An earlier feature set was scored on 2025 once; that is recorded in the metrics file.
- **Model:** scikit-learn `HistGradientBoostingClassifier`. The model and `static_source.json` metrics live in `data/output/models/` (gitignored). `/v1/map/hotspots?window=…` adds `static_probability`, `likely_static` and a `classifier` summary with the measured scores. Without a trained model these fields are null.
- **Result on 2025** (2.7% of detections are non-vegetation): precision **59.5%**, recall **48.1%**, average precision 0.63. The best simple rule ("hotspots in ≥ 4 of the previous 5 years") reached precision 2.9%. The model is a hint, not ground truth. The app shows flagged dots in grey and states these scores.

## Start the server

In `flutter-dev`, from the root with the environment activated:

```bash
python -m backend.api.server --port 8001
```

- API: http://127.0.0.1:8001
- Interactive Swagger documentation: http://127.0.0.1:8001/docs
- OpenAPI schema: http://127.0.0.1:8001/openapi.json
- Stop: Ctrl+C

### Automatic NASA fetch

While the server runs it fetches the rolling feeds **at startup and then every 3 hours**, the same work as `pipeline --fetch`. NASA updates NRT data within about 3 hours of each satellite pass.

```bash
python -m backend.api.server --port 8001                     # default: every 3h
python -m backend.api.server --port 8001 --fetch-every 1h    # 30m, 3h, 1d …; minimum 15m
python -m backend.api.server --port 8001 --fetch-every off   # manual fetching only
```

- Unchanged feeds (byte-identical to the newest report's inputs) create no new report.
- Only one fetch runs at a time. If NASA, the network or PostgreSQL fails, the server keeps serving the previous report and retries within 15 minutes.
- Progress appears in the server log (`NASA fetch: new_report #12, 3 hotspots`) and in `GET /health` → `nasa_fetch` (last run, result, next run).
- It stops with the server. For fetching while the server is off, use a systemd timer or cron running `pipeline --fetch`.
- The app cannot trigger NASA downloads. Its Refresh only reloads the newest stored report.
- Alternate port: `python -m backend.api.server --port 8001`

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process is alive; no database dependency |
| `GET /ready` | PostgreSQL and schema can be queried |
| `GET /v1/summary` | Counts by source, bbox, data timestamps and limitations |
| `GET /v1/calendar?source=VIIRS_SNPP_NRT` | Weekly counts, separately by sensor/platform/version |
| `GET /v1/observations?limit=100&offset=0` | Paginated map observations |
| `POST /v1/auth/register` | `{email, password, display_name?}` → 201 with access + refresh tokens |
| `POST /v1/auth/login` | Same response; wrong credentials 401, locked account 429 |
| `POST /v1/auth/refresh` | `{refresh_token}` → new token pair (old refresh token is revoked) |
| `POST /v1/auth/logout` | `{refresh_token}` → 204; revokes that session |
| `POST /v1/auth/logout-all` | Bearer → 204; revokes every session of the user |
| `GET` / `PATCH /v1/me` | Profile (`display_name` is editable) |
| `POST /v1/me/password` | `{current_password, new_password}`; ends all sessions, returns a new pair |
| `GET` / `POST /v1/me/areas`, `PUT` / `DELETE /v1/me/areas/{id}` | Saved bounding boxes (max 20 per user) |

`/v1/observations` also accepts `bbox=west,south,east,north`.

## Authentication

Every `/v1` data endpoint (`summary`, `calendar`, `observations`) requires `Authorization: Bearer <access_token>` and returns 401 without a valid one. The app refreshes on 401. Only `/health`, `/ready`, `/docs` and the `/v1/auth` sign-in routes are public (use **Authorize** in Swagger to paste an access token).

- **Passwords**: Argon2id (`argon2-cffi`), 8–128 characters, emails trimmed and lowercased. Hashes from the earlier scrypt version still work and are upgraded to Argon2id on the next login.
- **Access tokens**: HS256 JWT, 15 minutes, with `iss`, `aud`, `exp` and `typ=access` required. They are signed with `AUTH_JWT_SECRET` from `backend/.env`. `python -m backend.storage.manage migrate` adds a random secret if missing, and the server refuses to start without one. Changing the secret signs everyone out within 15 minutes.
- **Refresh tokens**: random, 30 days, stored only as SHA-256 in `refresh_sessions`. Every refresh rotates the token. Reusing an already-rotated token revokes that whole sign-in (token-theft detection). Logout, logout-all and password change revoke sessions immediately. An access token already issued stays valid until it expires (at most 15 minutes).
- **Abuse limits**: 10 wrong passwords lock an account for 15 minutes. Login and register allow 30 requests per 5 minutes per client address. That limit is in-process, so it resets on restart and is not shared between workers.
- **Audit**: `auth_events` records register, login success/failure/locked, refresh reuse, logout and password change by user id. It never stores passwords, hashes, tokens or attempted emails.
- **Not yet**: email verification, password reset and HTTPS. Registering with an existing email returns 409, which reveals that the account exists. Add these before a public deployment.

## Database schema and tests

Versioned SQL migrations live in `storage/migrations/`. `schema_migrations` tracks applied files, `reports` retains JSONB snapshots, and `observations` indexes map data by report/source/time. `003_auth_v2.sql` adds `refresh_sessions`, `saved_areas` and `auth_events`, and replaces the v1 `sessions` table, so existing users sign in again once. Report imports and observation inserts share one transaction, and digest uniqueness makes repeated imports idempotent.

```bash
python -m unittest discover -s backend/tests -v
RUN_POSTGRES_TESTS=1 python -m unittest discover -s backend/tests -v
```

The integration suite requires CREATEDB permission. It creates and removes a uniquely named temporary database; application tables are never truncated by tests. It covers transactions/rollback, duplicate imports, backfill ordering, filters, pinned pagination and API validation. The default suite skips real PostgreSQL integration tests.

## Scientific and deployment limits

- Detections are pixels/thermal anomalies, not distinct fires or burned area.
- Keep MODIS and VIIRS separate; calibrated harmonization and predictions are not implemented.
- Missing detections do not mean no fires. Observation/cloud coverage is unknown.
- Low-confidence records are retained with labels, not silently removed.
- Weeks can be partial. A 24-hour sample cannot establish a seasonal baseline.
- The local read-only service has no public write/import endpoints. This is a local development deployment; remote hosting, TLS and access controls are not configured.

Sources: [NASA FIRMS archive](https://firms.modaps.eosdis.nasa.gov/download/), [FIRMS API](https://firms.modaps.eosdis.nasa.gov/api/area/), [MODIS confidence definitions](https://www.earthdata.nasa.gov/s3fs-public/2023-09/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf).
