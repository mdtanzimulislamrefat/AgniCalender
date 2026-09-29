"""Start/stop a localhost PostgreSQL 17 container using host Podman.
Run this script in the PC's normal terminal; no secrets appear in arguments.
"""
import argparse
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAME = 'agnicalendar-postgres'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'], default='start', nargs='?')
    args = parser.parse_args()
    if args.action != 'start':
        subprocess.run(['podman', 'stop', NAME] if args.action == 'stop' else
                       ['podman', 'ps', '-a', '--filter', 'name='+NAME], check=True)
        return
    env_file = ROOT / '.env'
    if not env_file.exists():
        password = secrets.token_hex(24)
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(f'POSTGRES_USER=agnicalendar\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=agnicalendar\n'
                       f'DATABASE_URL=postgresql://agnicalendar:{password}@127.0.0.1:55432/agnicalendar\n'
                       f'AUTH_JWT_SECRET={secrets.token_urlsafe(48)}\n')
    exists = subprocess.run(['podman', 'container', 'exists', NAME]).returncode == 0
    if exists:
        subprocess.run(['podman', 'start', NAME], check=True)
    else:
        subprocess.run(['podman', 'run', '-d', '--name', NAME,
                        '--env-file', str(env_file), '-p', '127.0.0.1:55432:5432',
                        '-v', 'agnicalendar-pgdata:/var/lib/postgresql/data',
                        '--health-cmd', 'pg_isready -U agnicalendar -d agnicalendar',
                        '--health-interval', '5s', '--health-retries', '12',
                        'docker.io/library/postgres:17'], check=True)
    print('PostgreSQL on 127.0.0.1:55432. Credentials: backend/.env (gitignored).')


if __name__ == '__main__':
    main()
