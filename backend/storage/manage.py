"""Schema migrations, imports from JSON or legacy SQLite, and publishing data to a hosted database.

publish-data copies the NASA data tables (never user accounts) to --to URL, replacing its data:
    python -m backend.storage.manage publish-data --to "postgresql://…"
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from backend.config import ensure_jwt_secret
from backend.storage.publish import publish
from backend.storage.reports import migrate, save_report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['migrate', 'import-json', 'import-sqlite', 'publish-data'])
    parser.add_argument('file', nargs='?', type=Path)
    parser.add_argument('--to', metavar='DATABASE_URL', default=os.environ.get('PUBLISH_DATABASE_URL'),
                        help='publish-data target; or set PUBLISH_DATABASE_URL to keep it out of shell history')
    args = parser.parse_args()
    if args.command == 'publish-data':
        if not args.to:
            parser.error('publish-data needs --to DATABASE_URL or PUBLISH_DATABASE_URL')
        try:
            counts = publish(args.to)
        except ValueError as exc:
            parser.error(str(exc))
        for table, n in counts.items():
            print(f'{table}: {n} rows')
        print('Published. Accounts on the target were not touched.')
        return
    if args.command != 'migrate' and args.file is None:
        parser.error('An input file is required')
    migrate()
    if ensure_jwt_secret():
        print('Added a random AUTH_JWT_SECRET to backend/.env.')
    if args.command == 'import-json':
        save_report(json.loads(args.file.read_text()))
        print('Imported JSON report into PostgreSQL.')
    elif args.command == 'import-sqlite':
        with sqlite3.connect(args.file.resolve().as_uri()+'?mode=ro', uri=True) as connection:
            rows = connection.execute('SELECT payload FROM reports ORDER BY id').fetchall()
        for (payload,) in rows:
            save_report(json.loads(payload))
        print(f'Imported {len(rows)} snapshots; original SQLite file preserved.')
    else:
        print('PostgreSQL schema is ready.')


if __name__ == '__main__':
    main()
