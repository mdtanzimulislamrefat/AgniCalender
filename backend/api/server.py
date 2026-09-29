"""FastAPI service for AgniCalendar. Run from the repository root."""
import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from backend.api import account, archive, auth, hotspots
from backend.api.database import Connection, get_connection  # noqa: F401 - tests override get_connection
from backend.api.models import Calendar, ObservationPage, Summary
from backend.api.params import BBox, parse_bbox
from backend.api.scheduler import FetchScheduler, parse_interval
from backend.config import jwt_secret
from backend.storage.reports import get_report, observation_page

@asynccontextmanager
async def lifespan(app):
    scheduler = app.state.scheduler
    task = asyncio.create_task(scheduler.loop()) if scheduler and scheduler.interval else None
    yield
    if task:
        task.cancel()


app = FastAPI(title='AgniCalendar API', version='1.0.0', lifespan=lifespan,
              description='NASA FIRMS sensor-specific observations. No fire prediction or calibrated harmonization. '
                          'Data endpoints require a bearer access token from /v1/auth/login.')
app.state.scheduler = None  # main() installs the NASA fetch scheduler
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(archive.router)
app.include_router(hotspots.router)
app.add_middleware(GZipMiddleware, minimum_size=2000)  # month maps can be several MB of JSON


@app.exception_handler(psycopg.Error)
async def database_error(request, exc):
    # Credentials, SQL text and server addresses are never sent to the client.
    return JSONResponse(status_code=503, content={'detail': 'Database unavailable or schema not initialized'})


def required_report(connection, report_id):
    row = get_report(connection, report_id)
    if row is None:
        if report_id is not None:
            raise HTTPException(404, 'Report not found')
        raise HTTPException(503, 'No processed data. Import a report or run the pipeline.')
    return row


def metadata(row):
    report = row['payload']
    times = [datetime.fromisoformat(r['timestamp_utc']) for r in report['observations']]
    start, end = (min(times), max(times)) if times else (None, None)
    # Freshness means latest detected pixel age, not observation completeness.
    stale = end is None or datetime.now(timezone.utc) - end > timedelta(hours=48)
    return {key: report[key] for key in ('generated_at_utc', 'bbox', 'timezone', 'limitations')} | {
        'region': (report.get('region') or {}).get('name'),
        'report_id': row['id'], 'data_start_utc': start, 'data_end_utc': end, 'stale': stale,
    }


# Every /v1 data route requires a signed-in user; /health, /ready and /docs stay public.
Authenticated = [Depends(auth.current_user)]
ReportID = Annotated[int | None, Query(ge=1, description='Pin this ID across paginated requests; omit for newest generated report')]


@app.get('/health', tags=['health'])
def health():
    scheduler = app.state.scheduler
    return {'status': 'ok', 'service': 'agnicalendar',
            'nasa_fetch': scheduler.status if scheduler else {'enabled': False}}


@app.get('/ready', tags=['health'])
def ready(connection: Connection):
    connection.execute('SELECT id FROM reports LIMIT 1')
    return {'status': 'ready', 'database': 'postgresql'}


@app.get('/v1/summary', dependencies=Authenticated, response_model=Summary, tags=['data'])
def summary(connection: Connection, report_id: ReportID = None):
    row = required_report(connection, report_id)
    report = row['payload']
    return metadata(row) | {
        'schema_version': report['schema_version'], 'counts': report['counts'],
        'counts_by_source': dict(Counter(r['source'] for r in report['observations'])),
    }


@app.get('/v1/calendar', dependencies=Authenticated, response_model=Calendar, tags=['data'])
def calendar(connection: Connection, report_id: ReportID = None,
             source: Annotated[str | None, Query(max_length=80)] = None):
    row = required_report(connection, report_id)
    weeks = row['payload']['weekly_calendar']
    if source is not None:
        weeks = [week for week in weeks if week['source'] == source]
    return metadata(row) | {'weeks': weeks}


@app.get('/v1/observations', dependencies=Authenticated, response_model=ObservationPage, tags=['data'])
def observations(connection: Connection, report_id: ReportID = None,
                 limit: Annotated[int, Query(ge=1, le=1000)] = 100,
                 offset: Annotated[int, Query(ge=0)] = 0,
                 source: Annotated[str | None, Query(max_length=80)] = None,
                 start: date | None = None, end: date | None = None, bbox: BBox = None):
    if start and end and start > end:
        raise HTTPException(422, 'start must be on or before end')
    box = parse_bbox(bbox)
    row = required_report(connection, report_id)
    total, records = observation_page(connection, row['id'], limit, offset, source, start, end, box)
    return metadata(row) | {'total': total, 'offset': offset, 'limit': limit, 'observations': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--behind-proxy', action='store_true',
                        help='Trust X-Forwarded-For from a hosting proxy (per-client rate limits)')
    parser.add_argument('--fetch-every', default='3h', metavar='INTERVAL',
                        help="Fetch NASA's rolling feeds at startup and then every INTERVAL (e.g. 30m, 3h, 1d) "
                             "while the server runs; 'off' disables it")
    args = parser.parse_args()
    try:
        interval = parse_interval(args.fetch_every)
    except ValueError as exc:
        parser.error(f'--fetch-every: {exc}')
    app.state.scheduler = FetchScheduler(interval)
    try:
        jwt_secret()  # Refuse to start without a signing secret.
    except RuntimeError as exc:
        parser.exit(1, f'{exc}\n')
    # Behind a host's proxy every request comes from the proxy; use the forwarded client address.
    proxy = {'proxy_headers': True, 'forwarded_allow_ips': '*'} if args.behind_proxy else {}
    uvicorn.run(app, host=args.host, port=args.port, **proxy)


if __name__ == '__main__':
    main()
