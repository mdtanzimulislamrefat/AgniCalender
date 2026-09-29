"""Schema migrations and non-destructive imports from JSON or legacy SQLite."""
import argparse
import json
import sqlite3
from pathlib import Path

from backend.config import ensure_jwt_secret
from backend.storage.reports import migrate, save_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['migrate', 'import-json', 'import-sqlite'])
    parser.add_argument('file', nargs='?', type=Path)
    args = parser.parse_args()
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
