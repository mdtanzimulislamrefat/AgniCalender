# AgniCalendar Android app

Flutter client for the FastAPI/PostgreSQL backend in `../backend/`.

## Run (inside flutter-dev)

Start the backend on port 8001 in a separate terminal, then:

```bash
cd "$HOME/Documents/ChatGPT/nasa_space_chalange_2026/app"
adb devices
adb reverse tcp:8001 tcp:8001
flutter pub get
flutter run -d UGHILZHQKZ9LEAX8
```

Default API URL: `http://127.0.0.1:8001`. USB reverse connects the phone's port to the PC server. Re-run reverse after reconnecting USB. Android emulator users can use:

```bash
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8001
```

## Screens

- Sign in / register: required before anything is shown. Signing out, or a revoked session, returns to this screen.
- Overview: one large number per satellite (never summed) and the newest-hotspot age. The ⓘ button explains hotspots, sensors and limits in plain language.
- Map time: **24 h** (same data as Overview), **7 days / 30 days** (near-real-time, may include industrial heat sources), or **Month…** (any archive month from 2000 until NASA's quality-checked data ends, about 3 months ago; vegetation fires only). A caption line shows the count, the period and any caveat. Dots are canvas-drawn with larger invisible tap targets, so thousands of points stay smooth.
- Likely industrial: in 7/30-day windows, dots a backend model flags as likely industrial heat sources are grey with a sensor-coloured ring. **Hide likely industrial** filters them, the details card shows the model probability, and ⓘ explains the model with its measured test precision and recall.
- Clusters: hotspots within ~40 px at the current zoom merge into one count badge. The badge is sensor-coloured, blue-grey when MODIS and VIIRS are mixed, and grey when all are likely industrial, so the numbers on the map add up to the caption count. Tapping a badge zooms in until it splits. If the hotspots share one spot (e.g. several satellites saw the same fire), it lists them instead; pick one for details. Clusters are recomputed only when the zoom changes, not while panning.
- Map: shows the Bangladesh outline (bundled `assets/bangladesh.geojson`, same as the backend filter), dots coloured by sensor (faded = low confidence). Tap a dot for details. **Select area** shows a box: move and zoom until it covers your place, then save.
- Calendar → **Season (since 2000)**: one chart per satellite. Each column is the average number of hotspots in that month over complete years, the thin line runs from the quietest to the busiest year, and you can compare any single year as a dot. Tap a month for its numbers; "Show numbers" gives the table. **Recent weeks** keeps the weekly bars from the rolling feed.
- Saved areas also show their own multi-year season.
- Account: display name, saved areas (size, per-sensor counts, outside-region warning), sign out, or sign out on all devices.

Sessions: the refresh token and profile are kept in Android secure storage (`flutter_secure_storage`). The 15-minute access token stays in memory only. On a 401 the client refreshes once (parallel requests share that refresh) and retries. Signing out or a revoked session deletes the tokens and all cached data on the device. While signed in, the last snapshot stays available offline.
- Overview: source-specific counts, observation times, report ID, scientific limitations.
- Map: OpenStreetMap basemap, sensor colors, source filter, tappable detection markers.
- Calendar: weekly counts separately by sensor/platform/version. No invented zero weeks.

Refresh uses one pinned report ID for the calendar and every observation page. Only complete downloads replace the cache. Backend errors show the last locally saved snapshot with an explicit saved-data banner, or a retry screen when none exists. Cached data is scoped to the API URL. Basemap tiles need internet; offline basemap coverage is not guaranteed. Refresh reloads the newest report from the server. The server itself fetches new NASA data every 3 hours.

HTTP is allowed only for localhost/emulator hosts in the debug Android manifest. Release deployments require an HTTPS API URL. The application ID remains `com.example.simple_todo` so it updates the practice app without deleting its stored data; the display name is now AgniCalendar. The old to-do UI is replaced; its saved tasks key is untouched.

## Checks

```bash
flutter analyze
flutter test
flutter build apk --debug
```

The APK is at `build/app/outputs/flutter-apk/app-debug.apk`. Main code is organized into `lib/screens`, `lib/models`, and `lib/services`.

Map data: © OpenStreetMap contributors. Tile requests identify this application; no bulk tile download is performed. FIRMS detections are pixels, not individual fires or burned areas.
