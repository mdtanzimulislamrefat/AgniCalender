"""Keep the last 30 days of NRT detections (FIRMS area API, free MAP_KEY) for map time windows.

Example: python -m backend.processing.recent --days 30
"""
import argparse
import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.request import urlopen

import psycopg

from backend.config import firms_map_key
from backend.geo import boundary
from backend.paths import RAW_DIR
from backend.processing.pipeline import DEFAULT_BBOX, fetch_latest, normalize, read_csv
from backend.storage.reports import connect

log = logging.getLogger('uvicorn.error')
API = 'https://firms.modaps.eosdis.nasa.gov/api/area/csv'
# Kept separate: each satellite/product is its own source.
SOURCES = ('MODIS_NRT', 'VIIRS_SNPP_NRT', 'VIIRS_NOAA20_NRT', 'VIIRS_NOAA21_NRT')
MAX_DAYS_PER_REQUEST = 5  # FIRMS area API limit
KEEP_DAYS = 45


def plan(covered, today, days):
    """Chunks (start day, day count) for days not yet complete; today and yesterday are always refetched."""
    wanted = [today - timedelta(days=n) for n in range(days - 1, -1, -1)]
    missing = [d for d in wanted if d not in covered or d >= today - timedelta(days=1)]
    chunks = []
    for day in missing:
        if chunks and chunks[-1][0] + timedelta(days=chunks[-1][1]) == day and chunks[-1][1] < MAX_DAYS_PER_REQUEST:
            chunks[-1] = (chunks[-1][0], chunks[-1][1] + 1)
        else:
            chunks.append((day, 1))
    return chunks


def download(key, source, start, days):
    bbox = ','.join(str(v) for v in DEFAULT_BBOX)
    with urlopen(f'{API}/{key}/{source}/{bbox}/{days}/{start.isoformat()}', timeout=90) as response:
        return response.read()


def parse(data, source, region):
    """Rows inside the country outline, normalized like the 24-hour pipeline."""
    records, invalid, outside = [], 0, 0
    for row in read_csv(data):  # an error message instead of CSV raises ValueError
        try:
            record = normalize(row, source)
        except (ValueError, TypeError, AttributeError):
            invalid += 1
            continue
        if not region.contains(record['longitude'], record['latitude']):
            outside += 1
            continue
        records.append(record)
    return records, {'retained': len(records), 'invalid': invalid, 'outside_region': outside}


def store(connection, source, records, start, days, today):
    with connection.cursor() as cursor:
        cursor.executemany(
            'INSERT INTO recent_detections (source, satellite, version, observed_at, latitude, longitude, quality, '
            'confidence_raw, frp_mw, daynight) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            [(source, r['satellite'], r['version'], r['timestamp_utc'], r['latitude'], r['longitude'], r['quality'],
              r['confidence_raw'], r['frp_mw'], r['daynight']) for r in records])
        cursor.executemany(
            'INSERT INTO recent_coverage (source, day, complete) VALUES (%s,%s,%s) ON CONFLICT (source, day) '
            'DO UPDATE SET complete=EXCLUDED.complete, fetched_at=now()',
            [(source, start + timedelta(days=n), start + timedelta(days=n) < today - timedelta(days=1))
             for n in range(days)])


def refresh_recent(days=30, key=None, fetch=download, today=None, raw_dir=RAW_DIR):
    """Fetch missing recent days for every NRT source. Returns a summary without the key."""
    key = key or firms_map_key()
    if key is None:
        return {'status': 'disabled'}
    today = today or datetime.now(timezone.utc).date()
    region = boundary('bangladesh')
    raw_dir.mkdir(parents=True, exist_ok=True)
    requests = retained = 0
    for source in SOURCES:
        with connect() as connection:
            covered = {row['day'] for row in connection.execute(
                'SELECT day FROM recent_coverage WHERE source=%s AND complete', (source,))}
        for start, count in plan(covered, today, days):
            data = fetch(key, source, start, count)
            records, stats = parse(data, source, region)
            snapshot = raw_dir / f'{hashlib.sha256(data).hexdigest()}.csv'
            if not snapshot.exists():
                snapshot.write_bytes(data)
            with connect() as connection:  # one transaction per request
                store(connection, source, records, start, count, today)
            requests += 1
            retained += stats['retained']
    with connect() as connection:
        connection.execute('DELETE FROM recent_detections WHERE observed_at < %s', (today - timedelta(days=KEEP_DAYS),))
        connection.execute('DELETE FROM recent_coverage WHERE day < %s', (today - timedelta(days=KEEP_DAYS),))
    return {'status': 'ok', 'requests': requests, 'retained': retained}


def refresh_all():
    """Scheduler job: the 24-hour report and the recent store, each independent of the other's failure."""
    outcome, errors = {}, []
    try:
        outcome.update(fetch_latest())
    except Exception as exc:  # noqa: BLE001 - reported below; the other half still runs
        errors.append(exc)
        outcome['status'] = 'failed'
    try:
        outcome['recent'] = refresh_recent()
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
        outcome['recent'] = {'status': 'failed'}
        log.warning('Recent NASA fetch failed (%s)', type(exc).__name__)
    if len(errors) == 2:
        raise errors[0]
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--days', type=int, default=30, choices=range(1, KEEP_DAYS + 1), metavar='1-45')
    args = parser.parse_args()
    if firms_map_key() is None:
        parser.exit(1, 'Set FIRMS_MAP_KEY in backend/.env (free: https://firms.modaps.eosdis.nasa.gov/api/map_key/)\n')
    try:
        result = refresh_recent(args.days)
    except psycopg.Error:
        parser.exit(1, 'PostgreSQL unavailable or schema missing; run: python -m backend.storage.manage migrate\n')
    except (ValueError, OSError) as exc:
        # Messages never include the request URL, so the key is not printed.
        parser.exit(1, f'Fetch failed: {type(exc).__name__}: {exc}\n')
    print(f"{result['requests']} request(s), {result['retained']} detection(s) inside Bangladesh")


if __name__ == '__main__':
    main()
