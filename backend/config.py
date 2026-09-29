"""Read local configuration without exposing database credentials or signing secrets."""
import functools
import os
import secrets

from dotenv import load_dotenv
from backend.paths import BACKEND_DIR

ENV_FILE = BACKEND_DIR / '.env'


def database_url():
    load_dotenv(ENV_FILE, override=False)
    url = os.environ.get('DATABASE_URL', '')
    if not url.startswith(('postgresql://', 'postgres://')):
        raise RuntimeError('Set a PostgreSQL DATABASE_URL in backend/.env')
    return url


def firms_map_key():
    """Free NASA FIRMS API key, or None when not configured (7/30-day map windows off)."""
    load_dotenv(ENV_FILE, override=False)
    return os.environ.get('FIRMS_MAP_KEY', '').strip() or None


@functools.cache
def jwt_secret():
    load_dotenv(ENV_FILE, override=False)
    secret = os.environ.get('AUTH_JWT_SECRET', '')
    if len(secret) < 32:
        raise RuntimeError('Set AUTH_JWT_SECRET (32+ characters) in backend/.env; '
                           'run: python -m backend.storage.manage migrate')
    return secret


def ensure_jwt_secret(env_file=ENV_FILE):
    """Append a random signing secret to an existing .env once; never prints it."""
    if not env_file.exists() or 'AUTH_JWT_SECRET=' in env_file.read_text():
        return False
    with env_file.open('a') as file:
        file.write(f'AUTH_JWT_SECRET={secrets.token_urlsafe(48)}\n')
    return True
