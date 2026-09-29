# AgniCalendar workspace

One repository with separate Flutter app and Python backend folders.

```text
app/                 Flutter AgniCalendar dashboard, map and calendar
backend/
  processing/        NASA FIRMS download, cleanup and calendar pipeline
  api/               Local read-only HTTP API
  storage/           PostgreSQL report persistence
  data/raw/          Downloaded CSV snapshots (gitignored)
  data/output/       JSON reports and legacy migration inputs (gitignored)
  tests/             Python tests
```

## Android app

In `flutter-dev`:

```bash
cd "$HOME/Documents/ChatGPT/nasa_space_chalange_2026/app"
adb reverse tcp:8001 tcp:8001
flutter pub get
flutter run -d UGHILZHQKZ9LEAX8
```

Use `flutter devices` to check your phone's ID. Press `r` to hot reload and `q` to stop. See [app/README.md](app/README.md).

## Backend

FastAPI + PostgreSQL. First follow [backend setup](backend/README.md) to start PostgreSQL, install dependencies and migrate the schema. Then, inside `flutter-dev`, from the repository root:

```bash
cd "$HOME/Documents/ChatGPT/nasa_space_chalange_2026"
source backend/.venv-flutter/bin/activate
python -m pip install -r backend/requirements.lock.txt   # after pulling auth changes
python -m backend.storage.manage migrate                 # schema + AUTH_JWT_SECRET
python -m backend.processing.pipeline --fetch          # optional: the server also fetches automatically
python -m backend.processing.archive                     # multi-year season (once, ~2 min)
python -m backend.processing.recent --days 30           # 7/30-day map (needs free FIRMS_MAP_KEY in backend/.env)
python -m backend.ml.static_sources train                # industrial-source model (after the archive import)
python -m backend.api.server --port 8001
```

Server: `http://127.0.0.1:8001`; summary at `/v1/summary`, calendar at `/v1/calendar`, interactive docs at `/docs`. Ctrl+C stops it. Default data paths resolve inside `backend/data/`. See [backend/README.md](backend/README.md) for historical imports, endpoints and scientific limitations.

## Validation

From the repository root:

```bash
source backend/.venv-flutter/bin/activate
python -m unittest discover -s backend/tests -v
cd app
flutter analyze
flutter test
flutter build apk --debug
```

The Flutter app connects to the backend on port 8001 using USB port forwarding. While running, the server fetches NASA's rolling feeds at startup and every 3 hours (`--fetch-every`); the app's Refresh reloads the newest report. Sign-in is required to view data. The backend reports sensor-specific detections; it does not predict fires or provide calibrated cross-sensor trends.
