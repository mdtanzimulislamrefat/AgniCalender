"""Copy the NASA data tables (never accounts or sessions) to another PostgreSQL, e.g. a hosted one."""
from psycopg.conninfo import conninfo_to_dict

from backend.config import database_url
from backend.storage.reports import connect, migrate

# Parents before children; users, refresh_sessions, saved_areas and auth_events are never copied.
TABLES = ['archive_files', 'archive_detections', 'reports', 'observations', 'recent_detections', 'recent_coverage']
IDENTITY = ['archive_files', 'reports']


def same_database(a, b):
    keys = ('host', 'port', 'dbname')
    return all(conninfo_to_dict(a).get(k) == conninfo_to_dict(b).get(k) for k in keys)


def publish(target_url, source_url=None):
    """Replace the target's data tables with this database's, in one transaction. Returns row counts."""
    source_url = source_url or database_url()
    if same_database(source_url, target_url):
        raise ValueError('Target is the same database as the source')
    migrate(target_url)
    counts = {}
    with connect(source_url) as source, connect(target_url) as target:
        target.execute(f"TRUNCATE {', '.join(TABLES)}")
        for table in TABLES:
            columns = ', '.join(row['column_name'] for row in source.execute(
                'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() '
                'AND table_name=%s ORDER BY ordinal_position', (table,)))
            with source.cursor().copy(f'COPY {table} ({columns}) TO STDOUT') as out, \
                    target.cursor().copy(f'COPY {table} ({columns}) FROM STDIN') as into:
                for chunk in out:
                    into.write(chunk)
            counts[table] = target.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n']
        for table in IDENTITY:  # new rows (e.g. the next pipeline run) continue after the copied ids
            target.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                           f"(SELECT coalesce(max(id), 0) + 1 FROM {table}), false)")
    return counts
