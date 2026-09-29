"""Import yearly FIRMS archive data (standard products) for the multi-year season calendar.

Years with a published country file use it (public, no key). Newer years that NASA has not
published as files yet are fetched through the area API when FIRMS_MAP_KEY is set.

Example: python -m backend.processing.archive --years 2000-2026
"""
import argparse
import csv
import hashlib
import io
import json
import time
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import psycopg

from backend.config import firms_map_key
from backend.geo import boundary
from backend.paths import RAW_DIR
from backend.processing.pipeline import DEFAULT_BBOX, normalize, read_csv
from backend.processing.recent import MAX_DAYS_PER_REQUEST as MAX_DAYS
from backend.storage.archive import imported_digest, mark_unavailable, save_file
from backend.storage.reports import connect

BASE = 'https://firms.modaps.eosdis.nasa.gov/data/country'
# FIRMS directory name -> source label. JPSS-1 is NOAA-20.
PRODUCTS = {'modis': 'MODIS_ARCHIVE', 'viirs-snpp': 'VIIRS_SNPP_ARCHIVE', 'viirs-jpss1': 'VIIRS_NOAA20_ARCHIVE'}
FIRST_YEAR = {'modis': 2000, 'viirs-snpp': 2012, 'viirs-jpss1': 2018}


def file_url(product, year, country):
    return f'{BASE}/{product}/{year}/{product}_{year}_{country}.csv'


def parse(data, source):
    """Validate one yearly file. Returns (records, stats); bad rows are counted, never guessed."""
    rows = read_csv(data)
    if rows and 'type' not in rows[0]:
        raise ValueError('Not a FIRMS archive CSV: type column missing')
    stats = {'rows_read': len(rows), 'retained': 0, 'invalid': 0, 'exact_duplicates': 0}
    seen, records = set(), []
    for row in rows:
        try:
            record = normalize(row, source)
            fire_type = int(row['type'].strip())
            if fire_type not in (0, 1, 2, 3):
                raise ValueError('invalid type')
        except (ValueError, TypeError, AttributeError):
            stats['invalid'] += 1
            continue
        identity = json.dumps(row, sort_keys=True)
        if identity in seen:
            stats['exact_duplicates'] += 1
            continue
        seen.add(identity)
        records.append(record | {'fire_type': fire_type})
        stats['retained'] += 1
    return records, stats


def download(url, attempts=3):
    """Return the file bytes, or None when FIRMS has not published it (404)."""
    for attempt in range(attempts):
        try:
            with urlopen(url, timeout=90) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == attempts - 1:
                raise
        except URLError:
            if attempt == attempts - 1:
                raise
        time.sleep(2 * (attempt + 1))


API = 'https://firms.modaps.eosdis.nasa.gov/api'
# Same standard (SP) products as the yearly country files, via the MAP_KEY area API.
API_PRODUCTS = {'modis': 'MODIS_SP', 'viirs-snpp': 'VIIRS_SNPP_SP', 'viirs-jpss1': 'VIIRS_NOAA20_SP'}


def sp_availability(key):
    """Last day of standard-product data per API product, e.g. {'MODIS_SP': date(2026, 6, 30)}."""
    with urlopen(f'{API}/data_availability/csv/{key}/all', timeout=60) as response:
        rows = csv.DictReader(io.StringIO(response.read().decode()))
        return {r['data_id']: date.fromisoformat(r['max_date']) for r in rows if r['data_id'] in API_PRODUCTS.values()}


def api_download(key, product, start, days, attempts=5):
    """One area request (≤5 days); waits and retries when the transaction limit is reached."""
    bbox = ','.join(str(v) for v in DEFAULT_BBOX)
    for attempt in range(attempts):
        with urlopen(f'{API}/area/csv/{key}/{product}/{bbox}/{days}/{start.isoformat()}', timeout=90) as response:
            data = response.read()
        if b'latitude' in data.split(b'\n', 1)[0]:
            return data
        if b'limit' in data.lower() and attempt < attempts - 1:
            time.sleep(60)
            continue
        # FIRMS answers errors as plain text; never echo the URL (it holds the key).
        raise ValueError(f'FIRMS API refused the request: {data[:80].decode(errors="replace").strip()}')


