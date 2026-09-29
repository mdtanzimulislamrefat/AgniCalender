"""Map points for a time window: recent NRT days or one archive month."""
from datetime import datetime, time, timedelta, timezone

POINT = ("source, satellite, version, latitude, longitude, quality, confidence_raw, frp_mw, daynight, "
         "to_char(observed_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"+00:00\"') AS timestamp_utc")
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
          'July', 'August', 'September', 'October', 'November', 'December']


def counts(points):
    result = {}
    for p in points:
        result[p['source']] = result.get(p['source'], 0) + 1
    return result


def recent(connection, days, now=None):
    """The last `days` whole UTC days, today included."""
    now = now or datetime.now(timezone.utc)
    start = datetime.combine(now.date() - timedelta(days=days - 1), time.min, tzinfo=timezone.utc)
    points = connection.execute(f'SELECT {POINT} FROM recent_detections WHERE observed_at >= %s '
                                'ORDER BY observed_at, source', (start,)).fetchall()
    covered = {row['day'] for row in connection.execute(
        'SELECT DISTINCT day FROM recent_coverage WHERE day >= %s', (start.date(),))}
    wanted = [start.date() + timedelta(days=n) for n in range((now.date() - start.date()).days + 1)]
    return {'kind': 'recent', 'label': f'Last {days} days', 'start_utc': start, 'end_utc': now,
            'fire_type_known': False, 'excluded_other_types': 0,
            'missing_days': [d for d in wanted if d not in covered],
            'counts_by_source': counts(points), 'points': points}


def archive_month(connection, year, month, region='Bangladesh'):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=timezone.utc)
    base = ('FROM archive_detections d JOIN archive_files f ON f.id = d.file_id '
            'WHERE f.region=%s AND d.observed_at >= %s AND d.observed_at < %s')
    points = connection.execute(
        f'SELECT {POINT.replace("source", "f.source AS source", 1)} {base} AND d.fire_type = 0 '
        'ORDER BY d.observed_at, f.source', (region, start, end)).fetchall()
    other = connection.execute(f'SELECT count(*) AS n {base} AND d.fire_type <> 0', (region, start, end)).fetchone()['n']
    return {'kind': 'archive', 'label': f'{MONTHS[month - 1]} {year}', 'start_utc': start, 'end_utc': end,
            'fire_type_known': True, 'excluded_other_types': other, 'missing_days': [],
            'counts_by_source': counts(points), 'points': points}


def available(connection, region='Bangladesh'):
    months = connection.execute(
        "SELECT to_char(d.observed_at, 'YYYY-MM') AS month, count(*) AS count "
        'FROM archive_detections d JOIN archive_files f ON f.id = d.file_id '
        'WHERE f.region=%s AND d.fire_type = 0 GROUP BY 1 ORDER BY 1', (region,)).fetchall()
    days = connection.execute('SELECT min(day) AS first, max(day) AS last FROM recent_coverage').fetchone()
    return {'archive_months': months, 'recent_first_day': days['first'], 'recent_last_day': days['last']}
