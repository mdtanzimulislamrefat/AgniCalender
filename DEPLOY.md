# Free deployment

Everything here uses free plans with no credit card: **Neon** (PostgreSQL), **Render** (API server, HTTPS), **UptimeRobot** (keeps the free server awake), **GitHub** (code and APK download).

## 1. GitHub
Create an empty public repository, then from the repository root:
```bash
git remote add origin https://github.com/<you>/agnicalendar.git
git push -u origin main
```

## 2. Database (Neon)
1. Sign up at https://neon.tech and create a project (PostgreSQL 17, a region near Bangladesh such as Singapore).
2. Copy the **direct** connection string (turn *Connection pooling* **off**; the pooled one rejects the server's session settings). It looks like `postgresql://user:password@ep-….aws.neon.tech/neondb?sslmode=require`.
3. Copy your local NASA data into it (accounts are **not** copied). Run this from the repository root with the local database running:
   ```bash
   source backend/.venv-flutter/bin/activate
   read -rs PUBLISH_DATABASE_URL && export PUBLISH_DATABASE_URL   # paste the Neon URL, press Enter
   python -m backend.storage.manage publish-data
   ```
   It is safe to repeat later, e.g. after a new archive import.

## 3. API server (Render)
1. Sign up at https://render.com with GitHub, then **New → Blueprint** and pick the repository. `render.yaml` defines a free Docker web service.
2. When asked, set `DATABASE_URL` to the Neon URL and `FIRMS_MAP_KEY` to your FIRMS key. `AUTH_JWT_SECRET` is generated for you.
3. Wait for the deploy, then open `https://<service>.onrender.com/health`. It should return `"status":"ok"`, and `/docs` shows the API.

On start the container migrates the schema and serves with the NASA fetch every 3 hours. The trained classifier ships in `backend/models/`. Free services sleep after 15 minutes without requests and take about a minute to wake.

## 4. Keep it awake (UptimeRobot)
Sign up at https://uptimerobot.com and add an **HTTP(s)** monitor for `https://<service>.onrender.com/health` every 5 minutes. This also keeps the 3-hourly NASA fetch running.

## 5. Android APK for judges
```bash
cd app
flutter build apk --release --split-per-abi --dart-define=API_BASE_URL=https://<service>.onrender.com
```
Upload `app/build/app/outputs/flutter-apk/app-arm64-v8a-release.apk` (~20 MB; almost every phone since 2017) to a GitHub **Release**. The single universal APK without `--split-per-abi` is 55 MB. Release builds only allow HTTPS, and they are signed with the debug key (fine for side-loading, not for Play Store). Create a demo account in the app and put its email and password in your submission.

## Updating
- Code: `git push` makes Render redeploy automatically.
- Data: run the archive/recent import locally, then `publish-data` again.
- Model: retrain locally (`python -m backend.ml.static_sources train`), commit `backend/models/`, push.
