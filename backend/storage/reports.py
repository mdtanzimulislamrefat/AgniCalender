"""Transactional PostgreSQL snapshots with separately indexed map observations."""
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.config import database_url


def connect(url=None):
    return psycopg.connect(url or database_url(), connect_timeout=5,
                           options='-c statement_timeout=10000 -c timezone=UTC', row_factory=dict_row)


def migrate(url=None):
    with connect(url) as connection:
        # Serialize schema initialization across concurrent CLI processes.
        connection.execute('SELECT pg_advisory_xact_lock(71425001)')
        connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)')
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            if connection.execute('SELECT version FROM schema_migrations WHERE version=%s', (path.name,)).fetchone():
                continue
            connection.execute(path.read_text())
            connection.execute('INSERT INTO schema_migrations VALUES (%s)', (path.name,))


def save_report(report, url=None):
    # Keep original report for audit; do not mutate it or sum rolling snapshots.
    payload = json.dumps(report, sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with connect(url) as connection:
        row = connection.execute(
            'INSERT INTO reports (digest, generated_at, payload) VALUES (%s,%s,%s) '
            'ON CONFLICT (digest) DO NOTHING RETURNING id',
            (digest, report['generated_at_utc'], Jsonb(report)),
        ).fetchone()
        if row:
            values = [(row['id'], i, r['source'], r['timestamp_utc'], r['latitude'], r['longitude'], Jsonb(r))
                      for i, r in enumerate(report['observations'])]
            with connection.cursor() as cursor:
                cursor.executemany('INSERT INTO observations VALUES (%s,%s,%s,%s,%s,%s,%s)', values)
        else:
            row = connection.execute('SELECT id FROM reports WHERE digest=%s', (digest,)).fetchone()
    return row['id']


def get_report(connection, report_id=None):
    if report_id is None:
        # Historical backfills must not accidentally replace newer published data.
        return connection.execute('SELECT id, payload FROM reports ORDER BY generated_at DESC, id DESC LIMIT 1').fetchone()
    return connection.execute('SELECT id, payload FROM reports WHERE id=%s', (report_id,)).fetchone()


def latest_report(url=None):
    with connect(url) as connection:
        row = get_report(connection)
    return row['payload'] if row else None


def observation_page(connection, report_id, limit, offset, source=None, start=None, end=None, bbox=None):
    clauses, params = ['report_id=%s'], [report_id]
    if bbox is not None:
        clauses.append('longitude BETWEEN %s AND %s AND latitude BETWEEN %s AND %s')
        params += [bbox[0], bbox[2], bbox[1], bbox[3]]
    if source is not None:
        clauses.append('source=%s')
        params.append(source)
    if start is not None:
        clauses.append('observed_at >= %s::date')
        params.append(start)
    if end is not None:
        clauses.append("observed_at < (%s::date + INTERVAL '1 day')")
        params.append(end)
    where = ' AND '.join(clauses)
    count = connection.execute('SELECT count(*) AS total FROM observations WHERE '+where, params).fetchone()['total']
    rows = connection.execute('SELECT payload FROM observations WHERE '+where+' ORDER BY ordinal LIMIT %s OFFSET %s',
                              params + [limit, offset]).fetchall()
    return count, [row['payload'] for row in rows]