def api_year(key, product, year, through, fetch=api_download):
    """Concatenate 5-day API chunks for one year up to `through` into a single CSV."""
    start, end = date(year, 1, 1), min(date(year, 12, 31), through)
    header, rows, day = None, [], start
    while day <= end:
        count = min(MAX_DAYS, (end - day).days + 1)
        lines = fetch(key, API_PRODUCTS[product], day, count).decode('utf-8-sig').splitlines()
        if header is None:
            header = lines[0]
        elif lines[0] != header:
            raise ValueError('FIRMS API columns changed within a year')
        rows += [line for line in lines[1:] if line]
        day += timedelta(days=count)
    return ('\n'.join([header, *rows]) + '\n').encode()


def import_year(product, year, country, raw_dir, force=False, fetch=download, key=None, sp_end=None,
                api_fetch=api_download):
    """Import one year: the yearly country file, or (not yet published) the same product via the API."""
    source, url = PRODUCTS[product], file_url(product, year, country)
    data = fetch(url)
    via_api = False
    if data is None:
        through = (sp_end or {}).get(API_PRODUCTS[product])
        if key is None or through is None or through < date(year, 1, 1):
            mark_unavailable(source, year, country, url)
            return 'unavailable', None
        through = min(date(year, 12, 31), through)
        url = f'firms-api:{API_PRODUCTS[product]}/{year}?through={through.isoformat()}'
        with connect() as connection:
            stored = connection.execute('SELECT url FROM archive_files WHERE source=%s AND year=%s AND region=%s '
                                        "AND status='imported'", (source, year, country)).fetchone()
        if stored and stored['url'] == url and not force:
            return 'unchanged', None  # already fetched through the same day; skip ~70 requests
        data, via_api = api_year(key, product, year, through, api_fetch), True
    digest = hashlib.sha256(data).hexdigest()
    with connect() as connection:
        if not force and imported_digest(connection, source, year, country) == digest:
            return 'unchanged', None
    records, stats = parse(data, source)  # rejects HTML error pages before anything is stored
    if via_api:
        # The API returns a rectangle; keep the same country outline the FIRMS files use.
        region = boundary(country.lower())
        inside = [r for r in records if region.contains(r['longitude'], r['latitude'])]
        stats['outside_region'] = len(records) - len(inside)
        stats['retained'] = len(inside)
        records = inside
    snapshot = raw_dir / f'{digest}.csv'
    if not snapshot.exists():
        snapshot.write_bytes(data)
    save_file(source, year, country, url, digest, records, stats)
    return 'imported' + (' via API' if via_api else ''), stats


def year_range(text):
    first, _, last = text.partition('-')
    first, last = int(first), int(last or first)
    if not 2000 <= first <= last <= 2100:
        raise argparse.ArgumentTypeError('Use YEAR or FIRST-LAST between 2000 and 2100')
    return range(first, last + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--years', type=year_range, default=year_range(f'2000-{datetime.now(timezone.utc).year}'))
    parser.add_argument('--products', default=','.join(PRODUCTS), help='Comma list of ' + ', '.join(PRODUCTS))
    parser.add_argument('--country', default='Bangladesh', help='FIRMS country file name, e.g. Bangladesh')
    parser.add_argument('--force', action='store_true', help='Re-import files whose content is unchanged')
    args = parser.parse_args()
    products = args.products.split(',')
    if unknown := set(products) - set(PRODUCTS):
        parser.error(f'Unknown products: {", ".join(sorted(unknown))}')
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    key, sp_end = firms_map_key(), None
    if key:
        try:
            sp_end = sp_availability(key)
        except (ValueError, OSError, KeyError) as exc:
            print(f'FIRMS API availability unavailable ({type(exc).__name__}); using yearly files only')
    else:
        print('No FIRMS_MAP_KEY: years NASA has not published as files are skipped')
    failures = 0
    for product in products:
        for year in args.years:
            if year < FIRST_YEAR[product]:
                continue
            try:
                status, stats = import_year(product, year, args.country, RAW_DIR, args.force, key=key, sp_end=sp_end)
            except psycopg.Error:
                parser.exit(1, 'PostgreSQL unavailable or schema missing; run: python -m backend.storage.manage migrate\n')
            except (ValueError, OSError) as exc:
                failures += 1
                status, stats = f'failed: {exc}', None
            detail = f" {stats['retained']} kept, {stats['invalid']} invalid" if stats else ''
            if stats and 'outside_region' in stats:
                detail += f", {stats['outside_region']} outside Bangladesh"
            print(f'{PRODUCTS[product]} {year}: {status}{detail}', flush=True)
    if failures:
        parser.exit(1, f'{failures} file(s) failed; re-run to retry.\n')


if __name__ == '__main__':
    main()
