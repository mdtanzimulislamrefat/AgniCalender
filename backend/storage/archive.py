"""Multi-year FIRMS archive storage and the monthly season (climatology) query."""
import statistics
from datetime import datetime, timezone

from backend.storage.reports import connect

# First calendar year each product observed all twelve months:
# MODIS Aqua data starts Sep 2002 (Terra-only before), VIIRS S-NPP 2012-01-20, NOAA-20 2018-04-01.
FULL_FROM = {'MODIS_ARCHIVE': 2003, 'VIIRS_SNPP_ARCHIVE': 2013, 'VIIRS_NOAA20_ARCHIVE': 2019}
FIRE_TYPES = {0: 'vegetation', 1: 'volcano', 2: 'static_land', 3: 'offshore'}
COLUMNS = ('file_id', 'observed_at', 'latitude', 'longitude', 'satellite', 'version',
           'quality', 'confidence_raw', 'frp_mw', 'daynight', 'fire_type')
LIMITATIONS = [
    'Region is the FIRMS country boundary, not the rectangle used for recent data.',
    'Counts are detected pixels (hotspots), not distinct fires or burned area.',
    'MODIS, VIIRS S-NPP and VIIRS NOAA-20 are kept separate; do not add or directly compare their counts.',
    'Averages use complete mission years only. Orbits and sensor ageing change observation '
    'opportunities, so year-to-year differences are not calibrated trends.',
    'Each product keeps its FIRMS processing version per year (e.g. MODIS 6.2, 6.03 and 61.03); '
    'averages span these versions, which may differ slightly in detection.',
    'A month with no detections counts as zero detections, not zero fires (clouds, timing, small fires).',
    'Only FIRMS type 0 (presumed vegetation fire) is counted by default; static land and offshore '
    'detections are reported separately.',
    'Months are in UTC.',
]


def imported_digest(connection, source, year, region):
    row = connection.execute("SELECT sha256 FROM archive_files WHERE source=%s AND year=%s AND region=%s "
                             "AND status='imported'", (source, year, region)).fetchone()
    return row['sha256'] if row else None


def save_file(source, year, region, url, digest, records, stats):
    """Replace one yearly file's detections atomically."""
    with connect() as connection:
        file_id = connection.execute(
            "INSERT INTO archive_files (source, year, region, url, status, sha256, rows_read, retained, invalid) "
            "VALUES (%s,%s,%s,%s,'imported',%s,%s,%s,%s) "
            "ON CONFLICT (source, year, region) DO UPDATE SET url=EXCLUDED.url, status='imported', "
            "sha256=EXCLUDED.sha256, rows_read=EXCLUDED.rows_read, retained=EXCLUDED.retained, "
            "invalid=EXCLUDED.invalid, retrieved_at=now() RETURNING id",
            (source, year, region, url, digest, stats['rows_read'], stats['retained'], stats['invalid']),
        ).fetchone()['id']
        connection.execute('DELETE FROM archive_detections WHERE file_id=%s', (file_id,))
        with connection.cursor().copy(f"COPY archive_detections ({', '.join(COLUMNS)}) FROM STDIN") as copy:
            for r in records:
                copy.write_row((file_id, r['timestamp_utc'], r['latitude'], r['longitude'], r['satellite'],
                                r['version'], r['quality'], r['confidence_raw'], r['frp_mw'], r['daynight'],
                                r['fire_type']))
    return file_id


def mark_unavailable(source, year, region, url):
    """Record a missing yearly file; never downgrades a file that was imported before."""
    with connect() as connection:
        connection.execute(
            "INSERT INTO archive_files (source, year, region, url, status) VALUES (%s,%s,%s,%s,'unavailable') "
            "ON CONFLICT (source, year, region) DO UPDATE SET retrieved_at=now() "
            "WHERE archive_files.status='unavailable'", (source, year, region, url))


def season(connection, region, fire_types=(0,), bbox=None, now=None):
    """Monthly detection counts per product and year, with averages over complete years."""
    this_year = (now or datetime.now(timezone.utc)).year
    files = connection.execute("SELECT source, year, status FROM archive_files WHERE region=%s ORDER BY source, year",
                               (region,)).fetchall()
    clauses, params = ['f.region=%s'], [region]
    if bbox is not None:
        clauses.append('d.longitude BETWEEN %s AND %s AND d.latitude BETWEEN %s AND %s')
        params += [bbox[0], bbox[2], bbox[1], bbox[3]]
    where = ' AND '.join(clauses)
    rows = connection.execute(
        'SELECT f.source, f.year, extract(month FROM d.observed_at)::int AS month, d.fire_type, count(*) AS n '
        f'FROM archive_detections d JOIN archive_files f ON f.id = d.file_id WHERE {where} '
        'GROUP BY 1, 2, 3, 4', params).fetchall()
    spans = connection.execute(
        'SELECT f.source, d.version, min(f.year) AS first_year, max(f.year) AS last_year '
        'FROM archive_detections d JOIN archive_files f ON f.id = d.file_id WHERE f.region=%s '
        'GROUP BY 1, 2 ORDER BY 1, 3', (region,)).fetchall()
    versions = {}
    for span in spans:
        versions.setdefault(span['source'], []).append(
            {k: span[k] for k in ('version', 'first_year', 'last_year')})

    products = {}
    for f in files:
        product = products.setdefault(f['source'], {'source': f['source'], 'imported': {}, 'unavailable': [],
                                                    'other_types': {}})
        if f['status'] == 'imported':
            product['imported'][f['year']] = [0] * 12  # zero-filled: the file exists for this year
        else:
            product['unavailable'].append(f['year'])
    for row in rows:
        product = products[row['source']]
        if row['fire_type'] in fire_types:
            product['imported'][row['year']][row['month'] - 1] += row['n']
        else:
            name = FIRE_TYPES[row['fire_type']]
            product['other_types'][name] = product['other_types'].get(name, 0) + row['n']

    result = []
    for source, product in sorted(products.items()):
        years = sorted(product['imported'])
        complete = [y for y in years if FULL_FROM.get(source, 0) <= y < this_year]
        months = []
        for m in range(12):
            values = [product['imported'][y][m] for y in complete]
            months.append({'month': m + 1, 'mean': round(statistics.fmean(values), 1) if values else None,
                           'median': statistics.median(values) if values else None,
                           'min': min(values) if values else None, 'max': max(values) if values else None})
        means = [m['mean'] for m in months if m['mean'] is not None]
        result.append({
            'source': source, 'versions': versions.get(source, []),
            'complete_years': complete, 'partial_years': [y for y in years if y not in complete],
            'unavailable_years': product['unavailable'],
            'peak_month': months[means.index(max(means))]['month'] if means and max(means) > 0 else None,
            'months': months,
            'by_year': {str(y): product['imported'][y] for y in years},
            'other_types': product['other_types'],
        })
    return {'region': region, 'fire_types': [FIRE_TYPES[t] for t in fire_types], 'timezone': 'UTC',
            'bbox': list(bbox) if bbox else None, 'products': result, 'limitations': LIMITATIONS}
